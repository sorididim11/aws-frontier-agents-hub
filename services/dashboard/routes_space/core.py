"""
Core routes and shared helpers for routes_space package.

Includes:
  - Shared APIs: /api/jserror, /api/history
  - SPACE routes: /, /api/spaces, /api/space-info, /api/tagged-resources,
    /api/tagged-resources-all, /api/permission-check
  - VPC / Subnet / SG / NLB / Private Connections lookup
  - Shared helper functions used across sub-modules
"""
import json
import time

from flask import render_template, jsonify, request

from app_config import (
    _CFG, _cfg_get, AWS_REGION, AGENT_SPACE_ID, RUNS_TABLE, AWS_PROFILE,
    _req_space_id, _agent_space_id,
    _boto_session, _space_session, _assumed_session, _get_or_create_session,
    _session_for_association, _session_for_space, _get_aws_associations,
    _session_for_account_id,
    _tag_key_for_space, _tag_value_for_space, _fetch_tagged_resources,
)

from routes_space import space_bp

# ===================================================================
# Shared APIs
# ===================================================================

@space_bp.route("/api/jserror", methods=["POST"])
def js_error():
    data = request.get_json(silent=True) or {}
    print(f"\n*** JS ERROR: {data.get('message', '?')} ***\n{data.get('stack', '')}\n", flush=True)
    return jsonify({"ok": True})


@space_bp.route("/api/history")
def api_history():
    limit = request.args.get("limit", 30, type=int)
    space_id = request.args.get("space_id")
    if not space_id:
        return jsonify({"items": []})
    try:
        session = _space_session(space_id)
        client = session.client("devops-agent")

        # 1. Agent API = 원본 (조사 이력)
        task_resp = client.list_backlog_tasks(agentSpaceId=space_id)
        tasks = [t for t in task_resp.get("tasks", [])
                 if t.get("taskType") == "INVESTIGATION"]

        # 2. DB = 부가 정보 (scenario_id, run_id, 평가 등)
        db_by_task = {}
        try:
            app_session = _boto_session()
            tbl = app_session.resource("dynamodb").Table(RUNS_TABLE)
            from boto3.dynamodb.conditions import Attr
            resp = tbl.scan(
                FilterExpression=Attr("record_type").eq("run") & Attr("agent_space_id").eq(space_id),
                Limit=max(limit * 3, 100),
            )
            # incident_id → task_id 매핑 (backfill용)
            task_id_set = {t.get("taskId", "") for t in tasks}
            for item in resp.get("Items", []):
                tid = item.get("investigation_task_id", "")
                if tid:
                    db_by_task[tid] = item
                elif item.get("incident_id"):
                    # investigation_task_id 미연결 — incident_id로 역매칭 시도
                    iid = item["incident_id"]
                    for t in tasks:
                        ref_obj = t.get("reference", {}) or {}
                        ref = ref_obj.get("referenceId", "") if isinstance(ref_obj, dict) else ""
                        if ref == iid:
                            tid = t.get("taskId", "")
                            item["investigation_task_id"] = tid
                            db_by_task[tid] = item
                            # DDB backfill
                            try:
                                tbl.update_item(
                                    Key={"run_id": item["run_id"], "record_type": "run"},
                                    UpdateExpression="SET investigation_task_id = :tid",
                                    ExpressionAttributeValues={":tid": tid},
                                )
                            except Exception:
                                pass
                            break
        except Exception as e:
            print(f"[HISTORY] DB 조회 실패 (비치명적): {e}", flush=True)

        # 3. 합치기: Agent 데이터 + DB 부가 정보
        items = []
        for t in tasks:
            tid = t.get("taskId", "")
            ca = t.get("createdAt", "")
            db_item = db_by_task.pop(tid, {})
            items.append({
                "run_id": db_item.get("run_id", ""),
                "scenario_id": db_item.get("scenario_id", ""),
                "agent_space_id": space_id,
                "status": (t.get("status", "")).lower(),
                "investigation_task_id": tid,
                "started_at": db_item.get("started_at", "") or (str(ca)[:19] if ca else ""),
                "created_at": str(ca)[:19] if ca else "",
            })

        # 4. DB에만 있는 레코드 (Agent에서 삭제됐거나 조사 미연결 시나리오)
        for tid, db_item in db_by_task.items():
            items.append({
                "run_id": db_item.get("run_id", ""),
                "scenario_id": db_item.get("scenario_id", ""),
                "agent_space_id": space_id,
                "status": db_item.get("status", ""),
                "investigation_task_id": tid,
                "started_at": db_item.get("started_at", ""),
                "created_at": db_item.get("created_at", ""),
            })

        items.sort(key=lambda x: x.get("started_at") or x.get("created_at") or "", reverse=True)
        return jsonify({"items": items[:limit]})
    except Exception as e:
        return jsonify({"items": [], "error": str(e)})


