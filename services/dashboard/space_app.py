#!/usr/bin/env python3
"""
Agent Space Overview — standalone Flask app for Agent Space topology visualization.
Separate from dag_app.py and main dashboard.

Run: python space_app.py [--port 5003]
"""
import json
import os
import sys

from flask import Flask, render_template, jsonify, request

sys.path.insert(0, os.path.dirname(__file__))

app = Flask(__name__, template_folder="templates", static_folder="static")

try:
    from config import get as _cfg
    AWS_REGION = _cfg("aws.region", os.environ.get("AWS_REGION", "us-east-1"))
    AGENT_SPACE_ID = _cfg("agent.space_id", "")
    RUNS_TABLE = _cfg("dynamodb.runs_table", "devops-agent-test-scenario-runs")
except ImportError:
    AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
    AGENT_SPACE_ID = os.environ.get("AGENT_SPACE_ID", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    RUNS_TABLE = "devops-agent-test-scenario-runs"

AWS_PROFILE = os.environ.get("AWS_PROFILE", "member1-acc")

DAG_APP_PORT = int(os.environ.get("DAG_APP_PORT", "5002"))

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
    {
        "id": "cloudwatch", "label": "CloudWatch", "category": "monitoring",
        "permissions": ["GetMetricData", "GetMetricStatistics", "ListMetrics", "DescribeAlarms", "DescribeAlarmHistory"],
    },
    {
        "id": "xray", "label": "X-Ray", "category": "trace",
        "permissions": ["GetTraceSummaries", "BatchGetTraces", "GetServiceGraph", "GetTraceGraph"],
    },
    {
        "id": "logs", "label": "CloudWatch Logs", "category": "log",
        "permissions": ["GetLogEvents", "FilterLogEvents", "DescribeLogGroups", "DescribeLogStreams", "StartQuery", "StopQuery", "GetQueryResults"],
    },
    {
        "id": "eks", "label": "EKS", "category": "container",
        "permissions": ["DescribeCluster", "ListClusters", "DescribeNodegroup", "ListNodegroups", "AccessKubernetesApi"],
    },
    {
        "id": "ec2", "label": "EC2 / VPC", "category": "compute",
        "permissions": ["DescribeInstances", "DescribeSecurityGroups", "DescribeSubnets", "DescribeVpcs"],
    },
    {
        "id": "ecr", "label": "ECR", "category": "registry",
        "permissions": ["DescribeRepositories", "DescribeImages", "ListImages"],
    },
    {
        "id": "rds", "label": "RDS", "category": "database",
        "permissions": ["DescribeDBInstances", "DescribeDBClusters", "DescribeEvents"],
    },
    {
        "id": "secrets", "label": "Secrets Manager", "category": "security",
        "permissions": ["ListSecrets", "DescribeSecret"],
    },
]


def _boto_session():
    import boto3
    return boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)


@app.route("/")
def index():
    return render_template("space.html")


@app.route("/api/spaces")
def api_spaces():
    try:
        session = _boto_session()
        client = session.client("devops-agent")
        resp = client.list_agent_spaces()
        spaces = []
        for sp in resp.get("agentSpaces", []):
            spaces.append({
                "space_id": sp.get("agentSpaceId", ""),
                "name": sp.get("name", ""),
                "description": sp.get("description", ""),
                "locale": sp.get("locale", ""),
                "created_at": str(sp.get("createdAt", "")),
                "updated_at": str(sp.get("updatedAt", "")),
            })
        return jsonify({"ok": True, "spaces": spaces})
    except Exception as e:
        return jsonify({"ok": False, "spaces": [], "error": str(e)})


def _fetch_tagged_resources(tag_key, tag_value="true"):
    session = _boto_session()
    client = session.client("resourcegroupstaggingapi")
    all_res = []
    token = ""
    while True:
        kwargs = {
            "TagFilters": [{"Key": tag_key, "Values": [tag_value]}],
            "ResourcesPerPage": 100,
        }
        if token:
            kwargs["PaginationToken"] = token
        resp = client.get_resources(**kwargs)
        all_res.extend(resp.get("ResourceTagMappingList", []))
        token = resp.get("PaginationToken", "")
        if not token:
            break
    by_service = {}
    for r in all_res:
        arn = r.get("ResourceARN", "")
        parts = arn.split(":")
        svc = parts[2] if len(parts) > 2 else "unknown"
        if svc not in by_service:
            by_service[svc] = []
        name = arn.split("/")[-1] if "/" in arn else arn.split(":")[-1]
        by_service[svc].append({"arn": arn, "name": name})
    return len(all_res), by_service


