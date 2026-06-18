"""
Security Insights Blueprint — Security + DevOps 융합 인사이트 API.

엔드포인트:
  - /security/insights — 인사이트 대시보드 페이지
  - /api/security/insights/enriched-findings — 컨텍스트 재평가 findings
  - /api/security/insights/scenarios — finding → 시나리오 목록
  - /api/security/insights/scenarios/<id>/run — 시나리오 실행
  - /api/security/insights/reverify/<finding_id> — PR 수정 재검증
  - /api/security/insights/results — 실행 결과 이력
  - /api/security/insights/attack-paths — 토폴로지 기반 공격 경로
"""
import time

import json
from flask import Blueprint, render_template, jsonify, request

from security_enrichment import enrichment
from security_scenarios import scenario_engine

security_insights_bp = Blueprint("security_insights_bp", __name__)


# ---------------------------------------------------------------------------
# Cache — space별 캐싱 (Security Agent space_id 기준)
# ---------------------------------------------------------------------------

_cache = {}  # { sec_space_id: { job_id, findings, enriched, task_counts, ts } }


def _get_pentest_status(sec_space_id=None):
    """Pentest 진행 상태 + task timeline 반환 (IN_PROGRESS 대응)."""
    from routes_settings import _sa_client

    if not sec_space_id:
        from routes_settings import _security_agent_config
        cfg = _security_agent_config()
        sec_space_id = cfg.get("agent_space_id", "")

    if not sec_space_id:
        return {"status": "NO_SPACE", "tasks": [], "job_id": None}

    sa = _sa_client(sec_space_id=sec_space_id)
    resp = sa.list_pentests(agentSpaceId=sec_space_id)
    pentests = resp.get("pentestSummaries", [])
    if not pentests:
        return {"status": "NO_PENTEST", "tasks": [], "job_id": None}

    pentest_info = pentests[0]
    jobs_resp = sa.list_pentest_jobs_for_pentest(
        agentSpaceId=sec_space_id,
        pentestId=pentest_info["pentestId"],
    )
    all_jobs = jobs_resp.get("pentestJobSummaries", [])
    if not all_jobs:
        return {"status": "NO_JOBS", "tasks": [], "job_id": None}

    in_progress = [j for j in all_jobs if j.get("status") == "IN_PROGRESS"]
    if not in_progress:
        return {"status": "COMPLETED", "tasks": [], "job_id": None}

    job = in_progress[0]
    job_id = job["pentestJobId"]

    task_summaries = []
    next_token = None
    while True:
        kwargs = {"agentSpaceId": sec_space_id, "pentestJobId": job_id}
        if next_token:
            kwargs["nextToken"] = next_token
        tasks_resp = sa.list_pentest_job_tasks(**kwargs)
        task_summaries.extend(tasks_resp.get("taskSummaries", []))
        next_token = tasks_resp.get("nextToken")
        if not next_token:
            break

    tasks = []
    for t in task_summaries:
        tasks.append({
            "taskId": t.get("taskId", ""),
            "title": t.get("title", t.get("riskType", "")),
            "status": t.get("status", ""),
            "riskType": t.get("riskType", ""),
            "endpoint": t.get("targetEndpointUri", ""),
        })

    return {
        "status": "IN_PROGRESS",
        "tasks": tasks,
        "job_id": job_id,
        "started_at": job.get("startedAt", ""),
    }


def _get_cached_data(sec_space_id=None, target_job_id=None):
    """Security Agent space_id로 findings 조회. target_job_id 지정 시 해당 job만."""
    import re
    from routes_settings import _sa_client

    if not sec_space_id:
        from routes_settings import _security_agent_config
        cfg = _security_agent_config()
        sec_space_id = cfg.get("agent_space_id", "")

    if not sec_space_id:
        return [], [], {}, None, {"agent_space_id": ""}

    cache_key = f"{sec_space_id}:{target_job_id}" if target_job_id else sec_space_id

    # 캐시 hit (TTL 5분)
    if cache_key in _cache and _cache[cache_key].get("findings") is not None:
        c = _cache[cache_key]
        if time.time() - c.get("ts", 0) < 300:
            return c["findings"], c["enriched"], c["task_counts"], c["job_id"], {"agent_space_id": sec_space_id}

    sa = _sa_client(sec_space_id=sec_space_id)

    if target_job_id:
        job_id = target_job_id
    else:
        resp = sa.list_pentests(agentSpaceId=sec_space_id)
        pentests = resp.get("pentestSummaries", [])
        if not pentests:
            return [], [], {}, None, {"agent_space_id": sec_space_id}

        pentest_info = pentests[0]
        jobs_resp = sa.list_pentest_jobs_for_pentest(
            agentSpaceId=sec_space_id,
            pentestId=pentest_info["pentestId"],
        )
        completed = [j for j in jobs_resp.get("pentestJobSummaries", []) if j.get("status") == "COMPLETED"]
        if not completed:
            return [], [], {}, None, {"agent_space_id": sec_space_id}

        job_id = completed[0]["pentestJobId"]

    # job_id 동일하면 캐시 유지
    if cache_key in _cache and _cache[cache_key].get("job_id") == job_id and _cache[cache_key].get("findings") is not None:
        _cache[cache_key]["ts"] = time.time()
        c = _cache[cache_key]
        return c["findings"], c["enriched"], c["task_counts"], job_id, {"agent_space_id": sec_space_id}

    # 캐시 miss: 전체 다운로드
    findings_resp = sa.list_findings(agentSpaceId=sec_space_id, pentestJobId=job_id)
    summaries = findings_resp.get("findingsSummaries", [])
    finding_ids = [f["findingId"] for f in summaries]

    details_map = {}
    for i in range(0, len(finding_ids), 10):
        batch = finding_ids[i:i + 10]
        detail_resp = sa.batch_get_findings(agentSpaceId=sec_space_id, findingIds=batch)
        for d in detail_resp.get("findings", []):
            details_map[d["findingId"]] = d

    task_summaries = []
    next_token = None
    while True:
        kwargs = {"agentSpaceId": sec_space_id, "pentestJobId": job_id}
        if next_token:
            kwargs["nextToken"] = next_token
        tasks_resp = sa.list_pentest_job_tasks(**kwargs)
        task_summaries.extend(tasks_resp.get("taskSummaries", []))
        next_token = tasks_resp.get("nextToken")
        if not next_token:
            break

    endpoint_by_risk = {}
    task_counts = {}
    for t in task_summaries:
        rt = t.get("riskType", "")
        if rt:
            task_counts[rt] = task_counts.get(rt, 0) + 1
            if rt not in endpoint_by_risk:
                endpoint_by_risk[rt] = t.get("targetEndpointUri", "")

    findings = []
    for f in summaries:
        fid = f.get("findingId", "")
        detail = details_map.get(fid, {})
        crt = detail.get("codeRemediationTask", {})
        pr_link = ""
        if crt.get("taskDetails"):
            pr_link = crt["taskDetails"][0].get("pullRequestLink", "")

        endpoint = endpoint_by_risk.get(f.get("riskType", ""), "")
        if not endpoint:
            url_match = re.search(r'https?://[^\s"\'<>]+', detail.get("attackScript", ""))
            if url_match:
                endpoint = url_match.group(0).rstrip(")")
                endpoint = re.sub(r'/[^/]*$', '', endpoint)

        findings.append({
            "id": fid,
            "name": detail.get("name") or f.get("name", ""),
            "riskType": f.get("riskType", ""),
            "riskLevel": f.get("riskLevel", ""),
            "riskScore": detail.get("riskScore", ""),
            "confidence": f.get("confidence", ""),
            "description": detail.get("description", ""),
            "attackScript": detail.get("attackScript", ""),
            "status": f.get("status", ""),
            "remediationStatus": crt.get("status", ""),
            "prLink": pr_link,
            "endpoint": endpoint,
        })

    enriched_data = enrichment.enrich_findings(findings)

    _cache[cache_key] = {
        "job_id": job_id,
        "findings": findings,
        "enriched": enriched_data,
        "task_counts": task_counts,
        "ts": time.time(),
    }

    return findings, enriched_data, task_counts, job_id, {"agent_space_id": sec_space_id}


