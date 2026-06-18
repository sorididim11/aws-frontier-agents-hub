"""Semantic validator — CloudWatch dimension 매칭, 리소스 존재 확인.

비용: 중간 (AWS API 호출 필요: list_metrics, describe-alarms, kubectl).
오늘의 INSUFFICIENT_DATA 버그를 방지하는 핵심 검증기.
"""

from __future__ import annotations

import json
import os
import subprocess

from generation.types import ValidationIssue, ValidationResult


def _build_env(profile: str = "", region: str = "") -> dict:
    env = {**os.environ, "AWS_PAGER": ""}
    path = env.get("PATH", "")
    for p in ("/opt/homebrew/bin", "/usr/local/bin"):
        if p not in path:
            path = p + ":" + path
    env["PATH"] = path
    if profile:
        env["AWS_PROFILE"] = profile
    if region:
        env["AWS_REGION"] = region
    return env


def _run_cmd(cmd: str, env: dict, timeout: int = 15) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        return result.returncode == 0, result.stdout.strip()
    except (subprocess.TimeoutExpired, Exception):
        return False, ""


class CloudWatchDimensionValidator:
    """metric_check/alarm_spec의 dimension이 실제 CloudWatch 메트릭과 매칭되는지 검증."""

    stage = "semantic"

    def validate(self, scenario: dict, context: dict | None = None) -> ValidationResult:
        issues: list[ValidationIssue] = []
        ctx = context or {}
        steps = scenario.get("verification", {}).get("steps", [])

        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            if step.get("type") == "alarm_state" and step.get("alarm_spec"):
                self._check_dimensions(step["alarm_spec"], i, "alarm_spec", issues, ctx)
            elif step.get("type") == "metric_check":
                self._check_dimensions(step, i, "metric_check", issues, ctx)

        return ValidationResult(
            valid=not any(i.severity == "error" for i in issues),
            issues=issues,
        )

    def _check_dimensions(self, spec: dict, step_idx: int, source: str,
                          issues: list, ctx: dict):
        ns = spec.get("namespace", "")
        metric = spec.get("metric_name", "")
        given_dims = spec.get("dimensions", [])

        if not ns or not metric:
            return

        given_dim_names = {d.get("Name") for d in given_dims if isinstance(d, dict)}

        actual_combos = self._list_metric_dimensions(ns, metric, ctx)

        if actual_combos is None:
            issues.append(ValidationIssue(
                severity="warning", code="CW_API_UNAVAILABLE",
                message=f"steps[{step_idx}]: CloudWatch list-metrics 실패 — dimension 검증 미수행",
                field=f"verification.steps[{step_idx}].{source}",
                fix_hint="AWS credentials/network 확인. 정적 규칙(structural)만 적용됨.",
            ))
            return

        if not actual_combos:
            issues.append(ValidationIssue(
                severity="error", code="METRIC_NOT_FOUND",
                message=f"steps[{step_idx}]: 메트릭 {ns}/{metric} 데이터 없음",
                field=f"verification.steps[{step_idx}].{source}",
                fix_hint="namespace/metric_name 조합 확인. CloudWatch에서 존재하는 메트릭 사용하세요.",
            ))
            return

        exact_match = any(given_dim_names == combo for combo in actual_combos)
        subset_match = any(given_dim_names.issubset(combo) for combo in actual_combos)

        if not exact_match and not subset_match:
            actual_str = ", ".join(str(sorted(c)) for c in actual_combos[:3])
            issues.append(ValidationIssue(
                severity="error", code="DIMENSION_MISMATCH",
                message=(f"steps[{step_idx}]: dimensions {sorted(given_dim_names)} "
                         f"가 실제 조합과 불일치"),
                field=f"verification.steps[{step_idx}].dimensions",
                fix_hint=f"실제 dimension 조합: {actual_str}. 정확히 일치하는 조합 사용.",
            ))
        elif subset_match and not exact_match:
            matching = [c for c in actual_combos if given_dim_names.issubset(c)]
            missing = matching[0] - given_dim_names if matching else set()
            if missing:
                issues.append(ValidationIssue(
                    severity="error", code="DIMENSION_INCOMPLETE",
                    message=(f"steps[{step_idx}]: dimension 불완전 — "
                             f"{sorted(missing)} 추가 필요"),
                    field=f"verification.steps[{step_idx}].dimensions",
                    fix_hint=f"누락된 dimension: {sorted(missing)}. 전체 조합으로 변경하세요.",
                ))

    def _list_metric_dimensions(self, namespace: str, metric_name: str,
                                ctx: dict) -> list[set[str]] | None:
        """list_metrics API로 실제 dimension 조합 조회. 실패 시 None (검증 skip)."""
        profile = ctx.get("aws_profile", "")
        region = ctx.get("aws_region", "us-east-1")
        env = _build_env(profile, region)

        cmd = (
            f"aws cloudwatch list-metrics"
            f" --namespace '{namespace}' --metric-name '{metric_name}'"
            f" --query 'Metrics[*].Dimensions[*].Name' --output json"
        )
        ok, stdout = _run_cmd(cmd, env, timeout=10)
        if not ok or not stdout:
            return None

        try:
            raw = json.loads(stdout)
            combos = [set(dim_list) for dim_list in raw if dim_list]
            unique = []
            for c in combos:
                if c not in unique:
                    unique.append(c)
            return unique
        except (json.JSONDecodeError, TypeError):
            return None


class ResourceExistenceValidator:
    """target_service, alarm_name 등 리소스 존재 확인."""

    stage = "semantic"

    def validate(self, scenario: dict, context: dict | None = None) -> ValidationResult:
        issues: list[ValidationIssue] = []
        ctx = context or {}

        self._check_target_service(scenario, issues, ctx)
        self._check_alarm_names(scenario, issues, ctx)

        return ValidationResult(
            valid=not any(i.severity == "error" for i in issues),
            issues=issues,
        )

    def _check_target_service(self, scenario: dict, issues: list, ctx: dict):
        target = scenario.get("target_service", "").strip()
        if not target:
            return

        deployments = ctx.get("available_deployments", [])
        if not deployments:
            return

        if target not in deployments:
            issues.append(ValidationIssue(
                severity="error", code="TARGET_NOT_FOUND",
                message=f"Deployment '{target}' 존재하지 않음",
                field="target_service",
                fix_hint=f"가용 deployments: {deployments}",
            ))

    def _check_alarm_names(self, scenario: dict, issues: list, ctx: dict):
        alarms = ctx.get("available_alarms", [])
        if not alarms:
            return

        steps = scenario.get("verification", {}).get("steps", [])
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            alarm_name = step.get("alarm_name")
            if alarm_name and alarm_name not in alarms:
                issues.append(ValidationIssue(
                    severity="error", code="ALARM_NOT_FOUND",
                    message=f"steps[{i}]: 알람 '{alarm_name}' 존재하지 않음",
                    field=f"verification.steps[{i}].alarm_name",
                    fix_hint=f"가용 알람: {alarms[:5]}. 또는 alarm_spec 사용.",
                ))

        top_alarm = scenario.get("verification", {}).get("alarm_name")
        if top_alarm and top_alarm not in alarms:
            issues.append(ValidationIssue(
                severity="error", code="ALARM_NOT_FOUND",
                message=f"verification.alarm_name '{top_alarm}' 존재하지 않음",
                field="verification.alarm_name",
                fix_hint=f"가용: {alarms[:5]}. 또는 alarm_spec 사용.",
            ))
