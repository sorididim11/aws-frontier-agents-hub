"""
Settings routes Blueprint — Security Agent dashboard integration.

Includes:
  - /settings page
  - /api/settings/security/* — SAST findings, Pentest control, Remediation
"""
import json
import os
import subprocess
import time

from flask import Blueprint, render_template, jsonify, request
from botocore.config import Config as BotoConfig

from app_config import _CFG, _cfg_get, _boto_session

settings_bp = Blueprint("settings_bp", __name__)

_github_token_cache = None


def _get_github_token_from_keychain():
    """macOS keychain에서 GitHub 토큰을 가져온다 (git credential-osxkeychain)."""
    global _github_token_cache
    if _github_token_cache:
        return _github_token_cache
    try:
        proc = subprocess.run(
            ["git", "credential-osxkeychain", "get"],
            input="protocol=https\nhost=github.com\n",
            capture_output=True, text=True, timeout=5,
        )
        for line in proc.stdout.splitlines():
            if line.startswith("password="):
                _github_token_cache = line[len("password="):]
                return _github_token_cache
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Security Agent config + client
# ---------------------------------------------------------------------------

def _security_agent_config():
    """config.yaml 우선, fallback → state 파일."""
    agent_space_id = _cfg_get(_CFG, "security_agent.agent_space_id")
    pentest_id = _cfg_get(_CFG, "security_agent.pentest_id")
    integration_id = _cfg_get(_CFG, "security_agent.integration_id")

    if not agent_space_id:
        state_path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "infrastructure", ".security-agent-state-devops-agent-test.json",
        )
        if os.path.exists(state_path):
            with open(state_path) as f:
                state = json.load(f)
            agent_space_id = state.get("agent_space_id", "")
            pentest_id = state.get("pentest_id", "")
            integration_id = state.get("integration_id", "")

    return {
        "agent_space_id": agent_space_id or "",
        "pentest_id": pentest_id or "",
        "integration_id": integration_id or "",
    }


def _sa_client(space_id=None, sec_space_id=None):
    """Security Agent client.

    Resolve 우선순위:
      1) sec_space_id → link의 account_id → 해당 계정 session
      2) space_id (DevOps space) → monitor account session
      3) request.args fallback
    """
    from app_config import _space_session, _boto_session as _bs, RUNS_TABLE

    # sec_space_id → link.account_id 직접 resolve
    sec_sid = sec_space_id or request.args.get("sec_space_id", "").strip()
    if sec_sid:
        account_id = _find_account_for_security_space(sec_sid)
        if account_id:
            session = _space_session(account_id=account_id)
        else:
            session = _bs()
        return session.client("securityagent", config=BotoConfig(read_timeout=120, connect_timeout=10))

    if space_id:
        session = _space_session(space_id)
    else:
        devops_space_id = request.args.get("space_id", "").strip()
        session = _space_session(devops_space_id) if devops_space_id else _bs()
    return session.client("securityagent", config=BotoConfig(read_timeout=120, connect_timeout=10))


_sec_account_cache = {}


def _find_account_for_security_space(sec_space_id):
    """sec_space_id → account_id resolve.

    1) DDB link의 account_id (명시적 저장된 경우)
    2) 캐시 hit
    3) 모든 등록 계정 순회하여 list_pentests 성공하는 계정 탐색
    """
    if sec_space_id in _sec_account_cache:
        return _sec_account_cache[sec_space_id]

    from app_config import _boto_session as _bs, RUNS_TABLE

    # 1) DDB link에서 account_id 조회
    try:
        tbl = _bs().resource("dynamodb").Table(RUNS_TABLE)
        resp = tbl.scan(
            FilterExpression="record_type = :rt",
            ExpressionAttributeValues={":rt": "space_metadata"},
            ProjectionExpression="run_id, security_links",
        )
        for item in resp.get("Items", []):
            for link in (item.get("security_links") or []):
                if link.get("security_space_id") == sec_space_id:
                    acct = link.get("account_id", "")
                    if acct:
                        _sec_account_cache[sec_space_id] = acct
                        return acct
    except Exception:
        pass

    # 2) 모든 등록 계정 순회
    try:
        from account_registry import registry
        for acct in registry.list_all():
            if not acct.profile:
                continue
            try:
                import boto3
                from app_config import AWS_REGION
                session = boto3.Session(profile_name=acct.profile, region_name=AWS_REGION)
                sa = session.client("securityagent", config=BotoConfig(read_timeout=10, connect_timeout=5))
                sa.list_pentests(agentSpaceId=sec_space_id)
                _sec_account_cache[sec_space_id] = acct.account_id
                return acct.account_id
            except Exception:
                continue
    except Exception:
        pass

    _sec_account_cache[sec_space_id] = ""
    return ""


