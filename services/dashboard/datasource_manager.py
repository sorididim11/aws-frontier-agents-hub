"""
Datasource Manager — Provider registry, normalization, association sync, deploy state, CRUD endpoints.

Single source of truth for datasource lifecycle:
  1. PROVIDER_REGISTRY: 확장 가능한 provider 타입 정의
  2. normalize/build: DDB↔AWS 스키마 변환
  3. sync_associations: DDB intent → AWS deployed state 동기화
  4. Deploy state: settings_version, deploy_status 추적
  5. Blueprint endpoints: add/delete/backfill/retry/sync/rollback
"""
import time

from flask import Blueprint, jsonify, request

from app_config import (
    AWS_REGION, RUNS_TABLE,
    _boto_session,
)

datasource_bp = Blueprint("datasource_bp", __name__)


# ===================================================================
# Provider Registry
# ===================================================================

PROVIDER_REGISTRY = {
    "gitlab": {
        "cfn_config_key": "GitLab",
        "cfn_resource_prefix": "GitlabAssociation",
        "supports_private_connection": True,
        "supports_multiple": True,
        "required_fields": ["project_id", "repo"],
    },
    "github": {
        "cfn_config_key": "GitHub",
        "cfn_resource_prefix": "GitHubAssociation",
        "supports_private_connection": True,
        "supports_multiple": False,
        "required_fields": ["repo"],
    },
    "mcpserversplunk": {
        "cfn_config_key": "MCPServerSplunk",
        "cfn_resource_prefix": "SplunkAssociation",
        "supports_private_connection": False,
        "supports_multiple": True,
        "required_fields": [],
    },
    "slack": {
        "cfn_config_key": "Slack",
        "cfn_resource_prefix": "SlackAssociation",
        "supports_private_connection": False,
        "supports_multiple": False,
        "required_fields": [],
    },
}


# ===================================================================
# Schema normalization
# ===================================================================

def normalize_integration(raw, association=None):
    """어떤 소스에서 온 integration이든 정규 flat 스키마로 변환."""
    provider = raw.get("provider", "").lower()
    service_id = raw.get("service_id") or raw.get("integration_id", "")

    result = {
        "provider": provider,
        "service_id": service_id,
        "integration_id": service_id,
        "association_id": raw.get("association_id", ""),
        "target_url": raw.get("target_url", ""),
        "project_id": str(raw.get("project_id", "")),
        "repo": raw.get("repo", ""),
        "private_connection_name": raw.get("private_connection_name", ""),
        "name": raw.get("name", ""),
    }

    # Legacy nested config → flat fields
    config = raw.get("config", {})
    if config and isinstance(config, dict):
        if provider == "gitlab":
            result["target_url"] = result["target_url"] or config.get("targetUrl", "")
            result["project_id"] = result["project_id"] or str(config.get("projectId", ""))
            result["repo"] = result["repo"] or config.get("projectPath", "")
        elif provider == "github":
            if not result["repo"]:
                owner = config.get("owner", "")
                repo_name = config.get("repoName", "")
                result["repo"] = f"{owner}/{repo_name}" if owner else repo_name

    # Supplement from AWS association data
    if association:
        result["association_id"] = result["association_id"] or association.get("associationId", "")
        if not result["private_connection_name"]:
            result["private_connection_name"] = association.get("privateConnectionName", "")

    return result


def build_association_config(ds):
    """Provider별 AWS associate_service configuration dict 생성."""
    provider = ds.get("provider", "")
    if provider == "gitlab":
        return {"gitlab": {
            "projectId": ds.get("project_id", ""),
            "projectPath": ds.get("repo", ""),
        }}
    elif provider == "github":
        parts = ds.get("repo", "").split("/", 1)
        return {"github": {
            "owner": parts[0] if parts else "",
            "repoName": parts[1] if len(parts) > 1 else "",
            "repoId": ds.get("repo_id", ""),
            "ownerType": "user",
        }}
    elif provider == "mcpserversplunk":
        return {"mcpserversplunk": {}}
    elif provider == "slack":
        return {"slack": {}}
    return {provider: {}}


def cfn_resource_name(provider, service_id):
    """안정적인 CFn 리소스 이름. service_id 기반이므로 인덱스 shift 없음."""
    reg = PROVIDER_REGISTRY.get(provider, {})
    prefix = reg.get("cfn_resource_prefix", f"{provider.title()}Association")
    suffix = service_id.replace("-", "")[:8]
    return f"{prefix}{suffix}"


