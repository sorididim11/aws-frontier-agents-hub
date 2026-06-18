"""Simulation Engine v2 — App Executor.

시나리오 JSON의 trigger/restore/pre_cleanup을 실행하는 유일한 지점.
Agent는 절대 write 명령을 실행하지 않음 — 이 모듈이 유일한 실행 경계.

기존 v1 인프라를 재사용:
- _run_cmd() for subprocess execution
- ExecutionContext.inject_all() for credential injection
- CleanupRegistry for guaranteed teardown
- _resolve_scenario_variables() for ${VAR} substitution
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from verifier_utils import _run_cmd
from verifier_base import _resolve_scenario_variables
from execution_context import ExecutionContext
from engine_cleanup_registry import CleanupRegistry
from simulation_engine.safety_policy import SafetyPolicy
from simulation_engine.contracts import SimulationEvent

log = logging.getLogger(__name__)


@dataclass
class TriggerResult:
    """Trigger 실행 결과."""
    success: bool
    command: str = ""
    output: str = ""
    elapsed_seconds: float = 0.0


@dataclass
class RestoreResult:
    """Restore 실행 결과."""
    success: bool
    command: str = ""
    output: str = ""


class AppExecutor:
    """App-side 명령 실행기 — 시나리오 JSON 기반."""

    def __init__(
        self,
        exec_ctx: ExecutionContext,
        cleanup_registry: CleanupRegistry,
        policy: SafetyPolicy,
        on_event: Callable[[str, dict], None] | None = None,
    ):
        self._ctx = exec_ctx
        self._cleanup = cleanup_registry
        self._policy = policy
        self._emit = on_event

    def execute_trigger(self, scenario: dict) -> TriggerResult:
        """scenario.trigger.command 실행.

        Flow:
        1. 변수 치환
        2. SafetyPolicy 검증
        3. Credential 주입
        4. _run_cmd 실행
        5. CleanupRegistry 등록
        """
        trigger = scenario.get("trigger", {})
        raw_cmd = trigger.get("command", "")
        if not raw_cmd:
            return TriggerResult(success=False, output="No trigger command in scenario")

        cmd = _resolve_scenario_variables(raw_cmd, scenario, self._ctx.kubectl_context)
        cmd = self._ctx.inject_all(cmd)

        allowed, reason = self._policy.validate_write(cmd)
        if not allowed:
            self._event("trigger_denied", {"command": cmd[:200], "reason": reason})
            return TriggerResult(success=False, command=cmd, output=f"Policy denied: {reason}")

        self._event("trigger_start", {"command": cmd[:200]})
        start = time.time()
        success, stdout, stderr = _run_cmd(cmd, timeout=trigger.get("timeout", 120))
        elapsed = time.time() - start

        output = stdout if success else f"{stdout}\n{stderr}".strip()
        self._event("trigger_complete", {
            "success": success,
            "elapsed": round(elapsed, 1),
            "output": output[:300],
        })

        self._cleanup.register_from_trigger(trigger, output)

        return TriggerResult(
            success=success,
            command=cmd,
            output=output,
            elapsed_seconds=elapsed,
        )

    def execute_restore(self, scenario: dict, trigger_output: str = "") -> RestoreResult:
        """scenario.restore.command 실행."""
        restore = scenario.get("restore", {})
        raw_cmd = restore.get("command", "")
        if not raw_cmd:
            return RestoreResult(success=True, output="No restore command")

        cmd = _resolve_scenario_variables(raw_cmd, scenario, self._ctx.kubectl_context)
        cmd = self._ctx.inject_all(cmd)

        allowed, reason = self._policy.validate_write(cmd)
        if not allowed:
            log.warning(f"Restore command denied by policy: {reason}")
            return RestoreResult(success=False, command=cmd, output=f"Policy denied: {reason}")

        self._event("restore_start", {"command": cmd[:200]})
        success, stdout, stderr = _run_cmd(cmd, timeout=restore.get("timeout", 120))
        output = stdout if success else f"{stdout}\n{stderr}".strip()
        self._event("restore_complete", {"success": success, "output": output[:200]})

        return RestoreResult(success=success, command=cmd, output=output)

    def execute_pre_cleanup(self, scenario: dict) -> bool:
        """scenario.pre_cleanup.command 실행."""
        pre_cleanup = scenario.get("pre_cleanup", {})
        raw_cmd = pre_cleanup.get("command", "")
        if not raw_cmd:
            return True

        commands = raw_cmd.split(" && ") if " && " in raw_cmd else [raw_cmd]
        for sub_cmd in commands:
            cmd = _resolve_scenario_variables(sub_cmd.strip(), scenario, self._ctx.kubectl_context)
            cmd = self._ctx.inject_all(cmd)

            allowed, reason = self._policy.validate_write(cmd)
            if not allowed:
                log.warning(f"Pre-cleanup denied: {reason}")
                continue

            success, stdout, stderr = _run_cmd(cmd, timeout=60)
            if not success:
                log.warning(f"Pre-cleanup failed: {stderr[:200]}")

        return True

    def _event(self, event_type: str, data: dict):
        if self._emit:
            try:
                self._emit(event_type, data)
            except Exception:
                pass
