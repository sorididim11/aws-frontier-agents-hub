#!/usr/bin/env python3
"""
Runtime Observability Collector for Architecture Diagrams.

Queries AWS APIs (CloudWatch Application Signals, X-Ray, CloudWatch Alarms)
and enriches knowledge.json with live metrics. This data drives visual
annotations in draw.io diagrams (edge thickness, error rate coloring,
alarm badges, shadow dependency discovery).

Usage:
    python3 observe.py \
        --knowledge docs/architecture/knowledge.json \
        --output docs/architecture/knowledge.json \
        --region us-east-1 \
        --cluster devops-agent-test-cluster \
        --namespace dockercoins \
        --hours 3

Requires: boto3, valid AWS credentials with CloudWatch, X-Ray permissions.
"""
import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone


def load_config_yaml(config_path: str) -> dict:
    """Load config.yaml if available (plain parser, no PyYAML dependency)."""
    config = {}
    try:
        with open(config_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line and not line.endswith(":"):
                    key, val = line.split(":", 1)
                    val = val.strip().strip('"').strip("'")
                    if val:
                        config[key.strip()] = val
    except FileNotFoundError:
        pass
    return config


# ─── Collectors ─────────────────────────────────────────────────────────────


def _boto3_session(region: str, profile: str = None):
    """Create a boto3 session with optional profile."""
    import boto3
    if profile:
        return boto3.Session(profile_name=profile, region_name=region)
    return boto3.Session(region_name=region)


def collect_service_metrics(
    region: str, cluster: str, namespace: str,
    service_ids: list, hours: int, profile: str = None
) -> dict:
    """
    Query CloudWatch Application Signals for each service.
    Returns: {service_id: {error_count, fault_count, avg_latency_ms, p99_latency_ms, request_count, health}}
    """
    session = _boto3_session(region, profile)
    cw = session.client("cloudwatch")
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)
    environment = f"eks:{cluster}/{namespace}"

    results = {}

    for svc_id in service_ids:
        svc_data = {
            "collected_at": now.isoformat(),
            "window_hours": hours,
            "error_count": 0,
            "fault_count": 0,
            "avg_latency_ms": 0.0,
            "p99_latency_ms": 0.0,
            "request_count": 0,
            "health": "unknown",
        }

        dimensions = [
            {"Name": "Service", "Value": svc_id},
            {"Name": "Environment", "Value": environment},
        ]

        for metric_name, stat in [("Error", "Sum"), ("Fault", "Sum"), ("Latency", "Average")]:
            try:
                resp = cw.get_metric_statistics(
                    Namespace="ApplicationSignals",
                    MetricName=metric_name,
                    Dimensions=dimensions,
                    StartTime=start,
                    EndTime=now,
                    Period=300,
                    Statistics=[stat],
                )
                datapoints = resp.get("Datapoints", [])
                if datapoints:
                    if metric_name == "Error":
                        svc_data["error_count"] = int(sum(d[stat] for d in datapoints))
                    elif metric_name == "Fault":
                        svc_data["fault_count"] = int(sum(d[stat] for d in datapoints))
                    elif metric_name == "Latency":
                        avg = sum(d[stat] for d in datapoints) / len(datapoints)
                        svc_data["avg_latency_ms"] = round(avg, 2)
            except Exception as e:
                print(f"  [WARN] {svc_id}/{metric_name}: {e}", file=sys.stderr)

        # P99 latency via ExtendedStatistics
        try:
            resp = cw.get_metric_statistics(
                Namespace="ApplicationSignals",
                MetricName="Latency",
                Dimensions=dimensions,
                StartTime=start,
                EndTime=now,
                Period=hours * 3600,  # single period for percentile
                ExtendedStatistics=["p99"],
            )
            datapoints = resp.get("Datapoints", [])
            if datapoints and "ExtendedStatistics" in datapoints[0]:
                svc_data["p99_latency_ms"] = round(
                    datapoints[0]["ExtendedStatistics"].get("p99", 0.0), 2
                )
        except Exception:
            pass

        # Request count: Error + non-Error ≈ approximation from Latency sample count
        try:
            resp = cw.get_metric_statistics(
                Namespace="ApplicationSignals",
                MetricName="Latency",
                Dimensions=dimensions,
                StartTime=start,
                EndTime=now,
                Period=300,
                Statistics=["SampleCount"],
            )
            datapoints = resp.get("Datapoints", [])
            svc_data["request_count"] = int(sum(d["SampleCount"] for d in datapoints))
        except Exception:
            pass

        # Derive health
        total = svc_data["request_count"]
        errors = svc_data["error_count"] + svc_data["fault_count"]
        if total == 0:
            svc_data["health"] = "no_data"
        elif errors / max(total, 1) > 0.05:
            svc_data["health"] = "unhealthy"
        elif errors / max(total, 1) > 0.01:
            svc_data["health"] = "degraded"
        else:
            svc_data["health"] = "healthy"

        # Only store if we got any data
        if total > 0 or svc_data["error_count"] > 0:
            results[svc_id] = svc_data
            print(f"  {svc_id}: {total} reqs, {errors} errs, "
                  f"avg={svc_data['avg_latency_ms']}ms, health={svc_data['health']}",
                  file=sys.stderr)

    return results