def _resolve_sec_spaces():
    """request에서 Security Agent space 목록 결정.

    sec_space_id 파라미터가 있으면 그 space만 직접 반환 (Settings→Insights 경로).
    없으면 space_id → state file links → 전체 조회.
    """
    sec_space_id = request.args.get("sec_space_id", "").strip()
    if sec_space_id:
        return [{"security_space_id": sec_space_id, "name": "", "target_domain": "", "pentest_id": ""}]

    from app_config import _req_space_id
    from routes_security_targets import _find_security_spaces_for_devops
    return _find_security_spaces_for_devops(_req_space_id())


def _find_devops_for_sec(sec_space_id):
    """sec_space_id → 연결된 DevOps Space ID 역조회 (DDB security_links scan).
    prefix 매칭 지원 — short ID로도 검색 가능."""
    from app_config import _boto_session, RUNS_TABLE
    if not RUNS_TABLE or not sec_space_id:
        return ""
    try:
        tbl = _boto_session().resource("dynamodb").Table(RUNS_TABLE)
        resp = tbl.scan(
            FilterExpression="record_type = :rt",
            ExpressionAttributeValues={":rt": "space_metadata"},
            ProjectionExpression="run_id, security_links",
        )
        for item in resp.get("Items", []):
            for link in (item.get("security_links") or []):
                stored = link.get("security_space_id", "")
                if stored == sec_space_id or stored.startswith(sec_space_id) or sec_space_id.startswith(stored):
                    return item["run_id"].replace("space-meta-", "")
    except Exception:
        pass
    return ""


@security_insights_bp.route("/api/security/insights/linked-spaces")
def api_linked_spaces():
    """현재 DevOps space에 연결된 Security Agent space 목록 반환."""
    try:
        from app_config import _req_space_id
        from routes_security_targets import _find_security_spaces_for_devops
        devops_space_id = _req_space_id()
        if not devops_space_id:
            sec_sid = request.args.get("sec_space_id", "").strip()
            if sec_sid:
                devops_space_id = _find_devops_for_sec(sec_sid)
        spaces = _find_security_spaces_for_devops(devops_space_id) if devops_space_id else []
        return jsonify({"ok": True, "spaces": spaces, "devops_space_id": devops_space_id or ""})
    except Exception as e:
        return jsonify({"ok": False, "spaces": [], "error": str(e)})


def _req_job_id():
    """request에서 job_id 파라미터 추출."""
    return request.args.get("job_id", "").strip() or None


def _get_aggregated_data():
    """request → 단일 Security Agent space의 findings 반환. 합산 없음."""
    sec_spaces = _resolve_sec_spaces()
    job_id = _req_job_id()

    if sec_spaces:
        _, enriched, task_counts, _, _ = _get_cached_data(sec_spaces[0]["security_space_id"], target_job_id=job_id)
        return enriched, task_counts

    _, enriched, task_counts, _, _ = _get_cached_data(target_job_id=job_id)
    return enriched, task_counts


def _get_cached_data_for_finding(finding_id):
    """현재 space에서 finding_id를 포함하는 데이터를 반환."""
    sec_spaces = _resolve_sec_spaces()

    if sec_spaces:
        return _get_cached_data(sec_spaces[0]["security_space_id"])

    return _get_cached_data()


# ---------------------------------------------------------------------------
# Helpers — findings 가져오기 (routes_settings.py 재사용)
# ---------------------------------------------------------------------------

def _get_latest_findings() -> list[dict]:
    """request → 단일 Security Agent space의 raw findings 반환."""
    sec_spaces = _resolve_sec_spaces()

    if sec_spaces:
        findings, _, _, _, _ = _get_cached_data(sec_spaces[0]["security_space_id"])
        return findings

    findings, _, _, _, _ = _get_cached_data()
    return findings


# ---------------------------------------------------------------------------
# Helpers — task count + agent conclusion 추출
# ---------------------------------------------------------------------------

def _get_completed_job_id():
    """최신 완료된 pentest job ID 반환."""
    from routes_settings import _security_agent_config, _sa_client
    cfg = _security_agent_config()
    if not cfg["agent_space_id"]:
        return None, cfg
    sa = _sa_client(sec_space_id=cfg["agent_space_id"])
    resp = sa.list_pentests(agentSpaceId=cfg["agent_space_id"])
    pentests = resp.get("pentestSummaries", [])
    pentest_info = None
    for p in pentests:
        if p.get("pentestId") == cfg["pentest_id"]:
            pentest_info = p
            break
    if not pentest_info and pentests:
        pentest_info = pentests[0]
    if not pentest_info:
        return None, cfg
    jobs_resp = sa.list_pentest_jobs_for_pentest(
        agentSpaceId=cfg["agent_space_id"],
        pentestId=pentest_info["pentestId"],
    )
    completed = [j for j in jobs_resp.get("pentestJobSummaries", []) if j.get("status") == "COMPLETED"]
    if not completed:
        return None, cfg
    return completed[0]["pentestJobId"], cfg


def _get_task_counts_by_risk(cfg, job_id):
    """riskType별 task count 집계 (페이지네이션 포함)."""
    from routes_settings import _sa_client
    sa = _sa_client(sec_space_id=cfg.get("agent_space_id", ""))
    counts = {}
    next_token = None
    while True:
        kwargs = {"agentSpaceId": cfg["agent_space_id"], "pentestJobId": job_id}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = sa.list_pentest_job_tasks(**kwargs)
        for t in resp.get("taskSummaries", []):
            rt = t.get("riskType", "")
            if rt:
                counts[rt] = counts.get(rt, 0) + 1
        next_token = resp.get("nextToken")
        if not next_token:
            break
    return counts


def _extract_agent_conclusion(cfg, job_id, risk_type):
    """특정 riskType의 마지막 task 로그에서 Agent 분석 결론 추출."""
    import json as _json
    from routes_settings import _sa_client, _boto_session

    sa = _sa_client(sec_space_id=cfg.get("agent_space_id", ""))
    resp = sa.list_pentest_job_tasks(agentSpaceId=cfg["agent_space_id"], pentestJobId=job_id)
    task_ids = [t["taskId"] for t in resp.get("taskSummaries", []) if t.get("riskType") == risk_type]
    if not task_ids:
        return None

    # 마지막 task의 로그 위치 가져오기
    detail_resp = sa.batch_get_pentest_job_tasks(
        agentSpaceId=cfg["agent_space_id"], taskIds=task_ids[-1:]
    )
    tasks = detail_resp.get("tasks", [])
    if not tasks:
        return None

    task = tasks[0]
    cw_log = task.get("logsLocation", {}).get("cloudWatchLog", {})
    log_group = cw_log.get("logGroup", "")
    log_stream = cw_log.get("logStream", "")
    if not log_group or not log_stream:
        return None

    session = _boto_session()
    logs_client = session.client("logs", region_name="us-east-1")
    all_events = []
    kwargs = {"logGroupName": log_group, "logStreamName": log_stream, "startFromHead": True, "limit": 200}
    while True:
        r = logs_client.get_log_events(**kwargs)
        batch = r.get("events", [])
        all_events.extend(batch)
        nt = r.get("nextForwardToken")
        if not batch or nt == kwargs.get("nextToken"):
            break
        kwargs["nextToken"] = nt
        if len(all_events) > 500:
            break

    for ev in reversed(all_events):
        try:
            msg = _json.loads(ev["message"])
        except Exception:
            continue
        if "response" not in msg:
            continue
        for c in msg["response"].get("content", []):
            if c.get("type") == "text" and len(c.get("text", "")) > 200:
                return c["text"]
    return None