# ===================================================================
# Sync logic
# ===================================================================


def _config_changed(existing_assoc, desired_ds, provider):
    """기존 AWS association config vs DDB 원하는 config 비교. 변경 있으면 True."""
    existing_cfg = existing_assoc.get("configuration", {})
    if provider == "github":
        gh = existing_cfg.get("github", existing_cfg.get("GitHub", {}))
        existing_repo = f"{gh.get('owner', '')}/{gh.get('repoName', '')}".strip("/")
        return existing_repo != desired_ds.get("repo", "")
    elif provider == "gitlab":
        gl = existing_cfg.get("gitlab", existing_cfg.get("GitLab", {}))
        return str(gl.get("projectId", "")) != str(desired_ds.get("project_id", ""))
    return False


def update_association_config(client, space_id, ds):
    """Association config in-place 업데이트 via update_association API.

    Returns: (ok, error_msg, association_id)
    """
    association_id = ds.get("association_id", "")

    if not association_id:
        assocs = client.list_associations(agentSpaceId=space_id).get("associations", [])
        for a in assocs:
            if a.get("serviceId") == ds.get("service_id", ""):
                association_id = a.get("associationId", "")
                break

    if not association_id:
        return False, "기존 association을 찾을 수 없음", ""

    try:
        assoc_cfg = build_association_config(ds)
        client.update_association(
            agentSpaceId=space_id,
            associationId=association_id,
            configuration=assoc_cfg,
        )
        return True, "", association_id
    except Exception as e:
        return False, f"update_association 실패: {e}", association_id


def sync_associations(client, space_id, integrations):
    """DDB integrations[] → AWS associations 동기화.

    Returns: (updated_integrations, steps)
      - updated_integrations: association_id가 채워진 리스트
      - steps: 각 작업 결과 [{step, ok, error?}]
    """
    steps = []
    assoc_list = client.list_associations(agentSpaceId=space_id).get("associations", [])
    existing_map = {a.get("serviceId", ""): a.get("associationId", "") for a in assoc_list}
    assoc_by_service = {a.get("serviceId", ""): a for a in assoc_list}

    for ds in integrations:
        provider = ds.get("provider", "")
        service_id = ds.get("service_id", "")
        if not service_id:
            continue

        if service_id in existing_map:
            ds["association_id"] = existing_map[service_id]
            # config 변경 감지 (github/gitlab만 — association config에 의미 있는 필드가 있는 provider)
            if provider in ("github", "gitlab"):
                existing_assoc = assoc_by_service.get(service_id, {})
                if existing_assoc and _config_changed(existing_assoc, ds, provider):
                    ok, err, _ = update_association_config(client, space_id, ds)
                    if ok:
                        steps.append({"step": f"update_{provider}", "ok": True})
                    else:
                        steps.append({"step": f"update_{provider}", "ok": False, "error": err})
            continue

        try:
            assoc_cfg = build_association_config(ds)
            kwargs = {
                "agentSpaceId": space_id,
                "serviceId": service_id,
                "configuration": assoc_cfg,
            }
            resp = client.associate_service(**kwargs)
            ds["association_id"] = resp.get("associationId", resp.get("association", {}).get("associationId", ""))
            steps.append({"step": f"add_{provider}", "ok": True})
        except Exception as e:
            steps.append({"step": f"add_{provider}", "ok": False, "error": str(e)})

    return integrations, steps


def backfill_association_ids(session, space_id, integrations):
    """DDB integrations에 association_id 없는 항목을 AWS에서 채움. 변경 있으면 DDB 업데이트."""
    needs_backfill = any(not i.get("association_id") for i in integrations)
    if not needs_backfill or not integrations:
        return integrations

    try:
        client = session.client("devops-agent")
        assoc_resp = client.list_associations(agentSpaceId=space_id)
        assoc_map = {a.get("serviceId", ""): a.get("associationId", "")
                     for a in assoc_resp.get("associations", [])}
        changed = False
        for i in integrations:
            if not i.get("association_id") and i["service_id"] in assoc_map:
                i["association_id"] = assoc_map[i["service_id"]]
                changed = True
        if changed and RUNS_TABLE:
            tbl = session.resource("dynamodb").Table(RUNS_TABLE)
            tbl.update_item(
                Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"},
                UpdateExpression="SET integrations=:ig",
                ExpressionAttributeValues={":ig": integrations},
            )
    except Exception as e:
        print(f"[WARN] association_id backfill failed: {e}", flush=True)

    return integrations


