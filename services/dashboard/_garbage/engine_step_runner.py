"""
Engine Step Runner: adaptive single-step execution with progress detection.

Responsibilities:
- Execute a single verification step using existing VERIFIERS[type] functions
- Detect progress signals → auto-extend timeout (prevents false timeout on slow envs)
- Enforce minimum poll count before timeout declaration
- Execute error escalation chain on failure
- Return structured StepResult

Dependency: VERIFIERS dict, _classify_step_error, ErrorAction, _auto_correct_step.
No reverse dependency on execution_engine.
"""
import time
from dataclasses import dataclass, field
from enum import Enum

from verifier_checkers import VERIFIERS, _classify_step_error
from error_response_strategy import ErrorAction, compute_backoff_delay


class StepStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


@dataclass
class StepResult:
    status: str = "fail"
    detail: str = ""
    elapsed: float = 0.0
    polls: int = 0
    extensions: int = 0
    error_category: str | None = None
    error_reason: str | None = None
    events: list = field(default_factory=list)

    @property
    def passed(self):
        return self.status == "pass"


# ── Progress Detection ────────────────────────────────────────────────────

_PROGRESS_RULES = {
    "cw_alarm": [
        ("OK", "INSUFFICIENT_DATA"),
        ("INSUFFICIENT_DATA", "ALARM"),
    ],
    "alarm_state": [
        ("OK", "INSUFFICIENT_DATA"),
        ("INSUFFICIENT_DATA", "ALARM"),
    ],
    "metric_check": [
        ("데이터 없음", None),
    ],
    "investigation_event": [
        (None, "task_id"),
        (None, "IN_PROGRESS"),
        ("IN_PROGRESS", "COMPLETED"),
    ],
    "agent_investigation": [
        (None, "task_id"),
        (None, "IN_PROGRESS"),
    ],
    "fis_experiment": [
        ("initiating", "running"),
    ],
    "pod_status": [
        ("Running", "CrashLoopBackOff"),
        ("Running", "OOMKilled"),
        ("Pending", "Running"),
        ("Pending", "ErrImagePull"),
        ("Pending", "ImagePullBackOff"),
        ("ErrImagePull", "ImagePullBackOff"),
        ("ContainerCreating", "Running"),
        ("ContainerCreating", "CrashLoopBackOff"),
        ("파드 없음", "Pending"),
    ],
    "pod_logs": [
        (None, "Error"),
        (None, "Exception"),
        ("패턴 미발견", None),
    ],
}


def _detect_progress(step_type: str, prev_detail: str, curr_detail: str) -> bool:
    """Detect if verification is making progress toward the goal."""
    if prev_detail == curr_detail:
        return False

    rules = _PROGRESS_RULES.get(step_type, [])
    for prev_signal, curr_signal in rules:
        if prev_signal is None:
            if curr_signal and curr_signal in curr_detail and curr_signal not in prev_detail:
                return True
        elif curr_signal is None:
            if prev_signal in prev_detail and prev_signal not in curr_detail:
                return True
        else:
            if prev_signal in prev_detail and curr_signal in curr_detail:
                return True

    return False


# ── Transitional State Detection ─────────────────────────────────────────

_TRANSITIONAL_STATES = {
    "pod_status": {"Pending", "ContainerCreating", "PodInitializing", "Init:"},
    "fis_experiment": {"initiating", "pending"},
    "investigation_event": {"IN_PROGRESS"},
}


def _is_transitional_state(step_type: str, detail: str) -> bool:
    """Return True if the current detail indicates system is still working (not stale)."""
    patterns = _TRANSITIONAL_STATES.get(step_type, set())
    return any(p in detail for p in patterns)


# ── Adaptive Step Runner ──────────────────────────────────────────────────

