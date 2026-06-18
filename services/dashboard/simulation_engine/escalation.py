"""Simulation Engine v2 — Strategy escalation.

반복 실패 시 접근법 전환 또는 포기를 판단.
"""

from __future__ import annotations

from simulation_engine.contracts import (
    RoundRecord, Strategy, EscalationAction,
)


def should_escalate(history: list[RoundRecord]) -> Strategy | None:
    """실패 이력을 분석하여 에스컬레이션 필요 여부 판단.

    Rules:
    - 같은 failure_reason 2회 연속 → switch_approach
    - 3라운드 모두 같은 failure_reason → give_up
    - infra_missing (클러스터 접근 불가) → 즉시 give_up
    """
    if len(history) < 2:
        return None

    verdicts = [r.verdict for r in history if r.verdict and not r.verdict.passed]
    if len(verdicts) < 2:
        return None

    reasons = [v.failure_reason for v in verdicts]

    # 인프라 부재 → 즉시 포기
    last = verdicts[-1]
    if last.errors and any("INFRA" in e.code or "CLUSTER" in e.code for e in last.errors):
        return Strategy(
            action=EscalationAction.GIVE_UP,
            reason="인프라 접근 불가 — 클러스터 또는 리소스 미존재",
        )

    # 3회 연속 같은 이유 → 포기
    if len(reasons) >= 3 and reasons[-1] == reasons[-2] == reasons[-3]:
        return Strategy(
            action=EscalationAction.GIVE_UP,
            reason=f"동일 실패 3회 반복: {reasons[-1]}",
        )

    # 2회 연속 같은 이유 → 접근법 전환
    if reasons[-1] == reasons[-2] and reasons[-1]:
        failed_approach = _extract_approach(verdicts[-1])
        return Strategy(
            action=EscalationAction.SWITCH_APPROACH,
            reason=f"동일 실패 2회: {reasons[-1]}",
            new_constraints=[failed_approach] if failed_approach else [],
            suggested_approach=_suggest_alternative(failed_approach),
        )

    return None


def _extract_approach(verdict) -> str:
    """Verdict에서 실패한 접근법을 추출."""
    evidence = verdict.execution_evidence
    if not evidence:
        return ""
    cmd = evidence.trigger_command or ""
    if "scale" in cmd:
        return "scale_to_zero"
    if "delete pod" in cmd:
        return "pod_delete"
    if "set resources" in cmd:
        return "resource_exhaust"
    if "NetworkPolicy" in cmd or "apply -f" in cmd:
        return "network_policy"
    return ""


def _suggest_alternative(failed: str) -> str:
    """실패한 접근법에 대한 대안 제안."""
    alternatives = {
        "scale_to_zero": "resource_exhaust 또는 pod_delete 방식을 시도하세요",
        "pod_delete": "scale_to_zero 또는 resource_exhaust 방식을 시도하세요",
        "resource_exhaust": "scale_to_zero 또는 network_policy 방식을 시도하세요",
        "network_policy": "scale_to_zero 또는 pod_delete 방식을 시도하세요",
    }
    return alternatives.get(failed, "다른 접근법을 시도하세요")
