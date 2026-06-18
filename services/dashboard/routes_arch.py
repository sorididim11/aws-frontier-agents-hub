"""
Architecture Analysis routes — extracted from overview_app.py as a Flask Blueprint.
"""
import copy
import json
import os
import re
import queue
import shutil
import threading
import traceback

from datetime import datetime
from decimal import Decimal

from flask import Blueprint, Response, jsonify, request

import arch_analysis
import prompts as _prompts  # noqa: F401

from app_config import (
    _CFG, _cfg_get, AWS_REGION, AGENT_SPACE_ID, RUNS_TABLE,
    _req_space_id, _agent_space_id, _boto_session, _tag_key_for_space,
    _get_aws_associations, _session_for_association,
    AVAILABLE_MODELS, _fetch_tagged_resources, _tag_value_for_space,
)

arch_bp = Blueprint("arch_bp", __name__)


# ===================================================================
# DynamoDB persistence helpers
# ===================================================================

def _sanitize_ddb(obj):
    """Recursively convert for DynamoDB: remove empty strings, float→Decimal."""
    if isinstance(obj, dict):
        return {k: _sanitize_ddb(v) for k, v in obj.items()
                if v is not None and v != ""}
    if isinstance(obj, list):
        return [_sanitize_ddb(i) for i in obj]
    if isinstance(obj, float):
        if obj != obj:  # NaN
            return 0
        return Decimal(str(obj))
    return obj


