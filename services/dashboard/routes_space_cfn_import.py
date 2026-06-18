"""
Space CFn Import routes — Import existing resources into CFn stack, disconnect from CFn.

Enables any Space (regardless of origin) to be managed by CloudFormation:
- cfn-import: Brings existing associations under CFn management
- cfn-disconnect: Releases resources from CFn while preserving them in AWS
- cfn-status: Reports current CFn management state
"""
import time
import uuid

import yaml as _yaml
from flask import Blueprint, jsonify, request

from app_config import (
    AWS_REGION, RUNS_TABLE,
    _boto_session,
)
from datasource_manager import get_space_cfn_info, check_stack_busy

cfn_import_bp = Blueprint("cfn_import_bp", __name__)


# ===================================================================
# Helpers
# ===================================================================


def _read_actual_associations(client, space_id):
    """list_associations + get_association으로 실제 배포된 config 수집."""
    assoc_list = client.list_associations(agentSpaceId=space_id).get("associations", [])
    results = []
    for a in assoc_list:
        assoc_id = a.get("associationId", "")
        if not assoc_id:
            continue
        detail = client.get_association(agentSpaceId=space_id, associationId=assoc_id)
        assoc = detail.get("association", {})
        results.append({
            "association_id": assoc_id,
            "service_id": assoc.get("serviceId", ""),
            "configuration": assoc.get("configuration", {}),
            "status": assoc.get("status", ""),
        })
    return results


def _cfn_config_key(api_config):
    """API config dict의 key를 CFn 템플릿용 PascalCase로 변환.

    API: {'github': {...}} → CFn: {'GitHub': {...}}
    API: {'aws': {...}} → CFn: {'Aws': {...}}
    API: {'sourceAws': {...}} → CFn: {'SourceAws': {...}}
    """
    KEY_MAP = {
        "github": "GitHub",
        "gitlab": "GitLab",
        "slack": "Slack",
        "aws": "Aws",
        "sourceAws": "SourceAws",
        "mcpserversplunk": "MCPServerSplunk",
        "mcpserver": "MCPServer",
        "mcpserverdatadog": "MCPServerDatadog",
        "mcpservernewrelic": "MCPServerNewRelic",
        "mcpservergrafana": "MCPServerGrafana",
        "mcpserversigv4": "MCPServerSigV4",
        "dynatrace": "Dynatrace",
        "servicenow": "ServiceNow",
        "pagerduty": "PagerDuty",
        "eventChannel": "EventChannel",
        "azure": "Azure",
    }
    result = {}
    for api_key, value in api_config.items():
        cfn_key = KEY_MAP.get(api_key, api_key[0].upper() + api_key[1:])
        cfn_value = _pascal_case_keys(value) if isinstance(value, dict) else value
        result[cfn_key] = cfn_value
    return result


def _pascal_case_keys(d):
    """dict의 key를 camelCase → PascalCase로 변환 (1단계만)."""
    if not isinstance(d, dict):
        return d
    result = {}
    for k, v in d.items():
        pk = k[0].upper() + k[1:] if k else k
        if isinstance(v, dict):
            result[pk] = _pascal_case_keys(v)
        elif isinstance(v, list):
            result[pk] = [_pascal_case_keys(i) if isinstance(i, dict) else i for i in v]
        else:
            result[pk] = v
    return result


def _logical_name_for_assoc(service_id, config, idx):
    """Association에 대한 CFn logical resource name 생성."""
    config_keys = list(config.keys())
    if not config_keys:
        return f"Association{idx}"
    provider_key = config_keys[0]
    PROVIDER_NAMES = {
        "aws": "MonitorAssociation",
        "sourceAws": "SourceAwsAssociation",
        "github": "GitHubAssociation",
        "gitlab": "GitLabAssociation",
        "slack": "SlackAssociation",
        "mcpserversplunk": "SplunkAssociation",
        "mcpserver": "MCPServerAssociation",
        "eventChannel": "EventChannelAssociation",
        "dynatrace": "DynatraceAssociation",
        "pagerduty": "PagerDutyAssociation",
    }
    base = PROVIDER_NAMES.get(provider_key, f"{provider_key.title()}Association")
    if idx > 1:
        base = f"{base}{idx}"
    return base


