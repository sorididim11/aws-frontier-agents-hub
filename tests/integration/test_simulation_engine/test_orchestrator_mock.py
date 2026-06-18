"""Integration test: Orchestrator with mocked Generator/Verifier/AppExecutor.

Strands/Bedrock 불필요 — Generator/Verifier/AppExecutor를 mock으로 대체하여
Orchestrator 루프 동작만 검증.

v2 아키텍처: Generate → AppExecutor.trigger() → Verifier.observe() → AppExecutor.restore()
"""

import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "services", "dashboard"))

# Mock strands before importing orchestrator
sys.modules["strands"] = MagicMock()
sys.modules["strands.tools"] = MagicMock()
sys.modules["strands.agent"] = MagicMock()
sys.modules["strands.agent.agent"] = MagicMock()
sys.modules["strands.models"] = MagicMock()
sys.modules["strands.models.bedrock"] = MagicMock()
sys.modules["boto3"] = MagicMock()
sys.modules["botocore"] = MagicMock()
sys.modules["botocore.config"] = MagicMock()

from simulation_engine.contracts import (
    SimulationRequest,
    SimulationStatus,
    Artifact,
    ArtifactMetadata,
    Verdict,
    ExecutionEvidence,
    StepEvidence,
    SimulationEvent,
)
from simulation_engine.app_executor import TriggerResult, RestoreResult


def _make_request():
    return SimulationRequest(
        failure_mode_id="FM-03",
        target_service="worker",
        namespace="dockercoins",
        space_id="sp-test",
    )


def _pass_verdict():
    return Verdict(
        passed=True,
        execution_evidence=ExecutionEvidence(
            trigger_command="kubectl scale deploy/redis --replicas=0",
            trigger_success=True,
            steps=[StepEvidence(name="Redis 중단", passed=True, actual="0/1")],
        ),
    )


def _fail_verdict(reason="pod still Running"):
    return Verdict(
        passed=False,
        failure_reason=reason,
        fix_hint="worker pod restart 필요",
    )


def _artifact(scenario_id="G01-redis"):
    return Artifact(
        scenario_json={
            "id": scenario_id,
            "name": "Redis Blackhole",
            "trigger": {"type": "kubectl", "command": "kubectl scale deploy/redis --replicas=0 -n dockercoins"},
            "restore": {"command": "kubectl scale deploy/redis --replicas=1 -n dockercoins"},
        },
        metadata=ArtifactMetadata(attempt=1, strategy="initial"),
    )


def _trigger_result(success=True):
    return TriggerResult(
        success=success,
        command="kubectl scale deploy/redis --replicas=0 -n dockercoins",
        output="deployment.apps/redis scaled",
        elapsed_seconds=1.2,
    )


@patch("simulation_engine.orchestrator.ExecutionContext")
@patch("simulation_engine.orchestrator.CleanupRegistry")
def test_orchestrator_pass_round1(mock_cleanup, mock_exec_ctx):
    """Round 1에서 바로 PASS."""
    mock_exec_ctx.for_scenario.return_value = MagicMock(
        kubectl_context="test-ctx", profile="test-prof", region="us-east-1"
    )

    from simulation_engine.orchestrator import SimulationOrchestrator

    events = []
    request = _make_request()
    orch = SimulationOrchestrator(request=request, on_event=lambda evt: events.append(evt))

    with patch("simulation_engine.orchestrator.GeneratorAgent") as MockGen, \
         patch("simulation_engine.orchestrator.VerifierAgent") as MockVer, \
         patch("simulation_engine.orchestrator.AppExecutor") as MockExec, \
         patch("simulation_engine.orchestrator.SafetyPolicy") as MockPolicy:

        gen_instance = MockGen.return_value
        ver_instance = MockVer.return_value
        exec_instance = MockExec.return_value

        gen_instance.create.return_value = _artifact()
        exec_instance.execute_pre_cleanup.return_value = True
        exec_instance.execute_trigger.return_value = _trigger_result()
        ver_instance.observe.return_value = _pass_verdict()
        exec_instance.execute_restore.return_value = RestoreResult(success=True)

        MockPolicy.for_scenario.return_value = MagicMock()

        result = orch.run()

    assert result.success is True
    assert result.rounds_used == 1

    event_types = [e.event_type for e in events]
    assert "run_started" in event_types
    assert "artifact" in event_types
    assert "trigger_result" in event_types
    assert "verdict" in event_types
    assert "complete" in event_types


