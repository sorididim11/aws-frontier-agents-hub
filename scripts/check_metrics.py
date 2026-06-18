#!/usr/bin/env python3
"""Check Application Signals Error/Fault metrics for hasher"""
import os
import subprocess, json
from datetime import datetime, timedelta, timezone

AWS_PROFILE = os.environ.get("AWS_PROFILE", "member1-acc")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

def get_metric(metric_name, dims, hours=3):
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)
    dim_args = []
    for k, v in dims.items():
        dim_args.extend(["Name=" + k + ",Value=" + v])
    cmd = [
        "aws", "cloudwatch", "get-metric-statistics",
        "--namespace", "ApplicationSignals",
        "--metric-name", metric_name,
        "--dimensions"] + dim_args + [
        "--start-time", start.strftime("%Y-%m-%dT%H:%M:%S"),
        "--end-time", now.strftime("%Y-%m-%dT%H:%M:%S"),
        "--period", "300",
        "--statistics", "Sum",
        "--profile", AWS_PROFILE, "--region", AWS_REGION, "--no-cli-pager"
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(r.stdout)
    pts = sorted(data.get("Datapoints", []), key=lambda x: x["Timestamp"])
    return pts

print("=" * 60)
print("Application Signals Error/Fault Metric Check")
print("=" * 60)

# Check hasher with eks:devops-agent-test-cluster/dockercoins
for metric in ["Error", "Fault", "Latency"]:
    print(f"\n--- hasher {metric} (eks:devops-agent-test-cluster/dockercoins) ---")
    pts = get_metric(metric, {"Service": "hasher", "Environment": "eks:devops-agent-test-cluster/dockercoins"})
    nonzero = [p for p in pts if p["Sum"] > 0]
    print(f"  Total datapoints: {len(pts)}, Non-zero: {len(nonzero)}")
    for p in nonzero[-5:]:
        print(f"  {p['Timestamp']}: Sum={p['Sum']}")
    if not nonzero:
        print("  ALL ZERO")

# Check hasher with eks:default (old Ruby dimension)
for metric in ["Error", "Fault"]:
    print(f"\n--- hasher {metric} (eks:default) ---")
    pts = get_metric(metric, {"Service": "hasher", "Environment": "eks:default"})
    nonzero = [p for p in pts if p["Sum"] > 0]
    print(f"  Total datapoints: {len(pts)}, Non-zero: {len(nonzero)}")
    for p in nonzero[-5:]:
        print(f"  {p['Timestamp']}: Sum={p['Sum']}")
    if not nonzero:
        print("  ALL ZERO")

# Check Fault metric existence
print(f"\n--- Checking Fault metric dimensions ---")
cmd = ["aws", "cloudwatch", "list-metrics", "--namespace", "ApplicationSignals",
       "--metric-name", "Fault", "--profile", AWS_PROFILE, "--region", AWS_REGION, "--no-cli-pager"]
r = subprocess.run(cmd, capture_output=True, text=True)
data = json.loads(r.stdout)
for m in data.get("Metrics", []):
    dims = {d["Name"]: d["Value"] for d in m["Dimensions"]}
    if dims.get("Service") == "hasher":
        print(f"  hasher Fault: {dims}")