# ===================================================================
# SPACE routes  (was space_app.py)
# ===================================================================

SERVICE_LABELS = {
    'cloudwatch': 'CloudWatch', 'xray': 'X-Ray', 'logs': 'CloudWatch Logs',
    'eks': 'EKS', 'ec2': 'EC2 / VPC', 'ecr': 'ECR', 'rds': 'RDS',
    'secretsmanager': 'Secrets Manager', 'lambda': 'Lambda', 'dynamodb': 'DynamoDB',
    'sns': 'SNS', 'events': 'EventBridge', 'ssm': 'Systems Manager',
    'iam': 'IAM', 'aidevops': 'AI DevOps',
}

SVC_TEST_ACTIONS = {
    'cloudwatch':      {'read': ['cloudwatch:DescribeAlarms'],       'write': ['cloudwatch:PutMetricData']},
    'ec2':             {'read': ['ec2:DescribeInstances'],           'write': ['ec2:TerminateInstances']},
    'eks':             {'read': ['eks:DescribeCluster'],             'write': ['eks:DeleteCluster']},
    'ecr':             {'read': ['ecr:DescribeRepositories'],        'write': ['ecr:DeleteRepository']},
    'rds':             {'read': ['rds:DescribeDBInstances'],         'write': ['rds:DeleteDBInstance']},
    'logs':            {'read': ['logs:GetLogEvents'],               'write': ['logs:PutLogEvents']},
    'xray':            {'read': ['xray:GetTraceSummaries'],          'write': ['xray:PutTraceSegments']},
    'secretsmanager':  {'read': ['secretsmanager:ListSecrets'],      'write': ['secretsmanager:DeleteSecret']},
    'lambda':          {'read': ['lambda:ListFunctions'],            'write': ['lambda:DeleteFunction']},
    'dynamodb':        {'read': ['dynamodb:DescribeTable'],          'write': ['dynamodb:DeleteTable']},
    'sns':             {'read': ['sns:ListTopics'],                  'write': ['sns:Publish']},
    'events':          {'read': ['events:ListRules'],                'write': ['events:DeleteRule']},
    'ssm':             {'read': ['ssm:GetParameter'],                'write': ['ssm:DeleteParameter']},
    'iam':             {'read': ['iam:GetRole'],                     'write': ['iam:CreateRole']},
    'aidevops':        {'read': ['aidevops:ListChats'],              'write': ['aidevops:CreateChat']},
}

def _list_lambda_arns(s):
    return [f["FunctionArn"] for f in s.client("lambda").list_functions(MaxItems=50).get("Functions", [])]

def _list_dynamodb_arns(s):
    acct = s.client("sts").get_caller_identity()["Account"]
    return [f"arn:aws:dynamodb:{s.region_name}:{acct}:table/{t}" for t in s.client("dynamodb").list_tables(Limit=50).get("TableNames", [])]

def _list_secretsmanager_arns(s):
    return [sec["ARN"] for sec in s.client("secretsmanager").list_secrets(MaxResults=50).get("SecretList", [])]

def _list_rds_arns(s):
    return [db["DBInstanceArn"] for db in s.client("rds").describe_db_instances(MaxRecords=50).get("DBInstances", [])]

BOUNDARY_PROBES = {
    "lambda":         {"action": "lambda:GetFunction",            "list_fn": _list_lambda_arns},
    "dynamodb":       {"action": "dynamodb:DescribeTable",        "list_fn": _list_dynamodb_arns},
    "secretsmanager": {"action": "secretsmanager:DescribeSecret", "list_fn": _list_secretsmanager_arns},
    "rds":            {"action": "rds:DescribeDBInstances",       "list_fn": _list_rds_arns},
}