def _generate_import_template(space_id, space_name, associations):
    """Import 전용 CFn 템플릿 생성.

    - AgentSpaceId = 리터럴 (이미 존재)
    - config = get_association()에서 읽은 그대로
    - 모든 리소스에 DeletionPolicy: Retain
    """
    template = {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": f"DevOps Agent - {space_name} (imported)",
        "Resources": {},
    }

    # AgentSpace 자체도 import
    template["Resources"]["DevOpsAgentSpace"] = {
        "Type": "AWS::DevOpsAgent::AgentSpace",
        "DeletionPolicy": "Retain",
        "Properties": {
            "Name": space_name,
        },
    }

    # 각 Association
    provider_counters = {}
    logical_names = {}

    for assoc in associations:
        assoc_id = assoc["association_id"]
        config = assoc["configuration"]
        config_keys = list(config.keys())
        provider_key = config_keys[0] if config_keys else "unknown"

        provider_counters[provider_key] = provider_counters.get(provider_key, 0) + 1
        idx = provider_counters[provider_key]
        logical_name = _logical_name_for_assoc(assoc["service_id"], config, idx)

        cfn_config = _cfn_config_key(config)

        resource = {
            "Type": "AWS::DevOpsAgent::Association",
            "DeletionPolicy": "Retain",
            "Properties": {
                "AgentSpaceId": space_id,
                "ServiceId": assoc["service_id"],
                "Configuration": cfn_config,
            },
        }

        template["Resources"][logical_name] = resource
        logical_names[assoc_id] = logical_name

    return _yaml.dump(template, default_flow_style=False, allow_unicode=True), logical_names


def _build_resources_to_import(space_id, associations, logical_names):
    """CreateChangeSet의 ResourcesToImport 리스트 생성."""
    resources = []

    # AgentSpace
    resources.append({
        "ResourceType": "AWS::DevOpsAgent::AgentSpace",
        "LogicalResourceId": "DevOpsAgentSpace",
        "ResourceIdentifier": {"AgentSpaceId": space_id},
    })

    # Associations
    for assoc in associations:
        assoc_id = assoc["association_id"]
        logical_name = logical_names.get(assoc_id)
        if not logical_name:
            continue
        resources.append({
            "ResourceType": "AWS::DevOpsAgent::Association",
            "LogicalResourceId": logical_name,
            "ResourceIdentifier": {
                "AgentSpaceId": space_id,
                "AssociationId": assoc_id,
            },
        })

    return resources


