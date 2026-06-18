"""Simulation Engine v2 — Generate → Verify → Improve 단일 루프.

Public API:
    run_simulation(request, on_event) → RunResult
    SimulationRequest, RunResult (contracts)
"""

from __future__ import annotations

import logging
import threading

from simulation_engine.contracts import (
    SimulationRequest,
    SimulationStatus,
    RunResult,
    SimulationEvent,
)
from simulation_engine.events import SSERelay, create_relay, get_relay, remove_relay

log = logging.getLogger(__name__)

# In-memory active runs
_runs_lock = threading.Lock()
_active_runs: dict = {}


def run_simulation(request: SimulationRequest) -> str:
    """시뮬레이션을 백그라운드 스레드에서 시작. run_id를 즉시 반환.

    내부적으로 SSERelay를 생성하여 이벤트를 수집.
    클라이언트는 GET /api/simulation/{run_id}/stream 으로 수신.
    """
    from simulation_engine.orchestrator import SimulationOrchestrator
    from simulation_engine.persistence import save_run_result, save_scenario
    from simulation_engine.events import _runs_lock as rl, _active_relays

    relay = SSERelay()
    orchestrator = SimulationOrchestrator(request=request, on_event=relay.push)
    run_id = orchestrator.run_id

    with rl:
        _active_relays[run_id] = relay

    with _runs_lock:
        _active_runs[run_id] = orchestrator

    def _run():
        try:
            result = orchestrator.run()
            if result.success and result.final_artifact:
                save_scenario(result.final_artifact.scenario_json, request.space_id)
            save_run_result(result, request.space_id)
        except Exception as e:
            log.exception(f"[{run_id}] Simulation thread error: {e}")
        finally:
            with _runs_lock:
                _active_runs.pop(run_id, None)
            remove_relay(run_id)

    thread = threading.Thread(target=_run, name=f"sim-{run_id}", daemon=True)
    thread.start()

    return run_id


def get_run_status(run_id: str) -> dict | None:
    """현재 실행 중인 run의 상태 조회."""
    with _runs_lock:
        orch = _active_runs.get(run_id)
    if not orch:
        return None
    return {
        "run_id": run_id,
        "status": orch.status.value,
        "rounds_completed": len(orch._history),
        "max_rounds": orch.max_rounds,
    }


def cancel_run(run_id: str) -> bool:
    """실행 중인 run 취소."""
    with _runs_lock:
        orch = _active_runs.get(run_id)
    if not orch:
        return False
    orch.cancel()
    return True


def list_active_runs() -> list[dict]:
    """활성 실행 목록."""
    with _runs_lock:
        return [
            {"run_id": rid, "status": orch.status.value}
            for rid, orch in _active_runs.items()
        ]
