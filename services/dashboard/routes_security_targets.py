"""
Security Targets Blueprint — Security Agent Space 생성/연결 (DevOps Space 기반).

DevOps Agent Space의 repo 정보를 이용해서:
1. 신규 Security Agent Space 생성 + SAST 활성화
2. 기존 Security Agent Space와 동일 repo로 연결
"""
import json
import time


from flask import Blueprint, render_template, jsonify, request
from botocore.config import Config as BotoConfig

from app_config import _boto_session, _CFG, _cfg_get, AWS_REGION

security_targets_bp = Blueprint("security_targets_bp", __name__)

# ---------------------------------------------------------------------------
# AWS clients
# ---------------------------------------------------------------------------

def _da_client(space_id=None):
    from app_config import _space_session
    session = _space_session(space_id) if space_id else _boto_session()
    return session.client("devops-agent", config=BotoConfig(read_timeout=30))


def _sa_client(space_id=None):
    from app_config import _space_session
    session = _space_session(space_id) if space_id else _boto_session()
    return session.client("securityagent", config=BotoConfig(read_timeout=120, connect_timeout=10))


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------

@security_targets_bp.route("/settings/targets")
def settings_targets_page():
    return render_template("settings_targets.html", cache_bust=int(time.time()))


# ---------------------------------------------------------------------------
# API: DevOps Agent Space 목록 + repo 정보
# ---------------------------------------------------------------------------

@security_targets_bp.route("/api/settings/security/spaces")
def api_list_spaces():
    """DevOps Agent Space 목록 + 각 Space의 GitHub repo 정보."""
    try:
        da = _da_client()
        resp = da.list_agent_spaces()
        spaces = []
        for s in resp.get("agentSpaces", []):
            space_id = s.get("agentSpaceId", "")
            da_for_space = _da_client(space_id)
            repo_info = _get_github_from_devops_space(da_for_space, space_id)
            spaces.append({
                "id": space_id,
                "name": s.get("name", ""),
                "repo": repo_info,
            })
        return jsonify({"ok": True, "spaces": spaces})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


def _get_github_from_devops_space(da_client, space_id):
    """DevOps Agent Space의 associations에서 GitHub repo 정보 추출."""
    try:
        resp = da_client.list_associations(agentSpaceId=space_id)
        for a in resp.get("associations", []):
            cfg = a.get("configuration") or {}
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg)
                except Exception:
                    continue
            gh = cfg.get("github")
            if gh:
                return {
                    "owner": gh.get("owner", ""),
                    "name": gh.get("repoName", ""),
                    "repo_id": gh.get("repoId", ""),
                }
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# API: Security Agent Space 목록 (기존 것들)
# ---------------------------------------------------------------------------

@security_targets_bp.route("/api/settings/security/agent-spaces")
def api_list_security_spaces():
    """기존 Security Agent Space 목록 + 연결된 repo 정보.

    ?account_id= 로 특정 계정의 Security Space만 조회 가능.
    """
    account_id = request.args.get("account_id", "").strip()
    try:
        if account_id:
            from app_config import _space_session
            session = _space_session(account_id=account_id)
            sa = session.client("securityagent", config=BotoConfig(read_timeout=120, connect_timeout=10))
        else:
            sa = _sa_client()
        resp = sa.list_agent_spaces()
        spaces = []
        for s in resp.get("agentSpaceSummaries", []):
            space_id = s.get("agentSpaceId", "")
            repo_info = _get_repo_from_security_space(sa, space_id)
            spaces.append({
                "id": space_id,
                "name": s.get("name", ""),
                "repo": repo_info,
            })
        return jsonify({"ok": True, "spaces": spaces})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


def _get_repo_from_security_space(sa_client, space_id):
    """Security Agent Space에 연결된 repo 정보."""
    try:
        integrations = sa_client.list_integrations()
        for integ in integrations.get("integrationSummaries", []):
            integ_id = integ.get("integrationId", "")
            try:
                res = sa_client.list_integrated_resources(
                    agentSpaceId=space_id, integrationId=integ_id,
                )
                for r in res.get("integratedResourceSummaries", []):
                    gh = r.get("resource", {}).get("githubRepository", {})
                    if gh:
                        return {
                            "owner": gh.get("owner", ""),
                            "name": gh.get("name", ""),
                            "repo_id": gh.get("providerResourceId", ""),
                            "integration_id": integ_id,
                            "leave_comments": r.get("capabilities", {}).get("github", {}).get("leaveComments", False),
                            "remediate_code": r.get("capabilities", {}).get("github", {}).get("remediateCode", False),
                        }
            except Exception:
                continue
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Helper: DevOps Space → 연결된 Security Agent Space(들) 조회 (1:N)
# ---------------------------------------------------------------------------