@app.route("/api/tagged-resources")
def api_tagged_resources():
    tag_key = request.args.get("tag_key")
    if not tag_key:
        return jsonify({"ok": True, "total": 0, "by_service": {}, "no_boundary": True})
    tag_value = request.args.get("tag_value", "true")
    try:
        total, by_service = _fetch_tagged_resources(tag_key, tag_value)
        return jsonify({"ok": True, "total": total, "by_service": by_service})
    except Exception as e:
        return jsonify({"ok": False, "total": 0, "by_service": {}, "error": str(e)})


@app.route("/api/space-info")
def api_space_info():
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    result = {
        "ok": True,
        "space": {
            "space_id": space_id,
            "region": AWS_REGION,
            "name": "",
            "status": "UNKNOWN",
            "created_at": "",
        },
        "associations": [],
        "data_sources": DATA_SOURCES,
        "dag_port": DAG_APP_PORT,
    }

    session = _boto_session()
    client = session.client("devops-agent")

    # 1. GetAgentSpace
    try:
        space_resp = client.get_agent_space(agentSpaceId=space_id)
        sp = space_resp.get("agentSpace", space_resp)
        result["space"]["name"] = sp.get("name", sp.get("agentSpaceName", ""))
        result["space"]["status"] = sp.get("status", "ACTIVE")
        ca = sp.get("createdAt", sp.get("creationTime", ""))
        result["space"]["created_at"] = str(ca) if ca else ""
    except Exception as e:
        err = str(e)
        if "ResourceNotFoundException" in err:
            result["space"]["status"] = "NOT_FOUND"
        else:
            result["space"]["name"] = space_id[:8] + "..."
            result["space"]["status"] = "ERROR"
            result["space"]["error"] = f"GetAgentSpace failed: {err}"
            print(f"[ERROR] GetAgentSpace failed for {space_id}: {err}", flush=True)

    # 2. ListServices — cache for enrichment
    svc_cache = {}
    try:
        svc_resp = client.list_services()
        for svc in svc_resp.get("services", []):
            svc_cache[svc["serviceId"]] = svc
    except Exception as e:
        print(f"ListServices error (non-fatal): {e}", flush=True)

    # 3. ListAssociations — configuration can be a string repr of dict
    try:
        import ast
        assoc_resp = client.list_associations(agentSpaceId=space_id)
        for a in assoc_resp.get("associations", []):
            raw_cfg = a.get("configuration", a.get("serviceConfiguration", {}))
            if isinstance(raw_cfg, str):
                try:
                    raw_cfg = ast.literal_eval(raw_cfg)
                except Exception:
                    try:
                        raw_cfg = json.loads(raw_cfg)
                    except Exception:
                        raw_cfg = {}

            service_id = a.get("serviceId", "")
            svc_info = svc_cache.get(service_id, {})
            svc_type = svc_info.get("serviceType", "")
            svc_details = svc_info.get("additionalServiceDetails", {})
            svc_name = svc_info.get("name", "")
            if not svc_name:
                for _det_v in svc_details.values():
                    if isinstance(_det_v, dict) and _det_v.get("name"):
                        svc_name = _det_v["name"]
                        break

            cfg_key = next(iter(raw_cfg), "unknown")
            if cfg_key == "sourceAws":
                cfg_key = "aws"
            details = raw_cfg.get(next(iter(raw_cfg), ""), {})
            if not isinstance(details, dict):
                details = {}

            parsed = {
                "service_id": service_id,
                "association_id": a.get("associationId", ""),
                "status": a.get("status", ""),
                "type": cfg_key,
                "service_name": svc_name,
                "service_type": svc_type,
            }
            for _det_v in svc_details.values():
                if isinstance(_det_v, dict):
                    parsed.update({k: v for k, v in _det_v.items() if isinstance(v, str) and k not in parsed})
            parsed.update({k: v for k, v in details.items() if isinstance(v, str)})
            result["associations"].append(parsed)
    except Exception as e:
        print(f"ListAssociations error: {e}", flush=True)

    return jsonify(result)


