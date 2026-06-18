"""
Space discovery and registration routes.

Includes:
  - /api/spaces/registry — list all registered Space metadata
  - /api/spaces/discover — org-wide Agent Space discovery
  - /api/spaces/discover/register — register a discovered Space
"""
import json

from flask import jsonify, request

from app_config import (
    _CFG, _cfg_get, AWS_REGION, AGENT_SPACE_ID, RUNS_TABLE,
    _boto_session, _space_session, _get_or_create_session,
    _session_for_account_id,
)

from routes_space import space_bp
from routes_space.core import _get_space_meta, _save_space_metadata, _setup_event_channel


# ===================================================================
# Space Registry API (managed metadata)
# ===================================================================

@space_bp.route("/api/spaces/registry")
def api_spaces_registry():
    """List all registered Space metadata (DDB + state file).

    Returns both DevOps and Security spaces that are managed by this Frontier Agent Hub.
    """
    import os
    try:
        session = _boto_session()
        tbl = session.resource("dynamodb").Table(RUNS_TABLE)
        from boto3.dynamodb.conditions import Attr
        all_items = []
        scan_kwargs = {"FilterExpression": Attr("record_type").eq("space_metadata")}
        while True:
            resp = tbl.scan(**scan_kwargs)
            all_items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

        spaces = []
        seen_ids = set()
        for item in all_items:
            sid = item.get("space_id", "")
            if sid and sid not in seen_ids:
                seen_ids.add(sid)
                spaces.append({
                    "space_id": sid,
                    "space_name": item.get("space_name", ""),
                    "app_name": item.get("app_name", ""),
                    "app_tag_key": item.get("app_tag_key", ""),
                    "app_tag_value": item.get("app_tag_value", ""),
                    "account_id": item.get("account_id", ""),
                    "role_arn": item.get("role_arn", ""),
                    "security_agent_space_id": item.get("security_agent_space_id", ""),
                    "managed": item.get("managed", False),
                    "created_at": item.get("created_at", ""),
                    "type": "devops",
                })

        # Security links from DDB security_links field (1:N)
        for item in all_items:
            sid = item.get("space_id", "")
            for link in item.get("security_links", []):
                sec_id = link.get("security_space_id", "")
                if sec_id and sec_id not in seen_ids:
                    seen_ids.add(sec_id)
                    spaces.append({
                        "space_id": sec_id,
                        "space_name": link.get("name", ""),
                        "app_name": "",
                        "linked_devops_space_id": sid,
                        "created_at": link.get("linked_at", ""),
                        "type": "security",
                    })

        spaces.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return jsonify({"ok": True, "spaces": spaces})
    except Exception as e:
        return jsonify({"ok": False, "spaces": [], "error": str(e)})


# ===================================================================
# Discovery API — org 계정 전체에서 DevOps/Security Agent Space 탐색
# ===================================================================