def _get_space_meta_item(devops_space_id):
    """DDB space_metadata 레코드 전체 조회 (캐시 없이 단순 get)."""
    from app_config import _boto_session, RUNS_TABLE
    session = _boto_session()
    tbl = session.resource("dynamodb").Table(RUNS_TABLE)
    resp = tbl.get_item(Key={"run_id": f"space-meta-{devops_space_id}", "record_type": "space_metadata"})
    return resp.get("Item") or {}


def _get_repo_from_ddb(devops_space_id, item=None):
    """DDB space_metadata.integrations에서 GitHub repo 정보 반환."""
    try:
        if item is None:
            item = _get_space_meta_item(devops_space_id)
        for integ in (item.get("integrations") or []):
            if integ.get("provider") == "github" and integ.get("repo"):
                parts = integ["repo"].split("/", 1)
                return {
                    "owner": parts[0] if len(parts) > 1 else "",
                    "name": parts[1] if len(parts) > 1 else integ["repo"],
                    "repo_id": integ.get("repo_id", ""),
                }
    except Exception:
        pass
    return None


def _get_security_links_from_ddb(devops_space_id, item=None):
    """DDB space_metadata에서 security_links 읽기."""
    try:
        if item is None:
            item = _get_space_meta_item(devops_space_id)
        return item.get("security_links") or []
    except Exception:
        return []


def _save_security_links_to_ddb(devops_space_id, links):
    """DDB space_metadata에 security_links 저장."""
    from app_config import _boto_session, RUNS_TABLE
    session = _boto_session()
    tbl = session.resource("dynamodb").Table(RUNS_TABLE)
    tbl.update_item(
        Key={"run_id": f"space-meta-{devops_space_id}", "record_type": "space_metadata"},
        UpdateExpression="SET security_links = :v",
        ExpressionAttributeValues={":v": links},
    )


def _find_security_spaces_for_devops(devops_space_id, item=None):
    """DevOps space_id → 연결된 Security Agent space 목록 반환.

    조회 순서:
      1) DDB space_metadata.security_links
      2) config.yaml fallback (하위 호환)

    Returns: [{"security_space_id", "name", "target_domain", "pentest_id"}, ...]
    """
    results = []

    # 1) DDB security_links
    links = _get_security_links_from_ddb(devops_space_id, item)
    for link in links:
        sid = link.get("security_space_id", "")
        if sid:
            results.append({
                "security_space_id": sid,
                "name": link.get("name", ""),
                "target_domain": link.get("target_domain", ""),
                "pentest_id": link.get("pentest_id", ""),
            })

    if results:
        return results

    # 2) Config.yaml fallback (하위 호환)
    sec_space_id = _cfg_get(_CFG, "security_agent.agent_space_id", "")
    if sec_space_id:
        results.append({
            "security_space_id": sec_space_id,
            "name": "default (config)",
            "target_domain": "",
            "pentest_id": _cfg_get(_CFG, "security_agent.pentest_id", ""),
        })

    return results


def _get_target_domain_for_space(sa_client, space_id):
    """Security Agent space의 pentest target domain 조회."""
    try:
        resp = sa_client.list_pentests(agentSpaceId=space_id)
        for p in resp.get("pentestSummaries", []):
            targets = p.get("targetDomains", [])
            if targets:
                return targets[0]
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# API: 매칭 확인 (DevOps Space → Security Space 연결 가능 여부)
# ---------------------------------------------------------------------------

