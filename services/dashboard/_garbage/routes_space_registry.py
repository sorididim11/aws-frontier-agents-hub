"""
Space registry/discover routes — list registered spaces, discover across accounts, register.

Split from routes_space.py for maintainability.
"""
import json

from flask import Blueprint, jsonify, request

from app_config import (
    _CFG, _cfg_get, AWS_REGION, RUNS_TABLE,
    _boto_session,
)

registry_bp = Blueprint("registry_bp", __name__)


@registry_bp.route("/api/spaces/registry")
def api_spaces_registry():
    """List all registered Space metadata (DDB + state file)."""
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


@registry_bp.route("/api/spaces/discover")
def api_spaces_discover():
    """Discover DevOps + Security Agent Spaces across all org accounts."""
    import boto3
    from account_registry import registry

    session = _boto_session()
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

    # 2. Collect all accounts to scan
    account_profiles = _CFG.get("aws", {}).get("account_profiles", {})

    accounts_to_scan = []
    for acct in registry.list_all():
        profile = acct.profile or account_profiles.get(acct.account_id, "")
        accounts_to_scan.append({"account_id": acct.account_id, "profile": profile})

    # Management profile로 Organizations API 시도
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
                    profile = (reg_acct.profile if reg_acct else "") or account_profiles.get(aid, "")
                    accounts_to_scan.append({"account_id": aid, "profile": profile, "type": "secondary"})
    except Exception as e:
        print(f"[Discovery] Organizations API unavailable: {e}", flush=True)

    # 3. Scan each account for DevOps + Security Spaces
    for acct_info in accounts_to_scan:
        acct_id = acct_info["account_id"]
        profile = acct_info["profile"]

        acct_result = {
            "account_id": acct_id,
            "profile": profile,
            "account_type": acct_info["type"],
            "devops_spaces": [],
            "security_spaces": [],
            "error": None,
        }

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

                conditions = []
                eligible = True

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
                assocs = []
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


@registry_bp.route("/api/spaces/discover/register", methods=["POST"])
def api_spaces_discover_register():
    """Register a discovered Space into the overview app."""
    import boto3
    from routes_space import _save_space_metadata

    data = request.get_json(silent=True) or {}
    space_id = data.get("space_id", "").strip()
    space_type = data.get("space_type", "").strip()
    account_id = data.get("account_id", "").strip()
    profile = data.get("profile", "").strip()

    if not space_id or not space_type:
        return jsonify({"ok": False, "error": "space_id, space_type 필수"}), 400

    session = _boto_session()
    warnings = []

    try:
        if profile:
            acct_session = boto3.Session(profile_name=profile, region_name=AWS_REGION)
        else:
            acct_session = session

        if space_type == "devops":
            da = acct_session.client("devops-agent")
            sp_resp = da.get_agent_space(agentSpaceId=space_id)
            sp = sp_resp.get("agentSpace", sp_resp)
            space_name = sp.get("name", "")
            app_name = data.get("app_name", "").strip()
            app_tag_value = data.get("app_tag_value", "").strip() or app_name

            # --- 조건 재검증 ---
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

            has_aws = False
            assocs = []
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
                        has_aws = True
                        break
            except Exception:
                pass
            if not has_aws:
                return jsonify({"ok": False, "error": "AWS 연결이 없습니다. Space에 AWS Association을 먼저 설정하세요."}), 400

            # role_arn 추출
            reg_role_arn = ""
            try:
                for a in assocs:
                    cfg = a.get("configuration", {})
                    if isinstance(cfg, str):
                        try:
                            cfg = json.loads(cfg)
                        except Exception:
                            cfg = {}
                    aws_cfg = cfg.get("aws") or cfg.get("sourceAws")
                    if aws_cfg and aws_cfg.get("assumableRoleArn"):
                        reg_role_arn = aws_cfg["assumableRoleArn"]
                        break
            except Exception:
                pass

            # 기존 CFn Stack 탐지 — Space 이름에서 '-agent-space' 제거한 짧은 이름으로 검색
            detected_stack = ""
            try:
                cfn_client = acct_session.client("cloudformation")
                short_name = space_name.replace("-agent-space", "")
                for candidate in [f"{short_name}-devops-agent", f"{space_name}-devops-agent"]:
                    try:
                        desc = cfn_client.describe_stacks(StackName=candidate)
                        st = desc["Stacks"][0]["StackStatus"]
                        if st not in ("DELETE_COMPLETE", "DELETE_IN_PROGRESS"):
                            detected_stack = candidate
                            break
                    except Exception:
                        continue
            except Exception:
                pass

            meta = {
                "name": space_name,
                "app_name": app_name,
                "app_tag_key": "App",
                "app_tag_value": app_tag_value,
                "account_id": account_id,
                "role_arn": reg_role_arn,
            }
            if detected_stack:
                meta["deploy_method"] = "cloudformation"
                meta["stack_name"] = detected_stack
                meta["deploy_status"] = "synced"

            _save_space_metadata(session, space_id, meta, managed=False)

            # canonical 앱 이름 저장
            if app_tag_value:
                from routes_arch import _save_app_name
                _save_app_name(space_id, app_tag_value)

            # 기본 스킬 자동 배포
            try:
                from skill_manager import SkillManager
                mgr = SkillManager()
                skill_result = mgr.ensure_default_skills(space_id)
                if skill_result["failed"]:
                    warnings.append(f"스킬 배포 실패: {skill_result['failed']}")
            except Exception as e:
                warnings.append(f"스킬 자동 배포 중 에러: {e}")

            # CFn import 옵션: 등록과 동시에 CFn 스택으로 관리
            cfn_import = data.get("cfn_import", False)
            if cfn_import:
                try:
                    from routes_space_cfn_import import api_space_cfn_import
                    import_resp = api_space_cfn_import(space_id)
                    import_data = import_resp.get_json()
                    if not import_data.get("ok"):
                        warnings.append(f"CFn import 실패: {import_data.get('error', '알 수 없는 오류')}")
                except Exception as e:
                    warnings.append(f"CFn import 실패: {e}")

        elif space_type == "security":
            link_to = data.get("link_to_devops", "").strip()
            if not link_to:
                return jsonify({"ok": False, "error": "연결할 DevOps Space를 선택해야 합니다."}), 400

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
