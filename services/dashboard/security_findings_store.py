"""Security Findings DynamoDB persistence layer.

Stores findings per job as versioned snapshots + computed analysis results.
Provides fast reads (no Security Agent API dependency) and history comparison.

Schema:
  PK: sec_space_id (e.g. "as-776cca69-333e-4c78-8ef9-d91ef09620bf")
  SK: job_finding_id (e.g. "pj-abc123#f-def456")  — individual findings
      "JOB_META#pj-abc123"                         — job-level analysis

  GSI job-id-index: job_id (HASH) + sec_space_id (RANGE)
  GSI history-index: sec_space_id (HASH) + created_at (RANGE)
"""
import json
import time
import logging
from datetime import datetime, timezone

import boto3

from config import FINDINGS_TABLE

logger = logging.getLogger(__name__)

MAX_ITEM_BYTES = 380_000  # DDB 400KB limit with safety margin

_table = None


def _get_table():
    global _table
    if _table is None:
        table_name = FINDINGS_TABLE()
        if not table_name:
            return None
        from app_config import _boto_session
        session = _boto_session()
        ddb = session.resource("dynamodb", region_name="us-east-1")
        _table = ddb.Table(table_name)
    return _table


def save_findings(sec_space_id: str, job_id: str, findings: list[dict]):
    """Job 완료 시 findings 전체를 DDB에 저장."""
    table = _get_table()
    if not table:
        logger.debug("findings_table not configured, skipping DDB save")
        return

    created_at = datetime.now(timezone.utc).isoformat()

    try:
        _do_save(table, sec_space_id, job_id, findings, created_at)
    except Exception as e:
        logger.debug(f"DDB save failed (table may not exist yet): {e}")


def _do_save(table, sec_space_id, job_id, findings, created_at):
    with table.batch_writer() as batch:
        for f in findings:
            finding_id = f.get("id", "")
            if not finding_id:
                continue
            batch.put_item(Item={
                "sec_space_id": sec_space_id,
                "job_finding_id": f"{job_id}#{finding_id}",
                "job_id": job_id,
                "finding_id": finding_id,
                "created_at": created_at,
                "name": f.get("name", ""),
                "riskType": f.get("riskType", ""),
                "riskLevel": f.get("riskLevel", ""),
                "riskScore": str(f.get("riskScore", "")),
                "confidence": f.get("confidence", ""),
                "description": f.get("description", ""),
                "attackScript": f.get("attackScript", ""),
                "status": f.get("status", ""),
                "remediationStatus": f.get("remediationStatus", ""),
                "prLink": f.get("prLink", ""),
                "endpoint": f.get("endpoint", ""),
            })

    logger.info(f"Saved {len(findings)} findings to DDB: space={sec_space_id} job={job_id}")


def save_job_analysis(sec_space_id: str, job_id: str, analysis: dict):
    """Job별 분석 결과(enriched, chains, attack_graph, fix_priority) DDB 저장.

    analysis keys: enriched, chains, attack_graph, fix_priority, task_counts
    """
    table = _get_table()
    if not table:
        return

    try:
        created_at = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(analysis, ensure_ascii=False, default=str)

        if len(payload.encode("utf-8")) > MAX_ITEM_BYTES:
            trimmed = {k: v for k, v in analysis.items() if k != "attack_graph"}
            payload = json.dumps(trimmed, ensure_ascii=False, default=str)

        table.put_item(Item={
            "sec_space_id": sec_space_id,
            "job_finding_id": f"JOB_META#{job_id}",
            "job_id": job_id,
            "finding_id": "JOB_META",
            "created_at": created_at,
            "analysis_json": payload,
        })
        logger.info(f"Saved job analysis to DDB: space={sec_space_id} job={job_id} size={len(payload)}")
    except Exception as e:
        logger.debug(f"DDB save_job_analysis failed: {e}")


def load_job_analysis(sec_space_id: str, job_id: str) -> dict | None:
    """DDB에서 job별 분석 결과 로드. 없으면 None."""
    table = _get_table()
    if not table:
        return None

    try:
        resp = table.get_item(Key={
            "sec_space_id": sec_space_id,
            "job_finding_id": f"JOB_META#{job_id}",
        })
        item = resp.get("Item")
        if not item:
            return None
        return json.loads(item.get("analysis_json", "{}"))
    except Exception as e:
        logger.debug(f"DDB load_job_analysis failed: {e}")
        return None


