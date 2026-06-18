#!/usr/bin/env python3
"""
Evidence Dashboard — 처리 로직 + Blueprint

조사 journal records에서 evidence(관측, 결론, 신호)를 추출하고 구조화.
시각화는 evidence.html(프론트엔드)에서 담당.
"""
import json
import re


# ══════════════════════════════════════════════════════════
# 핵심 함수 3개
# ══════════════════════════════════════════════════════════


def parse_raw_records(records):
    """
    raw journal records → 구조화된 분류 결과.

    Args:
        records: list of dicts, 각각 {recordType, content, createdAt, ...}
                 content는 JSON 문자열 또는 dict.

    Returns:
        {
            "observations": {obs_id: {id, title, analysis, signals[], finding_refs[]}},
            "findings": [{id, title, description, supporting_observations[], ...}],
            "symptom": {title, description, ...} or None,
            "summary": {...} or None,
        }
    """
    observations = {}
    findings = []
    symptom = None
    summary = None

    for r in records:
        rt = r.get("recordType", r.get("record_type", ""))
        raw_content = r.get("content", "")

        # content 파싱: string이면 JSON parse, dict이면 그대로
        if isinstance(raw_content, dict):
            data = raw_content
        elif isinstance(raw_content, str):
            try:
                data = json.loads(raw_content)
            except (json.JSONDecodeError, TypeError):
                continue  # 파싱 불가 → 건너뜀
        else:
            continue

        if not isinstance(data, dict):
            continue

        if rt == "observation":
            obs_id = data.get("id")
            if not obs_id:
                continue
            observations[obs_id] = {
                "id": obs_id,
                "title": data.get("title", ""),
                "analysis": data.get("analysis", ""),
                "signals": data.get("signals", []),
                "finding_refs": [],
            }

        elif rt == "finding":
            findings.append({
                "id": data.get("id", ""),
                "title": data.get("title", ""),
                "description": data.get("description", ""),
                "finding_type": data.get("finding_type", ""),
                "supporting_observations": data.get("supporting_observations", []),
                "related_resources": data.get("related_resources", []),
            })

        elif rt == "symptom":
            symptom = {
                "title": data.get("title", ""),
                "description": data.get("description", ""),
                "start_time": data.get("start_time"),
                "end_time": data.get("end_time"),
                "related_resources": data.get("related_resources", []),
            }

        elif rt in ("investigation_summary",):
            summary = data

        # message, investigation_summary_md 등은 무시

    return {
        "observations": observations,
        "findings": findings,
        "symptom": symptom,
        "summary": summary,
    }


def build_evidence(parsed, region="us-east-1"):
    """
    파싱된 데이터 → evidence 구조 (Finding→Observation→Signal 연결 + dedup + 통계).

    Args:
        parsed: parse_raw_records()의 반환값
        region: AWS region (deep link 생성용)

    Returns:
        {
            "findings": [...],
            "observations": {id: {..., finding_refs: [...]}},
            "signals": [...],   # 전체 dedup된 signal 목록 (메타데이터 포함)
            "stats": {type: count},
            "symptom": ...,
            "summary": ...,
        }
    """
    observations = parsed["observations"]
    findings = parsed["findings"]

    # Finding → Observation 역참조: observation.finding_refs에 추가
    for f in findings:
        for obs_id in f.get("supporting_observations", []):
            if obs_id in observations:
                observations[obs_id]["finding_refs"].append({
                    "id": f["id"],
                    "title": f["title"],
                })

    # Signal 수집: dedup + 메타데이터 추가
    all_signals = []
    seen_ids = set()
    stats = {}

    for obs in observations.values():
        for sig in obs.get("signals", []):
            sig_id = sig.get("id", "")
            if sig_id in seen_ids:
                continue
            seen_ids.add(sig_id)

            sig_type = sig.get("type", "unknown")
            sig["_observation_id"] = obs["id"]
            sig["_observation_title"] = obs.get("title", "")
            sig["_finding_refs"] = obs.get("finding_refs", [])
            sig["_deep_link"] = make_deep_link(sig, region)

            all_signals.append(sig)
            stats[sig_type] = stats.get(sig_type, 0) + 1

    return {
        "findings": findings,
        "observations": observations,
        "signals": all_signals,
        "stats": stats,
        "symptom": parsed.get("symptom"),
        "summary": parsed.get("summary"),
    }