@space_bp.route("/api/spaces/discover")
def api_spaces_discover():
    """Discover DevOps + Security Agent Spaces across all org accounts.

    Returns spaces grouped by account with registration eligibility evaluation.
    Uses AccountRegistry SSO profiles for cross-account access.
    """
    import boto3
    from account_registry import registry

    session = _boto_session()  # DDB 조회용 (App 인프라)
    results = []

    # 1. Get registered spaces (DDB) to detect already-registered
    registered_ids = set()
    registered_devops_ids = set()
    try:
        tbl = session.resource("dynamodb").Table(RUNS_TABLE)
        from boto3.dynamodb.conditions import Attr
        scan_kwargs = {"FilterExpression": Attr("record_type").eq("space_metadata")}
        all_meta = []
        while True:
            resp = tbl.scan(**scan_kwargs)
            all_meta.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        for item in all_meta:
            sid = item.get("space_id", "")
            if sid:
                registered_ids.add(sid)
                registered_devops_ids.add(sid)
            sec_sid = item.get("security_agent_space_id", "")
            if sec_sid:
                registered_ids.add(sec_sid)
            for link in item.get("security_links", []):
                sec_id = link.get("security_space_id", "")
                if sec_id:
                    registered_ids.add(sec_id)
    except Exception:
        pass

    # 2. Collect all accounts to scan (AccountRegistry + Organizations)
    accounts_to_scan = []
    for acct in registry.list_all():
        accounts_to_scan.append({"account_id": acct.account_id, "profile": acct.profile})

    # Management 프로파일로 Organizations API 시도
    mgmt_profile = _CFG.get("aws", {}).get("mgmt_profile", "")
    try:
        if mgmt_profile:
            org_session = boto3.Session(profile_name=mgmt_profile, region_name=AWS_REGION)
        else:
            org_session = session
        org_client = org_session.client("organizations")
        paginator = org_client.get_paginator("list_accounts")
        for page in paginator.paginate():
            for acct in page.get("Accounts", []):
                if acct.get("Status") != "ACTIVE":
                    continue
                aid = acct["Id"]
                if not any(a["account_id"] == aid for a in accounts_to_scan):
                    reg_acct = registry.get(aid)
                    profile = reg_acct.profile if reg_acct else ""
                    accounts_to_scan.append({"account_id": aid, "profile": profile})
    except Exception as e:
        print(f"[Discovery] Organizations API unavailable: {e}", flush=True)

    # 3. Scan each account for DevOps + Security Spaces
    for acct_info in accounts_to_scan:
        acct_id = acct_info["account_id"]
        profile = acct_info["profile"]

        acct_result = {
            "account_id": acct_id,
            "profile": profile,
            "account_type": acct_info.get("type", ""),
            "devops_spaces": [],
            "security_spaces": [],
            "error": None,
        }

        # Create session for this account
        try:
            if profile:
                acct_session = boto3.Session(profile_name=profile, region_name=AWS_REGION)
            else:
                acct_result["error"] = "SSO 프로필 미설정 — 접근 불가"
                results.append(acct_result)
                continue
        except Exception as e:
            acct_result["error"] = f"세션 생성 실패: {e}"
            results.append(acct_result)
            continue

        # DevOps Agent Spaces
        try:
            da = acct_session.client("devops-agent")
            da_resp = da.list_agent_spaces()
            for sp in da_resp.get("agentSpaces", []):
                space_id = sp.get("agentSpaceId", "")
                space_name = sp.get("name", "")

                # Evaluate conditions
                conditions = []
                eligible = True

                # Already registered — skip
                if space_id in registered_ids:
                    continue

                # Check App Tag
                has_tag = False
                tag_value = ""
                try:
                    space_arn = f"arn:aws:aidevops:{AWS_REGION}:{acct_id}:agentspace/{space_id}"
                    tags_resp = da.list_tags_for_resource(resourceArn=space_arn)
                    tags = tags_resp.get("tags", {})
                    if isinstance(tags, list):
                        tags = {t["key"]: t["value"] for t in tags if "key" in t}
                    for k, v in tags.items():
                        if k.lower() == "app" or k.lower() == "application":
                            has_tag = True
                            tag_value = v
                            break
                except Exception:
                    pass
                conditions.append({
                    "check": "app_tag",
                    "pass": has_tag,
                    "msg": f"App 태그: {tag_value}" if has_tag else "App 태그 미설정 — 토폴로지 분석 불가",
                })
                if not has_tag:
                    eligible = False

                # Check AWS Association
                has_aws_assoc = False
                try:
                    assocs = da.list_associations(agentSpaceId=space_id).get("associations", [])
                    for a in assocs:
                        cfg = a.get("configuration", {})
                        if isinstance(cfg, str):
                            try:
                                cfg = json.loads(cfg)
                            except Exception:
                                cfg = {}
                        if cfg.get("aws") or cfg.get("sourceAws"):
                            has_aws_assoc = True
                            break
                except Exception:
                    pass
                conditions.append({
                    "check": "aws_association",
                    "pass": has_aws_assoc,
                    "msg": "AWS 연결 있음" if has_aws_assoc else "AWS 연결 없음 — 리소스 접근 불가",
                })
                if not has_aws_assoc:
                    eligible = False

                # Check GitHub Integration
                has_github = False
                github_repo = ""
                try:
                    for a in assocs:
                        cfg = a.get("configuration", {})
                        if isinstance(cfg, str):
                            try:
                                cfg = json.loads(cfg)
                            except Exception:
                                cfg = {}
                        if cfg.get("github"):
                            has_github = True
                            github_repo = cfg["github"].get("owner", "") + "/" + cfg["github"].get("repoName", "")
                            break
                except Exception:
                    pass
                conditions.append({
                    "check": "github",
                    "pass": has_github,
                    "msg": f"GitHub: {github_repo}" if has_github else "GitHub 연결 없음 (선택사항)",
                    "warning": not has_github,
                })

                acct_result["devops_spaces"].append({
                    "space_id": space_id,
                    "name": space_name,
                    "status": "discovered",
                    "eligible": eligible,
                    "conditions": conditions,
                    "tag_value": tag_value,
                    "github_repo": github_repo,
                })
        except Exception as e:
            acct_result["devops_spaces"] = []
            if "UnrecognizedClient" not in str(e) and "InvalidIdentityToken" not in str(e):
                acct_result["error"] = (acct_result.get("error") or "") + f" DevOps: {e}"

        # Security Agent Spaces
        try:
            sa = acct_session.client("securityagent")
            sa_resp = sa.list_agent_spaces()
            for sp in sa_resp.get("agentSpaceSummaries", []):
                space_id = sp.get("agentSpaceId", "")
                space_name = sp.get("name", "")

                conditions = []
                eligible = True

                if space_id in registered_ids:
                    continue

                # Check GitHub Integration on Security Space
                has_github = False
                sec_repo = ""
                try:
                    integ_resp = sa.list_integrations()
                    for integ in integ_resp.get("integrationSummaries", []):
                        if integ.get("provider") == "GITHUB":
                            integ_id = integ["integrationId"]
                            res_resp = sa.list_integrated_resources(
                                agentSpaceId=space_id, integrationId=integ_id,
                            )
                            for r in res_resp.get("integratedResourceSummaries", []):
                                repo_info = r.get("resource", {}).get("githubRepository", {})
                                if repo_info:
                                    has_github = True
                                    sec_repo = repo_info.get("owner", "") + "/" + repo_info.get("name", "")
                                    break
                            break
                except Exception:
                    pass
                conditions.append({
                    "check": "github",
                    "pass": has_github,
                    "msg": f"GitHub: {sec_repo}" if has_github else "GitHub 연결 없음 — 리포 등록 필요",
                })
                if not has_github:
                    eligible = False

                acct_result["security_spaces"].append({
                    "space_id": space_id,
                    "name": space_name,
                    "status": "discovered",
                    "eligible": eligible,
                    "conditions": conditions,
                    "github_repo": sec_repo,
                })
        except Exception as e:
            acct_result["security_spaces"] = []
            if "UnrecognizedClient" not in str(e) and "UnknownServiceError" not in str(e):
                if not acct_result.get("error"):
                    acct_result["error"] = f"Security: {e}"

        results.append(acct_result)

    # Summary
    total_devops = sum(len(a["devops_spaces"]) for a in results)
    total_security = sum(len(a["security_spaces"]) for a in results)
    eligible_devops = sum(1 for a in results for s in a["devops_spaces"] if s.get("eligible"))
    eligible_security = sum(1 for a in results for s in a["security_spaces"] if s.get("eligible"))

    return jsonify({
        "ok": True,
        "accounts": results,
        "summary": {
            "accounts_scanned": len(accounts_to_scan),
            "total_devops": total_devops,
            "total_security": total_security,
            "eligible_devops": eligible_devops,
            "eligible_security": eligible_security,
        },
    })


