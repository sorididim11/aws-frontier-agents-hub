"""
Deploy routes — Private Connection, CFn template generation, CFn deploy.

Includes:
  - /api/spaces/deploy-private-connection
  - /api/spaces/test-connection
  - /api/spaces/validate-step
  - /api/spaces/generate-cfn
  - /api/spaces/deploy-cfn
  - api_generate_cfn_internal (shared helper)
"""
import json
import time

from flask import Response, jsonify, request, stream_with_context

from app_config import (
    _CFG, _cfg_get, AWS_REGION, RUNS_TABLE,
    _boto_session, _space_session, _session_for_account_id,
)

from routes_space import space_bp
from routes_space.core import _get_space_meta, _save_space_metadata, _setup_event_channel, _append_integration


# ===================================================================
# Private Connection — Deploy + Test
# ===================================================================

@space_bp.route("/api/spaces/deploy-private-connection", methods=["POST"])
def api_deploy_private_connection():
    """Deploy a Private Connection via CFn stack, then test connectivity."""
    import yaml as _yaml

    data = request.get_json(force=True)
    print(f"[PC-DEPLOY] request body: {json.dumps(data, default=str)[:500]}", flush=True)
    connection_mode = (data.get("connection_mode") or "service_managed").strip()
    name = (data.get("name") or "gitlab-pc").strip()
    host_address = (data.get("host") or "").strip()
    vpc_id = (data.get("vpc_id") or "").strip()
    subnet_ids = data.get("subnets") or []
    security_group_ids = data.get("security_group_ids") or []
    sg = (data.get("security_group") or "").strip()
    if sg and not security_group_ids:
        security_group_ids = [sg]
    port = str(data.get("port", "443")).strip()
    target_account_id = (data.get("target_account_id") or "").strip()
    ca_certificate = (data.get("ca_certificate") or "").strip()

    if not host_address:
        return jsonify({"ok": False, "error": "호스트 주소가 필요합니다"})
    if not vpc_id:
        return jsonify({"ok": False, "error": "VPC ID가 필요합니다"})
    if not subnet_ids:
        return jsonify({"ok": False, "error": "최소 1개의 Subnet을 선택하세요"})
    if connection_mode == "self_managed" and not target_account_id:
        return jsonify({"ok": False, "error": "SelfManaged 모드에서는 대상 계정을 선택하세요"})

    if connection_mode == "self_managed":
        return _deploy_self_managed_pc(data, name, host_address, vpc_id, subnet_ids, security_group_ids, port, target_account_id, ca_certificate)

    # --- ServiceManaged (기존 로직) ---
    template = {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": f"Private Connection for {name}",
        "Resources": {
            "PrivateConnection": {
                "Type": "AWS::DevOpsAgent::PrivateConnection",
                "Properties": {
                    "Name": name,
                    "ConnectionConfiguration": {
                        "ServiceManaged": {
                            "HostAddress": host_address,
                            "VpcId": vpc_id,
                            "SubnetIds": subnet_ids,
                            "PortRanges": [port],
                        }
                    },
                },
            }
        },
        "Outputs": {
            "PrivateConnectionName": {
                "Value": {"Ref": "PrivateConnection"},
            },
        },
    }
    if security_group_ids:
        template["Resources"]["PrivateConnection"]["Properties"]["ConnectionConfiguration"]["ServiceManaged"]["SecurityGroupIds"] = security_group_ids

    yaml_str = _yaml.dump(template, default_flow_style=False, allow_unicode=True)
    stack_name = f"{name}-private-connection"

    def generate():
        try:
            import time as _time
            session = _boto_session()
            cfn = session.client("cloudformation")

            yield f"data: {json.dumps({'type': 'status', 'message': 'Private Connection 스택 생성 중...'})}\n\n"

            try:
                cfn.create_stack(
                    StackName=stack_name,
                    TemplateBody=yaml_str,
                    Capabilities=["CAPABILITY_NAMED_IAM"],
                    Tags=[{"Key": "Purpose", "Value": "private-connection"}],
                )
            except cfn.exceptions.AlreadyExistsException:
                try:
                    cfn.update_stack(
                        StackName=stack_name,
                        TemplateBody=yaml_str,
                        Capabilities=["CAPABILITY_NAMED_IAM"],
                    )
                except Exception as ue:
                    if "No updates are to be performed" in str(ue):
                        pass
                    else:
                        yield f"data: {json.dumps({'type': 'error', 'error': f'스택 업데이트 실패: {ue}'})}\n\n"
                        return

            # Poll stack status (5초 간격, 최대 5분)
            terminal_success = ("CREATE_COMPLETE", "UPDATE_COMPLETE")
            terminal_fail = ("CREATE_FAILED", "ROLLBACK_COMPLETE", "ROLLBACK_IN_PROGRESS", "UPDATE_ROLLBACK_COMPLETE", "DELETE_COMPLETE")
            for attempt in range(60):
                _time.sleep(5)
                try:
                    desc = cfn.describe_stacks(StackName=stack_name)
                    stack = desc["Stacks"][0]
                    status = stack.get("StackStatus", "")
                except Exception:
                    status = "DELETE_COMPLETE"

                if status in terminal_success:
                    break
                elif status in terminal_fail:
                    reason = ""
                    try:
                        events = cfn.describe_stack_events(StackName=stack_name)
                        for ev in events.get("StackEvents", []):
                            if "FAILED" in ev.get("ResourceStatus", "") and ev.get("ResourceStatusReason"):
                                reason = ev["ResourceStatusReason"]
                                break
                    except Exception:
                        pass
                    err_msg = f"스택 생성 실패 ({status}): {reason or '원인 불명'}"
                    yield f"data: {json.dumps({'type': 'error', 'error': err_msg})}\n\n"
                    return
                else:
                    yield f"data: {json.dumps({'type': 'status', 'message': f'배포 진행 중... ({status})'})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'error', 'error': '스택 생성 시간 초과 (5분)'})}\n\n"
                return

            stack_resp = cfn.describe_stacks(StackName=stack_name)
            outputs = stack_resp["Stacks"][0].get("Outputs", [])
            pc_name = ""
            for o in outputs:
                if o["OutputKey"] == "PrivateConnectionName":
                    pc_name = o["OutputValue"]

            yield f"data: {json.dumps({'type': 'status', 'message': 'Private Connection 생성 완료. 연결 테스트 중...'})}\n\n"

            import socket
            _time.sleep(5)
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10)
                result = sock.connect_ex((host_address, int(port)))
                sock.close()
                conn_ok = (result == 0)
            except Exception:
                conn_ok = False

            yield f"data: {json.dumps({'type': 'complete', 'ok': True, 'private_connection_name': pc_name, 'stack_name': stack_name, 'connection_test': conn_ok})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


