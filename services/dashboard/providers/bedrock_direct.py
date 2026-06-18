"""BedrockDirectProvider — converse API + tool_use loop.

Bedrock directly manages conversations with tool calling. When the model
requests a tool (kubectl, aws cli, file read), the app executes it locally
and returns the result, enabling Bedrock to reason about actual cluster state.
"""
import json
import os
import sys
import threading
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ai_provider import AIProvider
from providers.tools import DEVOPS_TOOLS
from providers.tool_executor import execute_tool
from providers.system_prompts import SCENARIO_GEN, SCENARIO_FIX, CODE_FIX, IMPROVEMENTS


class BedrockDirectProvider(AIProvider):
    def __init__(self, profile: str = None, region: str = None, **kwargs):
        if profile is None:
            try:
                from app_config import _CFG, _cfg_get, AWS_REGION
                profile = _cfg_get(_CFG, "aws.profile", "member1-acc")
                region = region or AWS_REGION
            except (ImportError, Exception):
                profile = os.environ.get("AWS_PROFILE", "member1-acc")
                region = region or os.environ.get("AWS_REGION", "us-east-1")
        self._profile = profile
        self._region = region or "us-east-1"
        self._client = None
        self._lock = threading.Lock()
        self._sessions: dict[str, list] = {}

        try:
            from app_config import _CFG, _cfg_get
            self._model_id = _cfg_get(_CFG, "bedrock.default_model", "us.anthropic.claude-sonnet-4-6-v1:0")
            self._kubectl_context = _cfg_get(_CFG, "clusters.primary.context", "")
            self._namespace = _cfg_get(_CFG, "kubernetes.namespace", "dockercoins")
        except (ImportError, Exception):
            self._model_id = "us.anthropic.claude-sonnet-4-6-v1:0"
            self._kubectl_context = ""
            self._namespace = "dockercoins"

        self._init_client()

    def _init_client(self):
        import boto3
        from botocore.config import Config
        session = boto3.Session(profile_name=self._profile, region_name=self._region)
        self._client = session.client(
            "bedrock-runtime",
            config=Config(read_timeout=300, connect_timeout=10),
        )
        print(f"[BEDROCK-PROVIDER] initialized (model={self._model_id}, profile={self._profile})")

    def send_raw(self, space_id: str, session_id: str, prompt: str,
                 user_id: str = "scenario") -> dict:
        if not session_id:
            session_id = str(uuid.uuid4())

        system_prompt = self._select_system_prompt(prompt)

        messages = self._sessions.get(session_id, [])
        messages.append({"role": "user", "content": [{"text": prompt}]})

        result = self._converse_loop(messages, system_prompt)

        self._sessions[session_id] = messages
        resp = {"ok": True, "reply": result["reply"], "session_id": session_id}
        if result.get("tool_calls"):
            resp["tool_calls"] = result["tool_calls"]
        return resp

    def _select_system_prompt(self, prompt: str) -> str:
        prompt_lower = prompt[:500].lower()
        if "dry-run" in prompt_lower or "수정한 전체 코드" in prompt_lower or "```python" in prompt_lower:
            return CODE_FIX
        if "오류가 있습니다" in prompt_lower or "수정해서 다시" in prompt_lower:
            return SCENARIO_FIX
        if "개선" in prompt_lower or "improvement" in prompt_lower:
            return IMPROVEMENTS
        return SCENARIO_GEN

    def generate(self, prompt: str, model_id: str = "", max_tokens: int = 8192) -> dict:
        messages = [{"role": "user", "content": [{"text": prompt}]}]
        result = self._converse_loop(
            messages, system_prompt="", model_id=model_id or self._model_id,
            max_tokens=max_tokens, use_tools=False,
        )
        return {"ok": True, "reply": result["reply"]}

    def generate_with_tools(self, prompt: str, tools: list = None,
                            tool_executor=None, system_prompt: str = "",
                            max_tokens: int = 16384, max_rounds: int = 20) -> dict:
        messages = [{"role": "user", "content": [{"text": prompt}]}]
        result = self._converse_loop(
            messages, system_prompt=system_prompt,
            max_tokens=max_tokens, max_rounds=max_rounds,
            tools=tools, custom_executor=tool_executor,
        )
        return {"ok": True, "reply": result["reply"], "tool_calls": result.get("tool_calls", [])}

    def _converse_loop(self, messages: list, system_prompt: str = "",
                       model_id: str = None, max_tokens: int = 16384,
                       max_rounds: int = 20, use_tools: bool = True,
                       tools: list = None, custom_executor=None) -> dict:
        model = model_id or self._model_id
        tool_config = {"tools": tools or DEVOPS_TOOLS} if use_tools else None
        tool_calls_log = []

        system = [{"text": system_prompt}] if system_prompt else None

        for round_num in range(max_rounds):
            kwargs = {
                "modelId": model,
                "messages": messages,
                "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0.1},
            }
            if system:
                kwargs["system"] = system
            if tool_config:
                kwargs["toolConfig"] = tool_config

            t0 = time.time()
            response = self._client.converse(**kwargs)
            elapsed = time.time() - t0

            output = response.get("output", {})
            stop_reason = response.get("stopReason", "end_turn")
            content_blocks = output.get("message", {}).get("content", [])

            messages.append({"role": "assistant", "content": content_blocks})

            if stop_reason == "tool_use":
                tool_results = []
                for block in content_blocks:
                    if "toolUse" in block:
                        tool_use = block["toolUse"]
                        tool_name = tool_use["name"]
                        tool_input = tool_use.get("input", {})
                        tool_id = tool_use["toolUseId"]

                        print(f"[BEDROCK-PROVIDER] tool_use: {tool_name}({json.dumps(tool_input, ensure_ascii=False)[:200]})")

                        if custom_executor:
                            result_text = custom_executor(tool_name, tool_input)
                        else:
                            result_text = execute_tool(
                                tool_name, tool_input,
                                context=self._kubectl_context,
                                profile=self._profile,
                                region=self._region,
                            )

                        tool_calls_log.append({
                            "tool": tool_name,
                            "input": tool_input,
                            "output_length": len(result_text),
                        })

                        tool_results.append({
                            "toolResult": {
                                "toolUseId": tool_id,
                                "content": [{"text": result_text}],
                            }
                        })

                messages.append({"role": "user", "content": tool_results})
                print(f"[BEDROCK-PROVIDER] round {round_num+1}: {len(tool_results)} tool(s), {elapsed:.1f}s")
                continue

            reply_text = ""
            for block in content_blocks:
                if "text" in block:
                    reply_text += block["text"]

            input_tokens = response.get("usage", {}).get("inputTokens", 0)
            output_tokens = response.get("usage", {}).get("outputTokens", 0)
            print(f"[BEDROCK-PROVIDER] done: {len(reply_text)} chars, "
                  f"{input_tokens}+{output_tokens} tokens, {elapsed:.1f}s, "
                  f"{len(tool_calls_log)} tool calls total")

            return {"reply": reply_text, "tool_calls": tool_calls_log}

        return {"reply": "(max rounds reached)", "tool_calls": tool_calls_log}