def remove_association(client, session, space_id, association_id="", service_id=""):
    """단건 association 삭제 + DDB 정리. association_id 없으면 service_id로 조회."""
    if not association_id and service_id:
        assoc_resp = client.list_associations(agentSpaceId=space_id)
        for a in assoc_resp.get("associations", []):
            if a.get("serviceId") == service_id:
                association_id = a.get("associationId", "")
                break
        if not association_id:
            return False, f"service_id={service_id}에 해당하는 association을 찾을 수 없음"

    client.disassociate_service(agentSpaceId=space_id, associationId=association_id)

    # DDB에서도 제거
    if RUNS_TABLE:
        try:
            tbl = session.resource("dynamodb").Table(RUNS_TABLE)
            resp = tbl.get_item(Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"})
            item = resp.get("Item", {})
            integrations = item.get("integrations", [])
            updated = [i for i in integrations
                       if i.get("service_id") != service_id and i.get("association_id") != association_id]
            if len(updated) != len(integrations):
                tbl.update_item(
                    Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"},
                    UpdateExpression="SET integrations=:ig, updated_at=:ua",
                    ExpressionAttributeValues={
                        ":ig": updated,
                        ":ua": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    },
                )
        except Exception as e:
            print(f"[WARN] DDB integration removal failed: {e}", flush=True)

    return True, ""


# ===================================================================
# Deploy State Management
# ===================================================================

DEPLOY_STATUS_SYNCED = "synced"
DEPLOY_STATUS_PENDING = "pending"
DEPLOY_STATUS_FAILED = "failed"
DEPLOY_STATUS_BUSY = "busy"


def get_deploy_state(session, space_id):
    """DDB에서 deploy state 조회."""
    if not RUNS_TABLE:
        return {}
    try:
        tbl = session.resource("dynamodb").Table(RUNS_TABLE)
        resp = tbl.get_item(
            Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"},
            ProjectionExpression="settings_version, last_deployed_version, deploy_status, deploy_error, integrations",
        )
        return resp.get("Item", {})
    except Exception:
        return {}


def update_deploy_status(session, space_id, status, error="", bump_version=False, mark_deployed=False):
    """deploy_status 업데이트. bump_version=True면 settings_version++, mark_deployed=True면 last_deployed=settings."""
    if not RUNS_TABLE:
        return
    tbl = session.resource("dynamodb").Table(RUNS_TABLE)
    parts = ["deploy_status = :ds", "updated_at = :ua"]
    vals = {
        ":ds": status,
        ":ua": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if error:
        parts.append("deploy_error = :de")
        vals[":de"] = error
    else:
        parts.append("deploy_error = :de")
        vals[":de"] = ""

    if bump_version:
        parts.append("settings_version = if_not_exists(settings_version, :zero) + :one")
        vals[":zero"] = 0
        vals[":one"] = 1

    if mark_deployed:
        parts.append("last_deployed_version = if_not_exists(settings_version, :one_init)")
        vals[":one_init"] = 1

    tbl.update_item(
        Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"},
        UpdateExpression="SET " + ", ".join(parts),
        ExpressionAttributeValues=vals,
    )


def check_stack_busy(session, stack_name):
    """CFn stack이 IN_PROGRESS 상태인지 확인. busy면 (True, status) 반환."""
    if not stack_name:
        return False, ""
    try:
        cfn = session.client("cloudformation")
        desc = cfn.describe_stacks(StackName=stack_name)
        status = desc["Stacks"][0]["StackStatus"]
        if "IN_PROGRESS" in status:
            return True, status
        return False, status
    except Exception:
        return False, ""


def get_space_cfn_info(session, space_id):
    """Space가 CFn managed인지 + stack_name 반환.

    Returns: (is_cfn_managed, stack_name)
    """
    if not RUNS_TABLE:
        return False, ""
    try:
        tbl = session.resource("dynamodb").Table(RUNS_TABLE)
        resp = tbl.get_item(
            Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"},
            ProjectionExpression="deploy_method, stack_name, space_name",
        )
        item = resp.get("Item", {})
        deploy_method = item.get("deploy_method", "")
        stack_name = item.get("stack_name", "")
        if not stack_name:
            sn = item.get("space_name", "")
            stack_name = f"{sn}-devops-agent" if sn else ""
        if deploy_method == "cloudformation" and stack_name:
            return True, stack_name
        return False, ""
    except Exception:
        return False, ""


def force_sync_from_aws(session, space_id):
    """AWS 실제 상태를 DDB에 강제 반영. drift 해소용.

    Returns: (synced_integrations, steps)
    """
    client = session.client("devops-agent")
    svc_resp = client.list_services()
    svc_map = {}
    for svc in svc_resp.get("services", []):
        sid = svc.get("serviceId", "")
        svc_map[sid] = svc

    assoc_resp = client.list_associations(agentSpaceId=space_id)
    assoc_list = assoc_resp.get("associations", [])

    synced = []
    for a in assoc_list:
        sid = a.get("serviceId", "")
        aid = a.get("associationId", "")
        cfg = a.get("configuration", {})

        if sid == "aws":
            continue

        provider = ""
        for key in cfg:
            provider = key.lower()
            break

        svc = svc_map.get(sid, {})
        raw = {
            "provider": provider or svc.get("serviceType", "").lower(),
            "service_id": sid,
            "association_id": aid,
            "name": svc.get("name", ""),
        }

        if provider == "gitlab":
            gl_cfg = cfg.get("gitlab", cfg.get("GitLab", {}))
            raw["project_id"] = str(gl_cfg.get("projectId", gl_cfg.get("ProjectId", "")))
            raw["repo"] = gl_cfg.get("projectPath", gl_cfg.get("ProjectPath", ""))
        elif provider == "github":
            gh_cfg = cfg.get("github", cfg.get("GitHub", {}))
            owner = gh_cfg.get("owner", gh_cfg.get("Owner", ""))
            repo_name = gh_cfg.get("repoName", gh_cfg.get("RepoName", ""))
            raw["repo"] = f"{owner}/{repo_name}" if owner else repo_name

        pc_name = a.get("privateConnectionName", "")
        if pc_name:
            raw["private_connection_name"] = pc_name

        synced.append(normalize_integration(raw))

    if RUNS_TABLE:
        tbl = session.resource("dynamodb").Table(RUNS_TABLE)
        tbl.update_item(
            Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"},
            UpdateExpression="SET integrations=:ig, deploy_status=:ds, deploy_error=:de, updated_at=:ua",
            ExpressionAttributeValues={
                ":ig": synced,
                ":ds": DEPLOY_STATUS_SYNCED,
                ":de": "",
                ":ua": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )

    return synced


# ===================================================================
# Blueprint endpoints
# ===================================================================

@datasource_bp.route("/api/spaces/<space_id>/datasources", methods=["POST"])
def api_space_datasource_add(space_id):
    """Add a data source (association) to an existing Space.

    CFn managed: DDB intent에 추가 + CFn update-stack
    Direct API: 즉시 associate_service()
    """
    data = request.get_json(force=True)
    provider = data.get("provider", "")
    integration_id = data.get("integration_id", "")
    config = data.get("config", {})
    if not integration_id:
        return jsonify({"ok": False, "error": "integration_id 필요"})
    try:
        session = _boto_session()
        is_cfn, stack_name = get_space_cfn_info(session, space_id)

        ds = normalize_integration({
            "provider": provider,
            "service_id": integration_id,
            "project_id": config.get("project_id", ""),
            "repo": config.get("repo", ""),
            "repo_id": config.get("repo_id", ""),
            "private_connection_name": config.get("private_connection_name", ""),
            "name": config.get("name", ""),
        })

        if is_cfn:
            return _cfn_datasource_change(session, space_id, stack_name, add_ds=ds)

        client = session.client("devops-agent")
        assoc_cfg = build_association_config(ds)
        kwargs = {
            "agentSpaceId": space_id,
            "serviceId": integration_id,
            "configuration": assoc_cfg,
        }
        resp = client.associate_service(**kwargs)
        assoc_id = resp.get("associationId", resp.get("association", {}).get("associationId", ""))

        _update_ddb_integrations(session, space_id, add_ds=ds, assoc_id=assoc_id)
        return jsonify({"ok": True, "association_id": assoc_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@datasource_bp.route("/api/spaces/<space_id>/datasources", methods=["DELETE"])
def api_space_datasource_delete(space_id):
    """Remove a data source (association) from a Space.

    CFn managed: DDB intent에서 제거 + CFn update-stack
    Direct API: 즉시 disassociate_service()
    """
    data = request.get_json(force=True)
    association_id = data.get("association_id", "")
    service_id = data.get("service_id", "")
    if not association_id and not service_id:
        return jsonify({"ok": False, "error": "association_id 또는 service_id 필요"})
    try:
        session = _boto_session()
        is_cfn, stack_name = get_space_cfn_info(session, space_id)

        if is_cfn:
            return _cfn_datasource_change(session, space_id, stack_name, remove_sid=service_id)

        client = session.client("devops-agent")
        ok, err = remove_association(client, session, space_id, association_id, service_id)
        if not ok:
            return jsonify({"ok": False, "error": err})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


def _update_ddb_integrations(session, space_id, add_ds=None, assoc_id="", remove_sid=""):
    """DDB integrations[] 단건 갱신 (Direct API 경로용)."""
    if not RUNS_TABLE:
        return
    tbl = session.resource("dynamodb").Table(RUNS_TABLE)
    resp = tbl.get_item(Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"})
    item = resp.get("Item", {})
    integrations = item.get("integrations", [])

    if remove_sid:
        integrations = [i for i in integrations if i.get("service_id") != remove_sid]

    if add_ds:
        exists = any(i.get("service_id") == add_ds.get("service_id") for i in integrations)
        if not exists:
            if assoc_id:
                add_ds["association_id"] = assoc_id
            integrations.append(add_ds)

    tbl.update_item(
        Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"},
        UpdateExpression="SET integrations=:ig, updated_at=:ua",
        ExpressionAttributeValues={
            ":ig": integrations,
            ":ua": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )


def _cfn_datasource_change(session, space_id, stack_name, add_ds=None, remove_sid=""):
    """CFn managed space에서 단건 datasource 변경: DDB 갱신 + CFn update-stack.

    Settings PUT과 동일한 흐름이지만 integrations 변경만 수행.
    """
    busy, busy_status = check_stack_busy(session, stack_name)
    if busy:
        return jsonify({
            "ok": False,
            "error": f"Stack 진행 중 ({busy_status}). 완료 후 재시도하세요.",
            "deploy_status": DEPLOY_STATUS_BUSY,
        })

    tbl = session.resource("dynamodb").Table(RUNS_TABLE)
    resp = tbl.get_item(Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"})
    item = resp.get("Item", {})
    integrations = [normalize_integration(i) for i in item.get("integrations", [])]

    if remove_sid:
        integrations = [i for i in integrations if i.get("service_id") != remove_sid]

    if add_ds:
        exists = any(i.get("service_id") == add_ds.get("service_id") for i in integrations)
        if not exists:
            integrations.append(add_ds)

    # CFn template 재생성 + update-stack
    from routes_space import api_generate_cfn_internal
    gen_data = {
        "space_name": item.get("space_name", ""),
        "integrations": integrations,
        "app_tag_key": item.get("app_tag_key", "App"),
        "app_tag_value": item.get("app_tag_value", ""),
        "aws_config": item.get("aws_config", {}),
    }
    gen_resp = api_generate_cfn_internal(gen_data)
    if not gen_resp.get("ok"):
        return jsonify({"ok": False, "error": gen_resp.get("error", "CFn 템플릿 생성 실패")})

    update_deploy_status(session, space_id, DEPLOY_STATUS_PENDING, bump_version=True)

    try:
        cfn = session.client("cloudformation")
        tags = [
            {"Key": "auto-delete", "Value": "never"},
            {"Key": "CreatedBy", "Value": "devops-agent-wizard"},
        ]
        tag_key = gen_data.get("app_tag_key", "App")
        tag_val = gen_data.get("app_tag_value", "") or item.get("space_name", "")
        if tag_key and tag_val:
            tags.insert(0, {"Key": tag_key, "Value": tag_val})
        # UPDATE_FAILED 복구 시 Replacement 허용 필요 → DisableRollback 해제
        is_recovery = busy_status == "UPDATE_FAILED"
        update_kwargs = {
            "StackName": stack_name,
            "TemplateBody": gen_resp["yaml"],
            "Capabilities": ["CAPABILITY_NAMED_IAM"],
            "Tags": tags,
        }
        if not is_recovery:
            update_kwargs["DisableRollback"] = True
        cfn.update_stack(**update_kwargs)
    except Exception as e:
        err_msg = str(e)
        if "No updates are to be performed" in err_msg:
            update_deploy_status(session, space_id, DEPLOY_STATUS_SYNCED, mark_deployed=True)
        else:
            update_deploy_status(session, space_id, DEPLOY_STATUS_FAILED, error=err_msg)
            return jsonify({"ok": False, "error": err_msg, "deploy_status": DEPLOY_STATUS_FAILED})

    # DDB intent 갱신
    tbl.update_item(
        Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"},
        UpdateExpression="SET integrations=:ig, updated_at=:ua",
        ExpressionAttributeValues={
            ":ig": integrations,
            ":ua": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )

    return jsonify({"ok": True, "deploy_status": DEPLOY_STATUS_PENDING})


@datasource_bp.route("/api/spaces/<space_id>/deploy-status")
def api_space_deploy_status(space_id):
    """현재 deploy state 조회. pending이면 CFn stack 상태도 확인하여 자동 갱신."""
    try:
        session = _boto_session()
        state = get_deploy_state(session, space_id)
        current_status = state.get("deploy_status", DEPLOY_STATUS_SYNCED)

        # pending 상태에서 CFn 완료 여부 확인
        if current_status == DEPLOY_STATUS_PENDING:
            tbl = session.resource("dynamodb").Table(RUNS_TABLE)
            meta_resp = tbl.get_item(
                Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"},
                ProjectionExpression="stack_name, space_name",
            )
            meta = meta_resp.get("Item", {})
            stack_name = meta.get("stack_name", "")
            if not stack_name:
                sn = meta.get("space_name", "")
                stack_name = f"{sn}-devops-agent" if sn else ""

            if stack_name:
                busy, cfn_status = check_stack_busy(session, stack_name)
                if not busy:
                    if cfn_status == "UPDATE_FAILED":
                        current_status = DEPLOY_STATUS_FAILED
                        update_deploy_status(session, space_id, DEPLOY_STATUS_FAILED,
                                             error="CFn update 실패 (부분 적용 상태). retry 또는 sync 필요.")
                    elif "ROLLBACK" in cfn_status or "FAILED" in cfn_status:
                        current_status = DEPLOY_STATUS_FAILED
                        update_deploy_status(session, space_id, DEPLOY_STATUS_FAILED,
                                             error=f"CFn stack: {cfn_status}")
                    elif cfn_status in ("UPDATE_COMPLETE", "CREATE_COMPLETE"):
                        current_status = DEPLOY_STATUS_SYNCED
                        update_deploy_status(session, space_id, DEPLOY_STATUS_SYNCED, mark_deployed=True)

        return jsonify({
            "ok": True,
            "settings_version": int(state.get("settings_version", 0)),
            "last_deployed_version": int(state.get("last_deployed_version", 0)),
            "deploy_status": current_status,
            "deploy_error": state.get("deploy_error", ""),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@datasource_bp.route("/api/spaces/<space_id>/drift")
def api_space_drift(space_id):
    """드리프트 감지: DDB intended vs AWS 실제 상태 비교."""
    try:
        session = _boto_session()
        client = session.client("devops-agent")

        # AWS 실제 상태
        assoc_resp = client.list_associations(agentSpaceId=space_id)
        aws_assocs = {
            a.get("serviceId", ""): a
            for a in assoc_resp.get("associations", [])
            if a.get("serviceId", "") != "aws"
        }

        # DDB intended 상태
        state = get_deploy_state(session, space_id)
        ddb_integrations = state.get("integrations", [])
        ddb_map = {i.get("service_id", ""): i for i in ddb_integrations if i.get("service_id")}

        aws_sids = set(aws_assocs.keys())
        ddb_sids = set(ddb_map.keys())

        missing_in_aws = []
        for sid in (ddb_sids - aws_sids):
            entry = ddb_map[sid]
            missing_in_aws.append({
                "service_id": sid,
                "provider": entry.get("provider", ""),
                "name": entry.get("name", ""),
                "reason": "DDB에 있지만 AWS에 없음 (외부 삭제 또는 배포 미완료)",
            })

        missing_in_ddb = []
        for sid in (aws_sids - ddb_sids):
            a = aws_assocs[sid]
            cfg = a.get("configuration", {})
            provider = next(iter(cfg), "").lower() if cfg else ""
            missing_in_ddb.append({
                "service_id": sid,
                "provider": provider,
                "association_id": a.get("associationId", ""),
                "reason": "AWS에 있지만 DDB에 없음 (외부 추가 또는 DDB 누락)",
            })

        has_drift = bool(missing_in_aws or missing_in_ddb)
        return jsonify({
            "ok": True,
            "has_drift": has_drift,
            "missing_in_aws": missing_in_aws,
            "missing_in_ddb": missing_in_ddb,
            "aws_count": len(aws_sids),
            "ddb_count": len(ddb_sids),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@datasource_bp.route("/api/spaces/<space_id>/sync", methods=["POST"])
def api_space_sync(space_id):
    """AWS 실제 상태를 DDB에 강제 동기화. Drift 해소용."""
    try:
        session = _boto_session()
        synced = force_sync_from_aws(session, space_id)
        return jsonify({"ok": True, "integrations": synced, "count": len(synced)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@datasource_bp.route("/api/spaces/<space_id>/rollback", methods=["POST"])
def api_space_rollback(space_id):
    """DDB intent를 AWS 실제 상태로 되돌림. 실패한 변경을 포기."""
    try:
        session = _boto_session()
        state = get_deploy_state(session, space_id)
        current_status = state.get("deploy_status", "")
        if current_status == DEPLOY_STATUS_SYNCED:
            return jsonify({"ok": False, "error": "이미 synced 상태 — rollback 불필요"})

        synced = force_sync_from_aws(session, space_id)
        return jsonify({"ok": True, "action": "rollback_to_aws_state", "integrations": synced})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@datasource_bp.route("/api/spaces/<space_id>/retry", methods=["POST"])
def api_space_retry(space_id):
    """실패한 배포를 DDB intended state 기준으로 재시도.
    CFn-managed: DDB intent → 템플릿 재생성 → update_stack
    Direct API: sync_associations()
    """
    try:
        session = _boto_session()
        state = get_deploy_state(session, space_id)
        current_status = state.get("deploy_status", "")
        if current_status == DEPLOY_STATUS_SYNCED:
            return jsonify({"ok": False, "error": "이미 synced 상태 — retry 불필요"})
        if current_status == DEPLOY_STATUS_BUSY:
            return jsonify({"ok": False, "error": "배포 진행 중 — 완료 후 재시도"})

        is_cfn, stack_name = get_space_cfn_info(session, space_id)
        if is_cfn:
            return _cfn_datasource_change(session, space_id, stack_name)

        integrations = [normalize_integration(i) for i in state.get("integrations", [])]
        if not integrations:
            return jsonify({"ok": False, "error": "저장된 integrations 없음"})

        client = session.client("devops-agent")
        update_deploy_status(session, space_id, DEPLOY_STATUS_PENDING)

        integrations, sync_steps = sync_associations(client, space_id, integrations)
        has_failure = any(not s.get("ok") for s in sync_steps)

        if has_failure:
            errors = "; ".join(s.get("error", "") for s in sync_steps if not s.get("ok"))
            update_deploy_status(session, space_id, DEPLOY_STATUS_FAILED, error=errors)
        else:
            update_deploy_status(session, space_id, DEPLOY_STATUS_SYNCED, mark_deployed=True)
            if RUNS_TABLE:
                tbl = session.resource("dynamodb").Table(RUNS_TABLE)
                tbl.update_item(
                    Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"},
                    UpdateExpression="SET integrations=:ig",
                    ExpressionAttributeValues={":ig": integrations},
                )

        return jsonify({
            "ok": not has_failure,
            "steps": sync_steps,
            "deploy_status": DEPLOY_STATUS_FAILED if has_failure else DEPLOY_STATUS_SYNCED,
        })
    except Exception as e:
        try:
            update_deploy_status(session, space_id, DEPLOY_STATUS_FAILED, error=str(e))
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(e)})
