"""Simulation Engine v2 — Verifier Agent.

Strands Agent(Sonnet)가 trigger 실행 결과를 관찰하고 구조화된 Verdict를 반환.
Agent는 관찰만 수행 — write 명령 실행 불가.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import boto3
from botocore.config import Config as BotocoreConfig
from strands import Agent
from strands.agent.agent import null_callback_handler
from strands.models.bedrock import BedrockModel

from simulation_engine.contracts import (
    Artifact, Verdict, VerdictError, VerdictErrorLayer,
    ExecutionEvidence, StepEvidence,
)
from simulation_engine.tools import AgentContext, make_verifier_tools
from simulation_engine.app_executor import TriggerResult
from simulation_engine.prompts import VERIFIER_SYSTEM_PROMPT, build_verifier_prompt


_VERIFIER_MODEL = "us.anthropic.claude-sonnet-4-6"


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
        model_id=os.environ.get("SIM_VERIFIER_MODEL", _VERIFIER_MODEL),
        max_tokens=8192,
    )


class VerifierAgent:
    """Verifier Agent — 관찰 전용. Trigger 결과를 받아 상태를 검증."""

    def __init__(self, ctx: AgentContext, policy=None, on_event=None):
        self._ctx = ctx
        self._on_event = on_event
        self._tools = make_verifier_tools(ctx, policy=policy, on_event=on_event)

    def observe(self, artifact: Artifact, trigger_result: TriggerResult) -> Verdict:
        """Trigger 실행 후 상태를 관찰하고 Verdict를 반환.

        Agent는 read-only tools + probe로만 동작.
        trigger_result는 컨텍스트로 프롬프트에 포함.
        """
        prompt = build_verifier_prompt(
            artifact.scenario_json,
            trigger_output=trigger_result.output,
            trigger_success=trigger_result.success,
            trigger_command=trigger_result.command,
        )
        model = _make_model(profile=self._ctx.profile, region=self._ctx.region)

        agent = Agent(
            model=model,
            system_prompt=VERIFIER_SYSTEM_PROMPT,
            tools=self._tools,
            callback_handler=null_callback_handler,
        )

        start = time.time()
        result_text = str(agent(prompt))
        elapsed_ms = int((time.time() - start) * 1000)

        verdict = self._parse_verdict(result_text, elapsed_ms, trigger_result)
        return verdict

    def _parse_verdict(self, text: str, elapsed_ms: int, trigger_result: TriggerResult) -> Verdict:
        """Agent 출력에서 Verdict JSON을 파싱."""
        verdict_json = self._extract_json(text)
        if not verdict_json:
            return Verdict(
                passed=False,
                layer_reached=VerdictErrorLayer.L4_EXECUTION,
                errors=[VerdictError(
                    layer=VerdictErrorLayer.L4_EXECUTION,
                    code="PARSE_ERROR",
                    message=f"Verifier 출력에서 JSON을 추출할 수 없음: {text[:300]}",
                )],
                failure_reason="Verifier JSON 파싱 실패",
                verdict_time_ms=elapsed_ms,
            )

        passed = verdict_json.get("passed", False)

        steps_evidence = []
        for s in verdict_json.get("steps", []):
            steps_evidence.append(StepEvidence(
                name=s.get("name", ""),
                passed=s.get("passed", False),
                command=s.get("command", ""),
                expected=s.get("expected", ""),
                actual=s.get("actual", ""),
                detail=s.get("detail", ""),
            ))

        execution_evidence = ExecutionEvidence(
            trigger_command=trigger_result.command,
            trigger_output=trigger_result.output[:1000],
            trigger_success=trigger_result.success,
            steps=steps_evidence,
            observed_state=verdict_json.get("observed_state", {}),
            elapsed_seconds=trigger_result.elapsed_seconds,
        )

        errors = []
        if not passed:
            for s in steps_evidence:
                if not s.passed:
                    errors.append(VerdictError(
                        layer=VerdictErrorLayer.L4_EXECUTION,
                        code="STEP_FAILED",
                        message=f"Step '{s.name}' 실패: {s.detail}",
                        field=f"verification.steps[{s.name}]",
                        fix_hint=s.detail,
                    ))

        return Verdict(
            passed=passed,
            layer_reached=VerdictErrorLayer.L4_EXECUTION,
            errors=errors,
            execution_evidence=execution_evidence,
            failure_reason=verdict_json.get("failure_reason", ""),
            fix_hint=verdict_json.get("fix_hint", ""),
            quality_score=1.0 if passed else 0.0,
            verdict_time_ms=elapsed_ms,
        )

    def _extract_json(self, text: str) -> dict | None:
        m = re.search(r"```json\s*\n(.*?)```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

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
