"""Unit tests for simulation_engine.escalation."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "services", "dashboard"))

from simulation_engine.contracts import (
    RoundRecord,
    Artifact,
    Verdict,
    VerdictError,
    VerdictErrorLayer,
    EscalationAction,
)
from simulation_engine.escalation import should_escalate


def _make_record(round_num, failure_reason="", error_code=""):
    errors = []
    if error_code:
        errors = [VerdictError(layer=VerdictErrorLayer.L4_EXECUTION, code=error_code, message="")]
    return RoundRecord(
        round_num=round_num,
        artifact=Artifact(scenario_json={"id": f"test-{round_num}"}),
        verdict=Verdict(passed=False, failure_reason=failure_reason, errors=errors),
    )


def test_no_escalation_first_round():
    history = [_make_record(1, "timeout")]
    assert should_escalate(history) is None


def test_no_escalation_different_reasons():
    history = [
        _make_record(1, "timeout"),
        _make_record(2, "connection refused"),
    ]
    assert should_escalate(history) is None


def test_switch_approach_on_same_failure_twice():
    history = [
        _make_record(1, "pod still Running"),
        _make_record(2, "pod still Running"),
    ]
    strategy = should_escalate(history)
    assert strategy is not None
    assert strategy.action == EscalationAction.SWITCH_APPROACH
    assert "동일 실패 2회" in strategy.reason


def test_give_up_on_same_failure_three_times():
    history = [
        _make_record(1, "cannot scale"),
        _make_record(2, "cannot scale"),
        _make_record(3, "cannot scale"),
    ]
    strategy = should_escalate(history)
    assert strategy is not None
    assert strategy.action == EscalationAction.GIVE_UP
    assert "3회 반복" in strategy.reason


def test_infra_error_immediate_give_up():
    history = [
        _make_record(1, "timeout"),
        _make_record(2, "cluster unreachable", error_code="INFRA_MISSING"),
    ]
    strategy = should_escalate(history)
    assert strategy is not None
    assert strategy.action == EscalationAction.GIVE_UP
    assert "인프라" in strategy.reason


def test_cluster_error_immediate_give_up():
    history = [
        _make_record(1, "x"),
        _make_record(2, "y", error_code="CLUSTER_NOT_FOUND"),
    ]
    strategy = should_escalate(history)
    assert strategy is not None
    assert strategy.action == EscalationAction.GIVE_UP


def test_passed_verdicts_ignored():
    history = [
        RoundRecord(round_num=1, verdict=Verdict(passed=True)),
        _make_record(2, "fail"),
    ]
    assert should_escalate(history) is None


def test_empty_failure_reason_no_switch():
    history = [
        _make_record(1, ""),
        _make_record(2, ""),
    ]
    assert should_escalate(history) is None
