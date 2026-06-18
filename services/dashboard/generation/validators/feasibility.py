"""Feasibility validator — timeout 산술 검증, 효과 발현 시간 체크.

비용: 무료 (in-memory arithmetic only).
"""

from __future__ import annotations

from generation.types import ValidationIssue, ValidationResult


_PHASE_TIMEOUT_RANGES = {
    "trigger_active": (10, 120),
    "effect_observed": (60, 600),
    "reaction_confirmed": (120, 900),
}

_EFFECT_BUDGET_TARGET = 300  # 5분


class TimeoutFeasibilityValidator:
    """timeout 값의 타당성 + 효과 발현 5분 예산 검증."""

    stage = "feasibility"

    def validate(self, scenario: dict, context: dict | None = None) -> ValidationResult:
        issues: list[ValidationIssue] = []
        steps = scenario.get("verification", {}).get("steps", [])

        effect_total = 0

        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            phase = step.get("phase", "")
            timeout = step.get("timeout")

            if timeout is not None:
                try:
                    timeout = int(timeout)
                except (TypeError, ValueError):
                    issues.append(ValidationIssue(
                        severity="error", code="INVALID_TIMEOUT",
                        message=f"steps[{i}]: timeout은 정수여야 합니다 (got: {timeout})",
                        field=f"verification.steps[{i}].timeout",
                    ))
                    continue

                if phase in _PHASE_TIMEOUT_RANGES:
                    lo, hi = _PHASE_TIMEOUT_RANGES[phase]
                    if timeout < lo:
                        issues.append(ValidationIssue(
                            severity="warning", code="TIMEOUT_TOO_LOW",
                            message=f"steps[{i}]: timeout={timeout}s, phase '{phase}' 최소 {lo}s 권장",
                            field=f"verification.steps[{i}].timeout",
                            fix_hint=f"timeout을 {lo}s 이상으로 설정.",
                        ))
                    elif timeout > hi:
                        issues.append(ValidationIssue(
                            severity="warning", code="TIMEOUT_TOO_HIGH",
                            message=f"steps[{i}]: timeout={timeout}s, phase '{phase}' 최대 {hi}s 권장",
                            field=f"verification.steps[{i}].timeout",
                        ))

            if phase in ("trigger_active", "effect_observed") and timeout:
                effect_total += int(timeout)

        if effect_total > _EFFECT_BUDGET_TARGET:
            issues.append(ValidationIssue(
                severity="warning", code="EFFECT_BUDGET_EXCEEDED",
                message=(f"효과 발현 합계 {effect_total}s > 목표 {_EFFECT_BUDGET_TARGET}s (5분). "
                         f"best effort이므로 경고만."),
                field="verification.steps",
                fix_hint="trigger 효과가 빠르게 나타나도록 공격적 조건 설정 고려.",
            ))

        # poll_interval 검증
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            poll = step.get("poll_interval")
            timeout = step.get("timeout")
            if poll and timeout:
                try:
                    poll_int = int(poll)
                    timeout_int = int(timeout)
                    if poll_int >= timeout_int:
                        issues.append(ValidationIssue(
                            severity="error", code="POLL_GE_TIMEOUT",
                            message=f"steps[{i}]: poll_interval({poll_int}) >= timeout({timeout_int})",
                            field=f"verification.steps[{i}].poll_interval",
                            fix_hint="poll_interval은 timeout보다 작아야 합니다.",
                        ))
                except (TypeError, ValueError):
                    pass

        return ValidationResult(
            valid=not any(i.severity == "error" for i in issues),
            issues=issues,
        )
