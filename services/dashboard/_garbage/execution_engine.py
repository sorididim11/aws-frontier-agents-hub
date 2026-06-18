"""
Phased Execution Engine: modular scenario orchestrator.

3-Phase lifecycle: PREPARE → EXECUTE → TEARDOWN
Solves timing, resource resolution, and execution control problems
without modifying existing verifier*.py code.

Integration: executor_type="engine" in config.yaml or scenario JSON.
"""
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

import cluster_manager
from execution_context import ExecutionContext
from engine_cleanup_registry import CleanupRegistry
from engine_resolver import EngineResolver, ResolveResult
from engine_step_runner import AdaptiveStepRunner, StepResult
from verifier_checkers import VERIFIERS
from verifier_utils import (
    _run_cmd, _cfg, _pre_flight_check, _agent_space_session,
    AWS_REGION, NAMESPACE, _AGENT_SPACE_ID, _RUNS_TABLE,
)


_PROJECT_NAME = _cfg("project.name", os.environ.get("PROJECT_NAME", "frontier-agent-hub"))


# ── Step Tier Classification ─────────────────────────────────────────────────
# Primary: always available in any K8s cluster (pass/fail determined here)
# Secondary: requires AWS infra that may or may not exist (optional enrichment)

PRIMARY_STEP_TYPES = frozenset({
    "pod_status", "pod_logs", "kubectl_check", "pod_restart_count",
})

SECONDARY_STEP_TYPES = frozenset({
    "cw_alarm", "alarm_state", "metric_check", "xray_trace",
    "investigation_event", "agent_investigation", "fis_experiment",
    "lambda_logs", "log_pattern",
})


def _classify_step_tier(step_config: dict) -> str:
    """Classify a verification step as 'primary' or 'secondary'.

    Explicit 'tier' field in JSON takes precedence.
    Otherwise auto-classify by step type.
    Unknown types default to primary (fail-safe).
    """
    explicit = step_config.get("tier")
    if explicit in ("primary", "secondary"):
        return explicit
    step_type = step_config.get("type", "")
    if step_type in SECONDARY_STEP_TYPES:
        return "secondary"
    return "primary"


# ── Default composite pass rules (replaces hardcoded cross-step inference) ──

DEFAULT_COMPOSITE_RULES = [
    {
        "condition": {"type_failed": ["cw_alarm", "alarm_state"],
                      "type_passed": ["investigation_event", "agent_investigation"]},
        "action": "retroactive_pass",
    },
]