class AdaptiveStepRunner:
    """Executes a single verification step with state convergence detection.

    Termination logic: NOT time-based. Instead:
    - PASS: verifier returns ok=True
    - FAIL (stale): N consecutive identical results = system stopped changing
    - Safety cap: max_polls prevents infinite loop (but is NOT the primary exit)
    """

    DEFAULT_POLL_INTERVAL = 5
    DEFAULT_STALE_THRESHOLD = 5   # N consecutive identical → stale → fail
    DEFAULT_MAX_POLLS = 60        # safety cap only (not primary exit condition)

    def run(self, step_config: dict, *, run_obj=None,
            timeout_override: float | None = None, fail_ok: bool = False) -> StepResult:
        """Execute a verification step using state convergence.

        Exit conditions (in priority order):
        1. ok=True → PASS
        2. stale_count >= stale_threshold → system stopped → FAIL
        3. poll_count >= max_polls → safety cap → FAIL
        """
        step_type = step_config.get("type", "")
        verifier_fn = VERIFIERS.get(step_type)
        if not verifier_fn:
            return StepResult(
                status="fail",
                detail=f"미등록 검증 타입: {step_type}",
                error_category="config_error",
                error_reason=f"VERIFIERS에 '{step_type}' 없음",
            )

        poll_interval = step_config.get("poll_interval", self.DEFAULT_POLL_INTERVAL)
        stale_threshold = step_config.get("stale_threshold", self.DEFAULT_STALE_THRESHOLD)
        max_polls = step_config.get("max_polls", self.DEFAULT_MAX_POLLS)

        # timeout_override → convert to max_polls (for effect_confirm and other callers)
        if timeout_override:
            max_polls = max(3, int(timeout_override / poll_interval))

        events = []
        start_time = time.time()
        poll_count = 0
        stale_count = 0
        last_detail = ""
        extensions_used = 0

        def _event(msg):
            events.append({"t": round(time.time(), 1), "msg": msg})

        _event(f"polling 시작 (stale_threshold={stale_threshold}, max_polls={max_polls}, interval={poll_interval}s)")

        # ── Polling loop: state convergence ──
        while poll_count < max_polls:
            ok, detail = verifier_fn(step_config)
            poll_count += 1

            if poll_count <= 3 or poll_count % 5 == 0 or ok:
                _event(f"poll#{poll_count}: {detail[:100]}")

            if ok:
                elapsed = round(time.time() - start_time, 1)
                _event(f"PASS ({elapsed}s, {poll_count} polls)")
                return StepResult(
                    status="pass", detail=detail, elapsed=elapsed,
                    polls=poll_count, extensions=extensions_used, events=events,
                )

            # State convergence detection
            if detail == last_detail:
                # Some states are inherently "in progress" — not stale
                # But skip this exemption for fail_ok probes (effect_confirm)
                if not fail_ok and _is_transitional_state(step_type, detail):
                    stale_count = 0
                else:
                    stale_count += 1
            else:
                # State changed — progress detected, reset stale counter
                if last_detail and _detect_progress(step_type, last_detail, detail):
                    extensions_used += 1
                    _event(f"progress 감지: {last_detail[:40]} → {detail[:40]}")
                stale_count = 0

            if stale_count >= stale_threshold:
                _event(f"stale 감지: {stale_count}회 연속 동일 결과 → 시스템 정지")
                break

            last_detail = detail
            time.sleep(poll_interval)

        # ── Convergence reached (stale) or safety cap ──
        elapsed = round(time.time() - start_time, 1)
        exit_reason = "stale" if stale_count >= stale_threshold else "max_polls"
        _event(f"{exit_reason} ({elapsed}s, {poll_count} polls)")

        if fail_ok:
            return StepResult(
                status="warn", detail=last_detail, elapsed=elapsed,
                polls=poll_count, extensions=extensions_used, events=events,
            )

        # ── Error escalation ──
        error_handling = step_config.get("error_handling", {})
        escalation = error_handling.get("escalation", [])
        skip_on_final = error_handling.get("skip_on_final_fail", False)

        cat, err_reason = _classify_step_error(step_type, last_detail, timed_out=True)

        if escalation:
            recovery_result = self._escalate(
                escalation, step_config, verifier_fn, run_obj, cat, _event)
            if recovery_result:
                recovery_result.events = events + recovery_result.events
                return recovery_result

        # ── Fallback: use static matrix ──
        from error_response_strategy import get_response_action
        action = get_response_action(step_type, cat, error_handling or None)
        recovery_result = self._execute_action(
            action, step_config, verifier_fn, run_obj, cat, _event)
        if recovery_result and recovery_result.passed:
            recovery_result.events = events + recovery_result.events
            return recovery_result

        # ── Final failure ──
        final_status = "warn" if skip_on_final else "fail"
        _event(f"최종 {final_status}: [{cat}] {err_reason}")
        return StepResult(
            status=final_status,
            detail=f"수렴 ({exit_reason}, {elapsed}s) - {last_detail}",
            elapsed=elapsed, polls=poll_count, extensions=extensions_used,
            error_category=cat, error_reason=err_reason, events=events,
        )

    # ── Error Escalation Chain ────────────────────────────────────────────

    def _escalate(self, escalation: list, step_config, verifier_fn,
                  run_obj, cat, _event) -> StepResult | None:
        """Try each action in escalation chain until one succeeds."""
        for i, action_name in enumerate(escalation):
            action = _ACTION_MAP.get(action_name)
            if not action:
                continue
            _event(f"escalation [{i+1}/{len(escalation)}]: {action_name}")
            result = self._execute_action(action, step_config, verifier_fn, run_obj, cat, _event)
            if result and result.passed:
                return result
        return None

    def _execute_action(self, action: ErrorAction, step_config, verifier_fn,
                        run_obj, cat, _event) -> StepResult | None:
        """Execute a single error recovery action."""
        start = time.time()

        if action == ErrorAction.BLOCKED:
            _event("[BLOCKED] 복구 불가")
            return None

        elif action == ErrorAction.RETRY_BACKOFF:
            max_retries = step_config.get("error_handling", {}).get("max_retries", 3)
            for attempt in range(max_retries):
                delay = compute_backoff_delay(attempt)
                _event(f"backoff #{attempt+1}: {delay}s 대기")
                time.sleep(delay)
                ok, detail = verifier_fn(step_config)
                if ok:
                    return StepResult(
                        status="pass", detail=detail,
                        elapsed=round(time.time() - start, 1),
                        events=[{"t": round(time.time(), 1),
                                 "msg": f"PASS (backoff #{attempt+1})"}],
                    )
            return None

        elif action == ErrorAction.AGENT_CORRECT:
            if not run_obj:
                _event("[AGENT_CORRECT] run_obj 없음 — 스킵")
                return None
            try:
                from verifier import _auto_correct_step
                step_dict = {"type": step_config.get("type", ""),
                             "name": step_config.get("name", ""),
                             "detail": step_config.get("_last_detail", "")}
                _ev_compat = lambda _step, msg: _event(msg)
                corrected = _auto_correct_step(run_obj, step_dict, step_config, _ev_compat)
                if corrected:
                    ok, detail = verifier_fn(step_config)
                    if ok:
                        return StepResult(
                            status="pass", detail=detail,
                            elapsed=round(time.time() - start, 1),
                            events=[{"t": round(time.time(), 1),
                                     "msg": "PASS (Agent 교정)"}],
                        )
            except Exception as e:
                _event(f"Agent 교정 실패: {e}")
            return None

        elif action == ErrorAction.TRIGGER_REINJECT:
            _event("[TRIGGER_REINJECT] step_runner에서는 미지원 — orchestrator 위임")
            return None

        elif action == ErrorAction.POLL_CONTINUE:
            poll_interval = step_config.get("poll_interval", self.DEFAULT_POLL_INTERVAL)
            extra_polls = step_config.get("stale_threshold", self.DEFAULT_STALE_THRESHOLD)
            _event(f"[POLL_CONTINUE] 추가 {extra_polls} polls")
            for _ in range(extra_polls):
                ok, detail = verifier_fn(step_config)
                if ok:
                    return StepResult(
                        status="pass", detail=detail,
                        elapsed=round(time.time() - start, 1),
                        events=[{"t": round(time.time(), 1),
                                 "msg": "PASS (추가 대기)"}],
                    )
                time.sleep(poll_interval)
            return None

        return None


# Action name → ErrorAction mapping for escalation chains
_ACTION_MAP = {
    "poll_continue": ErrorAction.POLL_CONTINUE,
    "retry_backoff": ErrorAction.RETRY_BACKOFF,
    "agent_correct": ErrorAction.AGENT_CORRECT,
    "trigger_reinject": ErrorAction.TRIGGER_REINJECT,
    "blocked": ErrorAction.BLOCKED,
}