def _find_devops_space_for_security(sec_space_id):
    """security_links를 역추적해서 devops_space_id 찾기."""
    from app_config import _boto_session as _bs, RUNS_TABLE
    try:
        tbl = _bs().resource("dynamodb").Table(RUNS_TABLE)
        resp = tbl.scan(
            FilterExpression="record_type = :rt",
            ExpressionAttributeValues={":rt": "space_metadata"},
            ProjectionExpression="run_id, security_links",
        )
        for item in resp.get("Items", []):
            for link in (item.get("security_links") or []):
                if link.get("security_space_id") == sec_space_id:
                    return item["run_id"].replace("space-meta-", "")
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------

@settings_bp.route("/settings")
def settings_page():
    cfg = _security_agent_config()
    return render_template("settings.html", cache_bust=int(time.time()), sec_space_id=cfg.get("agent_space_id", ""))


@settings_bp.route("/api/settings/infra")
def api_settings_infra():
    """인프라 설정 현황 반환."""
    return jsonify({
        "ok": True,
        "config": {
            "profile": _cfg_get(_CFG, "aws.profile", ""),
            "region": _cfg_get(_CFG, "aws.region", ""),
            "account_id": _cfg_get(_CFG, "aws.account_id", ""),
            "mgmt_profile": _cfg_get(_CFG, "aws.mgmt_profile", ""),
            "runs_table": _cfg_get(_CFG, "dynamodb.runs_table", ""),
            "events_table": _cfg_get(_CFG, "dynamodb.events_table", ""),
            "findings_table": _cfg_get(_CFG, "dynamodb.findings_table", ""),
            "expert_providers": _cfg_get(_CFG, "expert", {}).get("providers", {}) if isinstance(_cfg_get(_CFG, "expert", {}), dict) else {},
            "clusters": _cfg_get(_CFG, "clusters", {}),
        },
    })


# ---------------------------------------------------------------------------
# Code Review (SAST) — 등록 상태 + 리포 정보
# ---------------------------------------------------------------------------

@settings_bp.route("/api/settings/security/code-review")
def api_security_code_review():
    """코드 리뷰 설정 및 등록된 리포 상태 조회."""
    sec_override = request.args.get("sec_space_id", "").strip()
    cfg = _security_agent_config()
    space_id = sec_override or cfg["agent_space_id"]
    integration_id = cfg["integration_id"] if not sec_override else ""
    if not space_id:
        return jsonify({"ok": False, "error": "agent_space_id 미설정"})
    try:
        sa = _sa_client()
        repos = []
        # sec_override일 때 integration_id를 모르면 list_integrations로 찾기
        if sec_override and not integration_id:
            try:
                int_resp = sa.list_integrations()
                ints = int_resp.get("integrationSummaries", [])
                if ints:
                    integration_id = ints[0].get("integrationId", "")
            except Exception:
                pass
        if integration_id:
            resp = sa.list_integrated_resources(
                agentSpaceId=space_id,
                integrationId=integration_id,
            )
            for r in resp.get("integratedResourceSummaries", []):
                gh = r.get("resource", {}).get("githubRepository", {})
                caps = r.get("capabilities", {}).get("github", {})
                if gh:
                    repos.append({
                        "owner": gh.get("owner", ""),
                        "name": gh.get("name", ""),
                        "leaveComments": caps.get("leaveComments", False),
                        "remediateCode": caps.get("remediateCode", False),
                    })
        return jsonify({"ok": True, "repos": repos, "integrationId": integration_id})
    except Exception as e:
        return jsonify({"ok": False, "repos": [], "error": str(e)})


