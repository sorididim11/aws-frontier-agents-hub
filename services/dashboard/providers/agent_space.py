"""AgentSpaceProvider — wraps existing ChatWorker with AIProvider interface."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ai_provider import AIProvider
from chat_worker import init_worker, get_worker


class AgentSpaceProvider(AIProvider):
    def __init__(self, profile: str = None, region: str = None, **kwargs):
        if profile is None:
            try:
                from app_config import _CFG, _cfg_get, AWS_REGION
                profile = _cfg_get(_CFG, "aws.profile", "member1-acc")
                region = region or AWS_REGION
            except (ImportError, Exception):
                profile = os.environ.get("AWS_PROFILE", "member1-acc")
                region = region or os.environ.get("AWS_REGION", "us-east-1")
        region = region or "us-east-1"
        init_worker(profile=profile, region=region)

    def send_raw(self, space_id: str, session_id: str, prompt: str,
                 user_id: str = "scenario") -> dict:
        return get_worker().send_raw(
            space_id=space_id, session_id=session_id,
            prompt=prompt, user_id=user_id,
        )

    def generate(self, prompt: str, model_id: str = "", max_tokens: int = 8192) -> dict:
        try:
            from app_config import _CFG, _cfg_get
            space_id = _cfg_get(_CFG, "agent.space_id", "")
        except (ImportError, Exception):
            space_id = os.environ.get("AGENT_SPACE_ID", "")
        resp = self.send_raw(space_id=space_id, session_id="", prompt=prompt)
        return {"ok": resp.get("ok", False), "reply": resp.get("reply", "")}

    def generate_with_tools(self, prompt: str, tools: list = None,
                            tool_executor=None, system_prompt: str = "",
                            max_tokens: int = 16384, max_rounds: int = 20) -> dict:
        try:
            from app_config import _CFG, _cfg_get
            space_id = _cfg_get(_CFG, "agent.space_id", "")
        except (ImportError, Exception):
            space_id = os.environ.get("AGENT_SPACE_ID", "")
        resp = self.send_raw(space_id=space_id, session_id="", prompt=prompt)
        return {"ok": resp.get("ok", False), "reply": resp.get("reply", ""), "tool_calls": []}
