"""Simulation Engine v2 — Orchestrator.

Loop: Generate → Execute(App) → Observe(Agent) → Improve.
Agent는 생성과 관찰만. App이 실행을 담당.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Callable

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from simulation_engine.contracts import (
    SimulationRequest, SimulationStatus, Artifact, Verdict,
    RunResult, RoundRecord, Strategy, EscalationAction, SimulationEvent,
)
from simulation_engine.tools import AgentContext
from simulation_engine.generator import GeneratorAgent, GenerationError
from simulation_engine.verifier import VerifierAgent
from simulation_engine.escalation import should_escalate
from simulation_engine.safety_policy import SafetyPolicy
from simulation_engine.app_executor import AppExecutor, TriggerResult

from engine_cleanup_registry import CleanupRegistry
from execution_context import ExecutionContext

log = logging.getLogger(__name__)


class SimulationOrchestrator:
    """Generate → Execute(App) → Observe(Agent) → Improve 루프 컨트롤러."""

    def __init__(
        self,
        request: SimulationRequest,
        on_event: Callable[[SimulationEvent], None] | None = None,
    ):
        self.request = request
        self.run_id = f"sim-{uuid.uuid4().hex[:8]}"
        self.status = SimulationStatus.CREATED
        self.max_rounds = request.max_rounds
        self._on_event = on_event
        self._history: list[RoundRecord] = []
        self._cancelled = False

        self._exec_ctx = self._resolve_context(request)
        self._cleanup = CleanupRegistry(
            namespace=request.namespace,
            context=self._exec_ctx.kubectl_context,
            profile=self._exec_ctx.profile,
        )
        self._policy = SafetyPolicy.allow_all_known()

    def run(self) -> RunResult:
        """메인 루프 실행."""
        self._emit("run_started", {"run_id": self.run_id, "max_rounds": self.max_rounds})
        log.info(f"[{self.run_id}] Simulation started: FM={self.request.failure_mode_id}, target={self.request.target_service}")

        ctx = AgentContext(
            kubectl_context=self._exec_ctx.kubectl_context,
            profile=self._exec_ctx.profile,
            region=self._exec_ctx.region,
            namespace=self.request.namespace,
        )
        generator = GeneratorAgent(ctx=ctx, on_event=self._emit)
        verifier = VerifierAgent(ctx=ctx, policy=self._policy, on_event=self._emit)

        try:
            return self._loop(generator, verifier)
        except Exception as e:
            log.exception(f"[{self.run_id}] Simulation error: {e}")
            self._set_status(SimulationStatus.FAILED)
            self._emit("error_event", {"message": str(e)})
            return RunResult(run_id=self.run_id, success=False, reason=f"error: {e}",
                            history=self._history, rounds_used=len(self._history))
        finally:
            self._final_cleanup()

    def cancel(self):
        self._cancelled = True
        self._set_status(SimulationStatus.CANCELLED)

    def _loop(self, generator: GeneratorAgent, verifier: VerifierAgent) -> RunResult:
        for round_num in range(1, self.max_rounds + 1):
            if self._cancelled:
                return RunResult(run_id=self.run_id, success=False, reason="cancelled",
                                history=self._history, rounds_used=round_num - 1)

            self._emit("round_start", {"round": round_num, "max_rounds": self.max_rounds})
            record = RoundRecord(round_num=round_num)

            # ── Phase 1: GENERATE ──
            self._set_status(SimulationStatus.GENERATING)
            try:
                if round_num == 1 and not self.request.existing_scenario:
                    artifact = generator.create(self.request)
                elif round_num == 1 and self.request.existing_scenario:
                    artifact = Artifact(scenario_json=self.request.existing_scenario)
                else:
                    prev = self._history[-1]
                    if not prev.artifact:
                        artifact = generator.create(self.request)
                    else:
                        verdict_dict = _verdict_to_dict(prev.verdict) if prev.verdict else {}
                        artifact = generator.improve(self.request, prev.artifact, verdict_dict)
            except GenerationError as e:
                log.warning(f"[{self.run_id}] Round {round_num} generation failed: {e}")
                self._emit("generation_failed", {"round": round_num, "error": str(e)})
                record.artifact = None
                self._history.append(record)
                continue

            record.artifact = artifact
            self._emit("artifact", {
                "round": round_num,
                "scenario_id": artifact.scenario_json.get("id", ""),
                "scenario_name": artifact.scenario_json.get("name", ""),
            })

            # ── Phase 2: EXECUTE (App-driven) ──
            self._set_status(SimulationStatus.TRIGGERING)
            self._emit("phase_change", {"phase": "trigger", "round": round_num})

            scenario = artifact.scenario_json
            policy = SafetyPolicy.for_scenario(scenario)
            executor = AppExecutor(
                exec_ctx=self._exec_ctx,
                cleanup_registry=self._cleanup,
                policy=policy,
                on_event=self._emit,
            )

            executor.execute_pre_cleanup(scenario)
            trigger_result = executor.execute_trigger(scenario)

            self._emit("trigger_result", {
                "success": trigger_result.success,
                "command": trigger_result.command[:200],
                "output": trigger_result.output[:300],
            })

            # ── Phase 3: OBSERVE (Agent-driven, read-only) ──
            self._set_status(SimulationStatus.OBSERVING)
            self._emit("phase_change", {"phase": "observe", "round": round_num})

            verdict = verifier.observe(artifact, trigger_result)
            record.verdict = verdict

            self._emit("verdict", {
                "round": round_num,
                "passed": verdict.passed,
                "failure_reason": verdict.failure_reason,
                "fix_hint": verdict.fix_hint,
            })

            # ── Phase 4: RESTORE (App-driven, always) ──
            self._set_status(SimulationStatus.RESTORING)
            executor.execute_restore(scenario)

            self._history.append(record)

            # ── 판정 ──
            if verdict.passed:
                self._set_status(SimulationStatus.PASSED)
                self._emit("complete", {"result": "pass", "rounds": round_num, "final_scenario": scenario})
                return RunResult(
                    run_id=self.run_id, success=True, rounds_used=round_num,
                    final_artifact=artifact, final_verdict=verdict, history=self._history,
                )

            # ── 에스컬레이션 ──
            strategy = should_escalate(self._history)
            if strategy:
                record.strategy = strategy
                if strategy.action == EscalationAction.GIVE_UP:
                    self._set_status(SimulationStatus.FAILED)
                    self._emit("complete", {"result": "fail", "rounds": round_num, "reason": "escalation_give_up"})
                    return RunResult(
                        run_id=self.run_id, success=False, rounds_used=round_num,
                        reason="escalation_give_up", history=self._history,
                    )
                self._emit("escalating", {"strategy": strategy.action, "reason": strategy.reason})
                self.request.constraints.extend(strategy.new_constraints)

            self._emit("round_failed", {"round": round_num, "failure_reason": verdict.failure_reason})

        # Max rounds 소진
        self._set_status(SimulationStatus.FAILED)
        self._emit("complete", {"result": "fail", "rounds": self.max_rounds, "reason": "max_rounds"})
        return RunResult(
            run_id=self.run_id, success=False, rounds_used=self.max_rounds,
            final_artifact=self._history[-1].artifact if self._history else None,
            final_verdict=self._history[-1].verdict if self._history else None,
            history=self._history, reason="max_rounds_exhausted",
        )

    def _final_cleanup(self):
        """CleanupRegistry drain — belt-and-suspenders."""
        try:
            self._cleanup.drain()
        except Exception as e:
            log.warning(f"[{self.run_id}] Cleanup drain failed: {e}")

    def _resolve_context(self, request: SimulationRequest) -> ExecutionContext:
        """Space-first resolution."""
        if request.space_id:
            try:
                from app_config import _profile_for_space, AWS_REGION
                profile = _profile_for_space(request.space_id)
                from account_registry import registry
                for acct in registry.list_all():
                    if acct.profile == profile:
                        context = acct.contexts[0] if acct.contexts else ""
                        return ExecutionContext(
                            target_service=request.target_service,
                            account_id=acct.account_id,
                            profile=profile,
                            kubectl_context=context,
                            region=AWS_REGION,
                            namespace=request.namespace,
                        )
                return ExecutionContext(
                    target_service=request.target_service,
                    account_id="", profile=profile, kubectl_context="",
                    region=AWS_REGION, namespace=request.namespace,
                )
            except Exception:
                pass

        try:
            return ExecutionContext.for_scenario(
                {"target_service": request.target_service},
                namespace=request.namespace,
            )
        except Exception:
            from app_config import AWS_REGION
            return ExecutionContext(
                target_service=request.target_service,
                account_id="", profile="", kubectl_context="",
                region=AWS_REGION, namespace=request.namespace,
            )

    def _set_status(self, status: SimulationStatus):
        self.status = status

    def _emit(self, event_type: str, data: dict = None):
        event = SimulationEvent(event_type=event_type, data=data or {})
        if self._on_event:
            try:
                self._on_event(event)
            except Exception:
                pass


def _verdict_to_dict(verdict: Verdict) -> dict:
    evidence_dict = None
    if verdict.execution_evidence:
        evidence_dict = {
            "trigger_success": verdict.execution_evidence.trigger_success,
            "trigger_output": verdict.execution_evidence.trigger_output,
            "steps": [
                {"name": s.name, "passed": s.passed, "actual": s.actual, "detail": s.detail}
                for s in verdict.execution_evidence.steps
            ],
        }
    return {
        "passed": verdict.passed,
        "failure_reason": verdict.failure_reason,
        "fix_hint": verdict.fix_hint,
        "execution_evidence": evidence_dict,
    }
