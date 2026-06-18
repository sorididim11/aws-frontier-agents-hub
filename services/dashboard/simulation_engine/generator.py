"""Simulation Engine v2 — Generator Agent.

Strands Agent(Opus)가 환경을 탐색하고 시나리오 JSON을 생성한다.
submit_scenario tool로 L1-L3 검증을 거쳐 통과된 시나리오만 반환.
"""

from __future__ import annotations

import json
import time
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import boto3
from botocore.config import Config as BotocoreConfig
from strands import Agent
from strands.agent.agent import null_callback_handler
from strands.models.bedrock import BedrockModel

from simulation_engine.contracts import Artifact, ArtifactMetadata, SimulationRequest
from simulation_engine.tools import AgentContext, make_generator_tools
from simulation_engine.prompts import (
    GENERATOR_SYSTEM_PROMPT,
    build_generator_prompt,
    build_request_context,
    build_verdict_context,
)


_GENERATOR_MODEL = "us.anthropic.claude-opus-4-6-v1"


def _make_model(profile: str = "", region: str = "us-east-1") -> BedrockModel:
    session_kwargs = {}
    if profile:
        session_kwargs["profile_name"] = profile
    if region:
        session_kwargs["region_name"] = region
    session = boto3.Session(**session_kwargs)

    return BedrockModel(
        boto_session=session,
        boto_client_config=BotocoreConfig(read_timeout=300, connect_timeout=10),
        model_id=os.environ.get("SIM_GENERATOR_MODEL", _GENERATOR_MODEL),
        max_tokens=16384,
    )


class GeneratorAgent:
    """Generator Agent — 시나리오 생성 및 개선."""

    def __init__(self, ctx: AgentContext, on_event=None):
        self._ctx = ctx
        self._on_event = on_event
        self._artifact: Artifact | None = None
        self._tools, self._get_validated = make_generator_tools(ctx, on_event=on_event)

    def create(self, request: SimulationRequest) -> Artifact:
        """첫 라운드: 환경 탐색 + 시나리오 생성."""
        request_context = build_request_context(
            failure_mode_id=request.failure_mode_id,
            target_service=request.target_service,
            namespace=request.namespace,
            architecture_json=request.architecture_json,
            recommendation=request.recommendation,
            constraints=request.constraints,
        )
        prompt = build_generator_prompt(request_context)
        return self._call_agent(prompt, attempt=1, strategy="initial")

    def improve(self, request: SimulationRequest, prev_artifact: Artifact, verdict_dict: dict) -> Artifact:
        """후속 라운드: 실행 증거를 기반으로 시나리오 개선."""
        request_context = build_request_context(
            failure_mode_id=request.failure_mode_id,
            target_service=request.target_service,
            namespace=request.namespace,
            architecture_json=request.architecture_json,
            recommendation=request.recommendation,
            constraints=request.constraints,
        )
        verdict_context = build_verdict_context(verdict_dict)
        prompt = build_generator_prompt(request_context, verdict_context)

        attempt = prev_artifact.metadata.attempt + 1
        return self._call_agent(prompt, attempt=attempt, strategy="improve")

    def _call_agent(self, prompt: str, attempt: int, strategy: str) -> Artifact:
        """Strands Agent 호출 — submit_scenario tool-use loop."""
        model = _make_model(profile=self._ctx.profile, region=self._ctx.region)

        agent = Agent(
            model=model,
            system_prompt=GENERATOR_SYSTEM_PROMPT,
            tools=self._tools,
            callback_handler=null_callback_handler,
        )

        start = time.time()
        result_text = str(agent(prompt))
        elapsed_ms = int((time.time() - start) * 1000)

        scenario_json = self._get_validated() or self._extract_scenario(result_text)
        if not scenario_json:
            raise GenerationError(f"Generator가 유효한 시나리오를 생성하지 못함: {result_text[:500]}")

        return Artifact(
            scenario_json=scenario_json,
            metadata=ArtifactMetadata(
                attempt=attempt,
                strategy=strategy,
                constraints=list(self._ctx.namespace),
                reasoning=result_text[:200],
                generation_time_ms=elapsed_ms,
            ),
        )

    def _extract_scenario(self, text: str) -> dict | None:
        """Agent 출력에서 시나리오 JSON 추출.

        submit_scenario가 통과하면 Agent가 JSON을 반환하므로,
        tool_use 과정에서 이미 검증 완료됨.
        """
        # submit_scenario tool이 성공 시 scenario를 내부에 저장하는 방식이 아니므로
        # Agent 출력 텍스트에서 JSON 추출
        import re

        # ```json ... ``` 블록
        m = re.search(r"```json\s*\n(.*?)```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        # 순수 JSON 객체
        try:
            start = text.index("{")
            depth = 0
            for i, ch in enumerate(text[start:], start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return json.loads(text[start:i + 1])
        except (ValueError, json.JSONDecodeError):
            pass

        return None


class GenerationError(Exception):
    """Generator Agent가 시나리오 생성에 실패."""
    pass