def collect_trace_topology(region: str, hours: int, profile: str = None) -> tuple:
    """
    Query X-Ray trace summaries and extract service-to-service call topology.
    Returns: (flow_metrics, discovered_flows)
      flow_metrics: {(from, to): {call_count, avg_latency_ms, error_rate, last_seen}}
      discovered_flows: list of flows not in static knowledge
    """
    session = _boto3_session(region, profile)
    xray = session.client("xray")
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)

    # Collect trace summaries
    trace_ids = []
    paginator_kwargs = {
        "StartTime": start,
        "EndTime": now,
        "TimeRangeType": "TraceId",
        "Sampling": True,
    }

    try:
        resp = xray.get_trace_summaries(**paginator_kwargs)
        summaries = resp.get("TraceSummaries", [])
        trace_ids.extend([s["Id"] for s in summaries])

        # Paginate (up to 500 traces to keep API cost reasonable)
        while resp.get("NextToken") and len(trace_ids) < 500:
            resp = xray.get_trace_summaries(
                NextToken=resp["NextToken"], **paginator_kwargs
            )
            summaries = resp.get("TraceSummaries", [])
            trace_ids.extend([s["Id"] for s in summaries])
    except Exception as e:
        print(f"  [WARN] get_trace_summaries failed: {e}", file=sys.stderr)
        return {}, []

    print(f"  Collected {len(trace_ids)} trace IDs", file=sys.stderr)
    if not trace_ids:
        return {}, []

    # Batch-get traces (max 5 per call)
    edge_data = defaultdict(lambda: {"count": 0, "latencies": [], "errors": 0, "last_seen": ""})

    for i in range(0, len(trace_ids), 5):
        batch = trace_ids[i:i+5]
        try:
            resp = xray.batch_get_traces(TraceIds=batch)
            for trace in resp.get("Traces", []):
                _extract_edges(trace, edge_data)
        except Exception as e:
            print(f"  [WARN] batch_get_traces failed: {e}", file=sys.stderr)

    # Build flow_metrics
    flow_metrics = {}
    for (src, dst), data in edge_data.items():
        count = data["count"]
        avg_lat = round(sum(data["latencies"]) / len(data["latencies"]), 2) if data["latencies"] else 0.0
        err_rate = round(data["errors"] / max(count, 1), 4)
        flow_metrics[(src, dst)] = {
            "call_count": count,
            "avg_latency_ms": avg_lat,
            "error_rate": err_rate,
            "last_seen": data["last_seen"],
        }
        print(f"  {src} -> {dst}: {count} calls, avg={avg_lat}ms, err={err_rate}",
              file=sys.stderr)

    return flow_metrics, []