def _deploy_self_managed_pc(data, name, host_address, vpc_id, subnet_ids, security_group_ids, port, target_account_id, ca_certificate):
    """Deploy a SelfManaged Private Connection (cross-account via VPC Lattice + RAM)."""
    import yaml as _yaml
    import time as _time

    primary_account_id = (data.get("primary_account_id") or "").strip()
    if not primary_account_id:
        from app_config import _cfg_get
        primary_account_id = _cfg_get(_CFG, "aws.account_id", "")
    if not primary_account_id:
        try:
            sts = _boto_session().client("sts")
            primary_account_id = sts.get_caller_identity()["Account"]
        except Exception:
            pass

    lattice_stack_name = f"{name}-vpc-lattice"
    pc_stack_name = f"{name}-private-connection"

    lattice_template = {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": f"VPC Lattice resources for cross-account Private Connection: {name}",
        "Resources": {
            "ResourceGateway": {
                "Type": "AWS::VpcLattice::ResourceGateway",
                "Properties": {
                    "Name": f"{name}-rgw",
                    "VpcIdentifier": vpc_id,
                    "SubnetIds": subnet_ids,
                    "IpAddressType": "IPV4",
                },
            },
            "ResourceConfiguration": {
                "Type": "AWS::VpcLattice::ResourceConfiguration",
                "DependsOn": "ResourceGateway",
                "Properties": {
                    "Name": f"{name}-rcfg",
                    "ResourceConfigurationType": "SINGLE",
                    "ProtocolType": "TCP",
                    "PortRanges": [port],
                    "ResourceGatewayId": {"Fn::GetAtt": ["ResourceGateway", "Id"]},
                    "ResourceConfigurationDefinition": {
                        "DnsResource": {
                            "DomainName": host_address,
                            "IpAddressType": "IPV4",
                        }
                    },
                    "AllowAssociationToSharableServiceNetwork": True,
                },
            },
        },
        "Outputs": {
            "ResourceConfigurationArn": {
                "Value": {"Fn::GetAtt": ["ResourceConfiguration", "Arn"]},
            },
            "ResourceGatewayId": {
                "Value": {"Ref": "ResourceGateway"},
            },
        },
    }
    if security_group_ids:
        lattice_template["Resources"]["ResourceGateway"]["Properties"]["SecurityGroupIds"] = security_group_ids

    pc_template_base = {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": f"SelfManaged Private Connection: {name}",
        "Parameters": {
            "ResourceConfigurationArn": {"Type": "String"},
        },
        "Resources": {
            "PrivateConnection": {
                "Type": "AWS::DevOpsAgent::PrivateConnection",
                "Properties": {
                    "Name": name,
                    "ConnectionConfiguration": {
                        "SelfManaged": {
                            "ResourceConfigurationId": {"Ref": "ResourceConfigurationArn"},
                        }
                    },
                },
            }
        },
        "Outputs": {
            "PrivateConnectionName": {
                "Value": {"Ref": "PrivateConnection"},
            },
        },
    }
    if ca_certificate:
        pc_template_base["Resources"]["PrivateConnection"]["Properties"]["Certificate"] = ca_certificate

    def generate():
        share_arn = None
        try:
            # === Phase 1: Deploy VPC Lattice stack in target account ===
            yield f"data: {json.dumps({'type': 'status', 'message': '[1/4] 대상 계정에 VPC Lattice 리소스 배포 중...'})}\n\n"
            target_session = _session_for_account_id(target_account_id)
            target_cfn = target_session.client("cloudformation")
            lattice_yaml = _yaml.dump(lattice_template, default_flow_style=False, allow_unicode=True)

            try:
                target_cfn.create_stack(
                    StackName=lattice_stack_name,
                    TemplateBody=lattice_yaml,
                    Tags=[{"Key": "Purpose", "Value": "private-connection-vpc-lattice"}, {"Key": "PrimaryAccount", "Value": primary_account_id}],
                )
            except target_cfn.exceptions.AlreadyExistsException:
                try:
                    target_cfn.update_stack(StackName=lattice_stack_name, TemplateBody=lattice_yaml)
                except Exception as ue:
                    if "No updates are to be performed" not in str(ue):
                        yield f"data: {json.dumps({'type': 'error', 'error': f'대상 계정 스택 업데이트 실패: {ue}'})}\n\n"
                        return

            rcfg_arn = None
            for attempt in range(60):
                _time.sleep(5)
                try:
                    desc = target_cfn.describe_stacks(StackName=lattice_stack_name)
                    st = desc["Stacks"][0].get("StackStatus", "")
                except Exception:
                    st = "DELETE_COMPLETE"
                if st in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
                    for o in desc["Stacks"][0].get("Outputs", []):
                        if o["OutputKey"] == "ResourceConfigurationArn":
                            rcfg_arn = o["OutputValue"]
                    break
                elif "FAILED" in st or "ROLLBACK" in st or st == "DELETE_COMPLETE":
                    reason = ""
                    try:
                        for ev in target_cfn.describe_stack_events(StackName=lattice_stack_name).get("StackEvents", []):
                            if "FAILED" in ev.get("ResourceStatus", ""):
                                reason = ev.get("ResourceStatusReason", "")
                                break
                    except Exception:
                        pass
                    yield f"data: {json.dumps({'type': 'error', 'error': f'VPC Lattice 스택 실패 ({st}): {reason}'})}\n\n"
                    return
                else:
                    if attempt % 3 == 0:
                        yield f"data: {json.dumps({'type': 'status', 'message': f'[1/4] VPC Lattice 배포 중... ({st})'})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'error', 'error': 'VPC Lattice 스택 시간 초과'})}\n\n"
                return

            if not rcfg_arn:
                yield f"data: {json.dumps({'type': 'error', 'error': 'ResourceConfigurationArn 출력값을 찾을 수 없습니다'})}\n\n"
                return

            # === Phase 2: RAM Share ===
            yield f"data: {json.dumps({'type': 'status', 'message': '[2/4] RAM 리소스 공유 생성 및 수락 중...'})}\n\n"
            ram_target = target_session.client("ram")
            share_resp = ram_target.create_resource_share(
                name=f"{name}-pc-share",
                resourceArns=[rcfg_arn],
                principals=[primary_account_id],
                allowExternalPrincipals=True,
            )
            share_arn = share_resp["resourceShare"]["resourceShareArn"]

            primary_session = _session_for_account_id(primary_account_id) if primary_account_id else _boto_session()
            ram_primary = primary_session.client("ram")
            invitation_arn = None
            for _ in range(24):
                _time.sleep(5)
                try:
                    invitations = ram_primary.get_resource_share_invitations().get("resourceShareInvitations", [])
                    for inv in invitations:
                        if inv.get("status") == "PENDING" and inv.get("resourceShareArn") == share_arn:
                            invitation_arn = inv["resourceShareInvitationArn"]
                            break
                except Exception:
                    pass
                if invitation_arn:
                    break

            if not invitation_arn:
                yield f"data: {json.dumps({'type': 'error', 'error': 'RAM 공유 초대를 수신하지 못했습니다 (2분 타임아웃)'})}\n\n"
                return

            ram_primary.accept_resource_share_invitation(resourceShareInvitationArn=invitation_arn)
            _time.sleep(10)

            # === Phase 3: Deploy Private Connection in primary account ===
            yield f"data: {json.dumps({'type': 'status', 'message': '[3/4] Primary 계정에 Private Connection 생성 중...'})}\n\n"
            primary_cfn = primary_session.client("cloudformation")
            pc_yaml = _yaml.dump(pc_template_base, default_flow_style=False, allow_unicode=True)

            try:
                primary_cfn.create_stack(
                    StackName=pc_stack_name,
                    TemplateBody=pc_yaml,
                    Parameters=[{"ParameterKey": "ResourceConfigurationArn", "ParameterValue": rcfg_arn}],
                    Tags=[{"Key": "Purpose", "Value": "private-connection"}, {"Key": "Mode", "Value": "self-managed"}, {"Key": "TargetAccount", "Value": target_account_id}],
                )
            except primary_cfn.exceptions.AlreadyExistsException:
                try:
                    primary_cfn.update_stack(
                        StackName=pc_stack_name, TemplateBody=pc_yaml,
                        Parameters=[{"ParameterKey": "ResourceConfigurationArn", "ParameterValue": rcfg_arn}],
                    )
                except Exception as ue:
                    if "No updates are to be performed" not in str(ue):
                        yield f"data: {json.dumps({'type': 'error', 'error': f'PC 스택 업데이트 실패: {ue}'})}\n\n"
                        return

            pc_name = ""
            for attempt in range(60):
                _time.sleep(5)
                try:
                    desc = primary_cfn.describe_stacks(StackName=pc_stack_name)
                    st = desc["Stacks"][0].get("StackStatus", "")
                except Exception:
                    st = "DELETE_COMPLETE"
                if st in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
                    for o in desc["Stacks"][0].get("Outputs", []):
                        if o["OutputKey"] == "PrivateConnectionName":
                            pc_name = o["OutputValue"]
                    break
                elif "FAILED" in st or "ROLLBACK" in st or st == "DELETE_COMPLETE":
                    reason = ""
                    try:
                        for ev in primary_cfn.describe_stack_events(StackName=pc_stack_name).get("StackEvents", []):
                            if "FAILED" in ev.get("ResourceStatus", ""):
                                reason = ev.get("ResourceStatusReason", "")
                                break
                    except Exception:
                        pass
                    yield f"data: {json.dumps({'type': 'error', 'error': f'PC 스택 실패 ({st}): {reason}'})}\n\n"
                    return
                else:
                    if attempt % 3 == 0:
                        yield f"data: {json.dumps({'type': 'status', 'message': f'[3/4] Private Connection 배포 중... ({st})'})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'error', 'error': 'Private Connection 스택 시간 초과'})}\n\n"
                return

            # === Phase 4: Verify ACTIVE ===
            yield f"data: {json.dumps({'type': 'status', 'message': '[4/4] Private Connection 활성화 대기 중...'})}\n\n"
            if pc_name:
                agent_client = primary_session.client("devops-agent")
                for _ in range(24):
                    _time.sleep(5)
                    try:
                        pc_desc = agent_client.describe_private_connection(name=pc_name)
                        if pc_desc.get("status") == "ACTIVE":
                            break
                    except Exception:
                        pass

            yield f"data: {json.dumps({'type': 'complete', 'ok': True, 'private_connection_name': pc_name, 'stack_name': pc_stack_name, 'lattice_stack_name': lattice_stack_name, 'target_account_id': target_account_id, 'connection_test': None})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
            # Rollback attempt
            try:
                if share_arn:
                    ram_target = _session_for_account_id(target_account_id).client("ram")
                    ram_target.delete_resource_share(resourceShareArn=share_arn)
            except Exception:
                pass

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@space_bp.route("/api/spaces/test-connection", methods=["POST"])
def api_test_connection():
    """Test TCP connectivity to a private connection host."""
    import socket

    data = request.get_json(force=True)
    host = data.get("host", "").strip()
    port = int(data.get("port", 443))

    if not host:
        return jsonify({"ok": False, "error": "호스트 주소가 필요합니다"})
    if port < 1 or port > 65535:
        return jsonify({"ok": False, "error": "포트 범위: 1-65535"})

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, port))
        sock.close()
        if result == 0:
            return jsonify({"ok": True, "message": f"{host}:{port} 연결 성공"})
        else:
            return jsonify({"ok": False, "error": f"{host}:{port} 연결 실패 (errno={result})"})
    except socket.gaierror:
        return jsonify({"ok": False, "error": f"DNS 조회 실패: {host}"})
    except socket.timeout:
        return jsonify({"ok": False, "error": f"연결 시간 초과 (5초): {host}:{port}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ===================================================================
# Wizard Step Validation (async, per-step)
# ===================================================================

@space_bp.route("/api/spaces/validate-step", methods=["POST"])
def api_validate_step():
    """Validate wizard step data against real AWS state. Returns ok + warnings + defaults."""
    data = request.get_json(force=True)
    step = data.get("step", 0)
    warnings = []
    defaults = {}

    try:
        if step == 1:
            name = data.get("name", "")
            if name and len(name) > 37:
                return jsonify({"ok": False, "error": f"Space 이름이 너무 깁니다 ({len(name)}자). IAM Role 이름 제한으로 최대 37자까지 가능합니다."})
            # Check if space name already exists (API + DDB registry)
            # Skip check in edit mode (space already exists)
            edit_space_id = data.get("space_id", "")
            if name and not edit_space_id:
                try:
                    pri_acct = data.get("primary_account_id", "")
                    session = _space_session(account_id=pri_acct) if pri_acct else _space_session()
                    client = session.client("devops-agent")
                    spaces_resp = client.list_agent_spaces()
                    existing_names = {s.get("name", "") for s in spaces_resp.get("agentSpaces", [])}
                    try:
                        from boto3.dynamodb.conditions import Attr
                        tbl = session.resource("dynamodb").Table(RUNS_TABLE)
                        scan_resp = tbl.scan(FilterExpression=Attr("record_type").eq("space_metadata"))
                        for item in scan_resp.get("Items", []):
                            sn = item.get("space_name", "")
                            if sn:
                                existing_names.add(sn)
                    except Exception:
                        pass
                    if name in existing_names or f"{name}-agent-space" in existing_names:
                        return jsonify({"ok": False, "error": f"Space 이름 '{name}' 이(가) 이미 존재합니다"})
                except Exception:
                    pass

        elif step == 2:
            # Verify account access via registry profiles
            from account_registry import registry
            account_id = data.get("primary_account_id", "")
            if account_id:
                try:
                    import boto3
                    profile = registry.get_profile(account_id) or ""
                    sess = boto3.Session(profile_name=profile, region_name=AWS_REGION) if profile else _boto_session()
                    sts = sess.client("sts")
                    identity = sts.get_caller_identity()
                    if identity.get("Account") != account_id:
                        warnings.append(f"STS 계정 불일치: 요청={account_id}, 실제={identity.get('Account')}")
                except Exception as e:
                    return jsonify({"ok": False, "error": f"Primary 계정 접근 실패: {e}"})

            # Verify secondary account if present
            sec_id = data.get("secondary_account_id", "")
            if sec_id:
                try:
                    import boto3
                    profile = registry.get_profile(sec_id) or ""
                    sess = boto3.Session(profile_name=profile, region_name=AWS_REGION) if profile else _boto_session()
                    sts = sess.client("sts")
                    sts.get_caller_identity()
                except Exception as e:
                    return jsonify({"ok": False, "error": f"Secondary 계정 접근 실패: {e}"})

            # Suggest default role ARN
            if account_id:
                app_name = data.get("app_name", "myapp")
                defaults["role_arn"] = f"arn:aws:iam::{account_id}:role/{app_name.lower().replace(' ', '-')}-devops-agent-role"

        elif step == 3:
            import socket

            datasources = data.get("datasources", [])
            if not datasources:
                # No data sources selected — just pass (optional step)
                pass

            for ds in datasources:
                ds_id = ds.get("id", "")
                provider = ds.get("provider", "")
                integ_id = ds.get("integration_id", "")

                # 1. Integration 활성 상태 확인
                if integ_id:
                    try:
                        pri_acct = data.get("primary_account_id", "")
                        session = _space_session(account_id=pri_acct) if pri_acct else _space_session()
                        client = session.client("devops-agent")
                        svc = client.get_service(serviceId=integ_id)
                        status = svc.get("service", {}).get("status", "")
                        if status and status.upper() not in ("ACTIVE", "CONNECTED"):
                            return jsonify({"ok": False, "error": f"{provider}: 서비스 상태가 '{status}' — 활성 상태가 아닙니다. 연결을 확인하세요."})
                    except Exception as e:
                        warnings.append(f"{provider} 서비스 상태 확인 실패: {e}")

                # 2. GitHub/GitLab 리포 접근 확인
                repo = ds.get("repo", "")
                if provider == "github" and repo and integ_id:
                    try:
                        pri_acct = data.get("primary_account_id", "")
                        session = _space_session(account_id=pri_acct) if pri_acct else _space_session()
                        client = session.client("devops-agent")
                        repos_resp = client.list_repositories(serviceId=integ_id)
                        repo_names = [r.get("name", "") for r in repos_resp.get("repositories", [])]
                        repo_short = repo.split("/")[-1] if "/" in repo else repo
                        if repo_names and repo_short not in repo_names and repo not in repo_names:
                            warnings.append(f"GitHub 리포 '{repo}' — 접근 가능 목록에 없음 (권한 확인 필요)")
                    except Exception as e:
                        warnings.append(f"GitHub 리포 목록 조회 실패: {e}")

                # 3. Private Connection 연결 테스트
                pc = ds.get("private_connection")
                if pc and isinstance(pc, dict):
                    if pc.get("existing"):
                        pass  # 기존 PC 사용 — 검증 불필요
                    else:
                        host = pc.get("host", "").strip()
                        port = int(pc.get("port", 443))
                        pc_mode = pc.get("connection_mode", "service_managed")
                        if not host:
                            return jsonify({"ok": False, "error": f"{provider}: Private Connection 호스트 주소가 비어 있습니다"})
                    if pc_mode == "self_managed":
                        pass  # Internal NLB — 로컬에서 소켓 테스트 불가, 배포 후 PC ACTIVE로 검증
                    else:
                        try:
                            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            sock.settimeout(5)
                            result = sock.connect_ex((host, port))
                            sock.close()
                            if result != 0:
                                return jsonify({"ok": False, "error": f"{provider}: {host}:{port} 연결 실패 — Private Connection 설정을 확인하세요"})
                        except socket.gaierror:
                            return jsonify({"ok": False, "error": f"{provider}: DNS 조회 실패 — '{host}' 호스트를 찾을 수 없습니다"})
                        except socket.timeout:
                            return jsonify({"ok": False, "error": f"{provider}: {host}:{port} 연결 시간 초과 (5초) — 네트워크 또는 방화벽 확인 필요"})
                        except Exception as e:
                            return jsonify({"ok": False, "error": f"{provider}: 연결 테스트 실패 — {e}"})

                # 4. Splunk Cloud endpoint 접근 확인
                if provider == "mcpserversplunk" and integ_id:
                    try:
                        import urllib.request
                        pri_acct = data.get("primary_account_id", "")
                        session = _space_session(account_id=pri_acct) if pri_acct else _space_session()
                        client = session.client("devops-agent")
                        svc_resp = client.get_service(serviceId=integ_id)
                        svc_detail = svc_resp.get("service", {})
                        # endpoint는 additionalServiceDetails.mcpserversplunk.endpoint에 위치
                        splunk_cfg = svc_detail.get("additionalServiceDetails", {}).get("mcpserversplunk", {})
                        endpoint = splunk_cfg.get("endpoint", "") or svc_detail.get("configuration", {}).get("endpoint", "")
                        if endpoint:
                            req = urllib.request.Request(endpoint, method="HEAD")
                            req.add_header("User-Agent", "devops-wizard-healthcheck")
                            try:
                                urllib.request.urlopen(req, timeout=5)
                            except urllib.error.HTTPError as he:
                                if he.code in (401, 403, 405):
                                    pass  # Auth/method error = endpoint reachable
                                else:
                                    warnings.append(f"Splunk 엔드포인트 응답 이상: HTTP {he.code}")
                            except Exception as ue:
                                warnings.append(f"Splunk 엔드포인트 접근 실패: {endpoint} — {ue}")
                        else:
                            warnings.append("Splunk 서비스에서 endpoint URL을 찾을 수 없습니다")
                    except Exception as e:
                        warnings.append(f"Splunk 엔드포인트 확인 실패: {e}")

        elif step == 4:
            # Verify Role ARN exists (if provided)
            role_arn = data.get("role_arn", "")
            if role_arn:
                import boto3
                from account_registry import registry as _reg
                account_id = data.get("primary_account_id", "")
                profile = _reg.get_profile(account_id) or ""
                sess = boto3.Session(profile_name=profile, region_name=AWS_REGION) if profile else _boto_session()
                iam = sess.client("iam")
                try:
                    role_name = role_arn.split("/")[-1]
                    iam.get_role(RoleName=role_name)
                except iam.exceptions.NoSuchEntityException:
                    return jsonify({"ok": False, "error": f"Role을 찾을 수 없습니다: {role_arn}"})
                except Exception as e:
                    warnings.append(f"Role 확인 실패 (배포 시 자동 생성됨): {e}")

            # Verify EKS cluster exists (if provided)
            # eks_cluster_name = secondary 계정 클러스터 (UI 메인 드롭다운)
            # secondary_eks_cluster = primary 계정 클러스터 (UI 추가 드롭다운)
            cluster_name = data.get("eks_cluster_name", "")
            if cluster_name:
                try:
                    import boto3
                    from account_registry import registry as _reg
                    sec_id = data.get("secondary_account_id", "")
                    target_id = sec_id or data.get("primary_account_id", "")
                    profile = _reg.get_profile(target_id) or ""
                    sess = boto3.Session(profile_name=profile, region_name=AWS_REGION) if profile else _boto_session()
                    eks = sess.client("eks", region_name=AWS_REGION)
                    eks.describe_cluster(name=cluster_name)
                except Exception as e:
                    warnings.append(f"EKS 클러스터 확인 실패: {cluster_name} — {e}")

        elif step == 5:
            # Step 5 is optional, just pass through
            pass

        return jsonify({"ok": True, "warnings": warnings, "defaults": defaults})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ===================================================================
# CloudFormation Template Generator
# ===================================================================

@space_bp.route("/api/spaces/generate-cfn", methods=["POST"])
def api_generate_cfn():
    """Generate a CloudFormation YAML template from wizard data."""
    data = request.get_json(force=True)
    result = api_generate_cfn_internal(data)
    return jsonify(result)


# ===================================================================
# CloudFormation Deploy (create_stack + SSE polling)
# ===================================================================

@space_bp.route("/api/spaces/deploy-cfn", methods=["POST"])
def api_deploy_cfn():
    """Deploy the generated CFn template(s) via create_stack, stream events via SSE.

    If secondary account differs from primary, deploys cross-account role stack first,
    then primary stack.
    """

    data = request.get_json(force=True)

    def _deploy_and_poll(cfn, stack_name, template_body, tags, label=""):
        """Create stack and poll until terminal. Yields SSE events, last yield is final status string (not SSE)."""
        prefix = f"[{label}] " if label else ""

        try:
            resp = cfn.create_stack(
                StackName=stack_name,
                TemplateBody=template_body,
                Capabilities=["CAPABILITY_NAMED_IAM"],
                Tags=tags,
                DisableRollback=True,
            )
            stack_id = resp["StackId"]
        except cfn.exceptions.ClientError as ce:
            if "AlreadyExistsException" in str(ce):
                try:
                    cfn.update_stack(StackName=stack_name, TemplateBody=template_body, Capabilities=["CAPABILITY_NAMED_IAM"])
                except Exception as ue:
                    if "No updates are to be performed" in str(ue):
                        yield ("done", "CREATE_COMPLETE", stack_name)
                        return
                    raise
                stack_id = stack_name
            else:
                raise
        seen_events = set()
        terminal_states = {"CREATE_COMPLETE", "UPDATE_COMPLETE", "CREATE_FAILED", "UPDATE_FAILED", "ROLLBACK_COMPLETE", "ROLLBACK_FAILED", "UPDATE_ROLLBACK_COMPLETE"}

        for _ in range(120):
            time.sleep(5)
            try:
                desc = cfn.describe_stacks(StackName=stack_id)
                stack_status = desc["Stacks"][0]["StackStatus"]

                events_resp = cfn.describe_stack_events(StackName=stack_id)
                for evt in reversed(events_resp.get("StackEvents", [])):
                    evt_id = evt["EventId"]
                    if evt_id in seen_events:
                        continue
                    seen_events.add(evt_id)
                    resource = evt.get("LogicalResourceId", "")
                    status = evt.get("ResourceStatus", "")
                    yield ("event", f"data: {json.dumps({'type': 'event', 'resource': f'{prefix}{resource}', 'status': status})}\n\n")

                if stack_status in terminal_states:
                    yield ("done", stack_status, stack_id)
                    return
            except Exception as poll_err:
                yield ("event", f"data: {json.dumps({'type': 'event', 'resource': f'{prefix}polling', 'status': str(poll_err)})}\n\n")

        yield ("done", "TIMEOUT", stack_id)

    def generate():
        try:
            name = data.get("name", "my-agent-space")
            gen_resp = api_generate_cfn_internal(data)
            if not gen_resp.get("ok"):
                yield f"data: {json.dumps({'type': 'error', 'error': gen_resp.get('error', '템플릿 생성 실패')})}\n\n"
                return

            primary_account_id = data.get("primary_account_id", "")
            secondary_account_id = gen_resp.get("secondary_account_id", "")
            deploy_tags = [
                {"Key": data.get("app_tag_key", "App"), "Value": data.get("app_tag_value", name)},
                {"Key": "auto-delete", "Value": "never"},
                {"Key": "CreatedBy", "Value": "devops-agent-wizard"},
            ]

            # --- Phase 1: Deploy secondary cross-account role (if needed) ---
            if gen_resp.get("secondary_yaml"):
                yield f"data: {json.dumps({'type': 'event', 'resource': 'SecondaryStack', 'status': 'CREATE_IN_PROGRESS'})}\n\n"

                sec_session = _session_for_account_id(secondary_account_id)
                sec_cfn = sec_session.client("cloudformation")
                sec_stack_name = f"{name}-xaccount-role"

                sec_status = "TIMEOUT"
                for msg in _deploy_and_poll(sec_cfn, sec_stack_name, gen_resp["secondary_yaml"], deploy_tags, "secondary"):
                    if msg[0] == "event":
                        yield msg[1]
                    elif msg[0] == "done":
                        sec_status = msg[1]

                if sec_status not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
                    yield f"data: {json.dumps({'type': 'error', 'error': f'Secondary 스택 실패: {sec_status}'})}\n\n"
                    return

                yield f"data: {json.dumps({'type': 'event', 'resource': 'SecondaryStack', 'status': 'CREATE_COMPLETE'})}\n\n"

            # --- Phase 2: Deploy primary stack ---
            yield f"data: {json.dumps({'type': 'event', 'resource': 'PrimaryStack', 'status': 'CREATE_IN_PROGRESS'})}\n\n"

            session = _session_for_account_id(primary_account_id) if primary_account_id else _boto_session()
            cfn = session.client("cloudformation")
            stack_name = f"{name}-devops-agent"

            primary_status = "TIMEOUT"
            primary_stack_id = ""
            for msg in _deploy_and_poll(cfn, stack_name, gen_resp["yaml"], deploy_tags, "primary"):
                if msg[0] == "event":
                    yield msg[1]
                elif msg[0] == "done":
                    primary_status = msg[1]
                    primary_stack_id = msg[2] if len(msg) > 2 else ""

            if primary_status in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
                # Register space in app DDB
                try:
                    new_space_id = ""
                    desc = cfn.describe_stack_resources(StackName=primary_stack_id)
                    for res in desc.get("StackResources", []):
                        if res.get("LogicalResourceId") == "DevOpsAgentSpace":
                            new_space_id = res.get("PhysicalResourceId", "")
                            break
                    if new_space_id:
                        # Build aws_config with role_arn from CFn stack
                        primary_role_arn = ""
                        for res in desc.get("StackResources", []):
                            if res.get("LogicalResourceId") == "DevOpsAgentRole":
                                primary_role_arn = res.get("PhysicalResourceId", "")
                                break
                        if primary_role_arn and not primary_role_arn.startswith("arn:"):
                            primary_role_arn = f"arn:aws:iam::{primary_account_id}:role/{primary_role_arn}"

                        aws_config = {"aws": {"account_id": primary_account_id, "account_type": "monitor", "role_arn": primary_role_arn}}
                        if secondary_account_id:
                            sec_role = f"arn:aws:iam::{secondary_account_id}:role/{name}-xaccount-devops-agent-role"
                            aws_config["sourceAws"] = {"account_id": secondary_account_id, "account_type": "source", "role_arn": sec_role}

                        from account_registry import registry as _reg
                        profile = _reg.get_profile(primary_account_id) or ""

                        app_session = _boto_session()
                        _save_space_metadata(app_session, new_space_id, {
                            "name": name,
                            "app_name": data.get("app_name", name),
                            "app_tag_key": data.get("app_tag_key", "App"),
                            "app_tag_value": data.get("app_tag_value", ""),
                            "account_id": primary_account_id,
                            "profile": profile,
                            "stack_name": f"{name}-devops-agent",
                            "managed": True,
                            "deploy_method": "cloudformation",
                            "deploy_status": "CREATE_COMPLETE",
                            "aws_config": aws_config,
                            "integrations": data.get("integrations", []),
                        })
                except Exception as reg_err:
                    yield f"data: {json.dumps({'type': 'event', 'resource': 'AppRegistry', 'status': f'WARNING: {reg_err}'})}\n\n"

                # EventChannel + webhook secret 설정
                if new_space_id:
                    try:
                        import boto3 as _boto3
                        region = AWS_REGION
                        space_session = _boto3.Session(profile_name=profile, region_name=region) if profile else _boto_session()
                        da_client = space_session.client("devops-agent", region_name=region)
                        ec_result = _setup_event_channel(da_client, space_session, new_space_id)
                        # DDB metadata에 integration 추가
                        _append_integration(app_session, new_space_id, {
                            "service_id": ec_result["service_id"],
                            "association_id": ec_result["association_id"],
                            "provider": "eventChannel",
                            "name": "DevOps Webhook",
                        })
                        yield f"data: {json.dumps({'type': 'event', 'resource': 'DevOps Webhook', 'status': 'CREATED'})}\n\n"
                    except Exception as ec_err:
                        yield f"data: {json.dumps({'type': 'event', 'resource': 'DevOps Webhook', 'status': f'WARNING: {ec_err}'})}\n\n"

                yield f"data: {json.dumps({'type': 'complete', 'stack_id': primary_stack_id, 'space_id': new_space_id})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'error', 'error': f'Primary 스택 실패: {primary_status}'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return Response(stream_with_context(generate()), content_type="text/event-stream")


def api_generate_cfn_internal(data):
    """Internal helper: generates CFn template dict and returns {'ok': True, 'yaml': ..., 'filename': ...}."""
    import yaml

    try:
        name = data.get("name", "my-agent-space")
        app_name = data.get("app_name", "MyApp")
        tag_key = data.get("app_tag_key", "App")
        tag_value = data.get("app_tag_value", app_name)
        primary_account_id = data.get("primary_account_id", "")
        secondary_account_id = data.get("secondary_account_id", "")
        resource_tags = data.get("resource_tags", [])
        resources_list = data.get("resources", [])
        eks_cluster = data.get("eks_cluster_name", "")
        integrations = data.get("integrations", [])
        github_repo = data.get("github_repo", "")
        private_services = data.get("private_services", [])

        template = {
            "AWSTemplateFormatVersion": "2010-09-09",
            "Description": f"DevOps Agent - {name} (auto-generated by wizard)",
            "Parameters": {
                "ProjectName": {"Type": "String", "Default": name},
            },
            "Resources": {},
        }

        # Agent Space
        template["Resources"]["DevOpsAgentSpace"] = {
            "Type": "AWS::DevOpsAgent::AgentSpace",
            "Properties": {
                "Name": {"Fn::Sub": "${ProjectName}-agent-space"},
                "Description": f"Agent Space for {app_name}",
                "Tags": [
                    {"Key": tag_key, "Value": tag_value},
                    {"Key": "auto-delete", "Value": "never"},
                ],
            },
        }

        # IAM Role
        template["Resources"]["DevOpsAgentRole"] = {
            "Type": "AWS::IAM::Role",
            "Properties": {
                "RoleName": {"Fn::Sub": "${ProjectName}-devops-agent-role"},
                "AssumeRolePolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Principal": {"Service": "aidevops.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                        "Condition": {
                            "StringEquals": {"aws:SourceAccount": {"Ref": "AWS::AccountId"}},
                            "ArnLike": {"aws:SourceArn": {"Fn::Sub": "arn:aws:aidevops:*:${AWS::AccountId}:agentspace/*"}},
                        },
                    }],
                },
                "ManagedPolicyArns": ["arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy"],
                "Tags": [
                    {"Key": tag_key, "Value": tag_value},
                    {"Key": "auto-delete", "Value": "never"},
                ],
            },
        }

        # Monitor Association with Tags + Resources
        monitor_cfg = {
            "Aws": {
                "AccountId": {"Ref": "AWS::AccountId"} if not primary_account_id else primary_account_id,
                "AccountType": "monitor",
                "AssumableRoleArn": {"Fn::GetAtt": ["DevOpsAgentRole", "Arn"]},
            }
        }
        if resource_tags:
            monitor_cfg["Aws"]["Tags"] = [{"Key": t["key"], "Value": t["value"]} for t in resource_tags]
        if resources_list:
            monitor_cfg["Aws"]["Resources"] = [
                {"ResourceType": r["resource_type"], "ResourceArn": r["resource_arn"]} for r in resources_list
            ]

        template["Resources"]["MonitorAssociation"] = {
            "Type": "AWS::DevOpsAgent::Association",
            "DependsOn": ["DevOpsAgentSpace", "DevOpsAgentRole"],
            "Properties": {
                "AgentSpaceId": {"Fn::GetAtt": ["DevOpsAgentSpace", "AgentSpaceId"]},
                "ServiceId": "aws",
                "Configuration": monitor_cfg,
            },
        }

        # Source Account (secondary)
        secondary_eks_cluster = data.get("secondary_eks_cluster", "")
        if secondary_account_id:
            # SourceAws Association in primary template (references the cross-account role)
            source_cfg = {
                "SourceAws": {
                    "AccountId": secondary_account_id,
                    "AccountType": "source",
                    "AssumableRoleArn": {"Fn::Sub": f"arn:aws:iam::{secondary_account_id}:role/${{ProjectName}}-xaccount-devops-agent-role"},
                }
            }
            if resource_tags:
                source_cfg["SourceAws"]["Tags"] = [{"Key": t["key"], "Value": t["value"]} for t in resource_tags]

            template["Resources"]["SourceAccountAssociation"] = {
                "Type": "AWS::DevOpsAgent::Association",
                "DependsOn": ["DevOpsAgentSpace"],
                "Properties": {
                    "AgentSpaceId": {"Fn::GetAtt": ["DevOpsAgentSpace", "AgentSpaceId"]},
                    "ServiceId": "aws",
                    "Configuration": source_cfg,
                },
            }

        # Integration associations — use PROVIDER_REGISTRY + build_association_config as single source of truth
        from datasource_manager import PROVIDER_REGISTRY, build_association_config
        for integ in integrations:
            if isinstance(integ, str):
                continue
            provider = integ.get("provider", "").lower()
            integ_id = integ.get("integration_id", "")
            if not provider or not integ_id:
                continue
            reg = PROVIDER_REGISTRY.get(provider)
            if not reg:
                continue

            # GitHub: resolve repo_id from GitHub API if missing
            if provider == "github" and integ.get("repo") and not integ.get("repo_id"):
                try:
                    import urllib.request
                    repo_path = integ["repo"]
                    gh_resp = urllib.request.urlopen(f"https://api.github.com/repos/{repo_path}", timeout=10)
                    gh_data = json.loads(gh_resp.read())
                    integ["repo_id"] = str(gh_data.get("id", ""))
                except Exception:
                    pass

            resource_name = reg["cfn_resource_prefix"]
            cfn_key = reg["cfn_config_key"]
            api_cfg = build_association_config(integ)
            cfn_fields = {}
            for _k, fields in api_cfg.items():
                cfn_fields = {k[0].upper() + k[1:]: v for k, v in fields.items()} if fields else {}
            assoc_cfg = {cfn_key: cfn_fields}

            template["Resources"][resource_name] = {
                "Type": "AWS::DevOpsAgent::Association",
                "DependsOn": ["DevOpsAgentSpace"],
                "Properties": {
                    "AgentSpaceId": {"Fn::GetAtt": ["DevOpsAgentSpace", "AgentSpaceId"]},
                    "ServiceId": integ_id,
                    "Configuration": assoc_cfg,
                },
            }

        # EKS Access Entry — only for primary account's own cluster
        # If eks_cluster belongs to secondary account, it goes in sec_template instead
        if eks_cluster and not secondary_account_id:
            template["Resources"]["DevOpsAgentAccessEntry"] = {
                "Type": "AWS::EKS::AccessEntry",
                "Properties": {
                    "ClusterName": eks_cluster,
                    "PrincipalArn": {"Fn::GetAtt": ["DevOpsAgentRole", "Arn"]},
                    "Type": "STANDARD",
                    "AccessPolicies": [{
                        "PolicyArn": "arn:aws:eks::aws:cluster-access-policy/AmazonEKSViewPolicy",
                        "AccessScope": {"Type": "cluster"},
                    }],
                },
            }

        # 데이터소스 (Splunk Cloud, etc.)
        for integ in integrations:
            if isinstance(integ, str):
                continue
            integ_type = integ.get("type", "")
            if integ_type == "mcpserversplunk":
                existing_service_id = integ.get("existing_service_id", "")
                enable_webhook = integ.get("enable_webhook", True)

                if existing_service_id:
                    # 기존 데이터소스 연결 — Association만 생성
                    template["Resources"]["AssociationSplunkCloud"] = {
                        "Type": "AWS::DevOpsAgent::Association",
                        "DependsOn": ["DevOpsAgentSpace"],
                        "Properties": {
                            "AgentSpaceId": {"Fn::GetAtt": ["DevOpsAgentSpace", "AgentSpaceId"]},
                            "ServiceId": existing_service_id,
                            "Configuration": {
                                "MCPServerSplunk": {
                                    "EnableWebhookUpdates": enable_webhook,
                                }
                            },
                        },
                    }
                else:
                    # 새 데이터소스 등록 + 연결
                    svc_name = integ.get("name", "splunk-cloud")
                    endpoint = integ.get("endpoint", "")
                    token_value = integ.get("token_value", "")
                    if not endpoint:
                        continue
                    svc_resource = "ServiceSplunkCloud"
                    template["Resources"][svc_resource] = {
                        "Type": "AWS::DevOpsAgent::Service",
                        "Properties": {
                            "ServiceType": "mcpserversplunk",
                            "ServiceDetails": {
                                "MCPServerSplunk": {
                                    "Name": svc_name,
                                    "Endpoint": endpoint,
                                    "AuthorizationConfig": {
                                        "BearerToken": {
                                            "TokenName": f"{svc_name}-token",
                                            "TokenValue": token_value,
                                        }
                                    },
                                }
                            },
                        },
                    }
                    template["Resources"]["AssociationSplunkCloud"] = {
                        "Type": "AWS::DevOpsAgent::Association",
                        "DependsOn": ["DevOpsAgentSpace", svc_resource],
                        "Properties": {
                            "AgentSpaceId": {"Fn::GetAtt": ["DevOpsAgentSpace", "AgentSpaceId"]},
                            "ServiceId": {"Fn::GetAtt": [svc_resource, "ServiceId"]},
                            "Configuration": {
                                "MCPServerSplunk": {
                                    "Name": svc_name,
                                    "EnableWebhookUpdates": enable_webhook,
                                }
                            },
                        },
                    }

        # Private 데이터소스 (GitLab, MCP Server, etc.)
        for idx, svc in enumerate(private_services):
            svc_type = svc.get("type", "")  # gitlab, mcpserver, mcpserversplunk
            svc_name = svc.get("name", f"private-svc-{idx}")
            host_address = svc.get("host_address", "")
            vpc_id = svc.get("vpc_id", "")
            subnet_ids = svc.get("subnet_ids", [])
            security_group_ids = svc.get("security_group_ids", [])
            port_ranges = svc.get("port_ranges", ["443"])
            certificate = svc.get("certificate", "")

            if not host_address or not vpc_id:
                continue

            safe_name = svc_name.replace("-", "").replace("_", "").capitalize()

            # Private Connection
            pc_resource = f"PrivateConnection{safe_name}"
            pc_props = {
                "Name": svc_name,
                "ConnectionConfiguration": {
                    "ServiceManaged": {
                        "HostAddress": host_address,
                        "VpcId": vpc_id,
                    }
                },
            }
            if subnet_ids:
                pc_props["ConnectionConfiguration"]["ServiceManaged"]["SubnetIds"] = subnet_ids
            if security_group_ids:
                pc_props["ConnectionConfiguration"]["ServiceManaged"]["SecurityGroupIds"] = security_group_ids
            if port_ranges:
                pc_props["ConnectionConfiguration"]["ServiceManaged"]["PortRanges"] = port_ranges
            if certificate:
                pc_props["Certificate"] = certificate

            template["Resources"][pc_resource] = {
                "Type": "AWS::DevOpsAgent::PrivateConnection",
                "Properties": pc_props,
            }

            # Service Registration
            svc_resource = f"Service{safe_name}"
            svc_details = {}

            if svc_type == "gitlab":
                target_url = svc.get("target_url", f"https://{host_address}/")
                token_type = svc.get("token_type", "personal")
                token_value = svc.get("token_value", "")
                svc_details = {
                    "GitLab": {
                        "TargetUrl": target_url,
                        "TokenType": token_type,
                        "TokenValue": token_value,
                    }
                }
                if svc.get("group_id"):
                    svc_details["GitLab"]["GroupId"] = svc["group_id"]

            elif svc_type in ("mcpserver", "mcpserversplunk"):
                endpoint = svc.get("endpoint", f"https://{host_address}/mcp")
                auth_type = svc.get("auth_type", "api_key")
                mcp_name = svc.get("mcp_name", svc_name)

                if svc_type == "mcpserversplunk":
                    svc_details = {
                        "MCPServerSplunk": {
                            "Name": mcp_name,
                            "Endpoint": endpoint,
                            "AuthorizationConfig": {
                                "BearerToken": {
                                    "TokenName": svc.get("token_name", "splunk-token"),
                                    "TokenValue": svc.get("token_value", ""),
                                }
                            },
                        }
                    }
                else:
                    auth_cfg = {}
                    if auth_type == "api_key":
                        auth_cfg = {
                            "ApiKey": {
                                "ApiKeyName": svc.get("api_key_name", "api-key"),
                                "ApiKeyValue": svc.get("api_key_value", ""),
                                "ApiKeyHeader": svc.get("api_key_header", "Authorization"),
                            }
                        }
                    elif auth_type == "oauth":
                        auth_cfg = {
                            "OAuthClientCredentials": {
                                "ClientId": svc.get("oauth_client_id", ""),
                                "ClientSecret": svc.get("oauth_client_secret", ""),
                                "ExchangeUrl": svc.get("oauth_exchange_url", ""),
                            }
                        }
                    svc_details = {
                        "MCPServer": {
                            "Name": mcp_name,
                            "Endpoint": endpoint,
                            "AuthorizationConfig": auth_cfg,
                        }
                    }

            if svc_details:
                template["Resources"][svc_resource] = {
                    "Type": "AWS::DevOpsAgent::Service",
                    "DependsOn": [pc_resource],
                    "Properties": {
                        "ServiceType": svc_type,
                        "ServiceDetails": svc_details,
                    },
                }

                # Association
                assoc_resource = f"Association{safe_name}"
                svc_reg = PROVIDER_REGISTRY.get(svc_type, {})
                svc_cfn_key = svc_reg.get("cfn_config_key", svc_type.capitalize())
                assoc_cfg = {svc_cfn_key: {"ServiceInstanceId": {"Fn::GetAtt": [svc_resource, "ServiceId"]}}} if svc_reg else {}

                if assoc_cfg:
                    template["Resources"][assoc_resource] = {
                        "Type": "AWS::DevOpsAgent::Association",
                        "DependsOn": ["DevOpsAgentSpace", svc_resource],
                        "Properties": {
                            "AgentSpaceId": {"Fn::GetAtt": ["DevOpsAgentSpace", "AgentSpaceId"]},
                            "ServiceId": {"Fn::GetAtt": [svc_resource, "ServiceId"]},
                            "Configuration": assoc_cfg,
                        },
                    }

        yaml_str = yaml.dump(template, default_flow_style=False, allow_unicode=True, sort_keys=False)
        result = {"ok": True, "yaml": yaml_str, "filename": f"{name}-devops-agent.yml"}

        # Secondary 계정이 primary와 다를 때 → cross-account role CFN 생성
        if secondary_account_id and secondary_account_id != primary_account_id:
            sec_template = {
                "AWSTemplateFormatVersion": "2010-09-09",
                "Description": f"DevOps Agent - Cross-Account Role for {name} (secondary account)",
                "Parameters": {
                    "ProjectName": {"Type": "String", "Default": name},
                    "PrimaryAccountId": {
                        "Type": "String",
                        "Default": primary_account_id,
                        "Description": "Account ID where the Agent Space lives",
                    },
                },
                "Resources": {
                    "CrossAccountDevOpsAgentRole": {
                        "Type": "AWS::IAM::Role",
                        "Properties": {
                            "RoleName": {"Fn::Sub": "${ProjectName}-xaccount-devops-agent-role"},
                            "AssumeRolePolicyDocument": {
                                "Version": "2012-10-17",
                                "Statement": [{
                                    "Effect": "Allow",
                                    "Principal": {"Service": "aidevops.amazonaws.com"},
                                    "Action": "sts:AssumeRole",
                                    "Condition": {
                                        "StringEquals": {"aws:SourceAccount": {"Ref": "PrimaryAccountId"}},
                                        "ArnLike": {"aws:SourceArn": {"Fn::Sub": "arn:aws:aidevops:*:${PrimaryAccountId}:agentspace/*"}},
                                    },
                                }],
                            },
                            "ManagedPolicyArns": ["arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy"],
                            "Tags": [
                                {"Key": tag_key, "Value": tag_value},
                                {"Key": "auto-delete", "Value": "never"},
                            ],
                        },
                    },
                },
                "Outputs": {
                    "CrossAccountRoleArn": {
                        "Description": "Cross-account DevOps Agent Role ARN",
                        "Value": {"Fn::GetAtt": ["CrossAccountDevOpsAgentRole", "Arn"]},
                    },
                },
            }

            # EKS AccessEntry — eks_cluster belongs to secondary account
            target_cluster = eks_cluster or secondary_eks_cluster
            if target_cluster:
                sec_template["Resources"]["CrossAccountAccessEntry"] = {
                    "Type": "AWS::EKS::AccessEntry",
                    "Properties": {
                        "ClusterName": target_cluster,
                        "PrincipalArn": {"Fn::GetAtt": ["CrossAccountDevOpsAgentRole", "Arn"]},
                        "Type": "STANDARD",
                        "AccessPolicies": [{
                            "PolicyArn": "arn:aws:eks::aws:cluster-access-policy/AmazonEKSViewPolicy",
                            "AccessScope": {"Type": "cluster"},
                        }],
                    },
                }

            sec_yaml_str = yaml.dump(sec_template, default_flow_style=False, allow_unicode=True, sort_keys=False)
            result["secondary_yaml"] = sec_yaml_str
            result["secondary_filename"] = f"{name}-xaccount-role.yml"
            result["secondary_account_id"] = secondary_account_id

        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}