# ---------------------------------------------------------------------------
# Pentest
# ---------------------------------------------------------------------------

@settings_bp.route("/api/settings/security/pentest")
def api_security_pentest():
    sec_override = request.args.get("sec_space_id", "").strip()
    cfg = _security_agent_config()
    space_id = sec_override or cfg["agent_space_id"]
    if not space_id:
        return jsonify({"ok": False, "error": "agent_space_id 미설정"})
    try:
        sa = _sa_client()
        resp = sa.list_pentests(agentSpaceId=space_id)
        pentests = resp.get("pentestSummaries", [])

        pentest_info = None
        for p in pentests:
            if p.get("pentestId") == cfg["pentest_id"]:
                pentest_info = p
                break
        if not pentest_info and pentests:
            pentest_info = pentests[0]

        jobs = []
        if pentest_info:
            try:
                jobs_resp = sa.list_pentest_jobs_for_pentest(
                    agentSpaceId=space_id,
                    pentestId=pentest_info["pentestId"],
                )
                for j in jobs_resp.get("pentestJobSummaries", []):
                    job_entry = {
                        "jobId": j.get("pentestJobId", ""),
                        "status": j.get("status", ""),
                        "startedAt": str(j.get("startedAt", "")),
                        "completedAt": str(j.get("completedAt", "")),
                    }
                    if j.get("status") == "COMPLETED":
                        try:
                            fr = sa.list_findings(
                                agentSpaceId=space_id,
                                pentestJobId=j["pentestJobId"],
                            )
                            job_entry["findingsCount"] = len(fr.get("findingsSummaries", []))
                        except Exception:
                            pass
                    jobs.append(job_entry)
            except Exception:
                pass

        return jsonify({
            "ok": True,
            "pentest": {
                "pentestId": pentest_info.get("pentestId", "") if pentest_info else "",
                "title": pentest_info.get("title", "") if pentest_info else "",
                "status": pentest_info.get("status", "") if pentest_info else "",
            },
            "jobs": jobs,
            "config": {"agent_space_id": space_id, "pentest_id": cfg.get("pentest_id", "")},
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@settings_bp.route("/api/settings/security/pentest/run", methods=["POST"])
def api_security_pentest_run():
    sec_override = request.args.get("sec_space_id", "").strip()
    cfg = _security_agent_config()
    space_id = sec_override or cfg["agent_space_id"]
    if not space_id:
        return jsonify({"ok": False, "error": "pentest 설정 미완료"})
    try:
        sa = _sa_client()

        # pentest_id 결정: config에 있으면 사용, 아니면 list에서 첫번째
        pentest_id = cfg["pentest_id"] if not sec_override else ""
        if not pentest_id:
            resp = sa.list_pentests(agentSpaceId=space_id)
            pentests = resp.get("pentestSummaries", [])
            if not pentests:
                return jsonify({"ok": False, "error": "해당 space에 pentest가 없습니다"})
            pentest_id = pentests[0]["pentestId"]

        # pentest에 리포 연결 여부 확인 → 없으면 자동 연결
        try:
            pt_detail = sa.batch_get_pentests(agentSpaceId=space_id, pentestIds=[pentest_id])
            pt = pt_detail.get("pentests", [{}])[0]
            if not pt.get("assets", {}).get("integratedRepositories"):
                res_resp = sa.list_integrated_resources(agentSpaceId=space_id)
                repos = res_resp.get("integratedResourceSummaries", [])
                if repos:
                    r = repos[0]
                    gh = r.get("resource", {}).get("githubRepository", {})
                    sa.update_pentest(
                        agentSpaceId=space_id, pentestId=pentest_id,
                        assets={
                            "endpoints": pt.get("assets", {}).get("endpoints", []),
                            "actors": pt.get("assets", {}).get("actors", []),
                            "documents": pt.get("assets", {}).get("documents", []),
                            "sourceCode": pt.get("assets", {}).get("sourceCode", []),
                            "integratedRepositories": [{
                                "integrationId": r["integrationId"],
                                "providerResourceId": gh.get("providerResourceId", ""),
                            }],
                        })
        except Exception:
            pass

        # 이미 실행 중인 job이 있으면 중복 실행 방지
        jobs_resp = sa.list_pentest_jobs_for_pentest(
            agentSpaceId=space_id,
            pentestId=pentest_id,
        )
        active = [j for j in jobs_resp.get("pentestJobSummaries", [])
                  if j.get("status") == "IN_PROGRESS"]
        if active:
            return jsonify({
                "ok": False,
                "error": "이미 실행 중인 Job이 있습니다",
                "activeJobId": active[0]["pentestJobId"],
            })

        resp = sa.start_pentest_job(
            agentSpaceId=space_id,
            pentestId=pentest_id,
        )
        return jsonify({
            "ok": True,
            "jobId": resp["pentestJobId"],
            "status": resp.get("status", "IN_PROGRESS"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@settings_bp.route("/api/settings/security/pentest/job/<job_id>")
def api_security_pentest_job(job_id):
    sec_override = request.args.get("sec_space_id", "").strip()
    cfg = _security_agent_config()
    space_id = sec_override or cfg["agent_space_id"]
    try:
        sa = _sa_client()
        jobs_resp = sa.batch_get_pentest_jobs(
            agentSpaceId=space_id,
            pentestJobIds=[job_id],
        )
        job = jobs_resp["pentestJobs"][0] if jobs_resp.get("pentestJobs") else {}

        findings = []
        if job.get("status") == "COMPLETED":
            findings_resp = sa.list_findings(
                agentSpaceId=space_id,
                pentestJobId=job_id,
            )
            summaries = findings_resp.get("findingsSummaries", [])
            finding_ids = [f["findingId"] for f in summaries]
            details_map = {}
            for i in range(0, len(finding_ids), 10):
                batch = finding_ids[i:i+10]
                detail_resp = sa.batch_get_findings(
                    agentSpaceId=space_id,
                    findingIds=batch,
                )
                for d in detail_resp.get("findings", []):
                    details_map[d["findingId"]] = d

            for f in summaries:
                fid = f.get("findingId", "")
                detail = details_map.get(fid, {})
                crt = detail.get("codeRemediationTask", {})
                pr_link = ""
                rem_status = crt.get("status", "")
                if crt.get("taskDetails"):
                    pr_link = crt["taskDetails"][0].get("pullRequestLink", "")
                findings.append({
                    "id": fid,
                    "name": detail.get("name") or f.get("name", ""),
                    "riskType": f.get("riskType", ""),
                    "riskLevel": f.get("riskLevel", ""),
                    "confidence": f.get("confidence", ""),
                    "description": detail.get("description", ""),
                    "attackScript": detail.get("attackScript", ""),
                    "status": f.get("status", ""),
                    "remediationStatus": rem_status,
                    "prLink": pr_link,
                })

        steps = [{
            "name": s.get("name", ""),
            "status": s.get("status", "NOT_STARTED"),
        } for s in job.get("steps", [])]

        error_info = None
        if job.get("errorInformation"):
            error_info = {
                "code": job["errorInformation"].get("code", ""),
                "message": job["errorInformation"].get("message", ""),
            }

        return jsonify({
            "ok": True,
            "job": {
                "jobId": job_id,
                "status": job.get("status", "UNKNOWN"),
                "startedAt": str(job.get("startedAt", "")),
                "completedAt": str(job.get("completedAt", "")),
                "steps": steps,
                "error": error_info,
            },
            "findings": findings,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Pentest Job Tasks — 멀티스텝 시나리오 타임라인
# ---------------------------------------------------------------------------

@settings_bp.route("/api/settings/security/pentest/job/<job_id>/tasks")
def api_security_pentest_tasks(job_id):
    """Pentest job의 실행 태스크(멀티스텝 시나리오) 목록."""
    sec_override = request.args.get("sec_space_id", "").strip()
    cfg = _security_agent_config()
    space_id = sec_override or cfg["agent_space_id"]
    try:
        sa = _sa_client()
        tasks = []
        next_token = None
        while True:
            kwargs = {"agentSpaceId": space_id, "pentestJobId": job_id}
            if next_token:
                kwargs["nextToken"] = next_token
            resp = sa.list_pentest_job_tasks(**kwargs)
            tasks.extend(resp.get("taskSummaries", []))
            next_token = resp.get("nextToken")
            if not next_token:
                break

        task_ids = [t["taskId"] for t in tasks]
        details = []
        if task_ids:
            for i in range(0, len(task_ids), 10):
                batch = task_ids[i:i+10]
                detail_resp = sa.batch_get_pentest_job_tasks(
                    agentSpaceId=cfg["agent_space_id"], taskIds=batch
                )
                details.extend(detail_resp.get("tasks", []))

        result = []
        for d in details:
            logs_loc = d.get("logsLocation", {})
            cw_log = logs_loc.get("cloudWatchLog", {})
            result.append({
                "taskId": d.get("taskId", ""),
                "title": d.get("title", ""),
                "description": d.get("description", ""),
                "riskType": d.get("riskType", ""),
                "category": d.get("categories", [{}])[0].get("name", "") if d.get("categories") else "",
                "endpoint": d.get("targetEndpoint", {}).get("uri", ""),
                "status": d.get("executionStatus", ""),
                "createdAt": str(d.get("createdAt", "")),
                "updatedAt": str(d.get("updatedAt", "")),
                "logGroup": cw_log.get("logGroup", ""),
                "logStream": cw_log.get("logStream", ""),
            })

        result.sort(key=lambda x: x["createdAt"])
        return jsonify({"ok": True, "tasks": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Target Domain — 프라이빗 도메인 설정 + 검증 상태
# ---------------------------------------------------------------------------

@settings_bp.route("/api/settings/security/target-domain")
def api_security_target_domain():
    """이 앱의 pentest 대상 도메인만 표시."""
    sec_override = request.args.get("sec_space_id", "").strip()
    cfg = _security_agent_config()
    space_id = sec_override or cfg["agent_space_id"]
    pentest_id = cfg["pentest_id"] if not sec_override else ""
    try:
        sa = _sa_client()

        # sec_override일 때 pentest_id를 모르면 찾기
        if sec_override and not pentest_id:
            try:
                resp = sa.list_pentests(agentSpaceId=space_id)
                pentests = resp.get("pentestSummaries", [])
                if pentests:
                    pentest_id = pentests[0]["pentestId"]
            except Exception:
                pass

        # pentest endpoint에서 이 앱의 도메인 추출
        my_domains = set()
        if space_id and pentest_id:
            try:
                pt_resp = sa.batch_get_pentests(
                    agentSpaceId=space_id,
                    pentestIds=[pentest_id],
                )
                for p in pt_resp.get("pentests", []):
                    for ep in p.get("assets", {}).get("endpoints", []):
                        uri = ep.get("uri", "")
                        if uri:
                            import re
                            m = re.search(r'https?://([^/:]+)', uri)
                            if m:
                                my_domains.add(m.group(1))
            except Exception:
                pass

        resp = sa.list_target_domains()
        domains = []
        for td in resp.get("targetDomainSummaries", []):
            domain_name = td.get("domainName", "")
            if my_domains and domain_name not in my_domains:
                continue
            domains.append({
                "id": td.get("targetDomainId", ""),
                "domain": domain_name,
                "status": td.get("verificationStatus", "UNKNOWN"),
            })

        # Route53 private zone info
        zone_info = None
        zone_id = _cfg_get(_CFG, "security_agent.private_zone_id")
        if zone_id:
            try:
                session = _boto_session()
                r53 = session.client("route53")
                zone_resp = r53.get_hosted_zone(Id=zone_id)
                zone_info = {
                    "zoneId": zone_id,
                    "name": zone_resp["HostedZone"]["Name"].rstrip("."),
                    "vpcs": [v["VPCId"] for v in zone_resp.get("VPCs", [])],
                }
            except Exception:
                pass

        return jsonify({"ok": True, "domains": domains, "zone": zone_info})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Remediation
# ---------------------------------------------------------------------------

@settings_bp.route("/api/settings/security/findings/<finding_id>/remediate", methods=["POST"])
def api_security_remediate(finding_id):
    """Start code remediation for a finding."""
    sec_override = request.args.get("sec_space_id", "").strip()
    cfg = _security_agent_config()
    space_id = sec_override or cfg["agent_space_id"]
    data = request.json or {}
    pentest_job_id = data.get("pentest_job_id", "")
    try:
        sa = _sa_client()
        # pentest_job_id 미제공 시 최신 COMPLETED job 자동 조회
        if not pentest_job_id:
            resp = sa.list_pentests(agentSpaceId=space_id)
            for p in resp.get("pentestSummaries", []):
                jobs_resp = sa.list_pentest_jobs_for_pentest(
                    agentSpaceId=space_id, pentestId=p["pentestId"])
                for j in jobs_resp.get("pentestJobSummaries", []):
                    if j.get("status") == "COMPLETED":
                        pentest_job_id = j["pentestJobId"]
                        break
                if pentest_job_id:
                    break
        if not pentest_job_id:
            return jsonify({"ok": False, "error": "완료된 pentest job이 없습니다."})
        try:
            sa.start_code_remediation(
                agentSpaceId=space_id,
                pentestJobId=pentest_job_id,
                findingIds=[finding_id],
            )
        except sa.exceptions.ValidationException as ve:
            err_msg = str(ve)
            if "No repositories associated" in err_msg:
                return jsonify({"ok": False, "error": "이 조사에 리포지토리가 연결되어 있지 않습니다. 새 조사를 실행하면 자동으로 연결됩니다."})
            raise
        return jsonify({"ok": True, "status": "STARTED"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@settings_bp.route("/api/settings/security/findings/<finding_id>/status")
def api_security_finding_status(finding_id):
    """Get finding detail including codeRemediationTask status."""
    cfg = _security_agent_config()
    try:
        sa = _sa_client()
        resp = sa.batch_get_findings(
            agentSpaceId=cfg["agent_space_id"],
            findingIds=[finding_id],
        )
        findings = resp.get("findings", [])
        if not findings:
            return jsonify({"ok": False, "error": "Finding not found"})
        f = findings[0]
        crt = f.get("codeRemediationTask", {})
        return jsonify({
            "ok": True,
            "finding_id": finding_id,
            "name": f.get("name", ""),
            "status": f.get("status", ""),
            "remediation": {
                "status": crt.get("status", ""),
                "status_reason": crt.get("statusReason", ""),
                "task_details": [
                    {
                        "repo_name": td.get("repoName", ""),
                        "code_diff_link": td.get("codeDiffLink", ""),
                        "pull_request_link": td.get("pullRequestLink", ""),
                    }
                    for td in (crt.get("taskDetails") or [])
                ],
            },
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# GitHub PR Diff — before/after 코드 변경 조회
# ---------------------------------------------------------------------------

@settings_bp.route("/api/settings/security/pr-diff")
def api_security_pr_diff():
    """Fetch PR body + file diffs from GitHub API."""
    pr_url = request.args.get("pr_url", "")
    if not pr_url:
        return jsonify({"ok": False, "error": "pr_url required"})

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        token = _get_github_token_from_keychain()
    if not token:
        return jsonify({"ok": False, "error": "GitHub 토큰을 찾을 수 없습니다",
                       "pr_url": pr_url, "fallback": True})

    import re
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_url)
    if not m:
        return jsonify({"ok": False, "error": "Invalid PR URL format"})

    owner, repo, pr_number = m.group(1), m.group(2), m.group(3)

    try:
        import urllib.request

        def _gh_get(path):
            url = f"https://api.github.com/repos/{owner}/{repo}/{path}"
            req = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {token}",
                "User-Agent": "devops-dashboard",
            })
            return json.loads(urllib.request.urlopen(req, timeout=15).read())

        pr_data = _gh_get(f"pulls/{pr_number}")
        files_data = _gh_get(f"pulls/{pr_number}/files")

        diff_files = []
        for f in files_data:
            diff_files.append({
                "filename": f.get("filename", ""),
                "status": f.get("status", ""),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "patch": f.get("patch", ""),
            })

        return jsonify({
            "ok": True,
            "pr_url": pr_url,
            "pr_number": int(pr_number),
            "owner": owner,
            "repo": repo,
            "title": pr_data.get("title", ""),
            "body": pr_data.get("body", ""),
            "state": pr_data.get("state", ""),
            "branch": pr_data.get("head", {}).get("ref", ""),
            "files": diff_files,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "pr_url": pr_url, "fallback": True})


# ---------------------------------------------------------------------------
# Attack Logs (CloudWatch)
# ---------------------------------------------------------------------------

@settings_bp.route("/api/settings/security/task/<task_id>/logs")
def api_security_task_logs(task_id):
    """Fetch CloudWatch attack logs for a pentest task — parsed into interactions."""
    cfg = _security_agent_config()

    log_group = request.args.get("log_group", "")
    log_stream = request.args.get("log_stream", "")

    if not log_group or not log_stream:
        return jsonify({"ok": False, "error": "log_group and log_stream required"})

    try:
        session = _boto_session()
        logs_client = session.client("logs", region_name="us-east-1")

        all_events = []
        kwargs = {
            "logGroupName": log_group,
            "logStreamName": log_stream,
            "startFromHead": True,
            "limit": 200,
        }
        resp = logs_client.get_log_events(**kwargs)
        all_events.extend(resp.get("events", []))

        interactions = []
        for ev in all_events:
            try:
                msg = json.loads(ev["message"])
            except Exception:
                continue
            num = msg.get("interaction_number", 0)
            entry = {"num": num, "timestamp": ev.get("timestamp", 0)}

            if "response" in msg:
                content = msg["response"].get("content", [])
                texts = []
                tools = []
                for c in content:
                    if c.get("type") == "text":
                        texts.append(c["text"])
                    elif c.get("type") == "tool_use":
                        inp = c.get("input", {})
                        tools.append({
                            "name": c.get("name", ""),
                            "command": inp.get("command", inp.get("code", inp.get("url", "")))[:500],
                        })
                entry["type"] = "agent"
                entry["text"] = "\n".join(texts)[:2000]
                entry["tools"] = tools

            elif "request" in msg:
                messages = msg["request"].get("messages", [])
                results = []
                if messages:
                    last_msg = messages[-1]
                    for c in last_msg.get("content", []):
                        if c.get("type") == "tool_result":
                            results.append(c.get("content", "")[:1000])
                entry["type"] = "result"
                entry["results"] = results

            else:
                continue

            interactions.append(entry)

        return jsonify({"ok": True, "interactions": interactions, "total": len(interactions)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