def _extract_edges(trace: dict, edge_data: dict):
    """Parse X-Ray trace segments/subsegments to extract service edges."""
    segments = trace.get("Segments", [])
    for seg in segments:
        try:
            doc = json.loads(seg.get("Document", "{}"))
        except (json.JSONDecodeError, TypeError):
            continue

        origin = doc.get("name", "")
        start_time = doc.get("start_time", 0)
        ts_str = datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat() if start_time else ""

        # Subsegments are downstream calls
        for sub in doc.get("subsegments", []):
            target = sub.get("name", "")
            if not target or target == origin:
                continue

            duration_ms = round((sub.get("end_time", 0) - sub.get("start_time", 0)) * 1000, 2)
            is_error = sub.get("error", False) or sub.get("fault", False)

            key = (_normalize_svc_name(origin), _normalize_svc_name(target))
            edge_data[key]["count"] += 1
            if duration_ms > 0:
                edge_data[key]["latencies"].append(duration_ms)
            if is_error:
                edge_data[key]["errors"] += 1
            if ts_str > edge_data[key]["last_seen"]:
                edge_data[key]["last_seen"] = ts_str

            # Recurse into nested subsegments
            _extract_nested_subsegments(sub, target, edge_data, ts_str)


def _extract_nested_subsegments(parent_sub: dict, parent_name: str, edge_data: dict, ts_str: str):
    """Recurse through nested subsegments for deeper call chains."""
    for sub in parent_sub.get("subsegments", []):
        target = sub.get("name", "")
        if not target or target == parent_name:
            continue

        duration_ms = round((sub.get("end_time", 0) - sub.get("start_time", 0)) * 1000, 2)
        is_error = sub.get("error", False) or sub.get("fault", False)

        key = (_normalize_svc_name(parent_name), _normalize_svc_name(target))
        edge_data[key]["count"] += 1
        if duration_ms > 0:
            edge_data[key]["latencies"].append(duration_ms)
        if is_error:
            edge_data[key]["errors"] += 1
        if ts_str > edge_data[key]["last_seen"]:
            edge_data[key]["last_seen"] = ts_str

        _extract_nested_subsegments(sub, target, edge_data, ts_str)


def _normalize_svc_name(name: str) -> str:
    """Normalize X-Ray service names to match knowledge.json IDs."""
    # X-Ray often uses FQDN or k8s service names
    # Strip common suffixes/prefixes
    name = name.lower().strip()
    # Remove port suffix like ":80"
    if ":" in name:
        name = name.split(":")[0]
    # Remove k8s namespace suffix like ".dockercoins.svc.cluster.local"
    if ".svc.cluster.local" in name:
        name = name.split(".")[0]
    elif "." in name and not name.startswith("aws"):
        name = name.split(".")[0]
    return name


def collect_alarm_states(region: str, alarm_prefix: str, profile: str = None) -> list:
    """
    Query CloudWatch Alarms by prefix.
    Returns: list of {name, state, reason, updated_at}
    """
    session = _boto3_session(region, profile)
    cw = session.client("cloudwatch")
    alarms = []

    try:
        resp = cw.describe_alarms(AlarmNamePrefix=alarm_prefix)
        for a in resp.get("MetricAlarms", []):
            alarms.append({
                "name": a["AlarmName"],
                "state": a["StateValue"],
                "reason": (a.get("StateReason", ""))[:200],
                "updated_at": a.get("StateUpdatedTimestamp", ""),
            })
        for a in resp.get("CompositeAlarms", []):
            alarms.append({
                "name": a["AlarmName"],
                "state": a["StateValue"],
                "reason": (a.get("StateReason", ""))[:200],
                "updated_at": a.get("StateUpdatedTimestamp", ""),
            })
    except Exception as e:
        print(f"  [WARN] describe_alarms failed: {e}", file=sys.stderr)

    # Serialize datetime objects
    for alarm in alarms:
        if hasattr(alarm["updated_at"], "isoformat"):
            alarm["updated_at"] = alarm["updated_at"].isoformat()

    print(f"  {len(alarms)} alarms: "
          + ", ".join(f"{a['name']}={a['state']}" for a in alarms[:5]),
          file=sys.stderr)

    return alarms


# ─── Enrichment ─────────────────────────────────────────────────────────────


def _build_flow_alias_map(knowledge: dict) -> dict:
    """
    Build a mapping from X-Ray edge keys to knowledge flow keys.
    X-Ray uses base names (e.g., "hasher") while knowledge uses service IDs
    (e.g., "hasher-svc"). This tries multiple matching strategies.
    """
    svc_ids = {s["id"] for s in knowledge.get("services", [])}
    alias_map = {}  # (xray_src, xray_dst) → (knowledge_src, knowledge_dst)

    for flow in knowledge.get("data_flows", []):
        src, dst = flow["from"], flow["to"]
        # Direct match
        alias_map[(src, dst)] = (src, dst)

        # Alias: remove "-svc" suffix from dst (X-Ray sees "hasher" not "hasher-svc")
        if dst.endswith("-svc"):
            alias_map[(src, dst[:-4])] = (src, dst)
        if src.endswith("-svc"):
            alias_map[(src[:-4], dst)] = (src, dst)

    return alias_map