@security_targets_bp.route("/api/settings/security/match/<devops_space_id>")
def api_match_space(devops_space_id):
    """DevOps Space에 연결된 Security Agent Space 조회. 1:N 지원."""
    try:
        # DDB 한 번 읽기로 repo + links 모두 해결
        item = _get_space_meta_item(devops_space_id)
        devops_repo = _get_repo_from_ddb(devops_space_id, item)
        matches = _find_security_spaces_for_devops(devops_space_id, item)

        if matches:
            sec_space_id = matches[0]["security_space_id"]
            account_id = ""
            for link in _get_security_links_from_ddb(devops_space_id, item):
                if link.get("security_space_id") == sec_space_id:
                    account_id = link.get("account_id", "")
                    break
            sec_repo = None
            try:
                if account_id:
                    from app_config import _space_session
                    session = _space_session(account_id=account_id)
                    sa = session.client("securityagent", config=BotoConfig(read_timeout=120, connect_timeout=10))
                else:
                    sa = _sa_client()
                sec_repo = _get_repo_from_security_space(sa, sec_space_id)
            except Exception:
                pass
            return jsonify({
                "ok": True,
                "match": {
                    "security_space_id": sec_space_id,
                    "security_space_name": matches[0].get("name", ""),
                    "target_domain": matches[0].get("target_domain", ""),
                    "repo": sec_repo,
                },
                "matches": matches,
                "devops_repo": devops_repo,
                "message": f"{len(matches)}개 Security Agent Space가 연결되어 있습니다.",
            })

        if not devops_repo:
            return jsonify({"ok": True, "match": None, "matches": [], "devops_repo": None,
                           "message": "DevOps Space에 GitHub repo가 연결되어 있지 않습니다."})

        return jsonify({
            "ok": True,
            "match": None,
            "matches": [],
            "devops_repo": devops_repo,
            "message": "연결 가능한 Security Agent Space가 없습니다. 신규 생성이 필요합니다.",
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API: Security Agent Space 신규 생성
# ---------------------------------------------------------------------------

@security_targets_bp.route("/api/settings/security/create", methods=["POST"])
def api_create_security_space():
    """DevOps Space 정보를 기반으로 Security Agent Space + SAST 생성."""
    data = request.json or {}
    devops_space_id = data.get("devops_space_id", "")
    if not devops_space_id:
        return jsonify({"ok": False, "error": "devops_space_id 필요"})

    try:
        da = _da_client(devops_space_id)
        sa = _sa_client()

        devops_repo = _get_github_from_devops_space(da, devops_space_id)
        if not devops_repo:
            return jsonify({"ok": False, "error": "DevOps Space에 GitHub repo 없음"})

        devops_space_resp = da.list_agent_spaces()
        devops_name = ""
        for s in devops_space_resp.get("agentSpaces", []):
            if s.get("agentSpaceId") == devops_space_id:
                devops_name = s.get("name", "")
                break

        space_name = f"{devops_name}-security" if devops_name else f"security-{devops_repo['name']}"

        # 1. Security Agent Space 생성
        create_resp = sa.create_agent_space(
            name=space_name,
            description=f"Security Agent for {devops_repo['owner']}/{devops_repo['name']}",
            codeReviewSettings={
                "controlsScanning": True,
                "generalPurposeScanning": True,
            },
        )
        new_space_id = create_resp.get("agentSpaceId", "")

        # 2. 기존 integration에 repo 등록 (integration이 이미 있으면 재사용)
        integrations = sa.list_integrations()
        integration_id = ""
        for integ in integrations.get("integrationSummaries", []):
            if integ.get("provider") == "GITHUB":
                integration_id = integ["integrationId"]
                break

        repo_registered = False
        if integration_id:
            try:
                sa.update_integrated_resources(
                    agentSpaceId=new_space_id,
                    integrationId=integration_id,
                    items=[{
                        "resource": {
                            "githubRepository": {
                                "name": devops_repo["name"],
                                "owner": devops_repo["owner"],
                            }
                        },
                        "capabilities": {"github": {"leaveComments": True, "remediateCode": True}},
                    }],
                )
                repo_registered = True
            except Exception as e:
                repo_registered = False

        # 3. DDB security_links 저장
        links = _get_security_links_from_ddb(devops_space_id)
        links.append({
            "security_space_id": new_space_id,
            "name": space_name,
            "linked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        _save_security_links_to_ddb(devops_space_id, links)

        return jsonify({
            "ok": True,
            "security_space_id": new_space_id,
            "space_name": space_name,
            "repo_registered": repo_registered,
            "integration_id": integration_id,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API: 기존 Security Agent Space 연결 (repo 매칭)
# ---------------------------------------------------------------------------

@security_targets_bp.route("/api/settings/security/link", methods=["POST"])
def api_link_space():
    """DevOps Space와 기존 Security Agent Space를 연결 (state에 기록)."""
    data = request.json or {}
    devops_space_id = data.get("devops_space_id", "")
    security_space_id = data.get("security_space_id", "")
    account_id = data.get("account_id", "")
    if not devops_space_id or not security_space_id:
        return jsonify({"ok": False, "error": "devops_space_id + security_space_id 필요"})

    try:
        # Security Space 이름 조회 (account_id가 있으면 해당 계정으로)
        sec_name = ""
        try:
            if account_id:
                from app_config import _space_session
                session = _space_session(account_id=account_id)
                sa = session.client("securityagent", config=BotoConfig(read_timeout=120, connect_timeout=10))
            else:
                sa = _sa_client()
            resp = sa.batch_get_agent_spaces(agentSpaceIds=[security_space_id])
            spaces = resp.get("agentSpaces", [])
            if spaces:
                sec_name = spaces[0].get("name", "")
        except Exception:
            pass

        links = _get_security_links_from_ddb(devops_space_id)
        already = any(l.get("security_space_id") == security_space_id for l in links)
        if not already:
            links.append({
                "security_space_id": security_space_id,
                "name": sec_name,
                "account_id": account_id,
                "linked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            _save_security_links_to_ddb(devops_space_id, links)

        return jsonify({"ok": True, "linked": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API: 연결 해제 (disconnect)
# ---------------------------------------------------------------------------

@security_targets_bp.route("/api/settings/security/disconnect", methods=["POST"])
def api_disconnect_space():
    """DevOps Space ↔ Security Agent Space 연결 해제."""
    data = request.json or {}
    devops_space_id = data.get("devops_space_id", "")
    security_space_id = data.get("security_space_id", "")
    if not devops_space_id or not security_space_id:
        return jsonify({"ok": False, "error": "devops_space_id + security_space_id 필요"})

    try:
        sa = _sa_client()

        # Security Agent Space에서 repo integration 제거 (items=[] 로 초기화)
        repo_removed = False
        remove_error = None
        try:
            integrations = sa.list_integrations()
            for integ in integrations.get("integrationSummaries", []):
                if integ.get("provider") == "GITHUB":
                    integ_id = integ["integrationId"]
                    res = sa.list_integrated_resources(
                        agentSpaceId=security_space_id, integrationId=integ_id,
                    )
                    if res.get("integratedResourceSummaries"):
                        sa.update_integrated_resources(
                            agentSpaceId=security_space_id,
                            integrationId=integ_id,
                            items=[],
                        )
                        repo_removed = True
                    break
        except Exception as e:
            remove_error = str(e)

        # DDB에서 link 제거
        links = _get_security_links_from_ddb(devops_space_id)
        links = [l for l in links if l.get("security_space_id") != security_space_id]
        _save_security_links_to_ddb(devops_space_id, links)

        return jsonify({"ok": True, "disconnected": True, "repo_removed": repo_removed,
                       "remove_error": remove_error})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API: 현재 연결 상태
# ---------------------------------------------------------------------------

@security_targets_bp.route("/api/settings/security/links")
def api_get_links():
    """현재 DevOps ↔ Security Space 연결 목록 (all spaces)."""
    from app_config import _boto_session, RUNS_TABLE
    all_links = []
    try:
        session = _boto_session()
        tbl = session.resource("dynamodb").Table(RUNS_TABLE)
        resp = tbl.scan(
            FilterExpression="begins_with(run_id, :prefix) AND record_type = :rt",
            ExpressionAttributeValues={":prefix": "space-meta-", ":rt": "space_metadata"},
            ProjectionExpression="run_id, security_links",
        )
        for item in resp.get("Items", []):
            space_id = item["run_id"].replace("space-meta-", "")
            for link in (item.get("security_links") or []):
                link["devops_space_id"] = space_id
                all_links.append(link)
    except Exception:
        pass
    return jsonify({"ok": True, "links": all_links})
