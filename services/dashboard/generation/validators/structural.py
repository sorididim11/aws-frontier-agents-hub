"""Structural validator — JSON 필수 필드, 타입, phase 순서 검증.

비용: 무료 (in-memory only, no external calls).
기존 routes_scenario.py _validate_scenario() 로직을 추출.
"""

from __future__ import annotations

from generation.types import ValidationIssue, ValidationResult


_PHASE_ORDER = ["trigger_active", "effect_observed", "reaction_confirmed"]
_PHASE_ALLOWED_TYPES = {
    "trigger_active": {"kubectl_check", "fis_experiment", "pod_status"},
    "effect_observed": {"alarm_state", "metric_check", "log_pattern", "xray_trace", "xray_latency", "cw_alarm"},
    "reaction_confirmed": {"investigation_event", "agent_investigation", "slack_message"},
}
_REQUIRED_ALARM_SPEC_FIELDS = ["metric_name", "namespace", "statistic", "comparison", "threshold", "period"]


class ScenarioStructuralValidator:
    """시나리오 JSON 구조 검증 — 필수 필드, 타입, phase 순서."""

    stage = "structural"

    def validate(self, scenario: dict, context: dict | None = None) -> ValidationResult:
        issues: list[ValidationIssue] = []

        self._check_required_fields(scenario, issues)
        self._check_trigger(scenario, issues)
        self._check_verification_steps(scenario, issues)
        self._check_phase_order(scenario, issues)
        self._check_alarm_spec(scenario, issues)
        self._check_rubric(scenario, issues)
        self._check_restore(scenario, issues)

        return ValidationResult(
            valid=not any(i.severity == "error" for i in issues),
            issues=issues,
        )

    def _check_required_fields(self, scenario: dict, issues: list):
        required = ["id", "name", "target_service", "trigger", "verification",
                    "category", "layer", "trigger_mode"]
        missing = [f for f in required if not scenario.get(f)]
        if missing:
            issues.append(ValidationIssue(
                severity="error", code="MISSING_FIELDS",
                message=f"필수 필드 누락: {', '.join(missing)}",
                field=", ".join(missing),
                fix_hint="모든 필수 필드를 포함하세요.",
            ))

        if not scenario.get("purpose"):
            issues.append(ValidationIssue(
                severity="error", code="MISSING_PURPOSE",
                message="purpose 필수 — 시나리오 목적 1~2문장",
                field="purpose",
            ))

        sv = scenario.get("skill_version", "")
        if sv and sv != "2.1":
            issues.append(ValidationIssue(
                severity="warning", code="SKILL_VERSION",
                message=f"skill_version={sv}, 기대=2.1",
                field="skill_version",
                fix_hint='skill_version: "2.1"',
            ))

    def _check_trigger(self, scenario: dict, issues: list):
        trigger = scenario.get("trigger")
        if not isinstance(trigger, dict):
            issues.append(ValidationIssue(
                severity="error", code="INVALID_TRIGGER",
                message="trigger는 dict여야 합니다",
                field="trigger",
            ))
            return

        if not trigger.get("command", "").strip():
            issues.append(ValidationIssue(
                severity="error", code="EMPTY_TRIGGER_COMMAND",
                message="trigger.command 비어있음",
                field="trigger.command",
            ))

        if isinstance(trigger.get("commands"), list):
            issues.append(ValidationIssue(
                severity="error", code="TRIGGER_COMMANDS_ARRAY",
                message="trigger.commands 배열 금지 — trigger.command 단일 문자열 사용",
                field="trigger.command",
                fix_hint="commands 배열을 ' && '.join()으로 합치세요.",
            ))

        trigger_type = trigger.get("type", "")
        if trigger_type and trigger_type not in ("kubectl", "aws", "fis"):
            issues.append(ValidationIssue(
                severity="warning", code="UNKNOWN_TRIGGER_TYPE",
                message=f"trigger.type='{trigger_type}' — 허용: kubectl, aws, fis",
                field="trigger.type",
            ))

    def _check_verification_steps(self, scenario: dict, issues: list):
        verification = scenario.get("verification", {})
        if not isinstance(verification, dict):
            issues.append(ValidationIssue(
                severity="error", code="INVALID_VERIFICATION",
                message="verification은 dict여야 합니다",
                field="verification",
            ))
            return

        steps = verification.get("steps", [])
        if not steps:
            issues.append(ValidationIssue(
                severity="error", code="NO_VERIFICATION_STEPS",
                message="verification.steps 필수 — 검증 단계 배열 필요",
                field="verification.steps",
            ))
            return

        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            if not step.get("type"):
                issues.append(ValidationIssue(
                    severity="error", code="STEP_NO_TYPE",
                    message=f"steps[{i}]에 type 필드 누락",
                    field=f"verification.steps[{i}].type",
                ))
            if not step.get("phase"):
                issues.append(ValidationIssue(
                    severity="warning", code="STEP_NO_PHASE",
                    message=f"steps[{i}]에 phase 필드 누락",
                    field=f"verification.steps[{i}].phase",
                ))

    def _check_phase_order(self, scenario: dict, issues: list):
        steps = scenario.get("verification", {}).get("steps", [])
        last_phase_idx = -1

        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            phase = step.get("phase")
            if not phase:
                continue
            if phase not in _PHASE_ORDER:
                issues.append(ValidationIssue(
                    severity="error", code="INVALID_PHASE",
                    message=f"steps[{i}]: 유효하지 않은 phase '{phase}'",
                    field=f"verification.steps[{i}].phase",
                    fix_hint=f"허용: {_PHASE_ORDER}",
                ))
                continue

            phase_idx = _PHASE_ORDER.index(phase)
            if phase_idx < last_phase_idx:
                issues.append(ValidationIssue(
                    severity="error", code="PHASE_ORDER_VIOLATION",
                    message=f"steps[{i}]: phase 순서 역전 ({phase}가 이전 phase보다 앞)",
                    field=f"verification.steps[{i}].phase",
                    fix_hint="순서: trigger_active → effect_observed → reaction_confirmed",
                ))
            last_phase_idx = phase_idx

            step_type = step.get("type", "")
            allowed = _PHASE_ALLOWED_TYPES.get(phase, set())
            if step_type and allowed and step_type not in allowed:
                issues.append(ValidationIssue(
                    severity="warning", code="PHASE_TYPE_MISMATCH",
                    message=f"steps[{i}]: type '{step_type}'은 phase '{phase}'에 비표준",
                    field=f"verification.steps[{i}]",
                    fix_hint=f"phase '{phase}' 허용 types: {sorted(allowed)}",
                ))

    def _check_alarm_spec(self, scenario: dict, issues: list):
        steps = scenario.get("verification", {}).get("steps", [])
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            if step.get("type") not in ("alarm_state", "cw_alarm"):
                continue

            alarm_spec = step.get("alarm_spec")
            alarm_name = step.get("alarm_name") or step.get("alarm")

            if alarm_spec:
                if not isinstance(alarm_spec, dict):
                    issues.append(ValidationIssue(
                        severity="error", code="INVALID_ALARM_SPEC",
                        message=f"steps[{i}]: alarm_spec은 dict여야 합니다",
                        field=f"verification.steps[{i}].alarm_spec",
                    ))
                else:
                    missing = [f for f in _REQUIRED_ALARM_SPEC_FIELDS if f not in alarm_spec]
                    if missing:
                        issues.append(ValidationIssue(
                            severity="error", code="ALARM_SPEC_INCOMPLETE",
                            message=f"steps[{i}]: alarm_spec 필수 필드 누락: {', '.join(missing)}",
                            field=f"verification.steps[{i}].alarm_spec",
                            fix_hint=f"필수: {_REQUIRED_ALARM_SPEC_FIELDS}",
                        ))
            elif not alarm_name:
                issues.append(ValidationIssue(
                    severity="error", code="NO_ALARM_REF",
                    message=f"steps[{i}]: alarm_name 또는 alarm_spec 필수",
                    field=f"verification.steps[{i}]",
                    fix_hint="기존 알람이 있으면 alarm_name, 없으면 alarm_spec 사용",
                ))

    def _check_rubric(self, scenario: dict, issues: list):
        rubric = scenario.get("evaluation_rubric")
        if not rubric:
            return
        criteria = []
        if isinstance(rubric, dict):
            criteria = rubric.get("criteria", [])
        elif isinstance(rubric, list):
            criteria = rubric

        if criteria:
            total = sum(c.get("weight", 0) for c in criteria if isinstance(c, dict))
            if total != 100:
                issues.append(ValidationIssue(
                    severity="error", code="RUBRIC_WEIGHT_SUM",
                    message=f"evaluation_rubric weight 합계={total} (100이어야 함)",
                    field="evaluation_rubric",
                    fix_hint="각 criteria weight 조정하여 합계 100으로 맞추세요.",
                ))

    def _check_restore(self, scenario: dict, issues: list):
        restore = scenario.get("restore")
        if not restore:
            issues.append(ValidationIssue(
                severity="warning", code="NO_RESTORE",
                message="restore 누락 — 장애 주입 후 원상복구 명령 필수",
                field="restore",
            ))
        elif isinstance(restore, dict) and not restore.get("command", "").strip():
            issues.append(ValidationIssue(
                severity="warning", code="EMPTY_RESTORE",
                message="restore.command 비어있음",
                field="restore.command",
            ))