@patch("simulation_engine.orchestrator.ExecutionContext")
@patch("simulation_engine.orchestrator.CleanupRegistry")
def test_orchestrator_fail_then_pass(mock_cleanup, mock_exec_ctx):
    """Round 1 FAIL → Round 2 PASS (자동 개선)."""
    mock_exec_ctx.for_scenario.return_value = MagicMock(
        kubectl_context="test-ctx", profile="", region="us-east-1"
    )

    from simulation_engine.orchestrator import SimulationOrchestrator

    events = []
    request = _make_request()
    orch = SimulationOrchestrator(request=request, on_event=lambda evt: events.append(evt))

    with patch("simulation_engine.orchestrator.GeneratorAgent") as MockGen, \
         patch("simulation_engine.orchestrator.VerifierAgent") as MockVer, \
         patch("simulation_engine.orchestrator.AppExecutor") as MockExec, \
         patch("simulation_engine.orchestrator.SafetyPolicy") as MockPolicy:

        gen_instance = MockGen.return_value
        ver_instance = MockVer.return_value
        exec_instance = MockExec.return_value

        gen_instance.create.return_value = _artifact("G01-redis-v1")
        gen_instance.improve.return_value = _artifact("G01-redis-v2")

        exec_instance.execute_pre_cleanup.return_value = True
        exec_instance.execute_trigger.return_value = _trigger_result()
        exec_instance.execute_restore.return_value = RestoreResult(success=True)

        ver_instance.observe.side_effect = [_fail_verdict(), _pass_verdict()]
        MockPolicy.for_scenario.return_value = MagicMock()

        result = orch.run()

    assert result.success is True
    assert result.rounds_used == 2

    event_types = [e.event_type for e in events]
    assert "round_start" in event_types
    assert "verdict" in event_types
    assert "complete" in event_types


@patch("simulation_engine.orchestrator.ExecutionContext")
@patch("simulation_engine.orchestrator.CleanupRegistry")
def test_orchestrator_max_rounds_exhausted(mock_cleanup, mock_exec_ctx):
    """3라운드 모두 실패 → max_rounds 소진."""
    mock_exec_ctx.for_scenario.return_value = MagicMock(
        kubectl_context="", profile="", region="us-east-1"
    )

    from simulation_engine.orchestrator import SimulationOrchestrator

    request = _make_request()
    orch = SimulationOrchestrator(request=request)

    with patch("simulation_engine.orchestrator.GeneratorAgent") as MockGen, \
         patch("simulation_engine.orchestrator.VerifierAgent") as MockVer, \
         patch("simulation_engine.orchestrator.AppExecutor") as MockExec, \
         patch("simulation_engine.orchestrator.SafetyPolicy") as MockPolicy:

        gen_instance = MockGen.return_value
        ver_instance = MockVer.return_value
        exec_instance = MockExec.return_value

        gen_instance.create.return_value = _artifact()
        gen_instance.improve.return_value = _artifact()

        exec_instance.execute_pre_cleanup.return_value = True
        exec_instance.execute_trigger.return_value = _trigger_result()
        exec_instance.execute_restore.return_value = RestoreResult(success=True)

        ver_instance.observe.side_effect = [
            _fail_verdict("reason A"),
            _fail_verdict("reason B"),
            _fail_verdict("reason C"),
        ]
        MockPolicy.for_scenario.return_value = MagicMock()

        result = orch.run()

    assert result.success is False
    assert result.rounds_used == 3
    assert "max_rounds" in result.reason