class PhasedExecutor:
    """State-machine-based scenario executor. Drop-in via executor_type='engine'."""

    def __init__(self, scenario, agent_space_id=None, namespace=None):
        self.run_id = str(uuid.uuid4())[:8]
        self.scenario = scenario
        self.scenario_id = scenario["id"]
        self.agent_space_id = agent_space_id or _AGENT_SPACE_ID
        self.namespace = namespace or scenario.get("namespace") or NAMESPACE
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._started_ts = time.time()

        self._exec_ctx = ExecutionContext.for_scenario(scenario, namespace=self.namespace)
        self._scenario_context = self._exec_ctx.kubectl_context or None
        self._scenario_profile = self._exec_ctx.profile or None

        self.completed_at = None
        self.status = "running"
        self.result = None
        self.trigger_output = ""
        self.investigation_summary = None
        self.preflight = None
        self._incident_id = None
        self._investigation_task_id = None
        self._slack_thread_ts = None

        self.steps = []
        self._phase = "idle"
        self._resolver = EngineResolver(self._exec_ctx)
        self._step_runner = AdaptiveStepRunner()
        self._cleanup_registry = CleanupRegistry(
            namespace=self.namespace,
            context=self._scenario_context,
            profile=self._scenario_profile,
        )
        self._resolved_vars = {}
        self._retry_attempted = False
        self._correction_summary = None
        self._corrections_applied = False

        self._init_steps()

        print(f"[PhasedExecutor] {self.scenario_id} → account={self._exec_ctx.account_id} "
              f"profile={self._scenario_profile} context={self._scenario_context and self._scenario_context[:50]}")

    # ── Step Initialization ───────────────────────────────────────────────

    def _init_steps(self):
        self.steps.append(self._make_step("사전 점검 + 리소스 검증 (Prepare)", "pipeline_preflight"))

        if self.scenario.get("pre_cleanup"):
            self.steps.append(self._make_step("환경 초기화 (Pre-cleanup)", "pipeline_cleanup"))

        self.steps.append(self._make_step("장애 주입 (Trigger)", "pipeline_trigger"))
        self.steps.append(self._make_step("효과 확인 (Effect Confirm)", "pipeline_effect_confirm"))

        self._verify_start_idx = len(self.steps)

        verification = self.scenario.get("verification", {})
        step_defs = verification.get("steps") or verification.get("checks") or []
        for step_def in step_defs:
            name = step_def.get("name") or step_def.get("description") or step_def.get("type", "unknown")
            tier = _classify_step_tier(step_def)
            self.steps.append(self._make_step(name, step_def.get("type", "manual"), config=step_def, tier=tier))

        if self.scenario.get("restore"):
            self.steps.append(self._make_step("복원 (Restore)", "pipeline_restore"))

    def _make_step(self, name, step_type, config=None, tier="primary"):
        return {
            "name": name,
            "type": step_type,
            "tier": tier,
            "config": config or {},
            "status": "pending",
            "detail": "",
            "elapsed": None,
            "checked_at": None,
            "events": [],
        }

    # ── Main Pipeline ─────────────────────────────────────────────────────

    def _run_pipeline(self):
        """Execute full scenario: Prepare → Execute → Teardown → [Self-Correct → Retry]."""
        try:
            self._phase_prepare()
            if self.status in ("preflight_failed", "cancelled"):
                return

            self._phase_execute()
            self._phase_teardown()

            # Self-correction loop: if failed, probe + patch + retry once
            if self.result == "fail" and not self._retry_attempted:
                self.status = "self_correcting"
                self._attempt_self_correction()

        except Exception as e:
            import traceback
            self._log_global(f"엔진 예외: {e}\n{traceback.format_exc()[-300:]}")
            self.status = "completed"
            self.result = "fail"
            self.completed_at = datetime.now(timezone.utc).isoformat()
        finally:
            # Guaranteed resource cleanup — runs even on crash/exception
            if self._cleanup_registry.pending_count > 0:
                self._log_global(f"cleanup registry: {self._cleanup_registry.pending_count}건 정리")
                self._cleanup_registry.drain()
            self._persist_corrections()
            self.save()
            self._schedule_cleanup()

    # ── Phase 1: PREPARE (resolve + validate + preflight) ─────────────────

    def _phase_prepare(self):
        self._phase = "prepare"
        pf_step = self._get_step("pipeline_preflight")
        pf_step["status"] = "checking"
        pf_start = time.time()
        _ev = self._make_logger(pf_step)

        # Preflight checks (K8s access, AWS creds, tools)
        _ev("사전 점검 시작")
        pf_ok, pf_results = _pre_flight_check(self, self.scenario)
        self.preflight = pf_results
        for pf in pf_results:
            icon = "PASS" if pf["ok"] else "FAIL"
            _ev(f"[{icon}] {pf['check']}: {pf['detail']}")

        if not pf_ok:
            pf_ok = self._correct_preflight(pf_results)
        if not pf_ok:
            pf_step["status"] = "fail"
            pf_step["detail"] = "; ".join(r["detail"] for r in pf_results if not r["ok"])
            pf_step["error_category"] = "infra_missing"
            self._abort_all("Pre-flight 실패")
            self.status = "preflight_failed"
            self.result = "preflight_failed"
            self.completed_at = datetime.now(timezone.utc).isoformat()
            self.save()
            return

        # Variable resolution (eager, fail-fast)
        _ev("변수 resolve 시작")
        resolve_result = self._resolver.resolve_variables(self.scenario)
        if not resolve_result.ok:
            detail = "; ".join(str(f) for f in resolve_result.failures)
            _ev(f"FAIL: 변수 resolve 실패 — {detail}")
            pf_step["status"] = "fail"
            pf_step["detail"] = f"변수 resolve 실패: {detail}"
            pf_step["error_category"] = "infra_missing"
            self._abort_all("변수 resolve 실패")
            self.status = "preflight_failed"
            self.result = "preflight_failed"
            self.completed_at = datetime.now(timezone.utc).isoformat()
            self.save()
            return

        self._resolved_vars = resolve_result.resolved
        _ev(f"변수 resolve 완료: {list(self._resolved_vars.keys())}")

        # Apply resolved vars to scenario
        self._resolver.apply_resolved(self.scenario, self._resolved_vars)
        unresolved = self._resolver.find_unresolved(self.scenario)
        if unresolved:
            _ev(f"경고: 미치환 변수 {unresolved}")

        # Resource validation (tiered: primary blocks, secondary warns)
        _ev("리소스 존재 검증 시작")
        checks = self._resolver.validate_resources(self.scenario, self._resolved_vars)
        for c in checks:
            _ev(str(c))

        failed_checks = [c for c in checks if not c.exists]
        if failed_checks:
            # Classify failures by tier
            verification = self.scenario.get("verification", {})
            step_defs = verification.get("steps") or verification.get("checks") or []
            secondary_types = {s.get("type") for s in step_defs if _classify_step_tier(s) == "secondary"}

            primary_failures = [c for c in failed_checks if c.resource_type not in ("alarm", "log_group", "lambda")]
            secondary_failures = [c for c in failed_checks if c.resource_type in ("alarm", "log_group", "lambda")]

            if primary_failures:
                detail = "; ".join(str(c) for c in primary_failures)
                pf_step["status"] = "fail"
                pf_step["detail"] = f"리소스 부재: {detail}"
                pf_step["error_category"] = "infra_missing"
                self._abort_all("리소스 검증 실패")
                self.status = "preflight_failed"
                self.result = "preflight_failed"
                self.completed_at = datetime.now(timezone.utc).isoformat()
                self.save()
                return

            if secondary_failures:
                # Mark secondary steps as pre-skipped (won't block execution)
                names = {c.name for c in secondary_failures}
                _ev(f"secondary 리소스 부재 (실행 차단 안 함): {names}")
                self._mark_secondary_unavailable(names)

        pf_step["status"] = "pass"
        pf_step["detail"] = f"점검 {len(pf_results)}개 + 리소스 {len(checks)}개 통과"
        pf_step["elapsed"] = round(time.time() - pf_start, 1)
        _ev("PASS")

    # ── Phase 2: EXECUTE (cleanup + trigger + effect_confirm + verify) ────

    def _phase_execute(self):
        self._phase = "execute"

        # Pre-cleanup
        if self.scenario.get("pre_cleanup"):
            self._execute_cleanup()

        # Trigger (with correction on failure)
        trigger_ok = self._execute_trigger()
        if not trigger_ok:
            trigger_ok = self._correct_trigger()
        if not trigger_ok:
            self._abort_all("Trigger 실패 (교정 후에도)")
            self.status = "completed"
            self.result = "fail"
            self.completed_at = datetime.now(timezone.utc).isoformat()
            return

        # Effect confirmation (replaces blind settle)
        self._confirm_effect()

        # Verification steps
        self._execute_verify_steps()

    def _execute_cleanup(self):
        """Run pre-cleanup commands."""
        cl_step = self._get_step("pipeline_cleanup")
        cl_step["status"] = "checking"
        cl_start = time.time()
        _ev = self._make_logger(cl_step)
        _ev("환경 초기화 시작")

        pre_cleanup = self.scenario.get("pre_cleanup", {})
        if isinstance(pre_cleanup, list):
            cleanup_cmd = " && ".join(
                item.get("command", "") for item in pre_cleanup
                if isinstance(item, dict) and item.get("command"))
        else:
            cleanup_cmd = pre_cleanup.get("command", "")

        if cleanup_cmd:
            cleanup_cmd = self._inject_profile(cleanup_cmd)
            _ev(f"실행: {cleanup_cmd[:100]}")
            ok, stdout, stderr = _run_cmd(cleanup_cmd, timeout=120, context=self._scenario_context)
            _ev(f"{'성공' if ok else '실패'}: {(stdout or stderr)[:80]}")

        # Reset alarms
        alarm_names = pre_cleanup.get("reset_alarms", []) if isinstance(pre_cleanup, dict) else []
        if alarm_names:
            self._reset_alarms(alarm_names, _ev)

        cl_step["status"] = "pass"
        cl_step["detail"] = "환경 초기화 완료"
        cl_step["elapsed"] = round(time.time() - cl_start, 1)
        _ev("PASS")

    def _execute_trigger(self) -> bool:
        """Execute trigger command. Returns True on success."""
        tr_step = self._get_step("pipeline_trigger")
        tr_step["status"] = "checking"
        tr_start = time.time()
        _ev = self._make_logger(tr_step)
        _ev("장애 주입 시작")

        trigger = self.scenario.get("trigger", {})
        command = trigger.get("command", "")
        if not command and isinstance(trigger.get("commands"), list):
            command = " && ".join(trigger["commands"])

        if not command:
            tr_step["status"] = "pass"
            tr_step["detail"] = "트리거 명령 없음 (스킵)"
            tr_step["elapsed"] = round(time.time() - tr_start, 1)
            self.trigger_output = "트리거 명령 없음"
            return True

        command = self._inject_profile(command)
        _ev(f"실행: {command[:120]}")
        ok, stdout, stderr = _run_cmd(command, timeout=120, context=self._scenario_context)
        output = stdout or stderr
        self.trigger_output = output

        tr_step["elapsed"] = round(time.time() - tr_start, 1)
        if ok:
            tr_step["status"] = "pass"
            tr_step["detail"] = output[:200]
            _ev(f"성공: {output[:100]}")
            # Register created resources for guaranteed cleanup
            self._cleanup_registry.register_from_trigger(trigger, output)
            return True
        else:
            tr_step["status"] = "fail"
            tr_step["detail"] = f"trigger 실패: {output[:200]}"
            tr_step["error_category"] = "command_error"
            _ev(f"FAIL: {output[:100]}")
            return False

    def _confirm_effect(self):
        """Confirm trigger effect via polling (replaces blind sleep)."""
        ef_step = self._get_step("pipeline_effect_confirm")
        ef_step["status"] = "checking"
        ef_start = time.time()
        _ev = self._make_logger(ef_step)

        # Check for explicit effect_check in scenario
        effect_check = self.scenario.get("effect_check")
        if effect_check:
            _ev(f"명시적 effect_check: {effect_check.get('type')}")
            self._prepare_step_config(effect_check)
            result = self._step_runner.run(
                effect_check, run_obj=self, timeout_override=60, fail_ok=True)
            ef_step["detail"] = result.detail
            ef_step["status"] = "pass" if result.passed else "warn"
            ef_step["elapsed"] = round(time.time() - ef_start, 1)
            ef_step["events"].extend(result.events)
            return

        # No explicit check → use settle_delay or adaptive probe on first verify step
        settle_delay = self.scenario.get("settle_delay")
        if settle_delay is not None:
            _ev(f"settle_delay={settle_delay}s 대기")
            time.sleep(float(settle_delay))
            ef_step["status"] = "pass"
            ef_step["detail"] = f"settle_delay {settle_delay}s 완료"
            ef_step["elapsed"] = round(time.time() - ef_start, 1)
            return

        # Infer settle from step types
        verify_steps = self._get_verify_steps()
        inferred = self._infer_settle_delay(verify_steps)
        if inferred > 0:
            _ev(f"추론된 settle_delay={inferred}s")
            # Probe first step with short timeout instead of blind sleep
            if verify_steps:
                first_config = dict(verify_steps[0]["config"])
                self._prepare_step_config(first_config)
                result = self._step_runner.run(
                    first_config, run_obj=self,
                    timeout_override=min(inferred, 60), fail_ok=True)
                ef_step["events"].extend(result.events)
                if result.passed:
                    _ev("effect 조기 확인 — settle 완료")
                else:
                    _ev(f"effect 미확인 — 추가 대기 {max(0, inferred - 60)}s")
                    remaining = inferred - 60
                    if remaining > 0:
                        time.sleep(remaining)
            else:
                time.sleep(inferred)

        ef_step["status"] = "pass"
        ef_step["detail"] = f"effect confirm 완료 ({round(time.time() - ef_start, 1)}s)"
        ef_step["elapsed"] = round(time.time() - ef_start, 1)

    def _execute_verify_steps(self):
        """Run all verification steps with adaptive timing."""
        self.status = "verifying"
        verify_steps = self._get_verify_steps()

        for step in verify_steps:
            if self.status == "cancelled":
                step["status"] = "skipped"
                step["detail"] = "cancelled"
                continue

            # Guard evaluation
            guard = step["config"].get("guard")
            if guard and not self._evaluate_guard(guard, step):
                step["status"] = "skipped"
                step["detail"] = f"guard 미충족: {guard.get('type', 'unknown')}"
                continue

            # depends_on check
            depends_on = step["config"].get("depends_on", [])
            if depends_on and not self._check_depends(depends_on):
                step["status"] = "skipped"
                step["detail"] = f"의존 step 미통과: {depends_on}"
                continue

            step["status"] = "checking"
            config = dict(step["config"])
            self._prepare_step_config(config)

            result = self._step_runner.run(config, run_obj=self)
            step["status"] = result.status
            step["detail"] = result.detail
            step["elapsed"] = result.elapsed
            step["events"].extend(result.events)
            if result.error_category:
                step["error_category"] = result.error_category
                step["error_reason"] = result.error_reason

            # On hard failure (not warn): secondary failures never abort; primary may abort
            if result.status == "fail":
                if step.get("tier") == "secondary":
                    step["status"] = "warn"
                    step["detail"] += " (secondary — 판정에 미반영)"
                elif not self._has_composite_recovery(step, verify_steps):
                    # Bedrock correction: 1회 시도
                    if not step.get("_correction_attempted"):
                        step["_correction_attempted"] = True
                        self._log_global(f"[CORRECTION] step 실패 → Bedrock 교정 시도: {step['name']}")
                        correction = self._bedrock_correct_step(step, result, verify_steps)
                        if correction and correction.get("corrected_step"):
                            self._log_global(f"[CORRECTION] 교정 수신: {correction.get('reasoning', '')[:100]}")
                            corrected_config = correction["corrected_step"]
                            self._prepare_step_config(corrected_config)
                            step["events"].append({"t": round(time.time(), 1),
                                                   "msg": f"Bedrock 교정 적용: {correction.get('reasoning', '')[:80]}"})

                            # 수정된 step 재실행
                            try:
                                retry_result = self._step_runner.run(corrected_config, run_obj=self)
                            except Exception as retry_err:
                                self._log_global(f"[CORRECTION] 교정 step 재실행 예외: {retry_err}")
                                step["detail"] = f"교정 step 재실행 실패: {retry_err}"
                                step["events"].append({"t": round(time.time(), 1),
                                                       "msg": f"Agent 교정 실패: {retry_err}"})
                                retry_result = None

                            if retry_result and retry_result.status == "pass":
                                step["status"] = "pass"
                                step["detail"] = retry_result.detail + " (Bedrock 교정 후 통과)"
                                step["elapsed"] = (step.get("elapsed") or 0) + retry_result.elapsed
                                step["events"].extend(retry_result.events)
                                step["config"] = corrected_config
                                self._corrections_applied = True
                                # 후속 step 교체
                                if correction.get("subsequent_steps"):
                                    step_idx = verify_steps.index(step)
                                    self._replace_subsequent_steps(step_idx, correction["subsequent_steps"], verify_steps)
                                continue
                            elif retry_result:
                                step["detail"] = retry_result.detail + " (Bedrock 교정 후에도 실패)"
                                step["events"].extend(retry_result.events)

                    self._skip_remaining(step, verify_steps)
                    break

        # Apply composite pass rules
        self._apply_composite_rules(verify_steps)

    # ── Phase 3: TEARDOWN (restore + result) ──────────────────────────────

    def _phase_teardown(self):
        self._phase = "teardown"

        # Restore
        restore_step = self._get_step("pipeline_restore")
        if restore_step:
            self._execute_restore(restore_step)

        # Compute final result using tiered logic
        verify_steps = self._get_verify_steps()
        self.result = self._compute_tiered_result(verify_steps)

        self.status = "completed"
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def _compute_tiered_result(self, verify_steps) -> str:
        """Compute pass/fail using tiered verification.

        Pass rule (from scenario or default 'all_primary'):
        - all_primary: all primary steps pass → PASS (secondary ignored for judgment)
        - any_primary: at least one primary passes → PASS
        - primary_and_any_secondary: all primary + at least one secondary → PASS

        Secondary steps never cause FAIL — only reduce confidence.
        """
        pass_rule = self.scenario.get("verification", {}).get("pass_rule", "all_primary")

        primary = [s for s in verify_steps if s.get("tier") == "primary"]
        secondary = [s for s in verify_steps if s.get("tier") == "secondary"]

        primary_passed = sum(1 for s in primary if s["status"] == "pass")
        primary_warned = sum(1 for s in primary if s["status"] == "warn")
        secondary_passed = sum(1 for s in secondary if s["status"] == "pass")

        # If no primary steps exist, fall back to old logic (all steps treated equally)
        # But if ALL steps are skipped (missing resources), that's not a pass
        if not primary:
            total = len(verify_steps)
            passed = sum(1 for s in verify_steps if s["status"] == "pass")
            warned = sum(1 for s in verify_steps if s["status"] == "warn")
            skipped = sum(1 for s in verify_steps if s["status"] == "skipped")
            if total > 0 and skipped == total:
                return "fail"
            if total == 0 or passed == total:
                return "pass"
            elif passed + warned == total:
                return "partial"
            return "fail"

        # Tiered evaluation
        all_primary_pass = primary_passed == len(primary)
        all_primary_pass_or_warn = (primary_passed + primary_warned) == len(primary)

        if pass_rule == "any_primary":
            if primary_passed > 0:
                return "pass"
            elif primary_warned > 0:
                return "partial"
            return "fail"
        elif pass_rule == "primary_and_any_secondary":
            if all_primary_pass and secondary_passed > 0:
                return "pass"
            elif all_primary_pass_or_warn:
                return "partial"
            return "fail"
        else:  # all_primary (default)
            if all_primary_pass:
                return "pass"
            elif all_primary_pass_or_warn:
                return "partial"
            return "fail"

    # ── Self-Correction Loop ─────────────────────────────────────────────

    def _attempt_self_correction(self):
        """Probe environment, patch scenario, reset steps, retry verification."""
        from engine_self_correct import SelfCorrector

        self._retry_attempted = True
        self._log_global("자기 개선 루프 시작: probe + correct + retry")

        try:
            corrector = SelfCorrector(
                run_result=self.to_dict(),
                scenario=self.scenario,
                namespace=self.namespace,
                context=self._scenario_context,
                profile=self._scenario_profile,
            )
            patched_scenario = corrector.probe_and_correct()
            self._correction_summary = corrector.get_correction_summary()
        except Exception as e:
            self._log_global(f"self-correction probe 실패: {e}")
            self._correction_summary = f"probe 실패: {e}"
            return

        if not corrector.corrections:
            self._log_global("보정 사항 없음 — retry 생략")
            return

        self._log_global(f"보정 {len(corrector.corrections)}건 적용, retry 시작")
        for c in corrector.corrections:
            self._log_global(f"  [{c['step']}] {c['field']}: {c['old']} → {c['new']}")

        # Apply patched scenario
        self.scenario = patched_scenario
        self._corrections_applied = True

        # Reset verify steps with patched configs
        self.status = "running"
        self.result = None
        self.completed_at = None

        patched_steps = (patched_scenario.get("verification", {}).get("steps")
                         or patched_scenario.get("verification", {}).get("checks") or [])

        verify_steps = self._get_verify_steps()
        for i, s in enumerate(verify_steps):
            s["status"] = "pending"
            s["detail"] = ""
            s["elapsed"] = None
            s["events"] = [{"t": round(time.time(), 1), "msg": "retry (자기 개선 후)"}]
            # Update config from patched scenario
            if i < len(patched_steps):
                s["config"] = patched_steps[i]
                s["type"] = patched_steps[i].get("type", s["type"])

        self._execute_verify_steps()
        self._phase_teardown()

    # ── Bedrock Step Correction ─────────────────────────────────────────────

    def _bedrock_correct_step(self, failed_step: dict, result: "StepResult",
                              verify_steps: list) -> dict | None:
        """Send failure context to Bedrock for step correction via tool-use probing."""
        context = {
            "scenario_id": self.scenario_id,
            "trigger": self.scenario.get("trigger"),
            "trigger_output": (self.trigger_output or "")[:500],
            "failed_step": {
                "name": failed_step["name"],
                "type": failed_step["type"],
                "config": failed_step["config"],
                "error_detail": result.detail,
                "error_category": result.error_category,
                "events": [e.get("msg", "") for e in result.events[-5:]],
            },
            "subsequent_steps": [s["config"] for s in verify_steps
                                 if s["status"] == "pending"],
        }

        prompt = (
            f"시나리오 \"{self.scenario_id}\" 실행 중 검증 step이 실패했습니다.\n\n"
            f"## 실패한 Step\n"
            f"- 이름: {failed_step['name']}\n"
            f"- 타입: {failed_step['type']}\n"
            f"- config: {json.dumps(failed_step['config'], ensure_ascii=False)}\n"
            f"- 에러: {result.detail}\n"
            f"- 분류: {result.error_category or 'unknown'}\n"
            f"- 소요시간: {result.elapsed}s (polls={result.polls})\n\n"
            f"## Timing 정보\n"
            f"- poll_interval: {failed_step['config'].get('poll_interval', 5)}s\n"
            f"- max_polls: {failed_step['config'].get('max_polls', 12)}\n"
            f"- settle_delay: {self.scenario.get('settle_delay', 'N/A')}\n"
            f"- 타이밍 문제 가능성: stale/timeout이면 max_polls 또는 poll_interval 조정 필요\n\n"
            f"## Trigger 정보\n"
            f"- 명령: {self.scenario.get('trigger', {}).get('command', 'N/A')}\n"
            f"- 출력: {(self.trigger_output or 'N/A')[:300]}\n\n"
            f"## 후속 Steps (pending)\n"
            f"{json.dumps(context['subsequent_steps'], ensure_ascii=False, indent=2)}\n\n"
            f"환경을 tool-use로 프로빙(READ-ONLY)하여 실제 상태를 확인한 후, "
            f"corrected_step과 필요 시 subsequent_steps를 JSON으로 반환하세요.\n\n"
            f"## 교정 전략 (우선순위)\n"
            f"1. 타이밍 문제(아직 상태 전이 중) → max_polls/poll_interval 증가\n"
            f"2. expected 값 불일치 → 실제 관찰된 값으로 교정\n"
            f"3. 이미 복구 완료(ex: restartCount=0) → 검증 기준을 현재 상태에 맞게 변경\n"
            f"4. type이 'manual' → 실행 가능한 type(pod_status, kubectl_check)으로 변환\n"
            f"5. step 자체가 의미 없음 → {{\"skip\": true, \"reason\": \"...\"}}"
        )

        try:
            reply = self._call_bedrock_correction(prompt)
            return self._parse_correction_response(reply)
        except Exception as e:
            self._log_global(f"[CORRECTION] Bedrock 호출 실패: {e}")
            return None

    def _call_bedrock_correction(self, prompt: str) -> str:
        """Call Strands correction agent with read-only tool-use for step correction."""
        from providers.strands_agents import create_agent

        agent = create_agent(
            "correction",
            profile=self._scenario_profile,
            region=AWS_REGION,
            kubectl_context=self._scenario_context or "",
        )
        result = agent(prompt)
        return str(result).strip()

    def _parse_correction_response(self, reply: str) -> dict | None:
        """Parse Bedrock correction JSON from reply text."""
        if not reply:
            return None

        # Extract JSON from reply (may be wrapped in ```json blocks)
        json_match = re.search(r'```json\s*(.*?)\s*```', reply, re.DOTALL)
        raw = json_match.group(1) if json_match else reply.strip()

        # Try parsing as JSON directly
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Try finding JSON object in the text
            brace_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not brace_match:
                self._log_global(f"[CORRECTION] JSON 파싱 실패: {reply[:200]}")
                return None
            try:
                data = json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                self._log_global(f"[CORRECTION] JSON 파싱 실패: {reply[:200]}")
                return None

        if data.get("skip"):
            self._log_global(f"[CORRECTION] 교정 불가: {data.get('reason', 'unknown')}")
            return None

        return data

    def _replace_subsequent_steps(self, current_idx: int, new_steps: list,
                                  verify_steps: list):
        """Replace pending subsequent steps with Bedrock-corrected versions."""
        pending_indices = [i for i, s in enumerate(verify_steps)
                          if i > current_idx and s["status"] == "pending"]

        for i, new_config in enumerate(new_steps):
            if i >= len(pending_indices):
                break
            target_idx = pending_indices[i]
            verify_steps[target_idx]["config"] = new_config
            verify_steps[target_idx]["type"] = new_config.get("type", verify_steps[target_idx]["type"])
            name = new_config.get("name") or new_config.get("description")
            if name:
                verify_steps[target_idx]["name"] = name
            verify_steps[target_idx]["events"].append({
                "t": round(time.time(), 1),
                "msg": "Bedrock correction: step 교정됨",
            })

    # ── Trigger / Preflight Correction ────────────────────────────────────

    def _correct_trigger(self) -> bool:
        """Trigger 실패 시 Agent에게 교정 요청 → 교정된 command 재실행."""
        self._log_global("Trigger 교정 시작: Agent 프로빙 + 명령 교정")
        trigger = self.scenario.get("trigger", {})
        command = trigger.get("command", "")
        prompt = (
            f'시나리오 "{self.scenario_id}" 실행 중 Trigger가 실패했습니다.\n\n'
            f"## 실패한 Trigger\n"
            f"- 명령: {command}\n"
            f"- 에러 출력: {self.trigger_output[:500]}\n"
            f"- 시나리오 목적: {self.scenario.get('purpose', '')}\n"
            f"- namespace: {self.namespace}\n\n"
            f"## 시나리오 전체 trigger 설정\n"
            f"{json.dumps(trigger, ensure_ascii=False, indent=2)}\n\n"
            f"환경을 READ-ONLY로 확인하고, 교정된 trigger command를 반환하세요.\n\n"
            f"응답 형식 (JSON만):\n"
            f'{{"corrected_trigger": {{"command": "교정된 명령"}}, "reasoning": "왜 이렇게 수정했는지"}}'
        )
        try:
            reply = self._call_bedrock_correction(prompt)
            parsed = self._parse_correction_response(reply)
            if not parsed or "corrected_trigger" not in parsed:
                self._log_global(f"Trigger 교정 실패: 파싱 불가 — {reply[:200]}")
                return False

            corrected_cmd = parsed["corrected_trigger"].get("command", "")
            reasoning = parsed.get("reasoning", "")
            self._log_global(f"Trigger 교정 수신: {corrected_cmd[:120]} | 이유: {reasoning[:100]}")

            corrected_cmd = self._inject_profile(corrected_cmd)
            ok, stdout, stderr = _run_cmd(corrected_cmd, timeout=120, context=self._scenario_context)
            output = stdout or stderr
            self.trigger_output = output

            tr_step = self._get_step("pipeline_trigger")
            if ok:
                tr_step["status"] = "pass"
                tr_step["detail"] = f"교정 후 성공: {output[:200]}"
                tr_step["events"].append({"t": round(time.time(), 1), "msg": f"교정 적용: {reasoning[:80]}"})
                self.scenario["trigger"]["command"] = parsed["corrected_trigger"]["command"]
                self._corrections_applied = True
                self._cleanup_registry.register_from_trigger(self.scenario["trigger"], output)
                return True
            else:
                tr_step["detail"] = f"교정 후에도 실패: {output[:200]}"
                self._log_global(f"Trigger 교정 실행 실패: {output[:100]}")
                return False
        except Exception as e:
            self._log_global(f"Trigger 교정 예외: {e}")
            return False

    def _correct_preflight(self, pf_results: list) -> bool:
        """Preflight 실패 시 Agent에게 setup_commands 요청 → 실행 → 재검증."""
        self._log_global("Preflight 교정 시작: Agent 프로빙 + 인프라 생성")
        failed_checks = [r for r in pf_results if not r["ok"]]
        failed_detail = "\n".join(f"- [{r['check']}] {r['detail']}" for r in failed_checks)

        trigger = self.scenario.get("trigger", {})
        prompt = (
            f'시나리오 "{self.scenario_id}" 실행 전 사전 점검이 실패했습니다.\n\n'
            f"## 실패한 항목\n{failed_detail}\n\n"
            f"## 시나리오 정보\n"
            f"- 목적: {self.scenario.get('purpose', '')}\n"
            f"- Trigger: {trigger.get('command', 'N/A')}\n"
            f"- namespace: {self.namespace}\n\n"
            f"환경을 READ-ONLY로 확인하고, 필요한 인프라를 생성하는 명령을 반환하세요.\n"
            f"대규모 인프라(IAM role, VPC, EKS cluster)는 생성 불가 → skip 반환.\n"
            f"K8s 리소스(Deployment, ConfigMap, Service, NetworkPolicy)는 생성 가능.\n\n"
            f"응답 형식 (JSON만):\n"
            f'{{"setup_commands": ["kubectl apply ...", ...], "reasoning": "..."}}\n'
            f'또는 생성 불가한 경우:\n'
            f'{{"skip": true, "reason": "왜 생성 불가한지"}}'
        )
        try:
            reply = self._call_bedrock_correction(prompt)
            parsed = self._parse_correction_response(reply)
            if not parsed:
                self._log_global(f"Preflight 교정 실패: 파싱 불가 — {reply[:200]}")
                return False

            if parsed.get("skip"):
                self._log_global(f"Preflight 교정 skip: {parsed.get('reason', '')}")
                return False

            setup_commands = parsed.get("setup_commands", [])
            if not setup_commands:
                self._log_global("Preflight 교정: setup_commands 비어 있음")
                return False

            reasoning = parsed.get("reasoning", "")
            self._log_global(f"Preflight 교정 수신 ({len(setup_commands)}개 명령): {reasoning[:100]}")

            pf_step = self._get_step("pipeline_preflight")
            for cmd in setup_commands:
                cmd = self._inject_profile(cmd)
                ok, stdout, stderr = _run_cmd(cmd, timeout=120, context=self._scenario_context)
                output = stdout or stderr
                pf_step["events"].append({"t": round(time.time(), 1), "msg": f"setup: {cmd[:80]} → {'OK' if ok else 'FAIL'}"})
                if not ok:
                    self._log_global(f"Preflight setup 실패: {cmd[:80]} → {output[:100]}")
                    return False

            self._log_global("setup 완료, pod Ready 대기 (30s)...")
            time.sleep(30)
            pf_ok2, pf_results2 = _pre_flight_check(self, self.scenario)
            if pf_ok2:
                pf_step["status"] = "pass"
                pf_step["detail"] = f"교정 후 통과 ({len(setup_commands)}개 setup)"
                pf_step["events"].append({"t": round(time.time(), 1), "msg": f"교정 적용: {reasoning[:80]}"})
                self.scenario.setdefault("preflight_setup", []).extend(
                    parsed["setup_commands"])
                self._corrections_applied = True
                self.preflight = pf_results2
                return True
            else:
                self._log_global("Preflight 재검증 실패 (setup 실행 후에도)")
                return False
        except Exception as e:
            self._log_global(f"Preflight 교정 예외: {e}")
            return False

    def _persist_corrections(self):
        """교정된 시나리오를 DDB에 영구 저장 — pass일 때만."""
        if not self._corrections_applied:
            return
        if self.result not in ("pass", "partial_pass"):
            return
        try:
            from routes_arch import _save_scenario
            scenario_to_save = json.loads(json.dumps(self.scenario))
            for step in (scenario_to_save.get("verification", {}).get("steps") or []):
                step.pop("_correction_attempted", None)
            scenario_to_save.pop("_resolved", None)
            scenario_to_save.pop("preflight_setup", None)
            _save_scenario(self.agent_space_id, scenario_to_save)
            self._log_global(f"교정된 시나리오 영구 저장 완료: {self.scenario_id}")
        except Exception as e:
            self._log_global(f"시나리오 영구 저장 실패: {e}")

    # ── Restore ────────────────────────────────────────────────────────────

    def _execute_restore(self, step):
        """Execute restore command."""
        step["status"] = "checking"
        rs_start = time.time()
        _ev = self._make_logger(step)
        _ev("복원 시작")

        restore_cmd = self.scenario.get("restore", {}).get("command", "")
        if not restore_cmd:
            step["status"] = "pass"
            step["detail"] = "복원 명령 없음"
            step["elapsed"] = 0
            return

        # Handle FIS experiment ID substitution
        if "${FIS_EXPERIMENT_ID}" in restore_cmd and self.trigger_output:
            m = re.search(r'"id"\s*:\s*"(EXP[A-Za-z0-9]+)"', self.trigger_output)
            if m:
                restore_cmd = restore_cmd.replace("${FIS_EXPERIMENT_ID}", m.group(1))

        restore_cmd = self._inject_profile(restore_cmd)
        _ev(f"실행: {restore_cmd[:120]}")
        ok, stdout, stderr = _run_cmd(restore_cmd, timeout=60, context=self._scenario_context)
        output = stdout or stderr

        step["elapsed"] = round(time.time() - rs_start, 1)
        if ok:
            step["status"] = "pass"
            step["detail"] = f"복원 완료: {output[:100]}"
            # Mark registry entries as cleaned (restore handled it)
            for entry in self._cleanup_registry._entries:
                entry.cleaned = True
        else:
            step["status"] = "fail"
            step["detail"] = f"복원 실패: {output[:100]}"
            # Restore failed — registry drain in finally will attempt cleanup
        _ev(f"{'성공' if ok else '실패'}: {output[:80]}")

    # ── Composite Pass Rules ──────────────────────────────────────────────

    def _has_composite_recovery(self, failed_step, verify_steps) -> bool:
        """Check if this failure can be recovered by a later step (composite pass)."""
        failed_type = failed_step["type"]
        remaining_types = {s["type"] for s in verify_steps if s["status"] == "pending"}

        for rule in self._get_composite_rules():
            condition = rule.get("condition", {})
            failed_types = condition.get("type_failed", [])
            passed_types = condition.get("type_passed", [])
            if failed_type in failed_types and remaining_types & set(passed_types):
                return True
        return False

    def _apply_composite_rules(self, verify_steps):
        """Apply composite pass rules (e.g., alarm fail + investigation pass → retroactive pass)."""
        rules = self._get_composite_rules()

        for rule in rules:
            condition = rule.get("condition", {})
            failed_types = set(condition.get("type_failed", []))
            passed_types = set(condition.get("type_passed", []))

            has_failed = any(s["type"] in failed_types and s["status"] == "fail"
                            for s in verify_steps)
            has_passed = any(s["type"] in passed_types and s["status"] == "pass"
                            for s in verify_steps)

            if has_failed and has_passed and rule.get("action") == "retroactive_pass":
                for s in verify_steps:
                    if s["type"] in failed_types and s["status"] == "fail":
                        s["status"] = "pass"
                        s["detail"] += " (composite rule: 소급 PASS)"
                        s["events"].append({
                            "t": round(time.time(), 1),
                            "msg": "composite pass: investigation 성공으로 소급 PASS",
                        })

    def _get_composite_rules(self) -> list:
        """Get composite rules from scenario or defaults."""
        scenario_rules = self.scenario.get("verification", {}).get("composite_pass")
        if scenario_rules:
            return scenario_rules
        return DEFAULT_COMPOSITE_RULES

    # ── Guard Evaluation ──────────────────────────────────────────────────

    def _evaluate_guard(self, guard: dict, step: dict) -> bool:
        """Evaluate step guard (pre-condition)."""
        guard_type = guard.get("type", "")

        if guard_type == "metric_datapoints_exist":
            return self._guard_metric_exists(guard)
        elif guard_type == "alarm_exists":
            alarm = guard.get("alarm", step["config"].get("alarm", ""))
            checks = self._resolver.validate_resources(
                {"verification": {"steps": [{"type": "cw_alarm", "alarm": alarm}]}},
                self._resolved_vars)
            return all(c.exists for c in checks)
        elif guard_type == "pod_running":
            pod = guard.get("pod", step["config"].get("pod", ""))
            cmd = f"kubectl get pod -l app={pod} -n {self.namespace} --no-headers"
            ok, stdout, _ = _run_cmd(cmd, timeout=10, context=self._scenario_context)
            return ok and "Running" in stdout

        return True

    def _guard_metric_exists(self, guard: dict) -> bool:
        """Check if metric has recent datapoints."""
        import boto3
        try:
            session = boto3.Session(profile_name=self._scenario_profile) if self._scenario_profile else boto3.Session()
            cw = session.client("cloudwatch", region_name=AWS_REGION)
            from datetime import timedelta
            end = datetime.now(timezone.utc)
            start = end - timedelta(minutes=5)
            resp = cw.get_metric_statistics(
                Namespace=guard.get("namespace", "ApplicationSignals"),
                MetricName=guard.get("metric_name", ""),
                StartTime=start, EndTime=end,
                Period=60, Statistics=["Sum"],
                Dimensions=guard.get("dimensions", []),
            )
            min_dp = guard.get("min_datapoints", 1)
            return len(resp.get("Datapoints", [])) >= min_dp
        except Exception:
            return True

    def _check_depends(self, depends_on: list) -> bool:
        """Check if all dependency steps have passed."""
        for dep_name in depends_on:
            found = False
            for s in self.steps:
                if s["name"] == dep_name:
                    found = True
                    if s["status"] != "pass":
                        return False
                    break
            if not found:
                pass
        return True

    # ── Helpers ───────────────────────────────────────────────────────────

    def _mark_secondary_unavailable(self, missing_resource_names: set):
        """Mark secondary verification steps whose resources don't exist as skip."""
        for s in self.steps:
            if s.get("tier") != "secondary" or s["status"] != "pending":
                continue
            alarm = s["config"].get("alarm", "")
            log_group = s["config"].get("log_group", "")
            fn = s["config"].get("function", "")
            if alarm in missing_resource_names or log_group in missing_resource_names or fn in missing_resource_names:
                s["status"] = "skipped"
                s["detail"] = f"secondary 리소스 미존재 — 실행 생략 (리소스 배포 필요)"
                s["events"].append({"t": round(time.time(), 1),
                                    "msg": f"tier=secondary, 리소스 부재로 skip"})

    def _get_step(self, step_type: str):
        for s in self.steps:
            if s["type"] == step_type:
                return s
        return None

    def _get_verify_steps(self) -> list:
        return [s for s in self.steps[self._verify_start_idx:]
                if not s["type"].startswith("pipeline_")]

    def _prepare_step_config(self, config: dict):
        """Inject runtime context into step config for VERIFIERS compatibility."""
        config["_namespace"] = self.namespace
        config["_run_obj"] = self
        config["_scenario_context"] = self._scenario_context
        config["_scenario_profile"] = self._scenario_profile
        config["_run_started_at"] = self._started_ts

    def _inject_profile(self, cmd: str) -> str:
        if self._scenario_profile and "aws " in cmd and "--profile " not in cmd:
            cmd = cmd.replace("aws ", f"aws --profile {self._scenario_profile} ", 1)
        return cmd

    def _infer_settle_delay(self, verify_steps) -> float:
        """Infer settling delay from primary step types only."""
        primary_types = {s["type"] for s in verify_steps if s.get("tier") == "primary"}
        if primary_types & {"pod_status"}:
            return 10.0
        if primary_types & {"pod_logs", "kubectl_check"}:
            return 5.0
        return 5.0

    def _reset_alarms(self, alarm_names, _ev):
        """Reset CloudWatch alarms to OK."""
        import boto3
        try:
            session = boto3.Session(profile_name=self._scenario_profile) if self._scenario_profile else boto3.Session()
            cw = session.client("cloudwatch", region_name=AWS_REGION)
            for alarm in alarm_names:
                cw.set_alarm_state(AlarmName=alarm, StateValue="OK",
                                   StateReason="Pre-test reset by engine")
                _ev(f"알람 리셋: {alarm} → OK")
        except Exception as e:
            _ev(f"알람 리셋 실패: {e}")

    def _abort_all(self, reason):
        """Mark all pending steps as skipped."""
        for s in self.steps:
            if s["status"] == "pending":
                s["status"] = "skipped"
                s["detail"] = f"{reason}으로 건너뜀"

    def _skip_remaining(self, failed_step, verify_steps):
        """Skip all steps after the failed one."""
        found = False
        for s in verify_steps:
            if found and s["status"] == "pending":
                s["status"] = "skipped"
                s["detail"] = f"이전 단계 실패로 건너뜀 ({failed_step['name']})"
            if s is failed_step:
                found = True

    def _make_logger(self, step):
        def _ev(msg):
            step["events"].append({"t": round(time.time(), 1), "msg": msg})
        return _ev

    def _log_global(self, msg):
        """Log to first step's events as fallback."""
        if self.steps:
            self.steps[0]["events"].append({"t": round(time.time(), 1), "msg": msg})

    def _schedule_cleanup(self):
        """Schedule run removal from memory after 5 minutes."""
        import threading

        def _cleanup():
            time.sleep(300)
            try:
                from verifier import _runs_lock, _active_runs
                with _runs_lock:
                    _active_runs.pop(self.run_id, None)
            except Exception:
                pass

        threading.Thread(target=_cleanup, daemon=True).start()

    # ── Serialization (SimulationRun compatible) ──────────────────────────

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario.get("name", ""),
            "agent_space_id": self.agent_space_id or "",
            "started_at": self.started_at,
            "started_ts": self._started_ts,
            "completed_at": self.completed_at,
            "status": self.status,
            "result": self.result,
            "trigger_output": self.trigger_output,
            "incident_id": self._incident_id or "",
            "investigation_task_id": self._investigation_task_id,
            "architecture": self.scenario.get("architecture"),
            "normal_flow": self.scenario.get("normal_flow", []),
            "fault_flow": self.scenario.get("fault_flow", []),
            "flow": self.scenario.get("flow", []),
            "self_correction": self._correction_summary,
            "cleanup_registry": self._cleanup_registry.entries,
            "steps": [
                {
                    "index": i,
                    "name": s["name"],
                    "type": s["type"],
                    "tier": s.get("tier", "primary"),
                    "pod": s["config"].get("pod", ""),
                    "status": s["status"],
                    "detail": s["detail"],
                    "elapsed": s["elapsed"],
                    "events": s.get("events", []),
                    **({"error_category": s.get("error_category", ""), "error_reason": s.get("error_reason", "")}
                       if s.get("error_category") else {}),
                }
                for i, s in enumerate(self.steps)
            ],
        }

    def save(self) -> str:
        """Persist run to DynamoDB."""
        try:
            from decimal import Decimal
            table = _agent_space_session().resource("dynamodb", region_name=AWS_REGION).Table(_RUNS_TABLE)
            d = self.to_dict()
            item = json.loads(json.dumps(d), parse_float=Decimal)
            item["run_id"] = self.run_id
            item["record_type"] = "run"
            item["scenario_id"] = self.scenario_id
            if self.agent_space_id:
                item["agent_space_id"] = self.agent_space_id
            table.put_item(Item=item)
        except Exception as e:
            print(f"[PhasedExecutor] DynamoDB save failed: {e}")
            from verifier_utils import RESULTS_DIR
            os.makedirs(RESULTS_DIR, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            filepath = os.path.join(RESULTS_DIR, f"{ts}_{self.scenario_id}_{self.run_id}.json")
            with open(filepath, "w") as f:
                json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        return self.run_id