def make_deep_link(signal, region):
    """
    Signal 유형에 따른 deep link 생성.

    Returns:
        str (URL 또는 kubectl 명령어) or None
    """
    sig_type = signal.get("type", "")

    if sig_type == "trace":
        records = signal.get("traces", {}).get("records", [])
        if records:
            tid = records[0].get("trace_id", "")
            if tid:
                return (
                    f"https://{region}.console.aws.amazon.com"
                    f"/xray/home?region={region}#/traces/{tid}"
                )
        return None

    elif sig_type == "metric":
        datasets = signal.get("datasets", {}).get("metricDataset", [])
        if datasets:
            return (
                f"https://{region}.console.aws.amazon.com"
                f"/cloudwatch/home?region={region}#metricsV2"
            )
        return None

    elif sig_type == "code_snippet":
        meta = signal.get("code_snippet", {}).get("metadata", {})
        repo = meta.get("repository_id", "")
        diffs = signal.get("code_snippet", {}).get("code_diffs", [])
        if repo and diffs:
            fp = diffs[0].get("file_path", {}).get("new", "")
            line = diffs[0].get("start_line", {}).get("new", 1)
            if fp:
                return f"https://github.com/{repo}/blob/main/{fp}#L{line}"
        return None

    elif sig_type == "log":
        return (
            f"https://{region}.console.aws.amazon.com"
            f"/cloudwatch/home?region={region}#logsV2:log-groups"
        )

    elif sig_type == "change_event":
        resource = signal.get("change_event", {}).get("resource") or ""
        # "deployment/rng (dockercoins namespace)" → kubectl describe deployment rng -n dockercoins
        ns_match = re.search(r"\((\S+)\s+namespace\)", resource)
        ns = ns_match.group(1) if ns_match else "default"

        if "deployment/" in resource:
            name = resource.split("deployment/")[1].split(" ")[0].split("(")[0].strip()
            return f"kubectl describe deployment {name} -n {ns}"
        elif "pod/" in resource:
            name = resource.split("pod/")[1].split(" ")[0].split("(")[0].strip()
            return f"kubectl describe pod {name} -n {ns}"
        elif "replicaset/" in resource:
            name = resource.split("replicaset/")[1].split(" ")[0].split("(")[0].strip()
            return f"kubectl describe replicaset {name} -n {ns}"
        return None

    return None


# ══════════════════════════════════════════════════════════
# CloudWatch 대시보드 자동 생성
# ══════════════════════════════════════════════════════════

DEFAULT_CW_ENVIRONMENT = "eks:devops-agent-test-cluster/dockercoins"


def create_cw_dashboard(evidence, task_id, region="us-east-1"):
    """
    Evidence 데이터에서 CloudWatch 대시보드를 자동 생성.

    findings/symptom의 related_resources에서 서비스명을 추출하고,
    서비스별 Error/Fault/Latency 위젯을 만든다.

    Returns:
        {"dashboard_name": str, "dashboard_url": str, "services": list}
    """
    import boto3
    from datetime import datetime, timedelta, timezone

    # 1. 서비스명 추출
    services = set()
    for f in evidence.get("findings", []):
        for r in f.get("related_resources", []):
            services.add(r)
    symptom = evidence.get("symptom") or {}
    for r in symptom.get("related_resources", []):
        services.add(r)

    # "Hasher Service" → "hasher"
    svc_names = sorted(set(
        r.lower().replace(" service", "").strip()
        for r in services if r
    ))
    if not svc_names:
        raise ValueError("서비스명을 추출할 수 없음")

    # 2. Environment 추출 (symptom description에서)
    env = DEFAULT_CW_ENVIRONMENT
    desc = symptom.get("description", "")
    env_match = re.search(r"Environment=([^\s,)]+)", desc)
    if env_match:
        env = env_match.group(1)

    # 3. 시간 범위: symptom.start_time ±30분
    start_str = symptom.get("start_time", "")
    if start_str:
        incident_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
    else:
        incident_time = datetime.now(timezone.utc)
    time_start = incident_time - timedelta(minutes=30)
    time_end = incident_time + timedelta(minutes=30)

    # 4. 위젯 생성: 서비스별 Error/Fault/Latency
    widgets = []
    for i, svc in enumerate(svc_names):
        widgets.append({
            "type": "metric",
            "x": 0, "y": i * 6, "width": 24, "height": 6,
            "properties": {
                "metrics": [
                    ["ApplicationSignals", "Error",
                     "Service", svc, "Environment", env, {"stat": "Sum"}],
                    [".", "Fault", ".", ".", ".", ".", {"stat": "Sum"}],
                    [".", "Latency", ".", ".", ".", ".", {"stat": "Average"}],
                ],
                "period": 60,
                "region": region,
                "title": f"{svc} — Error / Fault / Latency",
                "yAxis": {"left": {"min": 0}},
                "view": "timeSeries",
                "stacked": False,
            },
        })

    # 5. PutDashboard
    dashboard_name = f"devops-agent-{task_id[:8]}"
    client = boto3.client("cloudwatch", region_name=region)
    client.put_dashboard(
        DashboardName=dashboard_name,
        DashboardBody=json.dumps({"widgets": widgets}),
    )

    # 6. URL
    iso_start = time_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    iso_end = time_end.strftime("%Y-%m-%dT%H:%M:%SZ")
    dashboard_url = (
        f"https://{region}.console.aws.amazon.com/cloudwatch/home"
        f"?region={region}#dashboards/dashboard/{dashboard_name}"
        f"?start={iso_start}&end={iso_end}"
    )

    return {
        "dashboard_name": dashboard_name,
        "dashboard_url": dashboard_url,
        "services": svc_names,
    }