@patch("simulation_engine.orchestrator.ExecutionContext")
@patch("simulation_engine.orchestrator.CleanupRegistry")
def test_orchestrator_cancel(mock_cleanup, mock_exec_ctx):
    """외부 cancel → 즉시 중단."""
    mock_exec_ctx.for_scenario.return_value = MagicMock(
        kubectl_context="", profile="", region="us-east-1"
    )

    from simulation_engine.orchestrator import SimulationOrchestrator

    request = _make_request()
    orch = SimulationOrchestrator(request=request)
    orch.cancel()

    with patch("simulation_engine.orchestrator.GeneratorAgent") as MockGen, \
         patch("simulation_engine.orchestrator.VerifierAgent") as MockVer, \
         patch("simulation_engine.orchestrator.AppExecutor") as MockExec, \
         patch("simulation_engine.orchestrator.SafetyPolicy") as MockPolicy:

        MockPolicy.for_scenario.return_value = MagicMock()
        result = orch.run()

    assert result.success is False
    assert "cancelled" in result.reason


@patch("simulation_engine.orchestrator.ExecutionContext")
@patch("simulation_engine.orchestrator.CleanupRegistry")
def test_orchestrator_existing_scenario_skips_generator(mock_cleanup, mock_exec_ctx):
    """existing_scenario 전달 시 Generator.create() 스킵."""
    mock_exec_ctx.for_scenario.return_value = MagicMock(
        kubectl_context="", profile="", region="us-east-1"
    )

    from simulation_engine.orchestrator import SimulationOrchestrator

    existing = {
        "id": "existing-01",
        "name": "Pre-made",
        "trigger": {"type": "kubectl", "command": "kubectl scale deploy/redis --replicas=0 -n dockercoins"},
        "restore": {"command": "kubectl scale deploy/redis --replicas=1 -n dockercoins"},
    }
    request = SimulationRequest(
        failure_mode_id="FM-03",
        target_service="worker",
        namespace="dockercoins",
        space_id="sp-test",
        existing_scenario=existing,
    )
    orch = SimulationOrchestrator(request=request)

    with patch("simulation_engine.orchestrator.GeneratorAgent") as MockGen, \
         patch("simulation_engine.orchestrator.VerifierAgent") as MockVer, \
         patch("simulation_engine.orchestrator.AppExecutor") as MockExec, \
         patch("simulation_engine.orchestrator.SafetyPolicy") as MockPolicy:

        gen_instance = MockGen.return_value
        ver_instance = MockVer.return_value
        exec_instance = MockExec.return_value

        exec_instance.execute_pre_cleanup.return_value = True
        exec_instance.execute_trigger.return_value = _trigger_result()
        ver_instance.observe.return_value = _pass_verdict()
        exec_instance.execute_restore.return_value = RestoreResult(success=True)
        MockPolicy.for_scenario.return_value = MagicMock()

        result = orch.run()

    gen_instance.create.assert_not_called()
    assert result.success is True
    assert result.rounds_used == 1


@patch("simulation_engine.orchestrator.ExecutionContext")
@patch("simulation_engine.orchestrator.CleanupRegistry")
def test_orchestrator_trigger_failure_still_observes(mock_cleanup, mock_exec_ctx):
    """Trigger 실패해도 Verifier가 관찰하고 Restore 실행."""
    mock_exec_ctx.for_scenario.return_value = MagicMock(
        kubectl_context="", profile="", region="us-east-1"
    )

    from simulation_engine.orchestrator import SimulationOrchestrator

    request = _make_request()
    orch = SimulationOrchestrator(request=request)

    with patch("simulation_engine.orchestrator.GeneratorAgent") as MockGen, \
         patch("simulation_engine.orchestrator.VerifierAgent") as MockVer, \
         patch("simulation_engine.orchestrator.AppExecutor") as MockExec, \
         patch("simulation_engine.orchestrator.SafetyPolicy") as MockPolicy:

        gen_instance = MockGen.return_value
        ver_instance = MockVer.return_value
        exec_instance = MockExec.return_value

        gen_instance.create.return_value = _artifact()
        exec_instance.execute_pre_cleanup.return_value = True
        exec_instance.execute_trigger.return_value = TriggerResult(
            success=False, command="kubectl scale...", output="error: not found",
        )
        ver_instance.observe.return_value = _fail_verdict("trigger failed")
        exec_instance.execute_restore.return_value = RestoreResult(success=True)
        MockPolicy.for_scenario.return_value = MagicMock()

        gen_instance.improve.return_value = _artifact("G01-v2")
        result = orch.run()

    # Verifier was still called despite trigger failure
    assert ver_instance.observe.call_count >= 1
    # Restore was still called
    assert exec_instance.execute_restore.call_count >= 1
