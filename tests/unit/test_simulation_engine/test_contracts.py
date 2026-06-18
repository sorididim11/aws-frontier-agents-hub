"""Unit tests for simulation_engine.contracts."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "services", "dashboard"))

from simulation_engine.contracts import (
    SimulationRequest,
    Artifact,
    ArtifactMetadata,
    Verdict,
    VerdictError,
    VerdictErrorLayer,
    ExecutionEvidence,
    StepEvidence,
    RoundRecord,
    RunResult,
    Strategy,
    EscalationAction,
    SimulationEvent,
    SimulationStatus,
)


def test_simulation_request_defaults():
    req = SimulationRequest(
        failure_mode_id="FM-03",
        target_service="worker",
        namespace="dockercoins",
        space_id="sp-123",
    )
    assert req.max_rounds == 3
    assert req.constraints == []
    assert req.existing_scenario is None
    assert req.architecture_json == {}


def test_simulation_request_with_existing_scenario():
    scenario = {"id": "test-01", "trigger": {"command": "kubectl scale ..."}}
    req = SimulationRequest(
        failure_mode_id="",
        target_service="worker",
        namespace="default",
        space_id="sp-1",
        existing_scenario=scenario,
    )
    assert req.existing_scenario["id"] == "test-01"


def test_artifact_creation():
    art = Artifact(
        scenario_json={"id": "G01-redis", "name": "Redis Blackhole"},
        metadata=ArtifactMetadata(attempt=1, strategy="initial", generation_time_ms=5000),
    )
    assert art.scenario_json["id"] == "G01-redis"
    assert art.metadata.attempt == 1
    assert art.metadata.generation_time_ms == 5000


def test_verdict_passed():
    v = Verdict(passed=True, quality_score=0.95)
    assert v.passed is True
    assert v.failure_reason == ""
    assert v.errors == []


def test_verdict_failed_with_evidence():
    evidence = ExecutionEvidence(
        trigger_command="kubectl scale deploy/redis --replicas=0",
        trigger_success=True,
        steps=[
            StepEvidence(name="Redis 중단", passed=True, actual="0/1 Ready"),
            StepEvidence(name="연결 실패", passed=False, actual="Connection OK", expected="CONNECTION_FAILED"),
        ],
    )
    v = Verdict(
        passed=False,
        failure_reason="Worker가 connection pool 재사용",
        fix_hint="worker pod restart 필요",
        execution_evidence=evidence,
    )
    assert v.passed is False
    assert "connection pool" in v.failure_reason
    assert len(v.execution_evidence.steps) == 2
    assert v.execution_evidence.steps[0].passed is True
    assert v.execution_evidence.steps[1].passed is False


def test_verdict_error():
    err = VerdictError(
        layer=VerdictErrorLayer.L2_SEMANTIC,
        code="RESOURCE_NOT_FOUND",
        message="deploy/redis-xyz not found",
        fix_hint="사용 가능한 deploy: redis, worker, hasher",
    )
    assert err.layer == VerdictErrorLayer.L2_SEMANTIC
    assert err.code == "RESOURCE_NOT_FOUND"


def test_round_record():
    art = Artifact(scenario_json={"id": "test"})
    v = Verdict(passed=False, failure_reason="timeout")
    rec = RoundRecord(round_num=1, artifact=art, verdict=v)
    assert rec.round_num == 1
    assert rec.artifact.scenario_json["id"] == "test"
    assert rec.verdict.passed is False


def test_run_result_success():
    art = Artifact(scenario_json={"id": "final"})
    v = Verdict(passed=True)
    result = RunResult(
        run_id="sim-abc123",
        success=True,
        rounds_used=2,
        final_artifact=art,
        final_verdict=v,
        history=[RoundRecord(round_num=1), RoundRecord(round_num=2, artifact=art, verdict=v)],
    )
    assert result.success is True
    assert result.rounds_used == 2
    assert len(result.history) == 2


def test_strategy_switch():
    s = Strategy(
        action=EscalationAction.SWITCH_APPROACH,
        reason="동일 실패 2회",
        new_constraints=["scale_to_zero"],
        suggested_approach="pod_delete 방식을 시도하세요",
    )
    assert s.action == EscalationAction.SWITCH_APPROACH
    assert "scale_to_zero" in s.new_constraints


def test_simulation_event():
    evt = SimulationEvent(event_type="round_start", data={"round": 1, "max_rounds": 3})
    assert evt.event_type == "round_start"
    assert evt.data["round"] == 1


def test_simulation_status_values():
    assert SimulationStatus.GENERATING == "generating"
    assert SimulationStatus.PASSED == "passed"
    assert SimulationStatus.FAILED == "failed"