@space_bp.route("/api/spaces/discover/register", methods=["POST"])
def api_spaces_discover_register():
    """Register a discovered Space into the Frontier Agent Hub.

    Body: {
      space_id: str,
      space_type: "devops" | "security",
      account_id: str,
      profile: str,
      app_name: str,        -- (devops only) from tag_value
      app_tag_value: str,   -- (devops only)
      link_to_devops: str,  -- (security only) devops space_id to link
    }
    """
    import boto3
    data = request.get_json(silent=True) or {}
    space_id = data.get("space_id", "").strip()
    space_type = data.get("space_type", "").strip()
    account_id = data.get("account_id", "").strip()
    profile = data.get("profile", "").strip()

    if not space_id or not space_type:
        return jsonify({"ok": False, "error": "space_id, space_type 필수"}), 400

    session = _boto_session()  # DDB용
    warnings = []

    try:
        if profile:
            import boto3 as _b3
            acct_session = _b3.Session(profile_name=profile, region_name=AWS_REGION)
        else:
            acct_session = _space_session(account_id=account_id) if account_id else _space_session()

        if space_type == "devops":
            da = acct_session.client("devops-agent")
            sp_resp = da.get_agent_space(agentSpaceId=space_id)
            sp = sp_resp.get("agentSpace", sp_resp)
            space_name = sp.get("name", "")
            space_description = sp.get("description", "")
            app_name = data.get("app_name", "").strip()
            app_tag_value = data.get("app_tag_value", "").strip() or app_name

            # --- 조건 재검증 ---
            # 1. App Tag 확인
            has_tag = False
            try:
                acct_id_resolved = account_id or acct_session.client("sts").get_caller_identity()["Account"]
                space_arn = f"arn:aws:aidevops:{AWS_REGION}:{acct_id_resolved}:agentspace/{space_id}"
                tags_resp = da.list_tags_for_resource(resourceArn=space_arn)
                tags = tags_resp.get("tags", {})
                if isinstance(tags, list):
                    tags = {t["key"]: t["value"] for t in tags if "key" in t}
                for k, v in tags.items():
                    if k.lower() in ("app", "application"):
                        has_tag = True
                        break
            except Exception:
                pass
            if not has_tag:
                return jsonify({"ok": False, "error": "App 태그가 설정되지 않았습니다. Space에 App 태그를 먼저 설정하세요."}), 400

            # 2. AWS Association 확인 + aws_config/integrations 수집
            aws_config = {}
            integrations = []
            svc_cache = {}
            try:
                svc_resp = da.list_services()
                for svc in svc_resp.get("services", []):
                    svc_cache[svc.get("serviceId", "")] = svc
            except Exception:
                pass
            try:
                assocs = da.list_associations(agentSpaceId=space_id).get("associations", [])
                for a in assocs:
                    cfg = a.get("configuration", {})
                    if isinstance(cfg, str):
                        try:
                            cfg = json.loads(cfg)
                        except Exception:
                            cfg = {}
                    cfg_key = next(iter(cfg), "") if cfg else ""
                    if cfg_key in ("aws", "sourceAws"):
                        details = cfg[cfg_key]
                        aws_config[cfg_key] = {
                            "account_id": details.get("accountId", ""),
                            "account_type": details.get("accountType", ""),
                            "role_arn": details.get("assumableRoleArn", ""),
                            "tags": details.get("tags", []),
                            "resources": details.get("resources", []),
                        }
                    elif cfg_key:
                        sid = a.get("serviceId", "")
                        svc = svc_cache.get(sid, {})
                        ig = {
                            "service_id": sid,
                            "association_id": a.get("associationId", ""),
                            "provider": cfg_key,
                            "name": svc.get("name", ""),
                        }
                        details = cfg.get(cfg_key, {})
                        if isinstance(details, dict):
                            if cfg_key == "github":
                                owner = details.get("owner", "")
                                repo_name = details.get("repoName", "")
                                ig["repo"] = f"{owner}/{repo_name}" if owner else repo_name
                            elif cfg_key == "gitlab":
                                ig["repo"] = details.get("projectPath", "")
                                ig["project_id"] = str(details.get("projectId", ""))
                        pc = a.get("privateConnectionName", "")
                        if pc:
                            ig["private_connection_name"] = pc
                        integrations.append(ig)
            except Exception:
                pass
            if not aws_config.get("aws") and not aws_config.get("sourceAws"):
                return jsonify({"ok": False, "error": "AWS 연결이 없습니다. Space에 AWS Association을 먼저 설정하세요."}), 400

            # --- 검증 통과 ---

            # EventChannel 없으면 자동 등록 (시뮬레이션 webhook용)
            has_event_channel = any(ig.get("provider") == "eventChannel" or ig.get("provider") == "eventchannel" for ig in integrations)
            if not has_event_channel:
                try:
                    ec_result = _setup_event_channel(da, acct_session, space_id)
                    integrations.append({
                        "service_id": ec_result["service_id"],
                        "association_id": ec_result["association_id"],
                        "provider": "eventChannel",
                        "name": "",
                    })
                except Exception as e:
                    warnings.append(f"EventChannel 자동 등록 실패: {e}")

            # 저장
            reg_role_arn = (aws_config.get("aws", {}).get("role_arn")
                           or aws_config.get("sourceAws", {}).get("role_arn", ""))

            _save_space_metadata(session, space_id, {
                "name": space_name,
                "description": space_description,
                "app_name": app_name,
                "app_tag_key": "App",
                "app_tag_value": app_tag_value,
                "account_id": account_id,
                "role_arn": reg_role_arn,
                "profile": profile,
                "managed": True,
                "aws_config": aws_config,
                "integrations": integrations,
            })

            # CFn import (기본 실행)
            if data.get("cfn_import", True):
                try:
                    from routes_space_cfn_import import api_space_cfn_import
                    import_resp = api_space_cfn_import(space_id)
                    import_data = import_resp.get_json()
                    if not import_data.get("ok"):
                        warnings.append(f"CFn import 실패: {import_data.get('error', '알 수 없는 오류')}")
                except Exception as e:
                    warnings.append(f"CFn import 실패: {e}")

            # 기본 스킬 자동 배포 (arch-discover, k8s-detail)
            try:
                from skill_manager import get_skill_manager
                skill_result = get_skill_manager().ensure_default_skills(space_id)
                if skill_result.get("failed"):
                    warnings.append(f"기본 스킬 배포 실패: {skill_result['failed']}")
            except Exception as e:
                warnings.append(f"스킬 자동 배포 중 에러: {e}")

        elif space_type == "security":
            link_to = data.get("link_to_devops", "").strip()
            if not link_to:
                return jsonify({"ok": False, "error": "연결할 DevOps Space를 선택해야 합니다."}), 400

            # --- 조건 재검증: GitHub 연결 확인 ---
            has_github = False
            try:
                sa = acct_session.client("securityagent")
                integ_resp = sa.list_integrations()
                for integ in integ_resp.get("integrationSummaries", []):
                    if integ.get("provider") == "GITHUB":
                        integ_id = integ["integrationId"]
                        res_resp = sa.list_integrated_resources(
                            agentSpaceId=space_id, integrationId=integ_id,
                        )
                        if res_resp.get("integratedResourceSummaries"):
                            has_github = True
                        break
            except Exception:
                pass
            if not has_github:
                return jsonify({"ok": False, "error": "GitHub 리포가 연결되지 않았습니다. Security Space에 리포를 먼저 등록하세요."}), 400

            # --- 검증 통과: 연결 저장 ---
            if not RUNS_TABLE:
                return jsonify({"ok": False, "error": "DynamoDB 테이블이 설정되지 않았습니다."}), 400
            tbl = session.resource("dynamodb").Table(RUNS_TABLE)
            try:
                tbl.update_item(
                    Key={"run_id": f"space-meta-{link_to}", "record_type": "space_metadata"},
                    UpdateExpression="SET security_agent_space_id = :sid",
                    ExpressionAttributeValues={":sid": space_id},
                )
            except Exception as e:
                warnings.append(f"DevOps Space 연결 업데이트 실패: {e}")
        else:
            return jsonify({"ok": False, "error": f"잘못된 space_type: {space_type}"}), 400

        return jsonify({"ok": True, "space_id": space_id, "warnings": warnings})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