def _fetch_pr_info(pr_url: str) -> dict | None:
    """GitHub PR 정보 가져오기 (title, state, diff)."""
    import os, re, json as _json, urllib.request

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        try:
            from routes_settings import _get_github_token_from_keychain
            token = _get_github_token_from_keychain()
        except Exception:
            pass
    if not token:
        return None

    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_url)
    if not m:
        return None

    owner, repo, pr_number = m.group(1), m.group(2), m.group(3)

    def _gh_get(path):
        url = f"https://api.github.com/repos/{owner}/{repo}/{path}"
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {token}",
            "User-Agent": "devops-dashboard",
        })
        return _json.loads(urllib.request.urlopen(req, timeout=15).read())

    try:
        pr_data = _gh_get(f"pulls/{pr_number}")
        files_data = _gh_get(f"pulls/{pr_number}/files")
        diff_lines = []
        for f in files_data[:5]:
            patch = f.get("patch", "")
            if patch:
                diff_lines.append(f"--- {f.get('filename', '')}")
                diff_lines.append(patch)
        return {
            "title": pr_data.get("title", ""),
            "state": "merged" if pr_data.get("merged") else pr_data.get("state", ""),
            "diff": "\n".join(diff_lines) if diff_lines else None,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------

@security_insights_bp.route("/security")
def security_page():
    """Security Agent 페이지 — 조사 시작 + 이력 + 타임라인."""
    sec_space_id = request.args.get("sec_space_id", "").strip()
    sec_space_name = ""
    if sec_space_id:
        try:
            from routes_settings import _sa_client
            sa = _sa_client(sec_space_id=sec_space_id)
            resp = sa.batch_get_agent_spaces(agentSpaceIds=[sec_space_id])
            spaces = resp.get("agentSpaces", [])
            if spaces:
                sec_space_name = spaces[0].get("name", "")
        except Exception:
            pass
    embed = request.args.get("embed", "").strip() == "1"
    return render_template("security.html", cache_bust=int(time.time()),
                           sec_space_id=sec_space_id, sec_space_name=sec_space_name,
                           embed=embed)


@security_insights_bp.route("/security/insights")
@security_insights_bp.route("/security/insights/")
@security_insights_bp.route("/security/insights/<space_id>")
def insights_page(space_id=None):
    from app_config import AGENT_SPACE_ID
    sid = space_id or request.args.get("space_id", "").strip() or AGENT_SPACE_ID
    return render_template("security_insights.html", cache_bust=int(time.time()), space_id=sid)


# ---------------------------------------------------------------------------
# API: 컨텍스트 재평가 findings
# ---------------------------------------------------------------------------

@security_insights_bp.route("/api/security/insights/enriched-findings")
def api_enriched_findings():
    """전체 findings + 컨텍스트 재평가 결과 (캐싱). IN_PROGRESS시 task timeline 포함."""
    try:
        enriched, task_counts = _get_aggregated_data()

        stats = {
            "total": len(enriched),
            "risk_reduced": sum(1 for f in enriched if f.get("risk_changed")),
            "remediated": sum(1 for f in enriched if f.get("remediationStatus") == "COMPLETED"),
            "critical": sum(1 for f in enriched if f.get("adjusted_risk") == "CRITICAL"),
            "high": sum(1 for f in enriched if f.get("adjusted_risk") == "HIGH"),
            "medium": sum(1 for f in enriched if f.get("adjusted_risk") == "MEDIUM"),
            "low": sum(1 for f in enriched if f.get("adjusted_risk") in ("LOW", "INFO")),
        }

        sec_spaces = _resolve_sec_spaces()
        sec_id = sec_spaces[0]["security_space_id"] if sec_spaces else None
        space_info = {
            "security_space_id": sec_id or "",
            "name": sec_spaces[0].get("name", "") if sec_spaces else "",
            "target_domain": sec_spaces[0].get("target_domain", "") if sec_spaces else "",
        }

        result = {"ok": True, "findings": enriched, "stats": stats, "task_counts": task_counts, "space_info": space_info}

        if not enriched:
            status = _get_pentest_status(sec_id)
            if status["status"] == "IN_PROGRESS":
                result["pentest_status"] = status

        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API: 서비스별 보안 요약 (Level 2)
# ---------------------------------------------------------------------------

@security_insights_bp.route("/api/security/insights/service-summary/<service_name>")
def api_service_summary(service_name):
    """서비스별 findings + task count (조사 깊이) 요약."""
    try:
        enriched, task_counts = _get_aggregated_data()

        svc_lower = service_name.lower()
        svc_findings = []
        for f in enriched:
            f_svc = ""
            ctx = f.get("operational_context", {})
            if ctx.get("service_name"):
                f_svc = ctx["service_name"].lower()
            if not f_svc and f.get("endpoint"):
                import re
                m = re.search(r'https?://([^/:]+)', f["endpoint"])
                if m:
                    f_svc = m.group(1).split(".")[0].lower()
            if not f_svc:
                f_svc = "unknown"
            if f_svc != svc_lower:
                continue

            risk_type = f.get("riskType", "")
            rem_status = f.get("remediationStatus", "")
            if rem_status == "COMPLETED":
                status = "remediated"
            elif f.get("risk_changed") and f.get("adjusted_risk") in ("LOW", "INFO"):
                status = "safe"
            else:
                status = "vulnerable"

            pr_link = f.get("prLink", "")
            pr_number = None
            if pr_link:
                import re as _re
                pm = _re.search(r'/pull/(\d+)', pr_link)
                if pm:
                    pr_number = int(pm.group(1))

            svc_findings.append({
                "id": f["id"],
                "name": f.get("name", ""),
                "riskType": risk_type,
                "riskLevel": f.get("riskLevel", ""),
                "adjusted_risk": f.get("adjusted_risk", ""),
                "task_count": task_counts.get(risk_type, 0),
                "status": status,
                "pr_link": pr_link,
                "pr_number": pr_number,
            })

        risk_order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}
        max_risk = "INFO"
        for sf in svc_findings:
            ar = sf.get("adjusted_risk") or sf.get("riskLevel", "")
            if risk_order.get(ar, 0) > risk_order.get(max_risk, 0):
                max_risk = ar

        return jsonify({
            "ok": True,
            "service_name": service_name,
            "findings": svc_findings,
            "total_tasks": sum(task_counts.values()),
            "max_risk": max_risk,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API: Finding 통합 상세 (Level 3)
# ---------------------------------------------------------------------------

@security_insights_bp.route("/api/security/insights/finding-detail/<finding_id>")
def api_finding_detail(finding_id):
    """Finding 통합 상세 — enrichment + 조사 결론 + PR + 시나리오."""
    try:
        findings, enriched_list, task_counts, job_id, cfg = _get_cached_data_for_finding(finding_id)

        target = None
        for f in findings:
            if f["id"] == finding_id:
                target = f
                break
        if not target:
            return jsonify({"ok": False, "error": "Finding not found"})

        enriched = None
        for ef in enriched_list:
            if ef.get("id") == finding_id:
                enriched = ef
                break
        if not enriched:
            enriched = target

        risk_type = target.get("riskType", "")
        task_count = task_counts.get(risk_type, 0)

        agent_conclusion = None
        if job_id and task_count > 0:
            try:
                agent_conclusion = _extract_agent_conclusion(cfg, job_id, risk_type)
            except Exception:
                pass

        # PR 정보
        pr_info = None
        pr_link = target.get("prLink", "")
        if pr_link:
            pr_info = {"url": pr_link, "title": "", "state": "", "diff": None}
            try:
                import re as _re
                pm = _re.search(r'/pull/(\d+)', pr_link)
                if pm:
                    pr_info["number"] = int(pm.group(1))
                pr_data = _fetch_pr_info(pr_link)
                if pr_data:
                    pr_info["title"] = pr_data.get("title", "")
                    pr_info["state"] = pr_data.get("state", "")
                    pr_info["diff"] = pr_data.get("diff", "")
            except Exception:
                pass

        # 시나리오 정보 — 현재 space 스코프 우선, 없으면 전역 fallback
        sec_sid = request.args.get("sec_space_id", "").strip()
        devops_sid = request.args.get("space_id", "").strip()
        if not devops_sid and sec_sid:
            devops_sid = _find_devops_for_sec(sec_sid)
        scenario_in_other_space = False
        scenario = scenario_engine.get_registered_scenario(finding_id, devops_space_id=devops_sid) if devops_sid else None
        if not scenario:
            scenario = scenario_engine.get_registered_scenario(finding_id)
            if scenario and devops_sid and scenario.get("devops_space_id", "") != devops_sid:
                scenario_in_other_space = True

        return jsonify({
            "ok": True,
            "finding": enriched,
            "investigation": {
                "task_count": task_count,
                "agent_conclusion": agent_conclusion,
            },
            "pr": pr_info,
            "scenario": scenario,
            "scenario_in_other_space": scenario_in_other_space,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API: 시나리오 목록 (findings → scenarios 변환)
# ---------------------------------------------------------------------------

@security_insights_bp.route("/api/security/insights/scenarios")
def api_scenarios():
    """모든 findings를 시나리오로 변환하여 반환."""
    try:
        findings = _get_latest_findings()
        scenarios = scenario_engine.convert_findings(findings)
        return jsonify({
            "ok": True,
            "scenarios": [
                {
                    "id": s.id,
                    "finding_id": s.finding_id,
                    "finding_name": s.finding_name,
                    "risk_type": s.risk_type,
                    "risk_level": s.risk_level,
                    "service_name": s.service_name,
                    "endpoint": s.endpoint,
                    "steps_count": len(s.attack_steps),
                }
                for s in scenarios
            ],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API: 시나리오 실행
# ---------------------------------------------------------------------------

@security_insights_bp.route("/api/security/insights/scenarios/<scenario_id>/run", methods=["POST"])
def api_run_scenario(scenario_id):
    """보안 시나리오 실행 — attackScript HTTP 재실행으로 취약 여부 판정.

    1) 최신 pentest 결과에서 finding 검색
    2) 없으면 로컬 등록된 시나리오의 attack_steps 사용 (pentest 갱신 후에도 동작)
    """
    from dataclasses import asdict
    from security_scenarios import SecurityScenario

    try:
        body = request.get_json(silent=True) or {}
        finding_id = body.get("finding_id") or scenario_id.replace("SEC-", "")
        finding_id_short = scenario_id.replace("SEC-", "")

        # 1) 최신 pentest findings에서 검색
        findings = _get_latest_findings()
        target_finding = None
        for f in findings:
            fid = f["id"]
            if fid == finding_id or fid.startswith(finding_id_short) or fid.startswith("f-" + finding_id_short):
                target_finding = f
                break

        if target_finding:
            scenario = scenario_engine.convert_finding(target_finding)
        else:
            # 2) 로컬 등록 시나리오 fallback (attack_steps 보존됨)
            registered = scenario_engine.get_registered_scenario(finding_id)
            if not registered:
                for s in scenario_engine.list_registered_scenarios():
                    if s.get("id") == scenario_id:
                        registered = s
                        break

            if not registered:
                return jsonify({"ok": False, "error": f"Finding not found (최신 pentest에 없고 등록된 시나리오도 없음): {scenario_id}"})

            scenario = SecurityScenario(
                id=registered.get("id", scenario_id),
                finding_id=registered.get("finding_id", finding_id),
                finding_name=registered.get("name", ""),
                risk_type=registered.get("risk_type", ""),
                risk_level=registered.get("risk_level", ""),
                service_name=registered.get("target_service", ""),
                endpoint=registered.get("endpoint", ""),
                attack_steps=registered.get("attack_steps", []),
                created_at=registered.get("registered_at", ""),
            )

        result = scenario_engine.execute(scenario)
        return jsonify({"ok": True, "result": asdict(result)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})




# ---------------------------------------------------------------------------
# API: 재검증 (PR merge 후)
# ---------------------------------------------------------------------------

@security_insights_bp.route("/api/security/insights/reverify/<finding_id>", methods=["POST"])
def api_reverify(finding_id):
    """특정 finding 재검증 — PR merge 후 attackScript 재실행."""
    try:
        findings = _get_latest_findings()
        target = None
        for f in findings:
            if f["id"] == finding_id:
                target = f
                break

        if not target:
            return jsonify({"ok": False, "error": f"Finding not found: {finding_id}"})

        if not target.get("prLink"):
            return jsonify({"ok": False, "error": "이 finding에 대한 PR이 없습니다"})

        result = scenario_engine.reverify_finding(target)
        from dataclasses import asdict
        return jsonify({"ok": True, "result": asdict(result)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API: 시나리오 등록 (persistent)
# ---------------------------------------------------------------------------

@security_insights_bp.route("/api/security/insights/register-scenario", methods=["POST"])
def api_register_scenario():
    """Finding을 시나리오 탭에 등록."""
    try:
        body = request.json or {}
        finding_id = body.get("finding_id", "")
        if not finding_id:
            return jsonify({"ok": False, "error": "finding_id required"})

        # devops_space_id 결정: body > sec_space_id 역조회 > AGENT_SPACE_ID
        from app_config import AGENT_SPACE_ID
        sec_sid = request.args.get("sec_space_id", "").strip() or body.get("sec_space_id", "").strip()
        devops_space_id = body.get("space_id", "").strip()
        if not devops_space_id and sec_sid:
            devops_space_id = _find_devops_for_sec(sec_sid)
        if not devops_space_id:
            devops_space_id = AGENT_SPACE_ID

        existing = scenario_engine.get_registered_scenario(finding_id, devops_space_id=devops_space_id)
        if existing:
            return jsonify({"ok": True, "scenario": existing, "already_registered": True, "space_id": devops_space_id})

        # sec_space_id: query param > body > space_id 역조회
        if not sec_sid:
            space_id = body.get("space_id", "").strip()
            if space_id:
                from routes_security_targets import _find_security_spaces_for_devops
                links = _find_security_spaces_for_devops(space_id)
                if links:
                    sec_sid = links[0].get("security_space_id", "")

        if sec_sid:
            findings, _, _, _, _ = _get_cached_data(sec_sid)
        else:
            findings = _get_latest_findings()

        target = None
        for f in findings:
            if f["id"] == finding_id:
                target = f
                break

        if not target:
            return jsonify({"ok": False, "error": f"Finding not found: {finding_id}"})

        # Space의 app_name을 fallback으로 전달
        space_app_name = ""
        if devops_space_id:
            from app_config import _boto_session, RUNS_TABLE
            try:
                tbl = _boto_session().resource("dynamodb").Table(RUNS_TABLE)
                meta = tbl.get_item(Key={"run_id": f"space-meta-{devops_space_id}", "record_type": "space_metadata"}).get("Item", {})
                space_app_name = meta.get("app_name", "")
            except Exception:
                pass

        registered = scenario_engine.register_scenario(target, space_app_name=space_app_name, devops_space_id=devops_space_id)
        return jsonify({"ok": True, "scenario": registered, "already_registered": False, "space_id": devops_space_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@security_insights_bp.route("/api/security/insights/registered-scenarios")
def api_registered_scenarios():
    """등록된 보안 시나리오 목록."""
    try:
        scenarios = scenario_engine.list_registered_scenarios()
        return jsonify({"ok": True, "scenarios": scenarios})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@security_insights_bp.route("/api/security/insights/finding-scenario/<finding_id>")
def api_finding_scenario(finding_id):
    """Finding에 대한 등록된 시나리오 조회."""
    try:
        scenario = scenario_engine.get_registered_scenario(finding_id)
        return jsonify({"ok": True, "scenario": scenario})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API: 태스크 로그 기반 방어 원인 분석
# ---------------------------------------------------------------------------

@security_insights_bp.route("/api/security/insights/defense-analysis/<finding_id>")
def api_defense_analysis(finding_id):
    """Finding의 원본 태스크 로그에서 Agent 분석 결론을 추출."""
    try:
        from routes_settings import _sa_client, _boto_session
        import json as _json

        findings, _, _, job_id, cfg = _get_cached_data_for_finding(finding_id)
        sec_space_id = cfg.get("agent_space_id", "")
        if not sec_space_id:
            return jsonify({"ok": False, "error": "agent_space_id not configured"})

        target = None
        for f in findings:
            if f["id"] == finding_id:
                target = f
                break
        if not target:
            return jsonify({"ok": False, "error": "Finding not found"})

        if not job_id:
            return jsonify({"ok": False, "error": "No completed job"})

        sa = _sa_client(sec_space_id=sec_space_id)

        # task 찾기 (riskType 매칭)
        risk_type = target.get("riskType", "")
        tasks_resp = sa.list_pentest_job_tasks(
            agentSpaceId=sec_space_id,
            pentestJobId=job_id,
        )
        task_ids = [t["taskId"] for t in tasks_resp.get("taskSummaries", [])]
        task_detail = None
        if task_ids:
            for i in range(0, len(task_ids), 10):
                batch = task_ids[i:i+10]
                detail_resp = sa.batch_get_pentest_job_tasks(
                    agentSpaceId=sec_space_id, taskIds=batch
                )
                for d in detail_resp.get("tasks", []):
                    if d.get("riskType") == risk_type:
                        task_detail = d
                        break
                if task_detail:
                    break

        if not task_detail:
            return jsonify({"ok": False, "error": f"Task not found for riskType: {risk_type}"})

        logs_loc = task_detail.get("logsLocation", {})
        cw_log = logs_loc.get("cloudWatchLog", {})
        log_group = cw_log.get("logGroup", "")
        log_stream = cw_log.get("logStream", "")

        if not log_group or not log_stream:
            return jsonify({"ok": False, "error": "No log location for task"})

        # CloudWatch에서 agent 분석 추출 (전체 읽기 후 역순 탐색)
        session = _boto_session()
        logs_client = session.client("logs", region_name="us-east-1")
        all_events = []
        kwargs = {
            "logGroupName": log_group,
            "logStreamName": log_stream,
            "startFromHead": True,
            "limit": 200,
        }
        while True:
            resp = logs_client.get_log_events(**kwargs)
            batch = resp.get("events", [])
            all_events.extend(batch)
            next_token = resp.get("nextForwardToken")
            if not batch or next_token == kwargs.get("nextToken"):
                break
            kwargs["nextToken"] = next_token
            if len(all_events) > 500:
                break

        # 마지막 agent response에서 가장 긴 text 추출 (역순)
        analysis_text = ""
        for ev in reversed(all_events):
            try:
                msg = _json.loads(ev["message"])
            except Exception:
                continue
            if "response" not in msg:
                continue
            content = msg["response"].get("content", [])
            for c in content:
                if c.get("type") == "text" and len(c.get("text", "")) > 200:
                    analysis_text = c["text"]
                    break
            if analysis_text:
                break

        if not analysis_text:
            return jsonify({"ok": False, "error": "No agent analysis found in logs"})

        return jsonify({
            "ok": True,
            "analysis": analysis_text,
            "task_id": task_detail.get("taskId", ""),
            "risk_type": risk_type,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API: 실행 결과 이력
# ---------------------------------------------------------------------------

@security_insights_bp.route("/api/security/insights/results")
def api_results():
    """저장된 실행 결과 목록."""
    try:
        results = scenario_engine.list_results()
        return jsonify({"ok": True, "results": results})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API: 공격 경로 (토폴로지 연동)
# ---------------------------------------------------------------------------

@security_insights_bp.route("/api/security/insights/attack-paths")
def api_attack_paths():
    """Finding을 서비스별로 그룹핑 → 토폴로지 badge 데이터."""
    try:
        enriched, _ = _get_aggregated_data()

        service_map = {}
        for f in enriched:
            svc = f.get("operational_context", {}).get("service_name", "") or "unknown"
            if svc not in service_map:
                service_map[svc] = {
                    "service_name": svc,
                    "findings_count": 0,
                    "max_risk": "INFO",
                    "findings": [],
                }
            service_map[svc]["findings_count"] += 1
            service_map[svc]["findings"].append({
                "id": f["id"],
                "name": f.get("name", ""),
                "adjusted_risk": f.get("adjusted_risk", ""),
                "risk_type": f.get("riskType", ""),
            })
            risk_order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}
            current_max = risk_order.get(service_map[svc]["max_risk"], 0)
            new_risk = risk_order.get(f.get("adjusted_risk", ""), 0)
            if new_risk > current_max:
                service_map[svc]["max_risk"] = f.get("adjusted_risk", "INFO")

        return jsonify({"ok": True, "services": list(service_map.values())})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API: 토폴로지 + 보안 오버레이 (시각화용)
# ---------------------------------------------------------------------------

@security_insights_bp.route("/api/security/insights/topology-overlay")
def api_topology_overlay():
    """Pentest findings에서 공격 토폴로지를 직접 생성 (DevOps arch 의존 제거)."""
    import re
    from urllib.parse import urlparse

    try:
        enriched, task_counts = _get_aggregated_data()

        if not enriched:
            return jsonify({"ok": True, "nodes": [], "edges": [],
                            "entry_points": [], "infra_findings": [],
                            "task_counts": task_counts})

        # 1) findings에서 서비스 노드 추출
        service_findings = {}
        infra_findings = []
        service_endpoints = {}  # name → endpoint URL

        for f in enriched:
            risk_type = f.get("riskType", "")

            # 인프라/IaC 분류
            if risk_type in ("SECURITY_MISCONFIGURATION", "DEFAULT_CREDENTIALS") or \
               "CloudFormation" in f.get("name", "") or \
               "GitHub Actions" in f.get("name", ""):
                infra_findings.append({
                    "id": f["id"],
                    "name": f.get("name", ""),
                    "riskType": risk_type,
                    "adjusted_risk": f.get("adjusted_risk", f.get("riskLevel", "")),
                    "category": "infra",
                })
                continue

            # 서비스명 추출: endpoint URL 기반
            svc = _extract_service_from_finding(f)
            if svc not in service_findings:
                service_findings[svc] = []
            service_findings[svc].append({
                "id": f["id"],
                "name": f.get("name", ""),
                "riskType": risk_type,
                "adjusted_risk": f.get("adjusted_risk", f.get("riskLevel", "")),
                "task_count": task_counts.get(risk_type, 0),
            })

            # endpoint 기록
            endpoint = f.get("endpoint", "")
            if endpoint and svc not in service_endpoints:
                service_endpoints[svc] = endpoint

            # attackScript에서 lateral movement 대상 추출
            internal_svcs = _extract_lateral_targets(f)
            for isvc in internal_svcs:
                if isvc != svc and isvc not in service_findings:
                    service_findings[isvc] = []

        # "unknown" findings → 주요 타겟 서비스로 귀속
        if "unknown" in service_findings and service_findings["unknown"]:
            real_svcs = {k: v for k, v in service_findings.items() if k != "unknown" and v}
            if real_svcs:
                primary = max(real_svcs, key=lambda k: len(real_svcs[k]))
                service_findings[primary].extend(service_findings["unknown"])
            service_findings.pop("unknown", None)

        # 2) 노드 구성
        all_svc_names = set(service_findings.keys()) - {"unknown"}
        risk_order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}

        topo_nodes = []
        entry_targets = set()

        for name, findings in service_findings.items():
            if name == "unknown" or not findings:
                continue

            max_risk = "NONE"
            for ff in findings:
                r = ff.get("adjusted_risk", "")
                if risk_order.get(r, 0) > risk_order.get(max_risk, 0):
                    max_risk = r

            endpoint = service_endpoints.get(name, "")
            port = ""
            if endpoint:
                parsed = urlparse(endpoint)
                port = str(parsed.port) if parsed.port else ("443" if parsed.scheme == "https" else "80")

            is_direct_target = any(f.get("endpoint") for f in enriched
                                   if _extract_service_from_finding(f) == name)
            if is_direct_target:
                entry_targets.add(name)

            svc_type = "app"
            name_lower = name.lower()
            if any(k in name_lower for k in ("redis", "postgres", "mysql", "mongo", "db")):
                svc_type = "db"
            elif any(k in name_lower for k in ("nginx", "gateway", "proxy", "alb", "nlb")):
                svc_type = "gateway"

            # role = 도메인 (경로 제외)
            role_label = ""
            if endpoint:
                parsed_ep = urlparse(endpoint)
                role_label = parsed_ep.hostname or name
            else:
                role_label = name

            topo_nodes.append({
                "name": name,
                "tier": "",
                "service_type": svc_type,
                "ports": [port] if port else [],
                "role": role_label,
                "findings_count": len(findings),
                "findings": findings,
                "max_risk": max_risk,
            })

        # 3) 엣지: lateral movement (attackScript에서 추출된 내부 서비스 참조)
        node_names = {n["name"] for n in topo_nodes}
        topo_edges = []
        seen_edges = set()
        for f in enriched:
            src = _extract_service_from_finding(f)
            if src == "unknown" or src not in node_names:
                continue
            targets = _extract_lateral_targets(f)
            for t in targets:
                if t != src and t in node_names:
                    edge_key = f"{src}->{t}"
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        topo_edges.append({
                            "source": src,
                            "target": t,
                            "description": "lateral movement",
                            "port": None,
                            "protocol": "HTTP",
                        })

        # 4) entry points
        entry_points = [{"target": t} for t in entry_targets]

        # 5) chain attacks — 토폴로지 위에 시각화할 체인 공격 흐름
        findings_raw = _get_latest_findings()
        topo_chains = []
        for f in (findings_raw or []):
            if not _is_chain_finding(f):
                continue
            attack = f.get("attackScript", "") or ""
            steps = _extract_chain_steps(attack)
            if len(steps) < 2:
                continue
            topo_chains.append({
                "id": f.get("id", ""),
                "name": f.get("name", "")[:100],
                "riskLevel": f.get("riskLevel", ""),
                "riskType": (f.get("riskType", "") or "").replace("_", " "),
                "steps": steps,
            })

        return jsonify({
            "ok": True,
            "nodes": topo_nodes,
            "edges": topo_edges,
            "entry_points": entry_points,
            "infra_findings": infra_findings,
            "chains": topo_chains,
            "task_counts": task_counts,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


_IGNORE_HOSTS = frozenset([
    "www", "localhost", "127", "0", "schemas", "tools", "xml",
    "fonts", "cdn", "ajax", "api", "static", "docs", "example",
])


def _is_noise_url(host):
    """well-known 도메인이나 non-service URL인지 판별."""
    import re
    parts = host.split(".")
    if parts[0] in _IGNORE_HOSTS:
        return True
    # pentest marker hosts (PEN-PLAY-*, FAKE-*, VALIDATION-* 등)
    if re.match(r'^(PEN-|FAKE-|VALIDATION-|SECURITY-)', host, re.IGNORECASE):
        return True
    # w3.org, googleapis.com, github.com 등
    known_externals = ("w3.org", "googleapis.com", "github.com", "githubusercontent.com",
                       "cloudflare.com", "jquery.com", "bootstrapcdn.com", "unpkg.com")
    for ext in known_externals:
        if host.endswith(ext):
            return True
    return False


def _extract_service_from_finding(finding):
    """Finding의 endpoint/attackScript에서 서비스 이름(hostname 기반) 추출."""
    import re

    # operational_context
    ctx = finding.get("operational_context", {}) or {}
    if ctx.get("service_name") and ctx["service_name"] not in _IGNORE_HOSTS:
        return ctx["service_name"]

    # endpoint URL → hostname의 첫 부분
    endpoint = finding.get("endpoint", "")
    if endpoint:
        m = re.search(r'https?://([^/:]+)', endpoint)
        if m:
            host = m.group(1)
            if _is_noise_url(host):
                pass
            else:
                return host.split(".")[0]

    # attackScript에서 타겟 도메인 URL 추출 (noise 제외)
    script = finding.get("attackScript", "")
    urls = re.findall(r'https?://([^/\s"<>]+)', script)
    for u in urls:
        host = u.split(":")[0]
        if not _is_noise_url(host):
            return host.split(".")[0]

    return "unknown"


def _extract_lateral_targets(finding):
    """attackScript에서 메인 타겟 외 내부 서비스 URL들 추출."""
    import re

    main_svc = _extract_service_from_finding(finding)
    script = finding.get("attackScript", "")
    urls = re.findall(r'https?://([^/\s"<>]+)', script)

    targets = set()
    for u in urls:
        host = u.split(":")[0]
        if _is_noise_url(host):
            continue
        name = host.split(".")[0]
        if name and name != main_svc:
            targets.add(name)

    return list(targets)


# ---------------------------------------------------------------------------
# API: Endpoint Attack Graph — 체인 공격 시각화
# ---------------------------------------------------------------------------

def _extract_api_paths(text):
    """텍스트에서 API 경로 추출 (정규화)."""
    import re
    paths = set()
    for m in re.finditer(r'(GET|POST|PUT|DELETE|PATCH)\s+(https?://[^\s"<>]+|/[^\s"<>]+)', text):
        url = m.group(2)
        path_m = re.search(r'(?:https?://[^/]+)?(/[^\s?"<>]+)', url)
        if path_m:
            p = path_m.group(1).rstrip(".,;:)")
            p = re.sub(r'/[A-Fa-f0-9-]{20,}', '/{id}', p)
            p = re.sub(r'/VALIDATION[^/]*', '/{marker}', p)
            p = re.sub(r'/SECURITY-VALIDATION[^/]*', '/{marker}', p)
            p = re.sub(r'/FM-\d+[^/]*', '/{scenario_id}', p)
            p = re.sub(r'/FAKE[^/]*', '/{id}', p)
            p = re.sub(r'/verify-[^/]+', '/{id}', p)
            if len(p) > 1 and not p.startswith('/static/'):
                paths.add(p)
    return paths


def _extract_chain_steps(attack_script):
    """attackScript에서 단계별 method + path + action 추출.

    두 가지 형식 지원:
      - "Step 1 - action:\\n  POST http://..."
      - "1. action:\\n   POST http://..."
    """
    import re
    steps = []

    # 형식 1: "Step N"
    parts = re.split(r'Step\s+(\d+)', attack_script)
    if len(parts) > 2:
        for i in range(1, len(parts) - 1, 2):
            step_num = int(parts[i])
            content = parts[i + 1]
            action_m = re.match(r'\s*[-:]\s*(.+?)(?:\n|Request:|Body:|$)', content, re.DOTALL)
            action = action_m.group(1).strip().rstrip(":") if action_m else ""
            action = re.sub(r'\s+', ' ', action)[:80]
            m = re.search(r'(GET|POST|PUT|DELETE|PATCH)\s+(https?://[^\s"<>]+|/[^\s"<>]+)', content)
            if m:
                steps.append(_normalize_step(step_num, m.group(1), m.group(2), action))

    # 형식 2: "N. action" (Step 형식에서 0~1개만 찾았을 때 fallback)
    if len(steps) < 2:
        steps = []
        num_parts = re.split(r'\n(\d+)\.\s+', "\n" + attack_script)
        for i in range(1, len(num_parts) - 1, 2):
            step_num = int(num_parts[i])
            content = num_parts[i + 1]
            action_m = re.match(r'(.+?)(?:\n|$)', content)
            action = action_m.group(1).strip().rstrip(":") if action_m else ""
            action = re.sub(r'\s+', ' ', action)[:80]
            m = re.search(r'(GET|POST|PUT|DELETE|PATCH)\s+(https?://[^\s"<>]+|/[^\s"<>]+)', content)
            if m:
                steps.append(_normalize_step(step_num, m.group(1), m.group(2), action))

    return steps


def _normalize_step(step_num, method, url, action):
    """Step path 정규화 (ID 치환 등)."""
    import re
    path_m = re.search(r'(?:https?://[^/]+)?(/[^\s?"<>]+)', url)
    path = path_m.group(1).rstrip(".,;:)") if path_m else ""
    path = re.sub(r'/[A-Fa-f0-9-]{20,}', '/{id}', path)
    path = re.sub(r'/FM-\d+[^/]*', '/{scenario_id}', path)
    path = re.sub(r'/FAKE[^/]*', '/{id}', path)
    path = re.sub(r'/SECURITY-VALIDATION[^/]*', '/{marker}', path)
    path = re.sub(r'/VALIDATION[^/]*', '/{marker}', path)
    return {"step": step_num, "method": method, "path": path, "action": action}


def _is_chain_finding(f):
    """체인 공격 finding 판별 — 여러 취약점을 조합한 실제 체인만 식별.

    판별 기준 (데이터 검증 완료):
      - description에 "chaining" (여러 취약점 연쇄)
      - description에 "attack chain" (단, "not demonstrated" 제외)
      - name에 "chained" (명시적 체인 표시)
      - name에 "+" (복합 취약점 조합)
    단순히 attackScript에 Step 번호가 있는 것은 체인이 아님 (검증 로그일 뿐).
    """
    if f.get("confidence") == "FALSE_POSITIVE":
        return False
    name = f.get("name", "")
    desc = f.get("description", "")
    name_lower = name.lower()
    desc_lower = desc.lower()
    if "chaining" in desc_lower:
        return True
    if "attack chain" in desc_lower and "not demonstrated" not in desc_lower:
        return True
    if "chained" in name_lower:
        return True
    if "+" in name:
        return True
    return False


@security_insights_bp.route("/api/security/insights/endpoint-attack-graph")
def api_endpoint_attack_graph():
    """Endpoint-level attack graph: 노드=endpoint, edge=체인 공격 흐름."""
    import re
    try:
        enriched, task_counts = _get_aggregated_data()
        findings = _get_latest_findings()
        if not findings:
            return jsonify({"ok": True, "nodes": [], "edges": [], "chains": []})

        # 1) 모든 finding에서 endpoint path 추출 → 노드 생성
        endpoint_nodes = {}  # path → {findings, max_risk, ...}
        for f in findings:
            attack = f.get("attackScript", "") or ""
            desc = f.get("description", "") or ""
            paths = _extract_api_paths(attack + " " + desc)
            # 주요 path = description 첫 부분에 나오는 것
            primary_path = ""
            first_desc = desc.split(".")[0] if desc else ""
            for p in paths:
                if p in first_desc:
                    primary_path = p
                    break
            if not primary_path and paths:
                primary_path = sorted(paths, key=len)[0]

            if primary_path:
                if primary_path not in endpoint_nodes:
                    endpoint_nodes[primary_path] = {
                        "path": primary_path,
                        "findings": [],
                        "max_risk": "INFO",
                        "total_tasks": 0,
                    }
                risk_order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0, "UNKNOWN": 0}
                endpoint_nodes[primary_path]["findings"].append({
                    "id": f.get("id", ""),
                    "name": f.get("name", "")[:80],
                    "riskLevel": f.get("riskLevel", ""),
                    "riskType": f.get("riskType", ""),
                    "confidence": f.get("confidence", ""),
                })
                cur_max = risk_order.get(endpoint_nodes[primary_path]["max_risk"], 0)
                new_r = risk_order.get(f.get("riskLevel", ""), 0)
                if new_r > cur_max:
                    endpoint_nodes[primary_path]["max_risk"] = f.get("riskLevel", "INFO")
                endpoint_nodes[primary_path]["total_tasks"] += task_counts.get(f.get("riskType", ""), 0)

        # 2) 체인 공격 → edge 생성
        chains = []
        for f in findings:
            if not _is_chain_finding(f):
                continue
            attack = f.get("attackScript", "") or ""
            steps = _extract_chain_steps(attack)
            if len(steps) < 2:
                continue

            # 체인의 구성 요소 finding 매칭
            components = []
            for step in steps:
                step_path = step["path"]
                # 이 step endpoint를 주요 대상으로 하는 개별 finding 찾기
                matched = None
                for other in findings:
                    if other.get("id") == f.get("id"):
                        continue
                    other_desc = (other.get("description", "") or "").split(".")[0]
                    if step_path in other_desc or step_path.replace("/{scenario_id}", "") in other.get("name", "").lower():
                        matched = {"id": other.get("id", ""), "name": other.get("name", "")[:60], "riskLevel": other.get("riskLevel", "")}
                        break
                components.append({**step, "component_finding": matched})

            # escalation 계산
            _level_score = {"CRITICAL": 9.0, "HIGH": 7.0, "MEDIUM": 5.0, "LOW": 3.0, "UNKNOWN": 0}
            comp_scores = [_level_score.get(c["component_finding"]["riskLevel"], 0)
                           for c in components if c.get("component_finding")]
            chain_score = float(f.get("riskScore", 0)) or _level_score.get(f.get("riskLevel", ""), 5)

            chains.append({
                "id": f.get("id", ""),
                "name": f.get("name", "")[:100],
                "riskLevel": f.get("riskLevel", ""),
                "riskScore": f.get("riskScore", ""),
                "steps": components,
                "escalation": {
                    "max_individual": max(comp_scores) if comp_scores else 0,
                    "chain_combined": chain_score,
                    "escalated": chain_score > (max(comp_scores) if comp_scores else 0),
                },
            })

        # 3) 체인 step간 edge
        edges = []
        for chain in chains:
            steps = chain["steps"]
            for i in range(len(steps) - 1):
                src_path = steps[i]["path"]
                dst_path = steps[i + 1]["path"]
                if src_path and dst_path and src_path != dst_path:
                    edges.append({
                        "source": src_path,
                        "target": dst_path,
                        "chain_id": chain["id"],
                        "chain_name": chain["name"],
                        "step_from": steps[i]["step"],
                        "step_to": steps[i + 1]["step"],
                        "method": steps[i + 1].get("method", ""),
                    })

        return jsonify({
            "ok": True,
            "nodes": list(endpoint_nodes.values()),
            "edges": edges,
            "chains": chains,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Chain Detail — 개별 체인 공격 상세 페이지
# ---------------------------------------------------------------------------

@security_insights_bp.route("/security/insights/chain/<chain_id>")
def chain_detail_page(chain_id):
    """체인 공격 상세 페이지."""
    return render_template("security_chain_detail.html", chain_id=chain_id)


@security_insights_bp.route("/api/security/insights/chain/<chain_id>")
def api_chain_detail(chain_id):
    """개별 체인 공격 상세: steps + component findings + escalation + attackScript."""
    import re
    try:
        findings = _get_latest_findings()
        if not findings:
            return jsonify({"ok": False, "error": "No findings"})

        target = None
        for f in findings:
            if f.get("id") == chain_id:
                target = f
                break

        if not target:
            return jsonify({"ok": False, "error": "Chain not found"})

        attack = target.get("attackScript", "") or ""
        steps = _extract_chain_steps(attack)

        # 구성 요소 finding 매칭
        components = []
        for step in steps:
            matched = None
            for other in findings:
                if other.get("id") == target.get("id"):
                    continue
                other_attack = other.get("attackScript", "") or ""
                other_desc = (other.get("description", "") or "").split(".")[0]
                if step["path"] in other_desc or step["path"] in other_attack[:200]:
                    matched = {
                        "id": other.get("id", ""),
                        "name": other.get("name", ""),
                        "riskLevel": other.get("riskLevel", ""),
                        "riskType": other.get("riskType", ""),
                        "description": (other.get("description", "") or "")[:200],
                    }
                    break
            components.append({**step, "component_finding": matched})

        # escalation
        _level_score = {"CRITICAL": 9.0, "HIGH": 7.0, "MEDIUM": 5.0, "LOW": 3.0, "UNKNOWN": 0}
        comp_scores = [_level_score.get(c["component_finding"]["riskLevel"], 0)
                       for c in components if c.get("component_finding")]
        chain_score = float(target.get("riskScore", 0)) or _level_score.get(target.get("riskLevel", ""), 5)

        return jsonify({
            "ok": True,
            "chain": {
                "id": target.get("id", ""),
                "name": target.get("name", ""),
                "riskLevel": target.get("riskLevel", ""),
                "riskType": target.get("riskType", ""),
                "riskScore": target.get("riskScore", ""),
                "description": target.get("description", ""),
                "attackScript": attack,
                "confidence": target.get("confidence", ""),
                "steps": components,
                "escalation": {
                    "max_individual": max(comp_scores) if comp_scores else 0,
                    "chain_combined": chain_score,
                    "escalated": chain_score > (max(comp_scores) if comp_scores else 0),
                },
            },
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API: Task Phase Timeline — stepName + category 기반 phase 분류
# ---------------------------------------------------------------------------

@security_insights_bp.route("/api/security/insights/task-timeline")
def api_task_timeline():
    """Task timeline: stepName(PREFLIGHT/STATIC_ANALYSIS/PENTEST/FINALIZING) + category 분류."""
    try:
        from routes_settings import _sa_client
        sec_spaces = _resolve_sec_spaces()
        if not sec_spaces:
            return jsonify({"ok": True, "phases": [], "tasks": []})

        sec_space_id = sec_spaces[0]["security_space_id"]
        sa = _sa_client(sec_space_id=sec_space_id)

        target_job_id = _req_job_id()

        resp = sa.list_pentests(agentSpaceId=sec_space_id)
        pentests = resp.get("pentestSummaries", [])
        if not pentests:
            return jsonify({"ok": True, "phases": [], "tasks": []})

        if target_job_id:
            job_id = target_job_id
        else:
            jobs_resp = sa.list_pentest_jobs_for_pentest(
                agentSpaceId=sec_space_id, pentestId=pentests[0]["pentestId"])
            completed = [j for j in jobs_resp.get("pentestJobSummaries", []) if j.get("status") == "COMPLETED"]
            if not completed:
                return jsonify({"ok": True, "phases": [], "tasks": []})
            job_id = completed[0]["pentestJobId"]

        # stepName별 tasks 수집
        phase_data = {}
        all_tasks = []
        for step in ["PREFLIGHT", "STATIC_ANALYSIS", "PENTEST", "FINALIZING"]:
            tasks = []
            next_token = None
            while True:
                kwargs = {"agentSpaceId": sec_space_id, "pentestJobId": job_id, "stepName": step, "maxResults": 100}
                if next_token:
                    kwargs["nextToken"] = next_token
                tasks_resp = sa.list_pentest_job_tasks(**kwargs)
                tasks.extend(tasks_resp.get("taskSummaries", []))
                next_token = tasks_resp.get("nextToken")
                if not next_token:
                    break
            phase_data[step] = len(tasks)
            for t in tasks:
                all_tasks.append({
                    "taskId": t.get("taskId", ""),
                    "title": t.get("title", ""),
                    "riskType": t.get("riskType", ""),
                    "status": t.get("executionStatus", ""),
                    "createdAt": str(t.get("createdAt", "")),
                    "updatedAt": str(t.get("updatedAt", "")),
                    "stepName": step,
                })

        # batch_get로 category 정보 추가 (최대 100개만 — UI 성능)
        task_ids = [t["taskId"] for t in all_tasks[:100]]
        details_map = {}
        for i in range(0, len(task_ids), 10):
            batch = task_ids[i:i + 10]
            detail_resp = sa.batch_get_pentest_job_tasks(agentSpaceId=sec_space_id, taskIds=batch)
            for d in detail_resp.get("tasks", []):
                cats = d.get("categories", [])
                primary = next((c["name"] for c in cats if c.get("isPrimary")), "")
                secondary = next((c["name"] for c in cats if not c.get("isPrimary")), "")
                details_map[d["taskId"]] = {
                    "primary_category": primary,
                    "secondary_category": secondary,
                    "description": (d.get("description", "") or "")[:200],
                }

        # task에 category 합치기 + architecture phase 분류
        # AWS Security Agent 아키텍처:
        #   PREFLIGHT → AUTH
        #   STATIC_ANALYSIS (scanning) → BASELINE (코드/엔드포인트 스캔)
        #   STATIC_ANALYSIS/VALIDATION → MANAGED (사전정의 검증 태스크)
        #   PENTEST/PLAN_GENERATION → GUIDED_PLAN (SA결과 기반 동적 계획)
        #   PENTEST/exec → GUIDED_EXEC (Plan에 따른 Swarm Worker 실행)
        #   PENTEST/VALIDATION → VALIDATION (최종 검증)
        #   PENTEST/CHAIN_ATTACK → CHAIN_ATTACK (체인 공격 발견)
        for t in all_tasks:
            detail = details_map.get(t["taskId"], {})
            t["primary_category"] = detail.get("primary_category", "")
            t["secondary_category"] = detail.get("secondary_category", "")
            t["description"] = detail.get("description", "")

            step = t.get("stepName", "")
            desc = t.get("description", "")
            scat = t.get("secondary_category", "")

            if step == "STATIC_ANALYSIS" and scat == "VALIDATOR":
                t["execution_type"] = "MANAGED_VALIDATION"
            elif step == "PENTEST":
                desc_lower = desc.lower()
                if "chain the " in desc_lower or "chaining " in desc_lower or "attack chain:" in desc_lower:
                    t["execution_type"] = "CHAIN_ATTACK"
                elif "Conduct comprehensive" in desc or "[all endpoints]" in desc:
                    t["execution_type"] = "PLAN_GENERATION"
                elif scat == "VALIDATOR" or desc.startswith("Validating "):
                    t["execution_type"] = "VALIDATION"
                else:
                    t["execution_type"] = "GUIDED_EXECUTION"
            else:
                t["execution_type"] = ""

        # 시간순 정렬
        all_tasks.sort(key=lambda t: t.get("createdAt", ""))

        return jsonify({
            "ok": True,
            "phases": phase_data,
            "tasks": all_tasks,
            "job_id": job_id,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