def _desanitize_ddb(obj):
    """Recursively convert from DynamoDB: Decimal→int/float."""
    if isinstance(obj, dict):
        return {k: _desanitize_ddb(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_desanitize_ddb(i) for i in obj]
    if isinstance(obj, Decimal):
        return int(obj) if obj == int(obj) else float(obj)
    return obj


def _arch_table():
    return _boto_session().resource("dynamodb").Table(RUNS_TABLE)


def _save_arch_analysis(space_id, analysis, model_id, app_name=None, is_main=False):
    """앱별 독립 저장. app_name 있으면 해당 앱 레코드 upsert, 없으면 레거시 타임스탬프 저장."""
    data = analysis.to_dict(include_conversations=False) if hasattr(analysis, 'to_dict') else analysis
    data.pop("conversations", None)
    if app_name:
        run_id = f"arch-app-{app_name}"
    else:
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        run_id = f"arch-{ts}"
    item = {
        "run_id": run_id,
        "record_type": "arch_analysis",
        "scenario_id": space_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "model_id": model_id,
        "status": "complete",
        "app_name": app_name or "",
        "is_main": is_main,
        **data,
    }
    _arch_table().put_item(Item=_sanitize_ddb(item))
    print(f"[ARCH-DDB] saved {run_id} for space {space_id} (is_main={is_main})")
    return run_id


def _load_latest_arch(space_id):
    """Space의 앱별 레코드를 로드하여 메인 원본 + boundary 확장 형태로 반환."""
    from boto3.dynamodb.conditions import Key
    resp = _arch_table().query(
        IndexName="scenario-id-index",
        KeyConditionExpression=Key("scenario_id").eq(space_id) & Key("run_id").begins_with("arch-app-"),
        ScanIndexForward=True,
    )
    items = resp.get("Items", [])
    if items:
        records = [_desanitize_ddb(i) for i in items]
        # is_main 플래그로 메인 선택, 없으면 created_at 최신 것 (마지막 메인 분석)
        main = None
        for r in records:
            if r.get("is_main"):
                main = r
                break
        if not main:
            # fallback: 노드 수 가장 적은 것 = 메인 앱 (boundary 포함 원본)
            records.sort(key=lambda r: r.get("created_at", ""))
            main = records[-1]  # 가장 최신
        extras = [r for r in records if r is not main]
        if extras:
            return _expand_boundaries(main, extras)
        return main
    # legacy fallback
    resp = _arch_table().query(
        IndexName="scenario-id-index",
        KeyConditionExpression=Key("scenario_id").eq(space_id) & Key("run_id").begins_with("arch-2"),
        ScanIndexForward=False,
        Limit=1,
    )
    items = resp.get("Items", [])
    if items:
        return _desanitize_ddb(items[0])
    return None


def _expand_boundaries(main, extras):
    """메인 원본 유지. boundary 노드에 추가 분석 결과를 children으로 삽입."""
    result = copy.deepcopy(main)
    extra_by_app = {}
    for r in extras:
        app = r.get("app_name", "")
        if app:
            extra_by_app[app] = r

    for node in result.get("graph", {}).get("nodes", []):
        if node.get("service_type") == "boundary":
            app_name = node.get("group", "")
            if app_name in extra_by_app:
                extra = extra_by_app[app_name]
                node["expanded"] = True
                node["children"] = extra.get("graph", {}).get("nodes", [])
                node["children_edges"] = extra.get("graph", {}).get("edges", [])
    return result


def _list_arch_versions(space_id, limit=20):
    from boto3.dynamodb.conditions import Key
    table = _arch_table()
    items = []
    for prefix in ("arch-app-", "arch-2"):
        resp = table.query(
            IndexName="scenario-id-index",
            KeyConditionExpression=Key("scenario_id").eq(space_id) & Key("run_id").begins_with(prefix),
            ScanIndexForward=False,
            Limit=limit,
            ProjectionExpression="run_id, created_at, model_id, system_name, app_name, is_main, #s",
            ExpressionAttributeNames={"#s": "status"},
        )
        items.extend(resp.get("Items", []))
    items.sort(key=lambda x: x.get("created_at", {}).get("S", "") if isinstance(x.get("created_at"), dict) else x.get("created_at", ""), reverse=True)
    return _desanitize_ddb(items[:limit])


def _load_arch_version(run_id):
    resp = _arch_table().get_item(Key={"run_id": run_id, "record_type": "arch_analysis"})
    item = resp.get("Item")
    return _desanitize_ddb(item) if item else None


def _save_arch_conversations(space_id, conversations, app_name=None):
    """Conversations를 별도 DDB 레코드로 저장. 앱별 분리."""
    if not conversations:
        return
    suffix = app_name or "main"
    run_id = f"arch-conv-{suffix}"
    # 각 턴의 answer를 2000자로 절삭하여 DDB 크기 방어
    trimmed = {}
    for layer_key, turns in conversations.items():
        if not isinstance(turns, list):
            continue
        trimmed[layer_key] = [
            {**t, "answer": (t.get("answer") or "")[:2000],
             "question": (t.get("question") or "")[:1000]}
            for t in turns
        ]
    item = {
        "run_id": run_id,
        "record_type": "arch_conversations",
        "scenario_id": space_id,
        "app_name": app_name or "",
        "conversations": trimmed,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    _arch_table().put_item(Item=_sanitize_ddb(item))
    print(f"[ARCH-DDB] saved conversations {run_id} ({sum(len(v) for v in trimmed.values())} turns)")


def _load_arch_conversations(space_id):
    """Space의 conversations 레코드를 병합하여 반환."""
    from boto3.dynamodb.conditions import Key
    resp = _arch_table().query(
        IndexName="scenario-id-index",
        KeyConditionExpression=Key("scenario_id").eq(space_id) & Key("run_id").begins_with("arch-conv-"),
        ScanIndexForward=True,
    )
    merged = {}
    for item in resp.get("Items", []):
        convs = _desanitize_ddb(item.get("conversations", {}))
        for layer_key, turns in convs.items():
            if layer_key not in merged:
                merged[layer_key] = []
            merged[layer_key].extend(turns)
    return merged if merged else None


def _save_arch_checkpoint(space_id, checkpoint):
    _arch_table().put_item(Item=_sanitize_ddb({
        "run_id": f"arch-cp-{space_id}",
        "record_type": "arch_checkpoint",
        "scenario_id": space_id,
        "checkpoint": checkpoint,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }))


def _load_arch_checkpoint(space_id):
    resp = _arch_table().get_item(
        Key={"run_id": f"arch-cp-{space_id}", "record_type": "arch_checkpoint"})
    item = resp.get("Item")
    return _desanitize_ddb(item.get("checkpoint")) if item else None


def _delete_arch_checkpoint(space_id):
    try:
        _arch_table().delete_item(
            Key={"run_id": f"arch-cp-{space_id}", "record_type": "arch_checkpoint"})
    except Exception:
        pass


def _save_run_state(space_id, status, current_layer=None, error_msg=None):
    """분석 진행 상태를 DDB에 영속화."""
    item = {
        "run_id": f"arch-run-state-{space_id}",
        "record_type": "arch_run_state",
        "scenario_id": space_id,
        "status": status,
        "current_layer": current_layer or "",
        "error_msg": error_msg or "",
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    try:
        _arch_table().put_item(Item=_sanitize_ddb(item))
    except Exception as e:
        print(f"[ARCH-DDB] run_state 저장 실패: {e}")


def _load_run_state(space_id):
    """DDB에서 마지막 분석 상태 로드. 없으면 None."""
    try:
        resp = _arch_table().get_item(
            Key={"run_id": f"arch-run-state-{space_id}", "record_type": "arch_run_state"})
        return resp.get("Item")
    except Exception:
        return None


def _delete_all_arch(space_id):
    from boto3.dynamodb.conditions import Key
    table = _arch_table()
    resp = table.query(
        IndexName="scenario-id-index",
        KeyConditionExpression=Key("scenario_id").eq(space_id) & Key("run_id").begins_with("arch-"),
        ProjectionExpression="run_id, record_type",
    )
    deleted = 0
    for item in resp.get("Items", []):
        table.delete_item(Key={"run_id": item["run_id"], "record_type": item["record_type"]})
        deleted += 1
    print(f"[ARCH-DDB] deleted {deleted} records for space {space_id}")
    return deleted


# ---------------------------------------------------------------------------
# Scenario DynamoDB helpers (Space-scoped)
# ---------------------------------------------------------------------------

def _save_scenario(space_id, scenario):
    sid = scenario.get("id", "").strip()
    item = {
        "run_id": f"scen-{sid}",
        "record_type": "scenario",
        "scenario_id": space_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "scenario_data": scenario,
    }
    _arch_table().put_item(Item=_sanitize_ddb(item))
    print(f"[SCEN-DDB] saved scen-{sid} for space {space_id}")
    return sid


def _list_scenarios(space_id):
    from boto3.dynamodb.conditions import Key
    resp = _arch_table().query(
        IndexName="scenario-id-index",
        KeyConditionExpression=Key("scenario_id").eq(space_id) & Key("run_id").begins_with("scen-"),
        ScanIndexForward=True,
    )
    results = []
    for item in resp.get("Items", []):
        sc = _desanitize_ddb(item.get("scenario_data", {}))
        verif = sc.get("verification", {})
        if isinstance(verif, dict):
            verif_count = len(verif.get("steps", []))
        elif isinstance(verif, list):
            verif_count = len(verif)
        else:
            verif_count = 0
        results.append({
            "id": sc.get("id", ""),
            "name": sc.get("name", ""),
            "description": sc.get("description", ""),
            "category": sc.get("category", ""),
            "layer": sc.get("layer", ""),
            "target_service": sc.get("target_service", ""),
            "failure_mode": sc.get("failure_mode", ""),
            "purpose": sc.get("purpose", ""),
            "expected_root_cause": sc.get("expected_root_cause", ""),
            "verification_count": verif_count,
            "source": sc.get("source", "manual"),
        })
    return results


def _get_scenario(space_id, scenario_id):
    resp = _arch_table().get_item(
        Key={"run_id": f"scen-{scenario_id}", "record_type": "scenario"},
    )
    item = resp.get("Item")
    if not item or item.get("scenario_id") != space_id:
        return None
    return _desanitize_ddb(item.get("scenario_data", {}))


def _delete_scenario(space_id, scenario_id):
    existing = _get_scenario(space_id, scenario_id)
    if not existing:
        return False
    _arch_table().delete_item(
        Key={"run_id": f"scen-{scenario_id}", "record_type": "scenario"},
    )
    local_dir = os.path.join(os.path.dirname(__file__), "scenarios", scenario_id)
    if os.path.isdir(local_dir):
        shutil.rmtree(local_dir, ignore_errors=True)
        print(f"[SCEN-DEL] removed local dir: {local_dir}")
    print(f"[SCEN-DDB] deleted scen-{scenario_id} for space {space_id}")
    return True


# ---------------------------------------------------------------------------
# Architecture state (per-space, in-memory)
# ---------------------------------------------------------------------------

_arch_states = {}
_arch_lock = threading.Lock()
_arch_cancels = {}
_arch_threads = {}
_arch_prompt_overrides_by_space = {}
_arch_app_gates = {}
_arch_app_selections = {}


def _get_arch_state(space_id):
    if space_id not in _arch_states:
        saved = _load_run_state(space_id)
        if saved and saved.get("status") == "running":
            status = "interrupted"
        elif saved:
            status = saved.get("status", "idle")
        else:
            status = "idle"
        _arch_states[space_id] = {
            "status": status,
            "current_layer": (saved or {}).get("current_layer") or None,
            "error_msg": (saved or {}).get("error_msg") or None,
            "layout": None,
        }
    return _arch_states[space_id]


# ---------------------------------------------------------------------------
# Icon / classification helpers
# ---------------------------------------------------------------------------

_ICON_RULES = [
    (re.compile(r"\beks\b.*\b(pod|deploy|daemon)", re.I), "k8s-deploy"),
    (re.compile(r"\bdaemonset\b", re.I), "k8s-ds"),
    (re.compile(r"\b(fluent.?bit|adot|otel.?collector)\b", re.I), "k8s-ds"),
    (re.compile(r"\bcronjob\b", re.I), "k8s-cj"),
    (re.compile(r"\bconfigmap\b", re.I), "k8s-cm"),
    (re.compile(r"\bsecrets?\s*manager\b", re.I), "aws-secrets-manager"),
    (re.compile(r"\bfis\b|\bfault\s*injection\b", re.I), "aws-fis"),
    (re.compile(r"\bprivatelink\b", re.I), "aws-vpc"),
    (re.compile(r"\bvpc\b", re.I), "aws-vpc"),
    (re.compile(r"\b(amazon\s+)?eks\b", re.I), "aws-eks"),
    (re.compile(r"\b(amazon\s+)?ecr\b", re.I), "aws-ecr"),
    (re.compile(r"\brds\b", re.I), "aws-rds"),
    (re.compile(r"\baurora\b", re.I), "aws-rds"),
    (re.compile(r"\belasticache\b", re.I), "aws-elasticache"),
    (re.compile(r"\bdynamodb\b", re.I), "aws-dynamodb"),
    (re.compile(r"\bs3\b", re.I), "aws-s3"),
    (re.compile(r"\bsqs\b", re.I), "aws-sqs"),
    (re.compile(r"\bsns\b", re.I), "aws-sns"),
    (re.compile(r"\blambda\b", re.I), "aws-lambda"),
    (re.compile(r"\bcloudwatch\b", re.I), "aws-cloudwatch"),
    (re.compile(r"\bx-ray\b", re.I), "aws-xray"),
    (re.compile(r"\bbedrock\b", re.I), "aws-bedrock"),
    (re.compile(r"\beventbridge\b", re.I), "aws-eventbridge"),
    (re.compile(r"\b(nlb|alb|elb)\b", re.I), "aws-elb"),
    (re.compile(r"\bload\s*balancer\b", re.I), "aws-elb"),
    (re.compile(r"\bingress\b", re.I), "aws-elb"),
    (re.compile(r"\bemr\b", re.I), "aws-generic"),
    (re.compile(r"\breplicaset\b", re.I), "k8s-rs"),
    (re.compile(r"\bserviceaccount\b", re.I), "k8s-sa"),
    (re.compile(r"\bnamespace\b", re.I), "k8s-ns"),
    (re.compile(r"\bjob\b", re.I), "k8s-job"),
]


def _resolve_icon_key(kind, name, service_type, namespace):
    combined = f"{kind} {name}"
    for pattern, icon in _ICON_RULES:
        if pattern.search(combined):
            return icon
    is_ext = namespace in ("external", "managed") or kind == "ExternalService"
    if is_ext:
        return {"db": "aws-rds", "cache": "aws-elasticache", "queue": "aws-sqs",
                "gateway": "aws-generic"}.get(service_type, "aws-generic")
    return {"app": "k8s-deploy", "gateway": "k8s-svc", "cache": "k8s-deploy",
            "db": "k8s-deploy", "queue": "k8s-deploy", "worker": "k8s-deploy"
            }.get(service_type, "k8s-deploy")


_TIER_OBSERVE = re.compile(
    r"CloudWatch|X-Ray|Application Signals|Alarm|Logs Log Group|SNS Topic", re.I)
_TIER_PLATFORM = re.compile(
    r"ECR |EKS Cluster|VPC|Subnet|NAT Gateway|Internet Gateway"
    r"|EC2 Instance|IAM |Secrets Manager|ConfigMap|PersistentVolume", re.I)
_TIER_OPS = re.compile(r"FIS |Systems Manager", re.I)
_TIER_OPS_GROUPS = re.compile(r"Chaos|Simulator|Scenario", re.I)


def _classify_tier(node, app_group=""):
    """플랫폼 독립 tier 분류. kind + name + service_type + namespace 패턴 사용."""
    kind = node.get("kind", "")
    name = node.get("name", "")
    svc_type = node.get("service_type", "")
    group = node.get("group", "")
    ns = node.get("namespace", "")
    combined = f"{kind} {name}"
    is_same_group = bool(app_group and group == app_group)

    if is_same_group and svc_type in ("worker", "app", "gateway", "queue"):
        return "core"
    if _TIER_OPS.search(combined):
        return "ops"
    if _TIER_OPS_GROUPS.search(group) and not is_same_group:
        return "ops"
    if _TIER_OBSERVE.search(combined):
        return "observe"
    if _TIER_PLATFORM.search(combined):
        return "platform"
    if svc_type == "platform" and not is_same_group:
        return "platform"
    if svc_type in ("db", "cache") and not is_same_group:
        return "data"
    if ns == "external" and svc_type not in ("app", "worker"):
        return "data"
    if is_same_group:
        return "core"
    if ns == "external":
        return "data"
    return "core"


def _enrich_graph_nodes(data, app_group=""):
    for node in data.get("graph", {}).get("nodes", []):
        node["icon_key"] = _resolve_icon_key(
            node.get("kind", ""), node.get("name", ""),
            node.get("service_type", ""), node.get("namespace", ""))
        node["tier"] = _classify_tier(node, app_group)


# ---------------------------------------------------------------------------
# View helpers (_is_managed, _view_l1, _view_l2, _view_l3)
# ---------------------------------------------------------------------------

def _is_managed(n):
    """JS _archIsManaged 동일 로직."""
    if n.get("namespace") in ("managed", "external") or n.get("kind") == "ExternalService":
        return True
    name = (n.get("name") or "").lower()
    kind = (n.get("kind") or "").lower()
    if name in ("browser", "client"):
        return True
    if re.search(r"cloudwatch|eks cluster|eks worker|ecs cluster", name) and not n.get("group"):
        return True
    if re.search(r"amazon |aws |elastic |lambda|sns|sqs|dynamodb|rds|s3|bedrock|cloudfront", kind) and not n.get("group"):
        return True
    return False


def _view_l1(nodes, edges):
    """JS _archDataL1 동일 로직: 그룹별 앱 박스 + 그룹 간 엣지."""
    groups = {}
    for n in nodes:
        if _is_managed(n):
            continue
        g = n.get("group") or "기타"
        groups.setdefault(g, []).append(n)

    app_nodes = []
    for g_name, svcs in groups.items():
        app_nodes.append({
            "name": g_name,
            "count": len(svcs),
            "services": [s["name"] for s in svcs],
        })

    node_group = {}
    for n in nodes:
        if not _is_managed(n):
            node_group[n["name"]] = n.get("group") or "기타"

    edge_key = {}
    for e in edges:
        sg = node_group.get(e["source"])
        tg = node_group.get(e["target"])
        if not sg or not tg or sg == tg:
            continue
        key = f"{sg}|||{tg}"
        if key not in edge_key:
            edge_key[key] = {"source": sg, "target": tg, "count": 0}
        edge_key[key]["count"] += 1

    return {"app_nodes": app_nodes, "app_edges": list(edge_key.values())}


def _view_l2(nodes, edges, app_group, tier_filter=None):
    """JS _archDataL2Unified 동일 로직 + tier 필터.

    tier_filter: None → 전체, set("core","data") → 해당 tier 노드만.
    """
    for n in nodes:
        if "tier" not in n:
            n["tier"] = _classify_tier(n, app_group)

    node_map = {n["name"]: n for n in nodes}
    app_names = {n["name"] for n in nodes if (n.get("group") or "기타") == app_group}

    result_nodes = {}
    result_edges = []

    for n in nodes:
        if n["name"] in app_names:
            result_nodes[n["name"]] = n

    for e in edges:
        src_in = e["source"] in app_names
        tgt_in = e["target"] in app_names
        if not src_in and not tgt_in:
            continue
        if e["source"] in node_map:
            result_nodes[e["source"]] = node_map[e["source"]]
        if e["target"] in node_map:
            result_nodes[e["target"]] = node_map[e["target"]]
        result_edges.append(e)

    for n in nodes:
        if (n.get("group") or "") == app_group:
            result_nodes.setdefault(n["name"], n)

    if tier_filter:
        allowed = {name for name, n in result_nodes.items()
                   if n.get("tier") in tier_filter}
        result_nodes = {k: v for k, v in result_nodes.items() if k in allowed}
        result_edges = [e for e in result_edges
                        if e["source"] in allowed and e["target"] in allowed]

    seen = set()
    deduped = []
    for e in result_edges:
        k = f"{e['source']}→{e['target']}"
        if k not in seen:
            seen.add(k)
            deduped.append(e)

    return {"nodes": list(result_nodes.values()), "edges": deduped}


def _view_l3(nodes, edges, service_name, analysis):
    """L3: 특정 서비스의 직접 연결 노드 + 엣지."""
    node_map = {n["name"]: n for n in nodes}
    target = node_map.get(service_name)
    if not target:
        return {"center": None, "connected_nodes": [], "edges": []}

    connected = {}
    svc_edges = []
    for e in edges:
        if e["source"] == service_name:
            if e["target"] in node_map:
                connected[e["target"]] = node_map[e["target"]]
            svc_edges.append(e)
        elif e["target"] == service_name:
            if e["source"] in node_map:
                connected[e["source"]] = node_map[e["source"]]
            svc_edges.append(e)

    spof = [s for s in (analysis or {}).get("spof", []) if s.get("service") == service_name]

    return {
        "center": target,
        "connected_nodes": list(connected.values()),
        "edges": svc_edges,
        "spof": spof,
    }


# ===================================================================
# Routes
# ===================================================================

@arch_bp.route("/api/arch/config", methods=["GET"])
def api_arch_config_get():
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    from arch_analysis import _load_agent_config
    agents_cfg, _ = _load_agent_config()
    overrides = _arch_prompt_overrides_by_space.get(space_id, {})
    result = {}
    for agent_type in ("L1", "L2", "L3"):
        base = agents_cfg.get(agent_type, {})
        override = overrides.get(agent_type, {})
        result[agent_type] = {
            "display_name": base.get("display_name", agent_type),
            "system_prompt": override.get("system_prompt", base.get("system_prompt", "")),
            "max_turns": override.get("max_turns", base.get("max_turns", 10)),
            "quality_threshold": override.get("quality_threshold", base.get("quality_threshold", 75)),
        }
    return jsonify({"ok": True, "agents": result})


@arch_bp.route("/api/arch/config", methods=["POST"])
def api_arch_config_save():
    data = request.get_json(silent=True) or {}
    space_id = data.get("space_id", AGENT_SPACE_ID)
    overrides = {}
    for agent_type in ("L1", "L2", "L3"):
        cfg = data.get(agent_type, {})
        if cfg:
            overrides[agent_type] = {
                "system_prompt": cfg.get("system_prompt", ""),
                "max_turns": int(cfg.get("max_turns", 10)),
                "quality_threshold": int(cfg.get("quality_threshold", 75)),
            }
    _arch_prompt_overrides_by_space[space_id] = overrides
    return jsonify({"ok": True})


@arch_bp.route("/api/arch/config", methods=["DELETE"])
def api_arch_config_reset():
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    _arch_prompt_overrides_by_space.pop(space_id, None)
    return jsonify({"ok": True})


@arch_bp.route("/api/arch/app-name", methods=["GET"])
def api_arch_app_name_get():
    """앱 이름 조회 — DDB 저장된 값 or Agent에게 질의."""
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    ask_agent = request.args.get("ask_agent", "false") == "true"

    saved = _load_app_name(space_id)
    if saved and not ask_agent:
        return jsonify({"ok": True, "app_name": saved, "source": "saved"})

    if ask_agent:
        try:
            from ai_provider import get_provider
            tag_key = _tag_key_for_space(space_id)
            prompt = (f"'{tag_key}' 태그가 붙은 리소스들은 어떤 앱에 속해? "
                      "앱 이름 하나만 짧게 답해. 마크다운 없이 이름만.")
            resp = get_provider().send_raw(
                space_id=space_id, session_id="", prompt=prompt, user_id="system")
            agent_name = resp.get("reply", "").strip().strip("*").strip()
            return jsonify({"ok": True, "app_name": agent_name,
                            "saved_name": saved, "source": "agent"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "app_name": saved or "", "source": "saved"})


@arch_bp.route("/api/arch/app-name", methods=["POST"])
def api_arch_app_name_save():
    """앱 이름 저장 — 사용자가 확정한 이름을 DDB에 저장."""
    data = request.get_json(silent=True) or {}
    space_id = data.get("space_id", AGENT_SPACE_ID)
    app_name = data.get("app_name", "").strip()
    if not app_name:
        return jsonify({"ok": False, "error": "app_name 필수"}), 400
    _save_app_name(space_id, app_name)
    return jsonify({"ok": True, "app_name": app_name})


def _load_app_name(space_id: str) -> str:
    """DDB에서 저장된 앱 이름 조회. 없으면 space_metadata.app_tag_value fallback."""
    try:
        resp = _arch_table().get_item(
            Key={"run_id": f"app-name-{space_id}", "record_type": "app_name"})
        item = resp.get("Item")
        if item and item.get("app_name", ""):
            return item["app_name"]
    except Exception:
        pass
    try:
        from app_config import _tag_value_for_space
        return _tag_value_for_space(space_id) or ""
    except Exception:
        return ""


def _save_app_name(space_id: str, app_name: str):
    """DDB에 앱 이름 저장."""
    from datetime import datetime
    _arch_table().put_item(Item={
        "run_id": f"app-name-{space_id}",
        "record_type": "app_name",
        "scenario_id": space_id,
        "app_name": app_name,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })


@arch_bp.route("/api/arch/models")
def api_arch_models():
    models = [
        {"id": "opus", "name": "Claude Opus 4.6", "model_id": AVAILABLE_MODELS["opus"]},
        {"id": "sonnet", "name": "Claude Sonnet 4.6", "model_id": AVAILABLE_MODELS["sonnet"]},
        {"id": "haiku", "name": "Claude Haiku 4.5", "model_id": AVAILABLE_MODELS["haiku"]},
    ]
    return jsonify({"models": models, "default": "opus"})


@arch_bp.route("/api/arch/topology")
def api_arch_topology():
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    try:
        saved = _load_latest_arch(space_id)
        if saved:
            saved.pop("run_id", None)
            saved.pop("record_type", None)
            saved.pop("scenario_id", None)
            app_group = saved.get("app_name", "")
            _enrich_graph_nodes(saved, app_group=app_group)
            convs = _load_arch_conversations(space_id)
            if convs:
                saved["conversations"] = convs
            return jsonify({"ok": True, **saved})
    except Exception as e:
        print(f"[ARCH-DDB] 최신 분석 로드 실패: {e}")
    return jsonify({"ok": True, "nodes": [], "edges": []})


@arch_bp.route("/api/arch/k8s-view")
def api_arch_k8s_view():
    """K8s View: namespace 단위로 재구성된 k8s_detail 반환."""
    space_id = _req_space_id("args")
    try:
        saved = _load_latest_arch(space_id)
    except Exception:
        saved = None
    if not saved:
        return jsonify({"ok": True, "namespaces": []})

    k8s_detail = saved.get("k8s_detail", {})
    ns_map = {}

    def _empty_ns(name, app):
        return {"name": name, "app": app, "labels": {},
                "resource_quota": None, "limit_range": None,
                "workloads": [], "service_accounts": [], "secrets": [],
                "configmaps": [], "pvcs": [], "network_policies": [], "ingresses": []}

    for app_name, detail in k8s_detail.items():
        for ns_info in detail.get("namespaces", []):
            ns_name = ns_info.get("name", "")
            if ns_name and ns_name not in ns_map:
                entry = _empty_ns(ns_name, app_name)
                entry["labels"] = ns_info.get("labels", {})
                entry["resource_quota"] = ns_info.get("resource_quota")
                entry["limit_range"] = ns_info.get("limit_range")
                ns_map[ns_name] = entry

        for w in detail.get("workloads", []):
            ns = w.get("namespace", "default")
            ns_map.setdefault(ns, _empty_ns(ns, app_name))["workloads"].append(w)

        for sa in detail.get("service_accounts", []):
            ns = sa.get("namespace", "default")
            ns_map.setdefault(ns, _empty_ns(ns, app_name))["service_accounts"].append(sa)

        for s in detail.get("secrets", []):
            ns = s.get("namespace", "default")
            ns_map.setdefault(ns, _empty_ns(ns, app_name))["secrets"].append(s)

        for cm in detail.get("configmaps", []):
            ns = cm.get("namespace", "default")
            ns_map.setdefault(ns, _empty_ns(ns, app_name))["configmaps"].append(cm)

        for pvc in detail.get("persistent_volume_claims", []):
            ns = pvc.get("namespace", "default")
            ns_map.setdefault(ns, _empty_ns(ns, app_name))["pvcs"].append(pvc)

        for np_item in detail.get("network_policies", []):
            ns = np_item.get("namespace", "default")
            ns_map.setdefault(ns, _empty_ns(ns, app_name))["network_policies"].append(np_item)

        for ing in detail.get("ingresses", []):
            ns = ing.get("namespace", "default")
            ns_map.setdefault(ns, _empty_ns(ns, app_name))["ingresses"].append(ing)

    return jsonify({"ok": True, "namespaces": list(ns_map.values())})


# ── View Data API: JS 렌더러와 동일한 로직으로 가공된 뷰 데이터 반환 ──

@arch_bp.route("/api/arch/view")
def api_arch_view():
    """렌더링 뷰 데이터 — JS가 그리는 것과 동일한 가공 결과 반환.

    ?fixture=파일명 으로 fixtures/ 디렉토리의 데이터를 직접 로드 가능.
    """
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    level = request.args.get("level", "L1").upper()
    app_group = request.args.get("app", "")
    service = request.args.get("service", "")
    fixture_name = request.args.get("fixture", "")
    tier_param = request.args.get("tier", "")
    tier_filter = set(tier_param.split(",")) if tier_param else None

    saved = None
    if fixture_name:
        import json as _json
        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", fixture_name)
        if not fixture_name.endswith(".json"):
            fixture_path += ".json"
        try:
            with open(fixture_path, encoding="utf-8") as f:
                raw = _json.load(f)
            saved = raw.get("result", raw)
        except Exception as e:
            return jsonify({"ok": False, "error": f"fixture 로드 실패: {e}"})
    else:
        try:
            saved = _load_latest_arch(space_id)
        except Exception:
            saved = None
    if not saved:
        return jsonify({"ok": False, "error": "분석 결과 없음"})

    graph = saved.get("graph", {})
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    if level == "L1":
        view = _view_l1(nodes, edges)
    elif level == "L2":
        if not app_group:
            return jsonify({"ok": False, "error": "app 파라미터 필요"})
        view = _view_l2(nodes, edges, app_group, tier_filter)
    elif level == "L3":
        if not service:
            return jsonify({"ok": False, "error": "service 파라미터 필요"})
        view = _view_l3(nodes, edges, service, saved)
    else:
        return jsonify({"ok": False, "error": f"알 수 없는 level: {level}"})

    return jsonify({"ok": True, "level": level, "space_id": space_id, **view})


@arch_bp.route("/api/arch/checkpoint")
def api_arch_checkpoint():
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    try:
        cp = _load_arch_checkpoint(space_id)
    except Exception:
        cp = None
    if not cp:
        return jsonify({"ok": True, "has_checkpoint": False})
    return jsonify({
        "ok": True,
        "has_checkpoint": True,
        "completed_layers": cp.get("completed_layers", []),
    })


@arch_bp.route("/api/arch/data", methods=["DELETE"])
def api_arch_delete():
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    with _arch_lock:
        st = _get_arch_state(space_id)
        st["status"] = "idle"
        st["current_layer"] = None
        st["error_msg"] = None
    try:
        deleted = _delete_all_arch(space_id)
        return jsonify({"ok": True, "deleted": deleted})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@arch_bp.route("/api/arch/status")
def api_arch_status():
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    with _arch_lock:
        st = _get_arch_state(space_id)
        status = st["status"]
        if status == "running":
            thr = _arch_threads.get(space_id)
            if not thr or not thr.is_alive():
                st["status"] = "interrupted"
                st["current_layer"] = None
                status = "interrupted"
        current_layer = st.get("current_layer")
        error_msg = st.get("error_msg")
    has_analysis = False
    has_checkpoint = False
    try:
        has_analysis = _load_latest_arch(space_id) is not None
        has_checkpoint = _load_arch_checkpoint(space_id) is not None
    except Exception:
        pass
    return jsonify({
        "ok": True,
        "status": status,
        "current_layer": current_layer,
        "has_analysis": has_analysis,
        "has_checkpoint": has_checkpoint,
        "error_msg": error_msg,
    })


@arch_bp.route("/api/arch/cancel", methods=["POST"])
def api_arch_cancel():
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    cancel_ev = _arch_cancels.get(space_id)
    if cancel_ev:
        cancel_ev.set()
    with _arch_lock:
        st = _get_arch_state(space_id)
        st["status"] = "idle"
        st["current_layer"] = None
    return jsonify({"ok": True})


@arch_bp.route("/api/arch/discover/select-apps", methods=["POST"])
def api_arch_select_apps():
    data = request.get_json(silent=True) or {}
    space_id = data.get("space_id", AGENT_SPACE_ID)
    apps = data.get("apps", [])
    print(f"[ARCH] select-apps 수신: space={space_id}, apps={apps}")
    _arch_app_selections[space_id] = apps
    # Always persist selection to checkpoint for resume resilience
    try:
        cp = _load_arch_checkpoint(space_id)
        if cp:
            cp["selected_apps"] = apps
            _save_arch_checkpoint(space_id, cp)
    except Exception as e:
        print(f"[ARCH] select-apps checkpoint 저장 실패: {e}")
    gate = _arch_app_gates.get(space_id)
    if gate:
        gate.set()
        return jsonify({"ok": True, "selected": apps})
    print(f"[ARCH] select-apps: gate 없음 (discover thread 종료됨)")
    return jsonify({"ok": True, "selected": apps, "gate_missing": True})


@arch_bp.route("/api/arch/layout", methods=["GET"])
def api_arch_layout_get():
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    with _arch_lock:
        st = _get_arch_state(space_id)
        layout = st.get("layout")
    return jsonify({"ok": True, "layout": layout})


@arch_bp.route("/api/arch/layout", methods=["POST"])
def api_arch_layout_save():
    data = request.get_json(silent=True) or {}
    space_id = data.get("space_id", AGENT_SPACE_ID)
    with _arch_lock:
        st = _get_arch_state(space_id)
        st["layout"] = data
    return jsonify({"ok": True})


@arch_bp.route("/api/arch/versions")
def api_arch_versions():
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    limit = request.args.get("limit", 20, type=int)
    try:
        versions = _list_arch_versions(space_id, limit)
        return jsonify({"ok": True, "versions": versions})
    except Exception as e:
        return jsonify({"ok": False, "versions": [], "error": str(e)})


@arch_bp.route("/api/arch/versions/<run_id>")
def api_arch_version_detail(run_id):
    try:
        item = _load_arch_version(run_id)
        if not item:
            return jsonify({"ok": False, "error": "not found"}), 404
        item.pop("run_id", None)
        item.pop("record_type", None)
        item.pop("scenario_id", None)
        return jsonify({"ok": True, "run_id": run_id, **item})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@arch_bp.route("/api/arch/discover/stream")
def api_arch_discover_stream():
    """SSE: L1→L2→L3 layer-by-layer architecture discovery with checkpoint/resume."""
    from arch_analysis import ArchitectureAgentDiscoverer

    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    model_key = request.args.get("model", "sonnet")
    model_id = AVAILABLE_MODELS.get(model_key, AVAILABLE_MODELS["sonnet"])
    resume = request.args.get("resume", "0") == "1"
    boundary_app = request.args.get("app_name", "").strip()

    prev_thread = _arch_threads.get(space_id)
    if prev_thread and prev_thread.is_alive():
        print(f"[ARCH] 이전 분석 취소 중 ({space_id})...")
        prev_cancel = _arch_cancels.get(space_id)
        if prev_cancel:
            prev_cancel.set()
        prev_thread.join(timeout=3)
    _arch_cancels[space_id] = threading.Event()

    checkpoint = None
    if resume:
        try:
            checkpoint = _load_arch_checkpoint(space_id)
        except Exception as e:
            print(f"[ARCH] DynamoDB 체크포인트 로드 실패: {e}")
        if checkpoint:
            print(f"[ARCH] 체크포인트에서 재개: {checkpoint.get('completed_layers', [])}")
        else:
            print("[ARCH] 체크포인트 없음, 처음부터 시작")
    else:
        try:
            _delete_arch_checkpoint(space_id)
        except Exception:
            pass

    with _arch_lock:
        st = _get_arch_state(space_id)
        st["status"] = "running"
        st["current_layer"] = None
        st["error_msg"] = None
    _save_run_state(space_id, "running")

    cancel_event = _arch_cancels[space_id]
    event_q = queue.Queue()

    def on_event(event):
        if event.get("type") == "layer_complete" and event.get("checkpoint"):
            try:
                _save_arch_checkpoint(space_id, event["checkpoint"])
            except Exception as e:
                print(f"[ARCH-DDB] 체크포인트 저장 실패: {e}")
        if event.get("type") == "phase_start":
            layer = event.get("agent")
            with _arch_lock:
                _get_arch_state(space_id)["current_layer"] = layer
            _save_run_state(space_id, "running", current_layer=layer)
        event_q.put(event)

    def _collect_tagged():
        tagged = {}
        tk = _tag_key_for_space(space_id)
        if not tk:
            return tagged
        tv = _tag_value_for_space(space_id) or ""
        try:
            aws_assocs = _get_aws_associations(space_id)
            for assoc in aws_assocs:
                acct = assoc["account_id"]
                sess = _session_for_association(assoc)
                if tv:
                    total, by_service = _fetch_tagged_resources(
                        tag_key=tk, tag_value=tv, session=sess)
                else:
                    total, by_service = _fetch_tagged_resources(
                        tag_key=tk, session=sess)
                tagged[acct] = {"total": total, "by_service": by_service, "ok": True}
        except Exception as e:
            print(f"[ARCH] tagged resources 수집 실패: {e}")
        return tagged

    def _collect_tagged_for_app(sid, app_name):
        """Boundary 앱 전용: 해당 앱 태그값으로 리소스 수집."""
        tagged = {}
        tk = _tag_key_for_space(sid)
        if not tk:
            return tagged
        try:
            aws_assocs = _get_aws_associations(sid)
            for assoc in aws_assocs:
                acct = assoc["account_id"]
                sess = _session_for_association(assoc)
                total, by_service = _fetch_tagged_resources(
                    tag_key=tk, tag_value=app_name, session=sess)
                if total > 0:
                    tagged[acct] = {"total": total, "by_service": by_service, "ok": True}
        except Exception as e:
            print(f"[ARCH] boundary tagged 수집 실패 ({app_name}): {e}")
        return tagged

    app_gate = threading.Event()
    _arch_app_gates[space_id] = app_gate
    _arch_app_selections.pop(space_id, None)

    def run_discovery():
        try:
            from skill_manager import get_skill_manager
            try:
                sr = get_skill_manager().ensure_default_skills(space_id)
                if sr.get("deployed"):
                    print(f"[ARCH] 스킬 자동 배포: {sr['deployed']}")
                    event_q.put({"type": "phase_start", "phase": "skill_check",
                                 "description": f"스킬 배포 완료: {', '.join(sr['deployed'])}"})
                if sr.get("failed"):
                    print(f"[ARCH] 스킬 배포 실패: {sr['failed']}")
                    event_q.put({"type": "error",
                                 "error": f"필수 스킬 배포 실패: {', '.join(sr['failed'])}. Skill 관리에서 수동 배포하세요."})
                    return
            except Exception as e:
                print(f"[ARCH] 스킬 확인 실패 (계속 진행): {e}")

            session = _boto_session()
            if boundary_app:
                tagged = _collect_tagged_for_app(space_id, boundary_app)
            else:
                tagged = _collect_tagged()
            saved_app_name = boundary_app or _load_app_name(space_id) or None
            disc = ArchitectureAgentDiscoverer(
                space_id=space_id, session=session,
                on_event=on_event, model_id=model_id,
                prompt_overrides=_arch_prompt_overrides_by_space.get(space_id, {}),
                tagged_resources=tagged,
                app_gate=app_gate,
                app_selection_ref=_arch_app_selections,
                app_name=saved_app_name,
                force_new_session=bool(boundary_app),
                is_boundary=bool(boundary_app),
            )
            analysis = disc.discover(checkpoint=checkpoint,
                                     cancel_event=cancel_event)
            has_data = bool(analysis and analysis.graph and analysis.graph.nodes)
            if cancel_event.is_set() and not has_data:
                _save_run_state(space_id, "cancelled")
                event_q.put({"type": "error", "error": "분석이 취소되었습니다"})
                return
            with _arch_lock:
                st = _get_arch_state(space_id)
                st["status"] = "idle"
                st["current_layer"] = None
            _save_run_state(space_id, "complete")

            if has_data:
                app_name = boundary_app or _load_app_name(space_id) or "default"
                is_main = not bool(boundary_app)
                try:
                    run_id = _save_arch_analysis(space_id, analysis, model_id, app_name=app_name, is_main=is_main)
                    _delete_arch_checkpoint(space_id)
                    print(f"[ARCH-DDB] 분석 저장 완료: {run_id} (is_main={is_main})")
                except Exception as e:
                    print(f"[ARCH-DDB] 분석 저장 실패: {e}")
                try:
                    _save_arch_conversations(space_id, analysis.conversations, app_name=app_name)
                except Exception as e:
                    print(f"[ARCH-DDB] conversations 저장 실패: {e}")

            event_q.put({
                "type": "complete",
                "analysis": analysis.to_dict() if has_data else {},
                "saved": has_data,
            })
        except Exception as e:
            with _arch_lock:
                st = _get_arch_state(space_id)
                st["status"] = "error"
                st["error_msg"] = str(e)
            _save_run_state(space_id, "error", error_msg=str(e))
            event_q.put({"type": "error", "error": str(e), "trace": traceback.format_exc()})
        finally:
            if _arch_app_gates.get(space_id) is app_gate:
                _arch_app_gates.pop(space_id, None)
                _arch_app_selections.pop(space_id, None)

    t = threading.Thread(target=run_discovery, daemon=True)
    _arch_threads[space_id] = t
    t.start()

    def generate():
        yield f"data: {json.dumps({'type': 'phase_start', 'agent': 'init', 'phase': 'init', 'label': '분석 준비', 'description': 'Agent 세션 생성 및 사전 정보 수집 중...'}, ensure_ascii=False)}\n\n"
        orphan_count = 0
        while True:
            try:
                event = event_q.get(timeout=15)
                orphan_count = 0
            except queue.Empty:
                if cancel_event.is_set():
                    return
                if not t.is_alive():
                    orphan_count += 1
                    if orphan_count > 2:
                        return
                yield ": heartbeat\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event["type"] in ("complete", "error"):
                return

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@arch_bp.route("/api/arch/boundary-analyze/stream")
def api_arch_boundary_analyze_stream():
    """SSE: Boundary 앱 분석 → 결과를 현재 토폴로지에 merge.

    boundary 노드 클릭 시 호출. 해당 외부 앱에 대해 #arch-q2 실행 후
    발견된 노드/엣지를 현재 Space의 최신 분석에 추가 저장.
    """
    from arch_analysis import AgentChatClient, _extract_recommendation_json

    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    app_name = request.args.get("app_name", "").strip()
    if not app_name:
        return jsonify({"ok": False, "error": "app_name required"}), 400

    event_q = queue.Queue()

    def run_boundary_analysis():
        try:
            chat_client = AgentChatClient(space_id=space_id)
            exec_id = chat_client.get_or_create_session()

            q = f"#arch-q2 1 {app_name}"
            q += f"\n이 앱의 App 태그: App={app_name}"
            q += f"\nApp={app_name} 태그가 붙은 리소스가 이 앱 소속입니다. 다른 App 태그를 가진 리소스는 경계 노드로 표현하세요."

            saved = _load_latest_arch(space_id)
            if saved:
                nodes = saved.get("graph", {}).get("nodes", [])
                known = []
                known_apps = set()
                for n in nodes:
                    if (n.get("group", "").lower() != app_name.lower()
                            and n.get("service_type") != "boundary"
                            and n.get("name")):
                        entry = n["name"]
                        kind = n.get("kind", "")
                        if kind:
                            entry += f" ({kind})"
                        known.append(entry)
                    if n.get("group") and n.get("group").lower() != app_name.lower():
                        known_apps.add(n["group"])
                if known_apps:
                    q += f"\n\n## 중복 금지\n이미 식별된 앱: {', '.join(sorted(known_apps))}"
                if known:
                    q += f"\n\n## 이미 발견된 리소스 (다른 앱 소속)"
                    q += "\n아래 리소스는 이미 다른 앱에서 발견됨. 동일 물리적 리소스를 다른 이름으로 중복 나열 금지:"
                    q += "\n" + ", ".join(known)

            event_q.put({"type": "phase_start", "phase": "boundary_q2",
                         "description": f"{app_name} 분석 중..."})

            resp = chat_client.ask(exec_id, q)
            answer = resp.final_text
            if not answer:
                event_q.put({"type": "error", "error": "Agent 응답 없음"})
                return

            q2_data = _extract_recommendation_json(answer)
            if not q2_data:
                event_q.put({"type": "error", "error": "JSON 파싱 실패",
                             "raw_answer": answer[:2000]})
                return

            new_nodes = []
            for n in q2_data.get("nodes", []):
                new_nodes.append({
                    "name": n.get("name", ""),
                    "namespace": n.get("namespace", ""),
                    "kind": n.get("kind", "Deployment"),
                    "service_type": n.get("service_type", "app"),
                    "group": n.get("group", app_name),
                    "labels": n.get("labels", {}),
                    "ports": n.get("ports", []),
                })
            for bn in q2_data.get("boundary_nodes", []):
                new_nodes.append({
                    "name": bn.get("name", ""),
                    "namespace": "external",
                    "kind": bn.get("kind", "External App"),
                    "service_type": "boundary",
                    "group": bn.get("app_name", bn.get("name", "")),
                    "labels": bn.get("labels", {}),
                    "ports": [],
                })

            new_edges = []
            for e in q2_data.get("edges", []):
                new_edges.append({
                    "source": e.get("source", ""),
                    "target": e.get("target", ""),
                    "protocol": e.get("protocol", ""),
                    "port": e.get("port", 0),
                    "description": e.get("description", ""),
                })

            new_workflows = q2_data.get("workflows", [])

            data = {
                "graph": {"nodes": new_nodes, "edges": new_edges},
                "workflows": new_workflows,
            }
            run_id = _save_arch_analysis(space_id, data, "", app_name=app_name)
            print(f"[ARCH] boundary 분석 독립 저장: {run_id}")

            event_q.put({
                "type": "complete",
                "app_name": app_name,
                "new_nodes": new_nodes,
                "new_edges": new_edges,
            })

        except Exception as e:
            event_q.put({"type": "error", "error": str(e)})

    t = threading.Thread(target=run_boundary_analysis, daemon=True)
    t.start()

    def generate():
        yield f"data: {json.dumps({'type': 'phase_start', 'phase': 'boundary_init', 'description': f'{app_name} 외부 앱 분석 시작...'}, ensure_ascii=False)}\n\n"
        missed = 0
        while True:
            try:
                event = event_q.get(timeout=15)
                missed = 0
            except queue.Empty:
                missed += 1
                if missed > 20:
                    return
                yield ": heartbeat\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event["type"] in ("complete", "error"):
                return

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@arch_bp.route("/api/arch/recommend", methods=["POST"])
def api_arch_recommend():
    """Bedrock Claude architecture analysis + scenario recommendation."""
    from arch_analysis import ArchitectureRecommender
    from botocore.config import Config as BotoConfig

    space_id = request.json.get("space_id", AGENT_SPACE_ID) if request.json else AGENT_SPACE_ID
    saved = None
    try:
        saved = _load_latest_arch(space_id)
    except Exception:
        pass
    if not saved or not saved.get("graph"):
        return jsonify({"ok": False, "error": "Run discover first"}), 400

    try:
        body = request.json or {}
        model_key = body.get("model", "opus")
        model_id = AVAILABLE_MODELS.get(model_key, AVAILABLE_MODELS["opus"])

        session = _boto_session()
        bedrock = session.client("bedrock-runtime", config=BotoConfig(read_timeout=300))

        from arch_analysis import ServiceGraph
        graph = ServiceGraph.from_dict(saved["graph"])
        recommender = ArchitectureRecommender(bedrock, model_id=model_id)
        result = recommender.recommend(graph)

        return jsonify({"ok": True, "result": result.to_dict(), "model": model_key})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


# ═══════════════════════════════════════════════════════════════
# Service-level code analysis endpoints
# ═══════════════════════════════════════════════════════════════

_svc_analysis_threads = {}
_svc_analysis_cancels = {}


def _save_service_diagram(space_id, service_name, diagram_type, data):
    """Save a single service diagram to DynamoDB."""
    import hashlib
    key = f"service/{service_name}/{diagram_type}"
    key_hash = hashlib.md5(key.encode()).hexdigest()[:12]
    item = {
        "run_id": f"diag-{space_id[:8]}-{key_hash}",
        "record_type": "diagram",
        "scenario_id": space_id,
        "diagram_key": key,
        "service_name": service_name,
        "diagram_type": diagram_type,
        "status": "done",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "data": data or {},
    }
    _arch_table().put_item(Item=_sanitize_ddb(item))
    print(f"[SVC-DIAG] saved {key} for space {space_id[:8]}")


def _load_service_diagrams(space_id, service_name):
    """Load all completed diagrams for a service."""
    from boto3.dynamodb.conditions import Key
    prefix = f"diag-{space_id[:8]}-"
    try:
        resp = _arch_table().query(
            IndexName="scenario-id-index",
            KeyConditionExpression=Key("scenario_id").eq(space_id) & Key("run_id").begins_with(prefix),
            FilterExpression="service_name = :sn",
            ExpressionAttributeValues={":sn": service_name},
        )
        results = {}
        for item in resp.get("Items", []):
            dtype = item.get("diagram_type", "")
            if dtype and item.get("status") == "done":
                results[dtype] = _desanitize_ddb(item.get("data", {}))
        return results
    except Exception as e:
        print(f"[SVC-DIAG] load error: {e}")
        return {}


@arch_bp.route("/api/arch/service-analysis/stream")
def api_arch_service_analysis_stream():
    """SSE: Service-level code analysis via Agent + GitHub (diagram-unit progressive).

    service_name can be comma-separated for multi-service composite analysis.
    Each service is analyzed individually, then results are merged per diagram type.
    """
    from arch_analysis import ServiceCodeAnalyzer

    ddb_space_id = request.args.get("space_id", AGENT_SPACE_ID)
    space_id = AGENT_SPACE_ID  # Agent Chat API requires the actual Agent Space ID
    service_name_raw = request.args.get("service_name", "")
    diagrams_str = request.args.get("diagrams", "static,api")
    requested = [d.strip() for d in diagrams_str.split(",") if d.strip()]

    service_names = [s.strip() for s in service_name_raw.split(",") if s.strip()]
    if not service_names:
        return jsonify({"ok": False, "error": "service_name required"}), 400

    composite_key = "+".join(sorted(service_names))
    thread_key = f"{space_id}-{composite_key}"
    prev = _svc_analysis_threads.get(thread_key)
    if prev and prev.is_alive():
        cancel = _svc_analysis_cancels.get(thread_key)
        if cancel:
            cancel.set()
        prev.join(timeout=3)

    cancel_event = threading.Event()
    _svc_analysis_cancels[thread_key] = cancel_event

    all_completed = {}
    for svc in service_names:
        svc_diagrams = _load_service_diagrams(ddb_space_id, svc)
        for dtype, data in svc_diagrams.items():
            if dtype not in all_completed:
                all_completed[dtype] = []
            all_completed[dtype].append(data)

    def _merge_diagrams(dtype, data_list):
        """Merge multiple service analysis results into one composite."""
        if not data_list:
            return None
        if len(data_list) == 1:
            return data_list[0]
        if dtype == "component":
            merged = {"service_name": composite_key, "language": "", "components": [], "relationships": [], "provided_interfaces": [], "required_interfaces": []}
            for d in data_list:
                merged["language"] = merged["language"] or d.get("language", "")
                svc_name = d.get("service_name", "")
                for comp in d.get("components", []):
                    if not comp.get("source_service"):
                        comp["source_service"] = svc_name
                    merged["components"].append(comp)
                merged["relationships"].extend(d.get("relationships", []))
                merged["provided_interfaces"].extend(d.get("provided_interfaces", []))
                merged["required_interfaces"].extend(d.get("required_interfaces", []))
            return merged
        elif dtype == "dynamic":
            merged = {"service_name": composite_key, "endpoint": "composite", "call_flow": []}
            for d in data_list:
                merged["call_flow"].extend(d.get("call_flow", []))
            return merged
        return data_list[0]

    cached_types = [d for d in requested if d in all_completed and len(all_completed[d]) == len(service_names)]
    pending_types = [d for d in requested if d not in cached_types]

    event_q = queue.Queue()

    def on_event(event):
        if event.get("type") == "diagram_done" and event.get("data"):
            parts = event["key"].split("/")
            svc = parts[1] if len(parts) > 1 else ""
            dtype = parts[-1]
            try:
                _save_service_diagram(ddb_space_id, svc, dtype, event["data"])
            except Exception as e:
                print(f"[SVC-DIAG] save error: {e}")
        event_q.put(event)

    def run_analysis():
        from ai_provider import get_provider
        get_provider()

        try:
            for svc in service_names:
                if cancel_event.is_set():
                    break
                svc_completed = _load_service_diagrams(ddb_space_id, svc)
                svc_pending = [d for d in pending_types if d not in svc_completed]
                if not svc_pending:
                    continue

                analyzer = ServiceCodeAnalyzer(
                    space_id=space_id,
                    service_name=svc,
                    on_event=on_event,
                    cancel_event=cancel_event,
                )
                analyzer.analyze(diagram_types=svc_pending)

            if not cancel_event.is_set():
                merged_results = {}
                for dtype in requested:
                    all_data = []
                    for svc in service_names:
                        svc_diagrams = _load_service_diagrams(ddb_space_id, svc)
                        if dtype in svc_diagrams:
                            all_data.append(svc_diagrams[dtype])
                    if all_data:
                        merged_results[dtype] = _merge_diagrams(dtype, all_data)

                for dtype, mdata in merged_results.items():
                    event_q.put({"type": "diagram_done", "key": f"service/{composite_key}/{dtype}", "data": mdata})

            event_q.put({"type": "complete"})
        except Exception as e:
            event_q.put({"type": "error", "error": str(e)})

    if pending_types:
        t = threading.Thread(target=run_analysis, daemon=True, name=f"svc-{composite_key}")
        _svc_analysis_threads[thread_key] = t
        t.start()

    def generate():
        if cached_types:
            for dtype in cached_types:
                merged = _merge_diagrams(dtype, all_completed[dtype])
                yield f"data: {json.dumps({'type': 'diagram_cached', 'key': f'service/{composite_key}/{dtype}', 'data': merged}, ensure_ascii=False)}\n\n"

        if not pending_types:
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"
            return

        missed = 0
        while True:
            try:
                event = event_q.get(timeout=15)
                missed = 0
            except queue.Empty:
                missed += 1
                if cancel_event.is_set() or missed > 20:
                    return
                yield ": heartbeat\n\n"
                continue
            if event.get("type") == "diagram_done" and event.get("key", "").startswith(f"service/{composite_key}/"):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            elif event.get("type") == "diagram_start":
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            elif event.get("type") == "verify_start":
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            elif event.get("type") in ("complete", "error"):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                return
            elif event.get("type") == "diagram_done":
                svc_name = event.get("key", "").split("/")[1] if "/" in event.get("key", "") else ""
                dtype = event.get("key", "").split("/")[-1]
                yield f"data: {json.dumps({'type': 'service_done', 'service': svc_name, 'diagram': dtype}, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@arch_bp.route("/api/arch/service-analysis")
def api_arch_service_analysis_get():
    """Get cached service analysis diagrams."""
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    service_name = request.args.get("service_name", "")
    if not service_name:
        return jsonify({"ok": False, "error": "service_name required"}), 400

    diagrams = _load_service_diagrams(space_id, service_name)
    return jsonify({"ok": True, "service_name": service_name, "diagrams": diagrams})


@arch_bp.route("/api/arch/service-analysis/history")
def api_arch_service_analysis_history():
    """Return all service code analysis entries for a space (for chat panel history)."""
    from boto3.dynamodb.conditions import Key
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    prefix = f"diag-{space_id[:8]}-"
    try:
        resp = _arch_table().query(
            IndexName="scenario-id-index",
            KeyConditionExpression=Key("scenario_id").eq(space_id) & Key("run_id").begins_with(prefix),
        )
        # Group by service_name
        by_service = {}
        for item in resp.get("Items", []):
            svc = item.get("service_name", "")
            if not svc:
                continue
            if svc not in by_service:
                by_service[svc] = {"services": [svc], "diagrams": {}, "completed_at": ""}
            dtype = item.get("diagram_type", "")
            if dtype and item.get("status") == "done":
                by_service[svc]["diagrams"][dtype] = _desanitize_ddb(item.get("data", {}))
                ts = item.get("generated_at", "")
                if ts > by_service[svc]["completed_at"]:
                    by_service[svc]["completed_at"] = ts

        analyses = sorted(by_service.values(), key=lambda x: x.get("completed_at", ""))
        return jsonify({"ok": True, "analyses": analyses})
    except Exception as e:
        print(f"[SVC-HISTORY] error: {e}")
        return jsonify({"ok": True, "analyses": []})


# ===================================================================
# Component Analysis (L3 in single-app mode)
# ===================================================================

@arch_bp.route("/api/arch/component/stream")
def api_arch_component_stream():
    """SSE: Service component analysis via Agent + GitHub code reading."""
    from arch_analysis import AgentChatClient, load_questions, _extract_recommendation_json

    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    service_name = request.args.get("service_name", "")
    if not service_name:
        return jsonify({"ok": False, "error": "service_name required"}), 400

    event_q = queue.Queue()

    def run_component_analysis():
        try:
            session = _boto_session()
            from ai_provider import init_provider
            init_provider()

            q_config = load_questions()
            svc_analysis = q_config.get("service_analysis", {})
            component_cfg = svc_analysis.get("component", {})
            template = component_cfg.get("question_template", "")
            if not template:
                event_q.put({"type": "error", "error": "component question_template not configured"})
                return

            question = template.replace("{service_name}", service_name)

            chat_client = AgentChatClient(space_id, session)
            exec_id = chat_client.get_or_create_session()

            event_q.put({"type": "phase_start", "phase": "component",
                         "description": f"{service_name} 컴포넌트 분석 중..."})

            resp = chat_client.ask(exec_id, question)
            answer = resp.final_text

            event_q.put({"type": "agent_answer", "agent": "component",
                         "answer": answer[:800]})

            parsed = _extract_recommendation_json(answer)
            if parsed:
                event_q.put({"type": "complete", "data": parsed})
            else:
                event_q.put({"type": "error", "error": "JSON 파싱 실패",
                             "raw_answer": answer[:2000]})
        except Exception as e:
            event_q.put({"type": "error", "error": str(e)})

    t = threading.Thread(target=run_component_analysis, daemon=True)
    t.start()

    def generate():
        while True:
            try:
                event = event_q.get(timeout=30)
            except queue.Empty:
                yield ": heartbeat\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event["type"] in ("complete", "error"):
                return

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
