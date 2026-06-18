"""AI Provider — pluggable interface for scenario lifecycle AI calls.

Supports:
  - "agent_space": DevOps Agent Space (ChatWorker)
  - "bedrock": Bedrock Direct (converse + tool_use)

Usage:
    from ai_provider import init_provider, get_provider
    init_provider()  # reads config.yaml ai.provider
    resp = get_provider().send_raw(space_id, session_id, prompt)
"""
from abc import ABC, abstractmethod

_provider = None


class AIProvider(ABC):
    @abstractmethod
    def send_raw(self, space_id: str, session_id: str, prompt: str,
                 user_id: str = "scenario") -> dict:
        """{"ok": True, "reply": str, "session_id": str}"""

    @abstractmethod
    def generate(self, prompt: str, model_id: str = "", max_tokens: int = 8192) -> dict:
        """Single-shot generation — {"ok": True, "reply": str}"""

    @abstractmethod
    def generate_with_tools(self, prompt: str, tools: list = None,
                            tool_executor=None, system_prompt: str = "",
                            max_tokens: int = 16384, max_rounds: int = 20) -> dict:
        """Tool-use loop — {"ok": True, "reply": str, "tool_calls": list}"""


def init_provider(provider_type: str = None, **kwargs) -> "AIProvider":
    global _provider
    if _provider is not None:
        return _provider

    if provider_type is None:
        try:
            from app_config import _CFG, _cfg_get
            provider_type = _cfg_get(_CFG, "ai.provider", "agent_space")
        except (ImportError, Exception):
            provider_type = "agent_space"

    if provider_type == "bedrock":
        from providers.bedrock_direct import BedrockDirectProvider
        _provider = BedrockDirectProvider(**kwargs)
    else:
        from providers.agent_space import AgentSpaceProvider
        _provider = AgentSpaceProvider(**kwargs)

    print(f"[AI-PROVIDER] initialized: {provider_type}")
    return _provider


def get_provider() -> "AIProvider":
    if _provider is None:
        raise RuntimeError("AIProvider not initialized — call init_provider() first")
    return _provider
