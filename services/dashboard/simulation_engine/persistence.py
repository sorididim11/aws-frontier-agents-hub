"""Simulation Engine v2 — DDB persistence.

실행 결과를 DynamoDB에 저장. 기존 테이블 스키마와 호환.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from simulation_engine.contracts import RunResult, Artifact, Verdict

log = logging.getLogger(__name__)


def save_run_result(run_result: RunResult, space_id: str = ""):
    """RunResult를 DDB에 저장."""
    try:
        from app_config import _CFG, _cfg_get
        import boto3

        table_name = _cfg_get(_CFG, "dynamodb.runs_table", "devops-agent-runs")
        region = _cfg_get(_CFG, "aws.region", "us-east-1")
        ddb = boto3.resource("dynamodb", region_name=region)
        table = ddb.Table(table_name)

        item = {
            "run_id": run_result.run_id,
            "record_type": "run",
            "executor_type": "simulation_v2",
            "space_id": space_id,
            "success": run_result.success,
            "rounds_used": run_result.rounds_used,
            "reason": run_result.reason or "",
            "started_at": int(time.time()),
            "completed_at": int(time.time()),
        }

        if run_result.final_artifact:
            item["scenario_json"] = json.dumps(
                run_result.final_artifact.scenario_json, ensure_ascii=False)
            item["scenario_id"] = run_result.final_artifact.scenario_json.get("id", "")

        if run_result.final_verdict:
            item["verdict"] = json.dumps({
                "passed": run_result.final_verdict.passed,
                "failure_reason": run_result.final_verdict.failure_reason,
                "fix_hint": run_result.final_verdict.fix_hint,
            }, ensure_ascii=False)

        # 라운드별 요약
        rounds_summary = []
        for record in run_result.history:
            r = {"round": record.round_num}
            if record.artifact:
                r["scenario_id"] = record.artifact.scenario_json.get("id", "")
            if record.verdict:
                r["passed"] = record.verdict.passed
                r["failure_reason"] = record.verdict.failure_reason
            if record.strategy:
                r["strategy"] = record.strategy.action
            rounds_summary.append(r)
        item["rounds"] = json.dumps(rounds_summary, ensure_ascii=False)

        table.put_item(Item=item)
        log.info(f"[{run_result.run_id}] Saved to DDB: success={run_result.success}")

    except Exception as e:
        log.warning(f"[{run_result.run_id}] DDB save failed (fallback to local): {e}")
        _save_local(run_result, space_id)


def save_scenario(scenario_json: dict, space_id: str):
    """성공한 시나리오를 DDB에 영구 저장."""
    try:
        from app_config import _CFG, _cfg_get
        import boto3

        table_name = _cfg_get(_CFG, "dynamodb.runs_table", "devops-agent-runs")
        region = _cfg_get(_CFG, "aws.region", "us-east-1")
        ddb = boto3.resource("dynamodb", region_name=region)
        table = ddb.Table(table_name)

        scenario_id = scenario_json.get("id", "unknown")
        table.put_item(Item={
            "run_id": f"scen-{scenario_id}",
            "record_type": "scenario",
            "space_id": space_id,
            "scenario_json": json.dumps(scenario_json, ensure_ascii=False),
            "saved_at": int(time.time()),
            "source": "simulation_v2",
        })
        log.info(f"Scenario saved: {scenario_id}")

    except Exception as e:
        log.warning(f"Scenario DDB save failed: {e}")


def _save_local(run_result: RunResult, space_id: str):
    """DDB 실패 시 로컬 JSON 파일로 fallback."""
    import os
    fallback_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "simulation_runs")
    os.makedirs(fallback_dir, exist_ok=True)
    path = os.path.join(fallback_dir, f"{run_result.run_id}.json")
    with open(path, "w") as f:
        json.dump({
            "run_id": run_result.run_id,
            "success": run_result.success,
            "rounds_used": run_result.rounds_used,
            "reason": run_result.reason,
            "space_id": space_id,
        }, f, ensure_ascii=False, indent=2)
    log.info(f"[{run_result.run_id}] Saved locally: {path}")
