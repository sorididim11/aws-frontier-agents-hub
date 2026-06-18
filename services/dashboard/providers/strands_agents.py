"""Strands Agent factory — creates purpose-specific Agent instances."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import boto3
from botocore.config import Config as BotocoreConfig
from strands import Agent
from strands.agent.agent import null_callback_handler
from strands.models.bedrock import BedrockModel

from providers.strands_tools import (
    READONLY_TOOLS, configure as configure_tools, make_tools,
)
from providers.system_prompts import (
    SCENARIO_GEN, SCENARIO_FIX, CODE_FIX, IMPROVEMENTS, STEP_CORRECTION,
)


AGENT_CONFIGS = {
    "scenario_gen": {"system_prompt": SCENARIO_GEN, "tools": READONLY_TOOLS},
    "correction":   {"system_prompt": STEP_CORRECTION, "tools": READONLY_TOOLS},
    "code_fix":     {"system_prompt": CODE_FIX, "tools": None},
    "scenario_fix": {"system_prompt": SCENARIO_FIX, "tools": None},
    "improvements": {"system_prompt": IMPROVEMENTS, "tools": None},
    "plain":        {"system_prompt": None, "tools": None},
}


def _default_model() -> str:
    try:
        from app_config import _CFG, _cfg_get
        return _cfg_get(_CFG, "bedrock.default_model", "us.anthropic.claude-sonnet-4-6-v1:0")
    except (ImportError, Exception):
        return os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6-v1:0")


def _make_model(model_id: str = None, profile: str = None, region: str = "us-east-1",
                max_tokens: int = 16384) -> BedrockModel:
    session_kwargs = {}
    if profile:
        session_kwargs["profile_name"] = profile
    if region:
        session_kwargs["region_name"] = region
    session = boto3.Session(**session_kwargs)

    return BedrockModel(
        boto_session=session,
        boto_client_config=BotocoreConfig(
            read_timeout=300,
            connect_timeout=10,
        ),
        model_id=model_id or _default_model(),
        max_tokens=max_tokens,
    )


def create_agent(agent_type: str, model_id: str = None, profile: str = None,
                 region: str = "us-east-1", extra_tools: list = None,
                 system_prompt: str = None, max_tokens: int = 16384,
                 kubectl_context: str = "",
                 account_context: "AccountContext" = None) -> Agent:
    """Create a purpose-specific Strands Agent.

    Args:
        agent_type: One of AGENT_CONFIGS keys (scenario_gen, correction, etc.)
        model_id: Override model ID (defaults to config.yaml bedrock.default_model)
        profile: AWS profile name (legacy — prefer account_context)
        region: AWS region (legacy — prefer account_context)
        extra_tools: Additional tools to append
        system_prompt: Override system prompt
        max_tokens: Max generation tokens
        kubectl_context: kubectl context for tool execution (legacy)
        account_context: AccountContext for per-agent isolation (preferred)
    """
    config = AGENT_CONFIGS.get(agent_type, AGENT_CONFIGS["plain"])

    # Resolve effective profile/region/context
    if account_context:
        eff_profile = account_context.profile
        eff_region = account_context.region
        eff_context = account_context.kubectl_context
    else:
        eff_profile = profile or ""
        eff_region = region
        eff_context = kubectl_context

    # Build tools: isolated (new) vs global (legacy)
    if account_context and config["tools"]:
        tools = make_tools(account_context)
    elif config["tools"]:
        configure_tools(kubectl_context=eff_context, profile=eff_profile, region=eff_region)
        tools = list(config["tools"])
    else:
        tools = []

    if extra_tools:
        tools.extend(extra_tools)

    model = _make_model(model_id=model_id, profile=eff_profile, region=eff_region,
                        max_tokens=max_tokens)

    agent = Agent(
        model=model,
        system_prompt=system_prompt or config["system_prompt"],
        tools=tools or None,
        callback_handler=null_callback_handler,
        name=agent_type,
    )

    print(f"[STRANDS] agent created: {agent_type} (model={model_id or _default_model()}, "
          f"tools={len(tools)}, profile={eff_profile}, "
          f"isolated={'yes' if account_context else 'no'})")

    return agent