@app.route("/api/history")
def api_history():
    limit = request.args.get("limit", 30, type=int)
    try:
        session = _boto_session()
        tbl = session.resource("dynamodb").Table(RUNS_TABLE)
        resp = tbl.scan(Limit=limit)
        items = []
        for item in resp.get("Items", []):
            if item.get("record_type") != "run":
                continue
            items.append({
                "run_id": item.get("run_id", ""),
                "scenario_id": item.get("scenario_id", ""),
                "status": item.get("status", ""),
                "investigation_task_id": item.get("investigation_task_id", ""),
                "started_at": item.get("started_at", ""),
                "created_at": item.get("created_at", ""),
            })
        items.sort(key=lambda x: x.get("started_at") or x.get("created_at") or "", reverse=True)
        return jsonify({"items": items[:limit]})
    except Exception as e:
        return jsonify({"items": [], "error": str(e)})


@app.route("/api/permission-check")
def api_permission_check():
    role_arn = request.args.get("role_arn", "")
    if not role_arn:
        return jsonify({"ok": False, "error": "role_arn required"})

    try:
        role_name = role_arn.split("/")[-1]
        session = _boto_session()
        iam = session.client("iam")

        managed_resp = iam.list_attached_role_policies(RoleName=role_name)
        managed_policies = [
            {"name": p["PolicyName"], "arn": p["PolicyArn"]}
            for p in managed_resp.get("AttachedPolicies", [])
        ]
        has_readonly = any("ReadOnlyAccess" in p["name"] for p in managed_policies)

        total_tagged, by_service = _fetch_tagged_resources()

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

        # --- Boundary probes: collect untagged ARNs per testable service ---
        tagged_arn_set = set()
        for svc_items in by_service.values():
            for item in svc_items:
                tagged_arn_set.add(item["arn"])

        untagged_by_svc = {}
        for svc, probe in BOUNDARY_PROBES.items():
            try:
                all_arns = probe["list_fn"](session)
                untagged = [a for a in all_arns if a not in tagged_arn_set]
                if untagged:
                    untagged_by_svc[svc] = untagged[0]
            except Exception as e:
                print(f"Boundary probe list error ({svc}): {e}", flush=True)

        alignment = []
        counts = {"read_only": 0, "read_write": 0, "no_access": 0, "no_tagged": 0}
        write_capable = []
        boundary_counts = {"contained": 0, "leaks": 0, "not_testable": 0, "not_applicable": 0}
        leak_via = set()

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

            # --- Boundary check ---
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
                        br = "leaks" if ud == "allowed" else "contained"
                        boundary = {
                            "result": br,
                            "tagged_arn": tagged_sample,
                            "untagged_arn": untagged_arn,
                            "untagged_name": untagged_arn.split("/")[-1] if "/" in untagged_arn else untagged_arn.split(":")[-1],
                            "untagged_decision": ud,
                            "via": via,
                        }
                        boundary_counts[br] += 1
                        if br == "leaks" and via:
                            leak_via.add(via)
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
            "role_name": role_name,
            "role_arn": role_arn,
            "has_readonly_access": has_readonly,
            "managed_policies": managed_policies,
            "alignment": alignment,
            "summary": {
                "total_services": len(all_svcs),
                "read_only": counts["read_only"],
                "read_write": counts["read_write"],
                "no_access": counts["no_access"],
                "no_tagged": counts["no_tagged"],
                "write_capable_services": write_capable,
                "boundary": {
                    "contained": boundary_counts["contained"],
                    "leaks": boundary_counts["leaks"],
                    "not_testable": boundary_counts["not_testable"],
                    "not_applicable": boundary_counts["not_applicable"],
                    "leak_via": list(leak_via),
                },
            },
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/jserror", methods=["POST"])
def js_error():
    data = request.get_json(silent=True) or {}
    print(f"\n*** JS ERROR: {data.get('message', '?')} ***\n{data.get('stack', '')}\n", flush=True)
    return jsonify({"ok": True})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5003)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    print(f"Agent Space Overview running on http://localhost:{args.port}")
    app.run(host=args.host, port=args.port, debug=True)
