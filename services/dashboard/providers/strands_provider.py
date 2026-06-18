"""StrandsProvider — AIProvider implementation using Strands SDK Agents."""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ai_provider import AIProvider
from providers.strands_agents import create_agent, _default_model
from providers.strands_session import SessionAgentStore


class StrandsProvider(AIProvider):
    """AIProvider backed by Strands Agents.

    - send_raw(): session-based multi-turn via per-session Agent instances
    - generate(): one-shot Agent (no session)
    - generate_with_tools(): delegates to legacy converse loop for harness compatibility
    """

    def __init__(self, profile: str = None, region: str = None, **kwargs):
        if profile is None:
            try:
                from app_config import _CFG, _cfg_get, AWS_REGION
                profile = _cfg_get(_CFG, "aws.profile", None) or None
                region = region or AWS_REGION
            except (ImportError, Exception):
                profile = os.environ.get("AWS_PROFILE") or None
                region = region or os.environ.get("AWS_REGION", "us-east-1")

        self._profile = profile
        self._region = region or "us-east-1"
        self._model_id = _default_model()
        self._session_store = SessionAgentStore(max_sessions=100)

        try:
            from app_config import _CFG, _cfg_get
            self._kubectl_context = _cfg_get(_CFG, "clusters.primary.context", "")
        except (ImportError, Exception):
            self._kubectl_context = ""

        print(f"[STRANDS-PROVIDER] initialized (model={self._model_id}, profile={self._profile})")

    def send_raw(self, space_id: str, session_id: str, prompt: str,
                 user_id: str = "scenario") -> dict:
        """Multi-turn conversation with session persistence."""
        if not session_id:
            session_id = str(uuid.uuid4())

        agent_type = self._select_agent_type(prompt)

        agent = self._session_store.get_or_create(
            session_id,
            lambda: create_agent(
                agent_type,
                model_id=self._model_id,
                profile=self._profile,
                region=self._region,
                kubectl_context=self._kubectl_context,
            ),
        )

        result = agent(prompt)
        reply = str(result).strip()

        return {"ok": True, "reply": reply, "session_id": session_id}

    def generate(self, prompt: str, model_id: str = "", max_tokens: int = 8192) -> dict:
        """Single-shot generation — no session, no tools."""
        agent = create_agent(
            "plain",
            model_id=model_id or self._model_id,
            profile=self._profile,
            region=self._region,
            max_tokens=max_tokens,
            kubectl_context=self._kubectl_context,
        )
        result = agent(prompt)
        reply = str(result).strip()
        return {"ok": True, "reply": reply}

    def generate_with_tools(self, prompt: str, tools: list = None,
                            tool_executor=None, system_prompt: str = "",
                            max_tokens: int = 16384, max_rounds: int = 20) -> dict:
        """Tool-use loop with custom executor — uses legacy converse for harness compatibility.

        The GenerationHarness passes a custom tool_executor (harness._dispatch) that
        handles submit_tool validation internally. This pattern requires intercepting
        each tool call before execution, which maps better to the direct converse loop.
        """
        from providers.bedrock_direct import BedrockDirectProvider
        legacy = BedrockDirectProvider(profile=self._profile, region=self._region)
        return legacy.generate_with_tools(
            prompt=prompt, tools=tools, tool_executor=tool_executor,
            system_prompt=system_prompt, max_tokens=max_tokens, max_rounds=max_rounds,
        )

    def _select_agent_type(self, prompt: str) -> str:
        """Select agent type based on prompt keywords (same heuristic as before)."""
        prompt_lower = prompt[:500].lower()
        if "dry-run" in prompt_lower or "수정한 전체 코드" in prompt_lower or "```python" in prompt_lower:
            return "code_fix"
        if "오류가 있습니다" in prompt_lower or "수정해서 다시" in prompt_lower:
            return "scenario_fix"
        if "개선" in prompt_lower or "improvement" in prompt_lower:
            return "improvements"
        return "scenario_gen"