DATA_SOURCES = [
    {"id": "cloudwatch", "label": "CloudWatch", "category": "monitoring",
     "permissions": ["GetMetricData", "GetMetricStatistics", "ListMetrics", "DescribeAlarms", "DescribeAlarmHistory"]},
    {"id": "xray", "label": "X-Ray", "category": "trace",
     "permissions": ["GetTraceSummaries", "BatchGetTraces", "GetServiceGraph", "GetTraceGraph"]},
    {"id": "logs", "label": "CloudWatch Logs", "category": "log",
     "permissions": ["GetLogEvents", "FilterLogEvents", "DescribeLogGroups", "DescribeLogStreams", "StartQuery", "StopQuery", "GetQueryResults"]},
    {"id": "eks", "label": "EKS", "category": "container",
     "permissions": ["DescribeCluster", "ListClusters", "DescribeNodegroup", "ListNodegroups", "AccessKubernetesApi"]},
    {"id": "ec2", "label": "EC2 / VPC", "category": "compute",
     "permissions": ["DescribeInstances", "DescribeSecurityGroups", "DescribeSubnets", "DescribeVpcs"]},
    {"id": "ecr", "label": "ECR", "category": "registry",
     "permissions": ["DescribeRepositories", "DescribeImages", "ListImages"]},
    {"id": "rds", "label": "RDS", "category": "database",
     "permissions": ["DescribeDBInstances", "DescribeDBClusters", "DescribeEvents"]},
    {"id": "secrets", "label": "Secrets Manager", "category": "security",
     "permissions": ["ListSecrets", "DescribeSecret"]},
]


@space_bp.route("/")
def space_index():
    return render_template("space.html", cache_bust=int(time.time()))


@space_bp.route("/api/spaces")
def api_spaces():
    """등록된 Space 목록 반환 (DDB 기반). 미등록 Space는 /api/spaces/discover에서 탐색."""
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
                    "name": item.get("space_name", ""),
                    "description": item.get("description", ""),
                    "app_name": item.get("app_name", ""),
                    "app_tag_value": item.get("app_tag_value", ""),
                    "account_id": item.get("account_id", ""),
                    "managed": item.get("managed", False),
                    "deploy_method": item.get("deploy_method", ""),
                    "deploy_status": item.get("deploy_status", ""),
                    "created_at": item.get("created_at", ""),
                    "updated_at": item.get("updated_at", ""),
                })

        spaces.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return jsonify({"ok": True, "spaces": spaces})
    except Exception as e:
        return jsonify({"ok": False, "spaces": [], "error": str(e)})


@space_bp.route("/api/tagged-resources")
def api_tagged_resources():
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    tag_key = request.args.get("tag_key") or _tag_key_for_space(space_id)
    tag_value = request.args.get("tag_value", "true")
    role_arn = request.args.get("role_arn", "")
    if not tag_key:
        return jsonify({"ok": True, "total": 0, "by_service": {}, "no_boundary": True})
    try:
        sess = _get_or_create_session(role_arn) if role_arn else None
        total, by_service = _fetch_tagged_resources(tag_key, tag_value, session=sess)
        return jsonify({"ok": True, "total": total, "by_service": by_service})
    except Exception as e:
        return jsonify({"ok": False, "total": 0, "by_service": {}, "error": str(e)})


_tagged_cache = {}  # {space_id: {"data": ..., "ts": ...}}

@space_bp.route("/api/tagged-resources-all")
def api_tagged_resources_all():
    """Fetch tagged resources from all associated accounts. Standard: App tag key."""
    space_id = request.args.get("space_id", AGENT_SPACE_ID)

    if space_id in _tagged_cache and (time.time() - _tagged_cache[space_id]["ts"]) < 120:
        return jsonify(_tagged_cache[space_id]["data"])

    tag_key = request.args.get("tag_key") or _tag_key_for_space(space_id) or "App"
    tag_value = request.args.get("tag_value") or _tag_value_for_space(space_id)
    if not tag_value:
        return jsonify({"ok": False, "error": f"Space {space_id}에 App 태그가 없습니다. Space에 App 태그를 설정하세요.", "total": 0, "by_service": {}})
    # Space 소유 계정 세션으로 association 조회
    space_session = _space_session(space_id)
    aws_assocs = _get_aws_associations(space_id, session=space_session)
    results = {}
    grand_total = 0
    for assoc in aws_assocs:
        acct = assoc["account_id"]
        try:
            sess = _session_for_association(assoc)
            total, by_service = _fetch_tagged_resources(tag_key, tag_value, session=sess)

            tagged_arns = set()
            for items in by_service.values():
                for item in items:
                    tagged_arns.add(item["arn"])

            boundary = {}
            for svc, probe in BOUNDARY_PROBES.items():
                try:
                    all_arns = probe["list_fn"](sess)
                    tagged_count = sum(1 for a in all_arns if a in tagged_arns)
                    untagged_count = len(all_arns) - tagged_count
                    boundary[svc] = {"total": len(all_arns), "tagged": tagged_count, "untagged": untagged_count}
                except Exception:
                    pass

            results[acct] = {
                "total": total, "by_service": by_service,
                "role_arn": assoc["role_arn"],
                "account_type": assoc.get("account_type", ""),
                "boundary": boundary,
                "ok": True,
            }
            grand_total += total
        except Exception as e:
            results[acct] = {
                "total": 0, "by_service": {},
                "role_arn": assoc["role_arn"],
                "account_type": assoc.get("account_type", ""),
                "boundary": {},
                "ok": False, "error": str(e),
            }
    resp_data = {"ok": True, "accounts": results, "grand_total": grand_total}
    _tagged_cache[space_id] = {"data": resp_data, "ts": time.time()}
    return jsonify(resp_data)