def _wait_changeset_ready(cfn, stack_name, changeset_name, timeout=120):
    """ChangeSet 상태 polling → CREATE_COMPLETE 대기."""
    for _ in range(timeout // 3):
        resp = cfn.describe_change_set(StackName=stack_name, ChangeSetName=changeset_name)
        status = resp.get("Status", "")
        if status == "CREATE_COMPLETE":
            return True, ""
        if status == "FAILED":
            reason = resp.get("StatusReason", "unknown")
            return False, reason
        time.sleep(3)
    return False, "timeout"


def _wait_stack_operation(cfn, stack_name, target_statuses, timeout=180):
    """Stack 상태 polling → target status 도달 대기."""
    for _ in range(timeout // 5):
        try:
            resp = cfn.describe_stacks(StackName=stack_name)
            stacks = resp.get("Stacks", [])
            if not stacks:
                if "DELETE_COMPLETE" in target_statuses:
                    return True, "DELETE_COMPLETE"
                return False, "STACK_NOT_FOUND"
            status = stacks[0].get("StackStatus", "")
            if status in target_statuses:
                return True, status
            if "FAILED" in status or "ROLLBACK_COMPLETE" == status:
                return False, status
        except cfn.exceptions.ClientError as e:
            if "does not exist" in str(e):
                if "DELETE_COMPLETE" in target_statuses:
                    return True, "DELETE_COMPLETE"
                return False, "STACK_NOT_FOUND"
            raise
        time.sleep(5)
    return False, "timeout"


# ===================================================================
# Endpoints
# ===================================================================


@cfn_import_bp.route("/api/spaces/<space_id>/cfn-import", methods=["POST"])
def api_space_cfn_import(space_id):
    """기존 Space + Associations를 CFn 스택으로 import."""
    try:
        session = _boto_session()

        # 1. 검증
        cfn_managed, existing_stack = get_space_cfn_info(session, space_id)
        if cfn_managed:
            return jsonify({"ok": False, "error": "이미 CFn으로 관리 중입니다"})

        # Space 메타 조회
        from routes_space import _get_space_meta
        meta = _get_space_meta(session, space_id)
        if not meta:
            return jsonify({"ok": False, "error": "Space 메타데이터를 찾을 수 없습니다"})

        space_name = meta.get("space_name", "")
        if not space_name:
            return jsonify({"ok": False, "error": "Space 이름이 없습니다"})

        # 계정 세션 결정: profile 우선, 없으면 role_arn AssumeRole
        import boto3 as _b3
        acct_profile = meta.get("profile", "")
        if acct_profile:
            acct_session = _b3.Session(profile_name=acct_profile, region_name=AWS_REGION)
        else:
            aws_config = meta.get("aws_config", {})
            role_arn = (aws_config.get("aws", {}).get("role_arn")
                        or meta.get("role_arn", ""))
            if role_arn and role_arn.startswith("arn:aws:iam::"):
                from app_config import _get_or_create_session
                try:
                    acct_session = _get_or_create_session(role_arn)
                except Exception:
                    acct_session = session
            else:
                acct_session = session

        # 2. 실제 association config 수집
        client = acct_session.client("devops-agent", region_name=AWS_REGION)
        associations = _read_actual_associations(client, space_id)
        if not associations:
            return jsonify({"ok": False, "error": "import할 association이 없습니다"})

        # 3. 템플릿 생성
        template_body, logical_names = _generate_import_template(
            space_id, space_name, associations
        )

        # 4. ResourcesToImport 생성
        resources_to_import = _build_resources_to_import(space_id, associations, logical_names)

        # 5. CreateChangeSet (IMPORT)
        stack_name = f"{space_name}-devops-agent"
        changeset_name = f"import-{uuid.uuid4().hex[:8]}"
        cfn = acct_session.client("cloudformation", region_name=AWS_REGION)

        # 기존 스택 존재 시 삭제 (DeletionPolicy:Retain이므로 리소스 보존)
        try:
            resp = cfn.describe_stacks(StackName=stack_name)
            stacks = resp.get("Stacks", [])
            if stacks and stacks[0].get("StackStatus") not in ("DELETE_COMPLETE",):
                cfn.delete_stack(StackName=stack_name)
                _wait_stack_operation(cfn, stack_name, {"DELETE_COMPLETE"}, timeout=120)
        except cfn.exceptions.ClientError as e:
            if "does not exist" not in str(e):
                raise

        cfn.create_change_set(
            StackName=stack_name,
            ChangeSetName=changeset_name,
            ChangeSetType="IMPORT",
            TemplateBody=template_body,
            ResourcesToImport=resources_to_import,
        )

        # 6. ChangeSet 대기
        ok, err = _wait_changeset_ready(cfn, stack_name, changeset_name)
        if not ok:
            # 실패 시 정리
            try:
                cfn.delete_change_set(StackName=stack_name, ChangeSetName=changeset_name)
            except Exception:
                pass
            return jsonify({"ok": False, "error": f"ChangeSet 생성 실패: {err}"})

        # 7. ExecuteChangeSet
        cfn.execute_change_set(StackName=stack_name, ChangeSetName=changeset_name)

        # 8. Stack 완료 대기
        ok, final_status = _wait_stack_operation(
            cfn, stack_name,
            {"IMPORT_COMPLETE", "CREATE_COMPLETE", "UPDATE_COMPLETE"},
            timeout=180,
        )
        if not ok:
            return jsonify({"ok": False, "error": f"Import 실패: {final_status}"})

        # 9. DDB 업데이트 — deploy 상태 + integrations 동기화
        if RUNS_TABLE:
            # associations → integrations 변환
            integrations = []
            for assoc in associations:
                config = assoc["configuration"]
                cfg_key = next(iter(config), "")
                if cfg_key in ("aws", "sourceAws"):
                    continue  # aws_config으로 별도 관리
                integrations.append({
                    "service_id": assoc["service_id"],
                    "association_id": assoc["association_id"],
                    "provider": cfg_key,
                })

            tbl = session.resource("dynamodb").Table(RUNS_TABLE)
            update_expr = "SET deploy_method=:dm, stack_name=:sn, deploy_status=:ds"
            expr_values = {
                ":dm": "cloudformation",
                ":sn": stack_name,
                ":ds": "synced",
            }
            if integrations:
                update_expr += ", integrations=:ig"
                expr_values[":ig"] = integrations

            tbl.update_item(
                Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expr_values,
            )

        return jsonify({
            "ok": True,
            "stack_name": stack_name,
            "resources_imported": len(resources_to_import),
            "status": final_status,
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@cfn_import_bp.route("/api/spaces/<space_id>/cfn-disconnect", methods=["POST"])
def api_space_cfn_disconnect(space_id):
    """오버뷰앱에서 분리. AWS 리소스/스택은 그대로 유지."""
    try:
        session = _boto_session()

        # 검증
        cfn_managed, stack_name = get_space_cfn_info(session, space_id)
        if not cfn_managed:
            return jsonify({"ok": False, "error": "CFn으로 관리 중이 아닙니다"})

        # DDB에서 관리 해제만 수행 (스택/리소스는 AWS에 그대로)
        if RUNS_TABLE:
            tbl = session.resource("dynamodb").Table(RUNS_TABLE)
            tbl.update_item(
                Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"},
                UpdateExpression="SET deploy_method=:dm, deploy_status=:ds REMOVE stack_name",
                ExpressionAttributeValues={
                    ":dm": "",
                    ":ds": "synced",
                },
            )

        return jsonify({
            "ok": True,
            "message": "오버뷰앱에서 분리 완료. AWS 스택/리소스는 유지됩니다.",
            "stack_name": stack_name,
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@cfn_import_bp.route("/api/spaces/<space_id>/cfn-status")
def api_space_cfn_status(space_id):
    """현재 CFn 관리 상태 반환."""
    try:
        session = _boto_session()
        cfn_managed, stack_name = get_space_cfn_info(session, space_id)

        result = {
            "ok": True,
            "deploy_method": "cloudformation" if cfn_managed else "",
            "stack_name": stack_name or "",
            "stack_status": "",
            "can_import": not cfn_managed,
            "can_disconnect": cfn_managed,
        }

        # CFn 관리 중이면 스택 상태도 조회
        if cfn_managed and stack_name:
            import boto3 as _b3
            from routes_space import _get_space_meta
            meta = _get_space_meta(session, space_id)
            acct_profile = meta.get("profile", "") if meta else ""
            if acct_profile:
                acct_session = _b3.Session(profile_name=acct_profile, region_name=AWS_REGION)
            else:
                from app_config import _get_or_create_session
                aws_config = meta.get("aws_config", {}) if meta else {}
                role_arn = (aws_config.get("aws", {}).get("role_arn")
                            or (meta.get("role_arn", "") if meta else ""))
                if role_arn and role_arn.startswith("arn:aws:iam::"):
                    try:
                        acct_session = _get_or_create_session(role_arn)
                    except Exception:
                        acct_session = session
                else:
                    acct_session = session
            cfn = acct_session.client("cloudformation", region_name=AWS_REGION)
            try:
                resp = cfn.describe_stacks(StackName=stack_name)
                stacks = resp.get("Stacks", [])
                if stacks:
                    result["stack_status"] = stacks[0].get("StackStatus", "")
            except Exception:
                result["stack_status"] = "NOT_FOUND"

        return jsonify(result)

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