# Patterns that indicate framework internals, not real service-to-service flows
_INTERNAL_PATTERNS = [
    "middleware", "request handler", "cookieparser", "session",
    "urlencodedparser", "jsonparser", "query", "expressinit",
    "bodyparser", "cors", "router", "servestatic",
]


def _is_internal_framework_call(name: str) -> bool:
    """Filter out Express.js middleware, route handlers, etc."""
    name_lower = name.lower()
    return any(p in name_lower for p in _INTERNAL_PATTERNS)


def enrich_knowledge(knowledge: dict, service_metrics: dict,
                     flow_metrics: dict, alarms: list,
                     hours: int) -> dict:
    """Add observed fields to knowledge.json (non-destructive merge)."""
    now = datetime.now(timezone.utc).isoformat()

    # Enrich services
    for svc in knowledge.get("services", []):
        svc_id = svc["id"]
        if svc_id in service_metrics:
            svc["observed"] = service_metrics[svc_id]

    # Build alias map for fuzzy flow matching
    alias_map = _build_flow_alias_map(knowledge)

    # Track which X-Ray edges matched a static flow
    matched_xray_edges = set()

    # Enrich data_flows (try direct match first, then alias)
    for flow in knowledge.get("data_flows", []):
        src = flow["from"]
        dst = flow["to"]

        # Try matching from flow_metrics with alias resolution
        for xray_key, knowledge_key in alias_map.items():
            if knowledge_key == (src, dst) and xray_key in flow_metrics:
                flow["observed"] = {
                    "collected_at": now,
                    "source": "xray",
                    **flow_metrics[xray_key],
                }
                matched_xray_edges.add(xray_key)
                break

    # Discover shadow flows (in X-Ray but not matched to any static flow)
    known_svc_ids = {s["id"] for s in knowledge.get("services", [])}
    observed_flows = []
    for (src, dst), metrics in flow_metrics.items():
        if (src, dst) in matched_xray_edges:
            continue
        # Skip framework internal calls
        if _is_internal_framework_call(src) or _is_internal_framework_call(dst):
            continue
        # Only report if at least one end is a known service
        if src in known_svc_ids or dst in known_svc_ids:
            observed_flows.append({
                "from": src,
                "to": dst,
                "call_count": metrics["call_count"],
                "avg_latency_ms": metrics["avg_latency_ms"],
                "error_rate": metrics["error_rate"],
                "last_seen": metrics["last_seen"],
                "note": "shadow dependency - discovered via X-Ray, not in static data_flows",
            })
    if observed_flows:
        knowledge["observed_flows"] = observed_flows
        print(f"\n  Discovered {len(observed_flows)} shadow flows!", file=sys.stderr)

    # Add alarms
    if alarms:
        knowledge["alarms"] = alarms

    # Add observe metadata
    knowledge.setdefault("meta", {})
    knowledge["meta"]["last_observed_at"] = now
    knowledge["meta"]["observe_window_hours"] = hours

    return knowledge


# ─── Utility: Compute edge visual properties ────────────────────────────────


def compute_edge_weight(call_count: int) -> int:
    """Map call_count to edge strokeWidth (1-4px, log scale)."""
    if call_count <= 0:
        return 1
    return min(4, max(1, int(math.log10(max(call_count, 1)) + 1)))


def compute_health_color(health: str) -> str:
    """Map health status to border color."""
    return {
        "healthy": "",          # default (no override)
        "degraded": "#FF9800",  # orange
        "unhealthy": "#D32F2F", # red
        "no_data": "#9E9E9E",   # gray
    }.get(health, "")