def load_findings(sec_space_id: str, job_id: str = None) -> list[dict] | None:
    """DDB에서 findings 로드. job_id 미지정 시 최신 job 자동 선택.

    Returns None if not found or table not ready (caller should fallback to API).
    """
    table = _get_table()
    if not table:
        return None

    try:
        if job_id:
            return _query_by_job(table, sec_space_id, job_id)

        latest_job_id = _get_latest_job_id(table, sec_space_id)
        if not latest_job_id:
            return None
        return _query_by_job(table, sec_space_id, latest_job_id)
    except Exception as e:
        logger.debug(f"DDB load failed (table may not exist yet): {e}")
        return None


def get_job_history(sec_space_id: str, limit: int = 10) -> list[dict]:
    """해당 space의 조사 이력 (job_id + created_at + finding count)."""
    table = _get_table()
    if not table:
        return []

    from boto3.dynamodb.conditions import Key
    resp = table.query(
        IndexName="history-index",
        KeyConditionExpression=Key("sec_space_id").eq(sec_space_id),
        ScanIndexForward=False,
        Limit=limit * 30,
    )

    jobs = {}
    for item in resp.get("Items", []):
        if item.get("finding_id") == "JOB_META":
            continue
        jid = item.get("job_finding_id", "").split("#")[0]
        if jid and jid not in jobs:
            jobs[jid] = {
                "job_id": jid,
                "created_at": item.get("created_at", ""),
                "finding_count": 0,
            }
        if jid:
            jobs[jid]["finding_count"] += 1

    return sorted(jobs.values(), key=lambda x: x["created_at"], reverse=True)[:limit]


def compare_jobs(sec_space_id: str, job_id_old: str, job_id_new: str) -> dict:
    """두 job 간 findings 비교 — 신규/수정/해결/재발."""
    old_findings = _query_by_job(_get_table(), sec_space_id, job_id_old) or []
    new_findings = _query_by_job(_get_table(), sec_space_id, job_id_new) or []

    old_map = {f["finding_id"]: f for f in old_findings}
    new_map = {f["finding_id"]: f for f in new_findings}

    new_ids = set(new_map.keys())
    old_ids = set(old_map.keys())

    return {
        "new": [new_map[fid] for fid in new_ids - old_ids],
        "resolved": [old_map[fid] for fid in old_ids - new_ids],
        "persistent": [new_map[fid] for fid in new_ids & old_ids],
        "total_old": len(old_findings),
        "total_new": len(new_findings),
    }


def _query_by_job(table, sec_space_id: str, job_id: str) -> list[dict] | None:
    """특정 job의 모든 findings 조회."""
    if not table:
        return None

    from boto3.dynamodb.conditions import Key
    resp = table.query(
        KeyConditionExpression=Key("sec_space_id").eq(sec_space_id)
        & Key("job_finding_id").begins_with(f"{job_id}#"),
    )

    items = resp.get("Items", [])
    while resp.get("LastEvaluatedKey"):
        resp = table.query(
            KeyConditionExpression=Key("sec_space_id").eq(sec_space_id)
            & Key("job_finding_id").begins_with(f"{job_id}#"),
            ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
        items.extend(resp.get("Items", []))

    if not items:
        return None

    findings = []
    for item in items:
        findings.append({
            "id": item.get("finding_id", ""),
            "name": item.get("name", ""),
            "riskType": item.get("riskType", ""),
            "riskLevel": item.get("riskLevel", ""),
            "riskScore": item.get("riskScore", ""),
            "confidence": item.get("confidence", ""),
            "description": item.get("description", ""),
            "attackScript": item.get("attackScript", ""),
            "status": item.get("status", ""),
            "remediationStatus": item.get("remediationStatus", ""),
            "prLink": item.get("prLink", ""),
            "endpoint": item.get("endpoint", ""),
        })
    return findings


def _get_latest_job_id(table, sec_space_id: str) -> str | None:
    """history-index에서 최신 job_id 조회."""
    from boto3.dynamodb.conditions import Key
    resp = table.query(
        IndexName="history-index",
        KeyConditionExpression=Key("sec_space_id").eq(sec_space_id),
        ScanIndexForward=False,
        Limit=5,
    )
    for item in resp.get("Items", []):
        fid = item.get("finding_id", "")
        if fid == "JOB_META":
            continue
        jid = item.get("job_finding_id", "").split("#")[0]
        if jid:
            return jid
    return None