# ══════════════════════════════════════════════════════════
# Direct DevOps Agent API fetch (fallback)
# ══════════════════════════════════════════════════════════

def _fetch_journal_records_direct(task_id, space_id, region="us-east-1"):
    """DevOps Agent API에서 직접 journal records를 가져온다.

    raw API가 사용 불가할 때 fallback으로 사용.
    content를 원본 그대로 보존하여 반환.
    """
    import boto3

    client = boto3.client("devops-agent", region_name=region)

    exec_resp = client.list_executions(
        agentSpaceId=space_id, taskId=task_id, limit=10
    )
    executions = exec_resp.get("executions", [])
    if not executions:
        return []

    all_records = []
    for exe in executions:
        exec_id = exe["executionId"]
        params = {
            "agentSpaceId": space_id,
            "executionId": exec_id,
            "limit": 100,
            "order": "ASC",
        }
        while True:
            resp = client.list_journal_records(**params)
            for r in resp.get("records", []):
                all_records.append({
                    "recordType": r.get("recordType", ""),
                    "content": r.get("content", ""),
                    "createdAt": str(r.get("createdAt", "")),
                })
            if "nextToken" not in resp:
                break
            params["nextToken"] = resp["nextToken"]

    return all_records


# ══════════════════════════════════════════════════════════
# Blueprint 라우트 (Flask 의존 — 별도 import)
# ══════════════════════════════════════════════════════════


def create_blueprint():
    """Flask Blueprint 생성. Flask 미설치 환경에서도 core 함수는 사용 가능."""
    from flask import Blueprint, render_template, request, jsonify

    evidence_bp = Blueprint("evidence", __name__)

    @evidence_bp.route("/evidence")
    def evidence_page():
        """독립 evidence 시각화 페이지."""
        task_id = request.args.get("task_id", "")
        return render_template("evidence.html", task_id=task_id)

    @evidence_bp.route("/api/evidence")
    def api_evidence():
        """task_id → journal records → evidence 구조화 → JSON 반환.

        데이터 소스 우선순위:
        1. /api/investigation-journal-raw (RAW_API_URL 환경변수)
        2. 직접 DevOps Agent API 호출 (fallback)
        """
        import os

        task_id = request.args.get("task_id")
        space_id = request.args.get("space_id")
        if not task_id:
            return jsonify({"ok": False, "error": "task_id is required"}), 400

        region = os.environ.get("AWS_REGION", "us-east-1")
        records = []

        # 방법 1: raw API 호출
        raw_url = os.environ.get("RAW_API_URL", "")
        if raw_url:
            try:
                import requests as req_lib
                resp = req_lib.get(raw_url, params={"task_id": task_id}, timeout=30)
                resp.raise_for_status()
                raw_data = resp.json()
                records = (
                    raw_data.get("records")
                    or raw_data.get("raw_records")
                    or raw_data.get("data", {}).get("records")
                    or []
                )
            except Exception:
                pass  # fallback to direct API

        # 방법 2: 직접 DevOps Agent API 호출 (fallback)
        if not records and space_id:
            try:
                records = _fetch_journal_records_direct(task_id, space_id, region)
            except Exception as e:
                return jsonify({"ok": False, "error": f"데이터 조회 실패: {e}"}), 502

        parsed = parse_raw_records(records)
        evidence = build_evidence(parsed, region)

        return jsonify({"ok": True, "task_id": task_id, "evidence": evidence})

    @evidence_bp.route("/api/evidence/dashboard", methods=["POST"])
    def api_evidence_dashboard():
        """CloudWatch 대시보드를 자동 생성하고 URL을 반환."""
        import os

        task_id = request.json.get("task_id") if request.is_json else request.args.get("task_id")
        space_id = (request.json.get("space_id") if request.is_json else None) or request.args.get("space_id")
        if not task_id:
            return jsonify({"ok": False, "error": "task_id is required"}), 400

        region = os.environ.get("AWS_REGION", "us-east-1")
        records = []

        raw_url = os.environ.get("RAW_API_URL", "")
        if raw_url:
            try:
                import requests as req_lib
                resp = req_lib.get(raw_url, params={"task_id": task_id}, timeout=30)
                resp.raise_for_status()
                raw_data = resp.json()
                records = (
                    raw_data.get("records")
                    or raw_data.get("raw_records")
                    or raw_data.get("data", {}).get("records")
                    or []
                )
            except Exception:
                pass

        if not records and space_id:
            try:
                records = _fetch_journal_records_direct(task_id, space_id, region)
            except Exception as e:
                return jsonify({"ok": False, "error": f"데이터 조회 실패: {e}"}), 502

        parsed = parse_raw_records(records)
        evidence = build_evidence(parsed, region)

        try:
            result = create_cw_dashboard(evidence, task_id, region)
            return jsonify({"ok": True, **result})
        except Exception as e:
            return jsonify({"ok": False, "error": f"대시보드 생성 실패: {e}"}), 500

    return evidence_bp