# ─── CLI ────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Collect runtime observability data and enrich knowledge.json"
    )
    parser.add_argument("--knowledge", required=True,
                        help="Path to knowledge.json (input)")
    parser.add_argument("--output", default=None,
                        help="Path to write enriched knowledge.json (default: overwrite input)")
    parser.add_argument("--region", default=None,
                        help="AWS region (default: from knowledge.json or us-east-1)")
    parser.add_argument("--cluster", default=None,
                        help="EKS cluster name (for Application Signals dimensions)")
    parser.add_argument("--namespace", default=None,
                        help="K8s namespace (for Application Signals dimensions)")
    parser.add_argument("--hours", type=int, default=3,
                        help="Time window in hours (default: 3)")
    parser.add_argument("--alarm-prefix", default=None,
                        help="CloudWatch alarm name prefix (default: auto from project)")
    parser.add_argument("--profile", default=None,
                        help="AWS CLI profile name (e.g., member1-acc)")
    parser.add_argument("--config", default=None,
                        help="Path to config.yaml for defaults")
    parser.add_argument("--dry-run", action="store_true",
                        help="Collect and display but do not write")
    args = parser.parse_args()

    # Load knowledge.json
    with open(args.knowledge) as f:
        knowledge = json.load(f)

    output_path = args.output or args.knowledge

    # Resolve config defaults
    config = {}
    if args.config:
        config = load_config_yaml(args.config)

    region = args.region or knowledge.get("meta", {}).get("region") or config.get("region") or "us-east-1"
    cluster = args.cluster or config.get("cluster_name") or "devops-agent-test-cluster"
    namespace = args.namespace or config.get("namespace") or "dockercoins"
    alarm_prefix = args.alarm_prefix or config.get("alarm_prefix") or "devops-agent-test"

    # Identify K8s workload service IDs (candidates for Application Signals)
    k8s_svc_ids = [
        s["id"] for s in knowledge.get("services", [])
        if s.get("category") in ("k8s_workload",)
        and s.get("namespace") is not None
    ]

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  Runtime Observability Collection", file=sys.stderr)
    print(f"  Region: {region}, Cluster: {cluster}, NS: {namespace}", file=sys.stderr)
    print(f"  Window: {args.hours}h, Services: {len(k8s_svc_ids)}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    profile = args.profile

    # Step 1: Application Signals metrics
    print("[1/3] Collecting Application Signals metrics...", file=sys.stderr)
    service_metrics = collect_service_metrics(
        region, cluster, namespace, k8s_svc_ids, args.hours, profile=profile
    )
    print(f"  -> {len(service_metrics)} services with data\n", file=sys.stderr)

    # Step 2: X-Ray trace topology
    print("[2/3] Collecting X-Ray trace topology...", file=sys.stderr)
    flow_metrics, _ = collect_trace_topology(region, args.hours, profile=profile)
    print(f"  -> {len(flow_metrics)} edges discovered\n", file=sys.stderr)

    # Step 3: CloudWatch Alarms
    print("[3/3] Collecting CloudWatch Alarm states...", file=sys.stderr)
    alarm_states = collect_alarm_states(region, alarm_prefix, profile=profile)
    print(f"  -> {len(alarm_states)} alarms\n", file=sys.stderr)

    # Enrich knowledge.json
    print("Enriching knowledge.json...", file=sys.stderr)
    enriched = enrich_knowledge(knowledge, service_metrics, flow_metrics, alarm_states, args.hours)

    # Summary
    svc_enriched = sum(1 for s in enriched.get("services", []) if "observed" in s)
    flow_enriched = sum(1 for f in enriched.get("data_flows", []) if "observed" in f)
    shadow_count = len(enriched.get("observed_flows", []))
    alarm_count = len(enriched.get("alarms", []))

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  Enrichment Summary", file=sys.stderr)
    print(f"  Services with metrics: {svc_enriched}/{len(enriched.get('services', []))}", file=sys.stderr)
    print(f"  Flows with trace data: {flow_enriched}/{len(enriched.get('data_flows', []))}", file=sys.stderr)
    print(f"  Shadow flows discovered: {shadow_count}", file=sys.stderr)
    print(f"  Alarms: {alarm_count}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    if args.dry_run:
        print(json.dumps(enriched, indent=2, default=str))
        print("\n[DRY RUN] No files written.", file=sys.stderr)
    else:
        with open(output_path, "w") as f:
            json.dump(enriched, f, indent=2, ensure_ascii=False, default=str)
        print(f"Written to {output_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
