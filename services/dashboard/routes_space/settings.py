"""
Space settings routes (setup-webhook, settings GET/PUT, datasources).

Includes:
  - /api/spaces/<space_id>/setup-webhook
  - /api/spaces/<space_id>/settings (GET/PUT)
  - /api/spaces/<space_id>/datasources (POST/DELETE)
"""
import json

from flask import jsonify, request

from app_config import (
    _CFG, _cfg_get, AWS_REGION, RUNS_TABLE,
    _boto_session, _space_session, _session_for_account_id,
)

from routes_space import space_bp
from routes_space.core import _get_space_meta, _save_space_metadata, _setup_event_channel
from routes_space.accounts import _get_github_repo_id


@space_bp.route("/api/spaces/<space_id>/setup-webhook", methods=["POST"])
def api_setup_webhook(space_id):
    """기존 Space에 eventChannel webhook을 추가. Secret + DDB integrations 모두 업데이트."""
    try:
        from app_config import _profile_for_space
        profile = _profile_for_space(space_id)
        import boto3
        session = boto3.Session(profile_name=profile, region_name=AWS_REGION)
        client = session.client("devops-agent")

        # 이미 있는지 확인
        sm = session.client("secretsmanager")
        try:
            sm.get_secret_value(SecretId=f"webhook-{space_id}")
            return jsonify({"ok": True, "message": "이미 webhook이 설정되어 있습니다."})
        except sm.exceptions.ResourceNotFoundException:
            pass

        ec_result = _setup_event_channel(client, session, space_id)

        # DDB metadata의 integrations에 추가
        app_session = _boto_session()
        tbl = app_session.resource("dynamodb").Table(RUNS_TABLE)
        key = {"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"}
        resp = tbl.get_item(Key=key)
        item = resp.get("Item", {})
        integrations = item.get("integrations", []) or []
        integrations.append({
            "service_id": ec_result["service_id"],
            "association_id": ec_result["association_id"],
            "provider": "eventChannel",
            "name": "",
        })
        tbl.update_item(
            Key=key,
            UpdateExpression="SET integrations = :ig",
            ExpressionAttributeValues={":ig": integrations},
        )
        return jsonify({"ok": True, "message": "webhook 설정 완료", **ec_result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ===================================================================
# Space Settings API (위자드 수정 모드)
# ===================================================================

@space_bp.route("/api/spaces/<space_id>/settings")
def api_space_settings(space_id):
    """Get full settings for a space (edit mode pre-fill). DDB only."""
    try:
        session = _boto_session()
        meta = _get_space_meta(session, space_id)

        if not meta:
            return jsonify({"ok": False, "error": "미등록 Space입니다."}), 404

        return jsonify({
            "ok": True,
            "space_id": space_id,
            "name": meta.get("space_name", ""),
            "app_name": meta.get("app_name", ""),
            "app_tag_key": meta.get("app_tag_key", "App"),
            "app_tag_value": meta.get("app_tag_value", ""),
            "managed": meta.get("managed", False),
            "deploy_method": meta.get("deploy_method", ""),
            "stack_name": meta.get("stack_name", ""),
            "deploy_status": meta.get("deploy_status", "synced"),
            "aws_config": meta.get("aws_config", {}),
            "integrations": meta.get("integrations", []),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})



@space_bp.route("/api/spaces/<space_id>/settings", methods=["PUT"])
def api_space_settings_update(space_id):
    """Update space settings via CloudFormation update-stack.

    Flow: collect current state → generate CFn template → update-stack.
    Falls back to direct API if no CFn stack exists for this Space.
    """
    import yaml as _yaml

    data = request.get_json(force=True)
    try:
        session = _boto_session()
        steps = []
        name = data.get("name", "")

        # Space의 primary account 세션 (CFn 호출용)
        space_meta = _get_space_meta(session, space_id)
        acct_id = (space_meta or {}).get("account_id", "")
        acct_session = _session_for_account_id(acct_id) if acct_id else session

        # Check if a CFn stack manages this Space
        from datasource_manager import get_space_cfn_info
        cfn_managed, stack_name = get_space_cfn_info(session, space_id)
        if not cfn_managed and name:
            stack_name = f"{name}-devops-agent"
            try:
                cfn = acct_session.client("cloudformation")
                desc = cfn.describe_stacks(StackName=stack_name)
                status = desc["Stacks"][0]["StackStatus"]
                if status not in ("DELETE_COMPLETE", "DELETE_IN_PROGRESS"):
                    cfn_managed = True
            except Exception:
                pass

        if cfn_managed:
            # CFn path: regenerate template from current wizard data → update-stack
            cfn = acct_session.client("cloudformation")
            from routes_space.deploy import api_generate_cfn_internal
            gen_resp = api_generate_cfn_internal(data)
            if not gen_resp.get("ok"):
                return jsonify({"ok": False, "error": gen_resp.get("error", "CFn 템플릿 생성 실패")})

            try:
                cfn.update_stack(
                    StackName=stack_name,
                    TemplateBody=gen_resp["yaml"],
                    Capabilities=["CAPABILITY_NAMED_IAM"],
                    Tags=[
                        {"Key": data.get("app_tag_key", "App"), "Value": data.get("app_tag_value", name)},
                        {"Key": "auto-delete", "Value": "never"},
                        {"Key": "CreatedBy", "Value": "devops-agent-wizard"},
                    ],
                )
                steps.append({"step": "cfn_update_stack", "ok": True})
            except cfn.exceptions.ClientError as e:
                err_msg = str(e)
                if "No updates are to be performed" in err_msg:
                    steps.append({"step": "cfn_update_stack", "ok": True, "note": "변경 없음"})
                else:
                    steps.append({"step": "cfn_update_stack", "ok": False, "error": err_msg})
        else:
            return jsonify({
                "ok": False,
                "error": "이 Space는 CFn으로 관리되지 않습니다. 먼저 CFn Import를 실행하세요.",
                "action_required": "cfn_import",
            }), 400

        # DDB 메타데이터도 갱신
        _save_space_metadata(session, space_id, {
            "name": name,
            "app_name": data.get("app_name", ""),
            "app_tag_key": data.get("app_tag_key", "App"),
            "app_tag_value": data.get("app_tag_value", ""),
        })
        steps.append({"step": "ddb_metadata_update", "ok": True})

        return jsonify({"ok": True, "steps": steps, "cfn_managed": cfn_managed})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@space_bp.route("/api/spaces/<space_id>/datasources", methods=["DELETE"])
def api_space_datasource_delete(space_id):
    """Remove a data source (association) from a Space."""
    data = request.get_json(force=True)
    association_id = data.get("association_id", "")
    if not association_id:
        return jsonify({"ok": False, "error": "association_id 필요"})
    try:
        session = _space_session(space_id)
        client = session.client("devops-agent")
        client.disassociate_service(
            agentSpaceId=space_id,
            associationId=association_id,
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@space_bp.route("/api/spaces/<space_id>/datasources", methods=["POST"])
def api_space_datasource_add(space_id):
    """Add a data source (association) to an existing Space."""
    data = request.get_json(force=True)
    provider = data.get("provider", "")
    integration_id = data.get("integration_id", "")
    config = data.get("config", {})
    if not integration_id:
        return jsonify({"ok": False, "error": "integration_id 필요"})
    try:
        session = _space_session(space_id)
        client = session.client("devops-agent")

        if provider == "github" and config.get("repo"):
            repo = config["repo"]
            owner = repo.split("/")[0] if "/" in repo else ""
            repo_name = repo.split("/")[-1] if "/" in repo else repo
            repo_id = _get_github_repo_id(client, integration_id, owner, repo_name)
            assoc_cfg = {
                "github": {
                    "repoName": repo_name,
                    "repoId": repo_id,
                    "owner": owner,
                    "ownerType": "user",
                }
            }
        elif provider == "gitlab" and config.get("project_id"):
            assoc_cfg = {
                "gitlab": {
                    "projectId": config["project_id"],
                }
            }
        elif provider == "slack":
            assoc_cfg = {"slack": {}}
        elif provider == "mcpserversplunk":
            assoc_cfg = {"mcpserversplunk": config}
        else:
            assoc_cfg = {provider: config}

        resp = client.associate_service(
            agentSpaceId=space_id,
            serviceId=integration_id,
            configuration=assoc_cfg,
        )
        assoc_id = resp.get("associationId", resp.get("association", {}).get("associationId", ""))
        return jsonify({"ok": True, "association_id": assoc_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
