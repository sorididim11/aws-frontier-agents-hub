"""Scenario evaluation routes: /api/evaluate/<run_id> POST and GET."""
import json

from flask import jsonify, request

import boto3

from app_config import (
    AWS_REGION, RUNS_TABLE,
    _req_space_id, _agent_space_id, _boto_session, _profile_for_space,
)
from routes_scenario import scenario_bp
from routes_scenario.crud import _load_scenario_by_id


@scenario_bp.route("/api/evaluate/<run_id>", methods=["POST"])
def api_evaluate(run_id):
    from evaluator import evaluate_investigation
    data = request.get_json() or {}
    task_id = data.get("task_id")
    scenario_id = data.get("scenario_id")

    if not task_id:
        try:
            tbl = _boto_session().resource("dynamodb").Table(RUNS_TABLE)
            resp = tbl.get_item(Key={"run_id": run_id, "record_type": "run"})
            item = resp.get("Item", {})
            task_id = item.get("investigation_task_id")
            scenario_id = scenario_id or item.get("scenario_id")
        except Exception:
            pass

    if not task_id:
        return jsonify({"error": "task_id not found"}), 400

    ui_space_id = data.get("space_id", "").strip() or _req_space_id()
    agent_sid = data.get("space_id", "").strip() or _agent_space_id()
    scenario = _load_scenario_by_id(scenario_id, space_id=ui_space_id) if scenario_id else None
    if not scenario or not scenario.get("evaluation_rubric"):
        return jsonify({"error": f"시나리오 {scenario_id}에 evaluation_rubric 없음 (space: {ui_space_id})"}), 400

    try:
        profile = _profile_for_space(agent_sid)
        session = boto3.Session(profile_name=profile, region_name=AWS_REGION) if profile else _boto_session()
        client = session.client("devops-agent", region_name=AWS_REGION)
        exec_resp = client.list_executions(agentSpaceId=agent_sid, taskId=task_id, limit=10)
        messages = []
        for exe in exec_resp.get("executions", []):
            jr = client.list_journal_records(
                agentSpaceId=agent_sid, executionId=exe["executionId"],
                limit=100, order="ASC",
            )
            for r in jr.get("records", []):
                content = r.get("content", {})
                raw_text = content.get("text", "") if isinstance(content, dict) else str(content)
                messages.append({
                    "text": raw_text,
                    "time": str(r.get("createdAt", ""))[:19],
                    "record_type": r.get("recordType", ""),
                })
    except Exception as e:
        return jsonify({"error": f"journal 조회 실패: {e}"}), 500

    if not messages:
        return jsonify({"error": "조사 메시지 없음"}), 400

    result = evaluate_investigation(messages, scenario)

    try:
        from decimal import Decimal
        tbl = _boto_session().resource("dynamodb").Table(RUNS_TABLE)
        eval_item = json.loads(json.dumps(result), parse_float=Decimal)
        tbl.put_item(Item={
            "run_id": run_id, "record_type": "evaluation",
            "scenario_id": scenario_id, "task_id": task_id,
            **eval_item,
        })
    except Exception as e:
        result["save_error"] = str(e)

    return jsonify(result)


@scenario_bp.route("/api/evaluate/<run_id>")
def api_get_evaluation(run_id):
    try:
        tbl = _boto_session().resource("dynamodb").Table(RUNS_TABLE)
        resp = tbl.get_item(Key={"run_id": run_id, "record_type": "evaluation"})
        item = resp.get("Item")
        if not item:
            return jsonify({"found": False}), 404
        return jsonify(json.loads(json.dumps(item, default=str)))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