@space_bp.route("/api/space-info")
def api_space_info():
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    session = _boto_session()
    space_meta = _get_space_meta(session, space_id)

    if not space_meta:
        return jsonify({"ok": False, "error": "미등록 Space입니다. discover → register를 먼저 실행하세요."}), 404

    return _space_info_from_ddb(space_id, space_meta)


def _space_info_from_ddb(space_id, meta):
    """DDB 메타데이터에서 space-info 응답 구성."""
    associations = []

    # AWS associations from aws_config
    aws_config = meta.get("aws_config", {})
    for cfg_key, details in aws_config.items():
        if not isinstance(details, dict):
            continue
        norm_key = "aws" if cfg_key == "sourceAws" else cfg_key
        parsed = {
            "type": norm_key,
            "account_id": details.get("account_id", ""),
            "role_arn": details.get("role_arn", ""),
            "account_type": details.get("account_type", ""),
            "status": "ACTIVE",
        }
        associations.append(parsed)

    # Integration associations from integrations[]
    for ig in meta.get("integrations", []):
        parsed = {
            "service_id": ig.get("service_id", ""),
            "association_id": ig.get("association_id", ""),
            "type": ig.get("provider", ""),
            "service_name": ig.get("name", ""),
            "status": "ACTIVE",
        }
        for k, v in ig.items():
            if k not in ("service_id", "association_id", "provider", "name") and v:
                parsed[k] = v
        # UI 호환: repo → owner/repoName 분리, projectPath 별칭
        repo = ig.get("repo", "")
        if repo and "/" in repo:
            if "owner" not in parsed:
                parsed["owner"] = repo.split("/")[0]
            if "repoName" not in parsed:
                parsed["repoName"] = repo.split("/", 1)[1]
            if ig.get("provider") == "gitlab" and "projectPath" not in parsed:
                parsed["projectPath"] = repo
        associations.append(parsed)

    return jsonify({
        "ok": True,
        "app_name": meta.get("app_name", ""),
        "space": {
            "space_id": space_id,
            "region": AWS_REGION,
            "name": meta.get("space_name", ""),
            "status": "ACTIVE",
            "created_at": meta.get("created_at", ""),
            "tag_key": meta.get("app_tag_key", ""),
            "tag_value": meta.get("app_tag_value", ""),
        },
        "associations": associations,
        "data_sources": DATA_SOURCES,
    })



MANAGED_BASELINE_POLICY = "AIDevOpsAgentAccessPolicy"


def _get_inline_extra_actions(iam, role_name):
    """Inline policy에서 managed baseline 밖 추가 액션과 리소스 범위를 추출."""
    extras = []
    try:
        pol_names = iam.list_role_policies(RoleName=role_name).get("PolicyNames", [])
        for pname in pol_names:
            doc = iam.get_role_policy(RoleName=role_name, PolicyName=pname)["PolicyDocument"]
            for stmt in doc.get("Statement", []):
                if stmt.get("Effect") != "Allow":
                    continue
                actions = stmt.get("Action", [])
                if isinstance(actions, str):
                    actions = [actions]
                resource = stmt.get("Resource", "*")
                if isinstance(resource, list):
                    resource = ", ".join(resource)
                is_scoped = resource != "*"
                for action in actions:
                    extras.append({
                        "action": action,
                        "resource": resource,
                        "scoped": is_scoped,
                        "policy_name": pname,
                        "sid": stmt.get("Sid", ""),
                    })
    except Exception as e:
        print(f"Inline policy read error: {e}", flush=True)
    return extras


