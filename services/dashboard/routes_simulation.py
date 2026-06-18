"""Flask routes for Simulation Engine v2.

Endpoints:
    POST /api/simulation/run           — 시뮬레이션 시작
    GET  /api/simulation/<run_id>/stream — SSE 실시간 스트림
    GET  /api/simulation/<run_id>/status — 상태 조회
    POST /api/simulation/<run_id>/cancel — 실행 취소
    GET  /api/simulation/active         — 활성 실행 목록
"""

from flask import Blueprint, request, jsonify, Response

simulation_bp = Blueprint("simulation", __name__)


@simulation_bp.route("/api/simulation/run", methods=["POST"])
def api_simulation_run():
    """시뮬레이션 시작 — Generate → Verify → Improve 단일 루프."""
    from simulation_engine import run_simulation
    from simulation_engine.contracts import SimulationRequest

    body = request.json or {}

    failure_mode_id = body.get("failure_mode_id", "")
    target_service = body.get("target_service", "")
    namespace = body.get("namespace", "default")
    space_id = body.get("space_id", "")
    max_rounds = body.get("max_rounds", 3)
    existing_scenario = body.get("existing_scenario")
    architecture_json = body.get("architecture_json", {})
    recommendation = body.get("recommendation", {})

    if not failure_mode_id and not existing_scenario:
        return jsonify({"error": "failure_mode_id 또는 existing_scenario 필요"}), 400
    if not target_service and not existing_scenario:
        return jsonify({"error": "target_service 필요"}), 400

    sim_request = SimulationRequest(
        failure_mode_id=failure_mode_id,
        target_service=target_service,
        namespace=namespace,
        space_id=space_id,
        architecture_json=architecture_json,
        recommendation=recommendation,
        max_rounds=max_rounds,
        existing_scenario=existing_scenario,
    )

    run_id = run_simulation(sim_request)

    return jsonify({"run_id": run_id, "status": "started"})


@simulation_bp.route("/api/simulation/<run_id>/stream")
def api_simulation_stream(run_id: str):
    """SSE 실시간 이벤트 스트림."""
    from simulation_engine.events import get_relay

    relay = get_relay(run_id)
    if not relay:
        return jsonify({"error": "run not found"}), 404

    return Response(
        relay.stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@simulation_bp.route("/api/simulation/<run_id>/status")
def api_simulation_status(run_id: str):
    """상태 조회 (폴링 fallback)."""
    from simulation_engine import get_run_status

    status = get_run_status(run_id)
    if not status:
        return jsonify({"error": "run not found or completed"}), 404
    return jsonify(status)


@simulation_bp.route("/api/simulation/<run_id>/cancel", methods=["POST"])
def api_simulation_cancel(run_id: str):
    """실행 취소."""
    from simulation_engine import cancel_run

    if cancel_run(run_id):
        return jsonify({"ok": True, "message": "cancelled"})
    return jsonify({"error": "run not found"}), 404


@simulation_bp.route("/api/simulation/active")
def api_simulation_active():
    """활성 실행 목록."""
    from simulation_engine import list_active_runs

    return jsonify({"runs": list_active_runs()})