@space_bp.route("/api/permission-check")
def api_permission_check():
    role_arn = request.args.get("role_arn", "")
    account_id = request.args.get("account_id", "")
    if not role_arn:
        return jsonify({"ok": False, "error": "role_arn required"})

    try:
        role_name = role_arn.split("/")[-1]
        try:
            secondary = _get_or_create_session(role_arn)
        except Exception:
            secondary = _boto_session()
        iam = secondary.client("iam")

        managed_resp = iam.list_attached_role_policies(RoleName=role_name)
        managed_policies = [
            {"name": p["PolicyName"], "arn": p["PolicyArn"]}
            for p in managed_resp.get("AttachedPolicies", [])
        ]
        has_agent_policy = any(MANAGED_BASELINE_POLICY in p["name"] for p in managed_policies)
        has_readonly = any("ReadOnlyAccess" in p["name"] for p in managed_policies)

        inline_extras = _get_inline_extra_actions(iam, role_name)

        trust_doc = {}
        try:
            role_info = iam.get_role(RoleName=role_name)["Role"]
            trust_doc = role_info.get("AssumeRolePolicyDocument", {})
        except Exception:
            pass
        has_source_account = False
        has_source_arn = False
        for stmt in trust_doc.get("Statement", []):
            cond = stmt.get("Condition", {})
            if "StringEquals" in cond and "aws:SourceAccount" in cond["StringEquals"]:
                has_source_account = True
            if "ArnLike" in cond and "aws:SourceArn" in cond["ArnLike"]:
                has_source_arn = True

        total_tagged, by_service = _fetch_tagged_resources("App", session=secondary)

        all_svcs = set(by_service.keys()) | set(SVC_TEST_ACTIONS.keys())
        all_actions = []
        for svc in all_svcs:
            test = SVC_TEST_ACTIONS.get(svc)
            if test:
                all_actions.extend(test["read"])
                all_actions.extend(test["write"])

        sim_results = {}
        marker = None
        while True:
            kwargs = {
                "PolicySourceArn": role_arn,
                "ActionNames": all_actions,
                "MaxItems": 500,
            }
            if marker:
                kwargs["Marker"] = marker
            resp = iam.simulate_principal_policy(**kwargs)
            for er in resp.get("EvaluationResults", []):
                sim_results[er["EvalActionName"]] = er["EvalDecision"]
            if not resp.get("IsTruncated"):
                break
            marker = resp.get("Marker")

        tagged_arn_set = set()
        for svc_items in by_service.values():
            for item in svc_items:
                tagged_arn_set.add(item["arn"])

        untagged_by_svc = {}
        for svc, probe in BOUNDARY_PROBES.items():
            try:
                all_arns = probe["list_fn"](secondary)
                untagged = [a for a in all_arns if a not in tagged_arn_set]
                if untagged:
                    untagged_by_svc[svc] = untagged[0]
            except Exception as e:
                print(f"Boundary probe list error ({svc}): {e}", flush=True)

        inline_svc_set = set()
        for ex in inline_extras:
            prefix = ex["action"].split(":")[0]
            inline_svc_set.add(prefix)

        alignment = []
        counts = {"read_only": 0, "read_write": 0, "no_access": 0, "no_tagged": 0}
        write_capable = []
        boundary_counts = {"managed_baseline": 0, "inline_extra": 0, "contained": 0, "not_testable": 0, "not_applicable": 0}

        for svc in sorted(all_svcs):
            tagged_count = len(by_service.get(svc, []))
            test = SVC_TEST_ACTIONS.get(svc)
            if not test:
                alignment.append({
                    "service": svc, "label": SERVICE_LABELS.get(svc, svc),
                    "tagged_count": tagged_count,
                    "read_allowed": None, "write_allowed": None,
                    "status": "no_test", "read_actions": [], "write_actions": [],
                    "boundary": {"result": "not_applicable"},
                })
                boundary_counts["not_applicable"] += 1
                continue

            read_results = [
                {"action": a, "decision": sim_results.get(a, "unknown")}
                for a in test["read"]
            ]
            write_results = [
                {"action": a, "decision": sim_results.get(a, "unknown")}
                for a in test["write"]
            ]
            read_ok = any(r["decision"] == "allowed" for r in read_results)
            write_ok = any(r["decision"] == "allowed" for r in write_results)

            if tagged_count == 0 and not read_ok and not write_ok:
                status = "no_tagged"
            elif read_ok and write_ok:
                status = "read_write"
            elif read_ok:
                status = "read_only"
            else:
                status = "no_access"

            counts[status] += 1
            if write_ok:
                write_capable.append(svc)

            boundary = {"result": "not_applicable"}
            probe = BOUNDARY_PROBES.get(svc)
            if probe and tagged_count > 0:
                untagged_arn = untagged_by_svc.get(svc)
                if not untagged_arn:
                    boundary = {"result": "not_testable"}
                    boundary_counts["not_testable"] += 1
                else:
                    tagged_sample = by_service[svc][0]["arn"]
                    try:
                        sim_b = iam.simulate_principal_policy(
                            PolicySourceArn=role_arn,
                            ActionNames=[probe["action"]],
                            ResourceArns=[untagged_arn],
                            MaxItems=1,
                        )
                        ud = sim_b["EvaluationResults"][0]["EvalDecision"]
                        matched = sim_b["EvaluationResults"][0].get("MatchedStatements", [])
                        via = matched[0]["SourcePolicyId"] if matched else ""
                        if ud == "allowed":
                            is_inline = svc in inline_svc_set
                            if is_inline:
                                br = "inline_extra"
                                boundary_counts["inline_extra"] += 1
                            else:
                                br = "managed_baseline"
                                boundary_counts["managed_baseline"] += 1
                        else:
                            br = "contained"
                            boundary_counts["contained"] += 1
                        boundary = {
                            "result": br,
                            "tagged_arn": tagged_sample,
                            "untagged_arn": untagged_arn,
                            "untagged_name": untagged_arn.split("/")[-1] if "/" in untagged_arn else untagged_arn.split(":")[-1],
                            "untagged_decision": ud,
                            "via": via,
                        }
                    except Exception as e:
                        boundary = {"result": "error", "error": str(e)}
                        boundary_counts["not_testable"] += 1
            else:
                boundary_counts["not_applicable"] += 1

            alignment.append({
                "service": svc,
                "label": SERVICE_LABELS.get(svc, svc),
                "tagged_count": tagged_count,
                "read_allowed": read_ok,
                "write_allowed": write_ok,
                "status": status,
                "read_actions": read_results,
                "write_actions": write_results,
                "boundary": boundary,
            })

        return jsonify({
            "ok": True,
            "account_id": account_id,
            "role_name": role_name,
            "role_arn": role_arn,
            "managed_policies": managed_policies,
            "has_agent_policy": has_agent_policy,
            "has_readonly_access": has_readonly,
            "trust_conditions": {
                "has_source_account": has_source_account,
                "has_source_arn": has_source_arn,
            },
            "inline_extras": inline_extras,
            "alignment": alignment,
            "summary": {
                "total_services": len(all_svcs),
                "read_only": counts["read_only"],
                "read_write": counts["read_write"],
                "no_access": counts["no_access"],
                "no_tagged": counts["no_tagged"],
                "write_capable_services": write_capable,
                "boundary": {
                    "managed_baseline": boundary_counts["managed_baseline"],
                    "inline_extra": boundary_counts["inline_extra"],
                    "contained": boundary_counts["contained"],
                    "not_testable": boundary_counts["not_testable"],
                    "not_applicable": boundary_counts["not_applicable"],
                },
            },
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ===================================================================
# VPC / Subnet / SG lookup for Pentest VPC config
# ===================================================================

@space_bp.route("/api/spaces/vpc-list")
def api_vpc_list():
    """List VPCs in account. Pass ?account_id=X for cross-account."""
    try:
        account_id = request.args.get("account_id", "").strip()
        session = _session_for_account_id(account_id) if account_id else _boto_session()
        ec2 = session.client("ec2")
        vpcs = ec2.describe_vpcs().get("Vpcs", [])
        result = []
        for v in vpcs:
            name = ""
            for tag in v.get("Tags", []):
                if tag["Key"] == "Name":
                    name = tag["Value"]
                    break
            result.append({"vpc_id": v["VpcId"], "name": name, "cidr": v.get("CidrBlock", "")})
        return jsonify({"ok": True, "vpcs": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@space_bp.route("/api/spaces/vpc-detail")
def api_vpc_detail():
    """List subnets and security groups for a VPC. Pass ?account_id=X for cross-account."""
    vpc_id = request.args.get("vpc_id", "").strip()
    if not vpc_id:
        return jsonify({"ok": False, "error": "vpc_id required"})
    try:
        account_id = request.args.get("account_id", "").strip()
        session = _session_for_account_id(account_id) if account_id else _boto_session()
        ec2 = session.client("ec2")
        subnets_resp = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
        subnets = []
        for s in subnets_resp.get("Subnets", []):
            name = ""
            for tag in s.get("Tags", []):
                if tag["Key"] == "Name":
                    name = tag["Value"]
                    break
            subnets.append({"subnet_id": s["SubnetId"], "name": name, "az": s.get("AvailabilityZone", ""), "cidr": s.get("CidrBlock", "")})
        sgs_resp = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
        security_groups = []
        for sg in sgs_resp.get("SecurityGroups", []):
            security_groups.append({"sg_id": sg["GroupId"], "name": sg.get("GroupName", ""), "description": sg.get("Description", "")})
        return jsonify({"ok": True, "subnets": subnets, "security_groups": security_groups})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@space_bp.route("/api/spaces/vpc-nlbs")
def api_vpc_nlbs():
    """List NLBs in a VPC. Helps users find the host address for Private Connection."""
    vpc_id = request.args.get("vpc_id", "").strip()
    account_id = request.args.get("account_id", "").strip()
    if not vpc_id:
        return jsonify({"ok": False, "error": "vpc_id required"})
    try:
        session = _session_for_account_id(account_id) if account_id else _boto_session()
        elbv2 = session.client("elbv2")
        lbs = elbv2.describe_load_balancers().get("LoadBalancers", [])
        nlbs = []
        for lb in lbs:
            if lb.get("VpcId") == vpc_id and lb.get("Type") == "network":
                nlbs.append({
                    "dns": lb.get("DNSName", ""),
                    "name": lb.get("LoadBalancerName", ""),
                    "scheme": lb.get("Scheme", ""),
                    "state": lb.get("State", {}).get("Code", ""),
                    "azs": [az.get("SubnetId", "") for az in lb.get("AvailabilityZones", [])],
                })
        return jsonify({"ok": True, "nlbs": nlbs})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@space_bp.route("/api/spaces/private-connections")
def api_list_private_connections():
    """List existing Private Connections in the account."""
    account_id = request.args.get("account_id", "").strip()
    try:
        session = _space_session(account_id=account_id) if account_id else _space_session()
        client = session.client("devops-agent")
        resp = client.list_private_connections()
        pcs = []
        for pc in resp.get("privateConnections", []):
            pcs.append({
                "name": pc.get("name", ""),
                "type": pc.get("type", ""),
                "status": pc.get("status", ""),
                "host": pc.get("hostAddress", ""),
                "vpc_id": pc.get("vpcId", ""),
            })
        return jsonify({"ok": True, "private_connections": pcs})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ===================================================================
# Shared helper functions (used by multiple sub-modules)
# ===================================================================

def _get_space_meta(session, space_id):
    """DDB에서 Space 메타데이터 조회. 없으면 None."""
    try:
        tbl = session.resource("dynamodb").Table(RUNS_TABLE)
        resp = tbl.get_item(Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"})
        return resp.get("Item")
    except Exception:
        return None


def _append_integration(session, space_id, integration: dict):
    """DDB Space metadata의 integrations 리스트에 항목 추가."""
    tbl = session.resource("dynamodb").Table(RUNS_TABLE)
    tbl.update_item(
        Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"},
        UpdateExpression="SET integrations = list_append(if_not_exists(integrations, :empty), :val)",
        ExpressionAttributeValues={":val": [integration], ":empty": []},
    )


def _save_space_metadata(session, space_id, metadata, steps=None):
    """Save Space metadata to DDB via update_item (부분 업데이트, 기존 필드 보존)."""
    if not RUNS_TABLE:
        return
    from datetime import datetime
    tbl = session.resource("dynamodb").Table(RUNS_TABLE)

    fields = {
        "space_id": space_id,
        "scenario_id": space_id,
        "status": "active",
    }
    if "managed" in metadata:
        fields["managed"] = metadata["managed"]

    FIELD_MAP = {
        "name": "space_name",
        "description": "description",
        "app_name": "app_name",
        "app_tag_key": "app_tag_key",
        "app_tag_value": "app_tag_value",
        "account_id": "account_id",
        "role_arn": "role_arn",
        "profile": "profile",
        "aws_config": "aws_config",
        "integrations": "integrations",
        "deploy_method": "deploy_method",
        "stack_name": "stack_name",
        "deploy_status": "deploy_status",
        "security_agent_space_id": "security_agent_space_id",
    }
    for src_key, ddb_key in FIELD_MAP.items():
        if src_key in metadata:
            val = metadata[src_key]
            if val or val == 0 or val is False:
                fields[ddb_key] = val

    # account_id/role_arn 파생
    if not fields.get("account_id"):
        acct = (metadata.get("aws_config", {}).get("aws", {}).get("account_id")
                or metadata.get("aws_config", {}).get("sourceAws", {}).get("account_id"))
        if acct:
            fields["account_id"] = acct
    if not fields.get("role_arn"):
        arn = (metadata.get("aws_config", {}).get("aws", {}).get("role_arn")
               or metadata.get("aws_config", {}).get("sourceAws", {}).get("role_arn"))
        if arn:
            fields["role_arn"] = arn

    # provisioned from steps
    if steps:
        provisioned = {}
        for s in steps:
            if s.get("ok"):
                if s["step"] == "create_role":
                    provisioned["role_arn"] = s.get("role_arn", "")
                elif s["step"] == "aws_association":
                    provisioned["monitor_association_id"] = s.get("association_id", "")
                elif s["step"] == "github_association":
                    provisioned["github_integration_id"] = s.get("integration_id", "")
                elif s["step"] == "event_channel":
                    provisioned["event_channel_service_id"] = s.get("service_id", "")
                    provisioned["event_channel_association_id"] = s.get("association_id", "")
            if provisioned:
                fields["provisioned"] = provisioned

    # Build update expression
    now = datetime.utcnow().isoformat() + "Z"
    set_parts = ["#updated_at = :updated_at", "#created_at = if_not_exists(#created_at, :now)"]
    names = {"#updated_at": "updated_at", "#created_at": "created_at"}
    values = {":updated_at": now, ":now": now}

    for i, (key, val) in enumerate(fields.items()):
        alias = f"#f{i}"
        val_alias = f":v{i}"
        set_parts.append(f"{alias} = {val_alias}")
        names[alias] = key
        values[val_alias] = val

    tbl.update_item(
        Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def _setup_event_channel(client, session, space_id):
    """Register eventChannel service and associate with Space, store webhook in SecretsManager."""
    # Find or register eventChannel service
    service_id = None
    try:
        resp = client.list_services()
        for svc in resp.get("services", []):
            if svc.get("serviceType") == "eventChannel":
                service_id = svc["serviceId"]
                break
    except Exception:
        pass

    if not service_id:
        reg_resp = client.register_service(
            service="eventChannel",
            serviceDetails={"eventChannel": {"type": "webhook"}},
        )
        service_id = reg_resp["serviceId"]

    # Associate eventChannel with Space → returns webhook credentials
    assoc_resp = client.associate_service(
        agentSpaceId=space_id,
        serviceId=service_id,
        configuration={"eventChannel": {}},
    )
    webhook = assoc_resp.get("webhook", {})
    webhook_url = webhook.get("webhookUrl", "")
    webhook_secret = webhook.get("webhookSecret", "")
    association_id = assoc_resp.get("association", {}).get("associationId", "")

    # Store in SecretsManager
    if webhook_url and webhook_secret:
        sm = session.client("secretsmanager")
        secret_name = f"webhook-{space_id}"
        secret_value = json.dumps({
            "webhookUrl": webhook_url,
            "webhookSecret": webhook_secret,
            "spaceId": space_id,
        })
        try:
            sm.create_secret(
                Name=secret_name,
                Description=f"Webhook credentials for Space {space_id}",
                SecretString=secret_value,
                Tags=[
                    {"Key": "App", "Value": "DevOpsAgent"},
                    {"Key": "SpaceId", "Value": space_id},
                ],
            )
        except sm.exceptions.ResourceExistsException:
            sm.put_secret_value(SecretId=secret_name, SecretString=secret_value)

    return {
        "service_id": service_id,
        "association_id": association_id,
        "webhook_url": webhook_url[:50] + "..." if webhook_url else "",
    }


def _save_hub_meta(session, space_id, metadata, sec_space_id):
    """Save Frontier Agent Hub's own space reference to DDB.
    Two records: fixed PK for settings lookup + per-space PK for tag lookup.
    """
    if not RUNS_TABLE:
        return
    from datetime import datetime
    tbl = session.resource("dynamodb").Table(RUNS_TABLE)
    common = {
        "record_type": "space_metadata",
        "space_id": space_id,
        "space_name": metadata.get("name", ""),
        "app_name": metadata.get("app_name", ""),
        "app_tag_key": metadata.get("app_tag_key", ""),
        "app_tag_value": metadata.get("app_tag_value", ""),
        "security_agent_space_id": sec_space_id or "",
        "status": "active",
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    tbl.put_item(Item={"run_id": "space-meta-frontier-agent-hub", "scenario_id": "frontier-agent-hub", **common})
    tbl.put_item(Item={"run_id": f"space-meta-{space_id}", "scenario_id": space_id, **common})
