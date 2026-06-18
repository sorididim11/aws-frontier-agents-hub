#!/usr/bin/env python3
"""Independent test for Agent Space skills: arch-discover and scenario-generate.

Tests that the Agent responds with correct format when given only skill triggers
+ dynamic data (without the full format spec/rules that are now in the skill).

Compares: existing full prompt (baseline) vs skill trigger (optimized).

Usage:
    python skills/test_skills.py                    # run all tests
    python skills/test_skills.py arch-q1            # test Q1 only
    python skills/test_skills.py arch-q2            # test Q2 only
    python skills/test_skills.py scenario-generate  # test scenario only
"""
import json
import re
import sys
import time
import os

import boto3
from botocore.config import Config

SPACE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PROFILE = "member1-acc"
REGION = "us-east-1"

# --- boto3 Agent helpers ---------------------------------------------------

def _client():
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    for svc_name in ("devops-agent", "devopsagent"):
        try:
            return session.client(
                svc_name,
                config=Config(read_timeout=300, connect_timeout=10),
            )
        except Exception:
            continue
    raise RuntimeError("Cannot create devops-agent client — check boto3 version")


def _create_session(client):
    resp = client.create_chat(agentSpaceId=SPACE_ID, userId="skill-test")
    exec_id = resp["executionId"]
    print(f"  session: {exec_id[:24]}...")
    return exec_id


def _send(client, session_id, prompt):
    t0 = time.time()
    print(f"  sending {len(prompt)} chars...")
    resp = client.send_message(
        agentSpaceId=SPACE_ID,
        executionId=session_id,
        content=prompt,
        userId="skill-test",
    )
    text = _parse_text(resp)
    elapsed = time.time() - t0
    print(f"  response: {len(text)} chars in {elapsed:.1f}s")
    return text


def _parse_text(resp):
    """Extract final text from event stream."""
    blocks = {}
    for event in resp.get("events", []):
        if not isinstance(event, dict):
            continue
        for etype, edata in event.items():
            if etype == "contentBlockStart":
                idx = edata.get("index", 0)
                blocks[idx] = {"type": edata.get("type", "unknown"), "text": ""}
            elif etype == "contentBlockDelta":
                idx = edata.get("index", 0)
                text = edata.get("delta", {}).get("textDelta", {}).get("text", "")
                if idx in blocks:
                    blocks[idx]["text"] += text
    for idx in sorted(blocks.keys(), reverse=True):
        b = blocks[idx]
        if b["type"] in ("final_response", "text") and b["text"]:
            return b["text"]
    return ""


def _extract_json(text):
    """Extract first JSON block from markdown code fence."""
    m = re.search(r"```json\s*\n(.*?)```", text, re.DOTALL)
    if not m:
        m = re.search(r"```\s*\n(\{.*?\})\s*\n```", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        print(f"  JSON parse error: {e}")
        return None


def _save_result(test_name, text, data, errors):
    """Save response to results/ for post-analysis."""
    results_dir = os.path.join(os.path.dirname(__file__), "test_results")
    os.makedirs(results_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    base = os.path.join(results_dir, f"{ts}_{test_name}")
    with open(f"{base}_response.md", "w") as f:
        f.write(text)
    if data:
        with open(f"{base}_parsed.json", "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    if errors:
        with open(f"{base}_errors.txt", "w") as f:
            f.write("\n".join(errors))
    print(f"  saved: {base}_*")


# --- Dynamic data (simulates what the app would provide) --------------------

TAGGED_RESOURCES = """- EKS Cluster: devops-simulator (us-east-1)
  - Namespace: dockercoins
    - Deployments: hasher, rng, webui, worker, redis
  - Namespace: alarm-pipeline
    - Deployments: alarm-receiver
- CloudWatch Alarms: dockercoins-high-latency, alarm-pipeline-errors
- SNS Topics: devops-agent-alerts
- Lambda Functions: alarm-to-agent
- DynamoDB Tables: devops-investigations, devops-scenarios"""


# --- Skill trigger prompts (optimized — no format spec, just trigger + data) -

Q1_TRIGGER = f"""#arch-q1

## 알려진 AWS 리소스
{TAGGED_RESOURCES}
"""

Q2_TRIGGER = """#arch-q2 1 DockerCoins

## 알려진 서비스
- EKS Cluster: devops-simulator (us-east-1)
  - Namespace: dockercoins
    - hasher: Deployment, port 8080, hash 생성 서비스
    - rng: Deployment, port 8080, 랜덤 바이트 생성
    - webui: Deployment, port 8080, 웹 UI
    - worker: Deployment, 주기적으로 rng+hasher 호출
    - redis: Deployment, port 6379, 카운터 저장
"""

K8S_DETAIL_TRIGGER = """#k8s-detail DockerCoins

## 알려진 서비스
- EKS 클러스터: devops-simulator
- 네임스페이스: dockercoins
  - hasher: Deployment, port 8080
  - rng: Deployment, port 8080
  - webui: Deployment, port 8080
  - worker: Deployment
  - redis: Deployment, port 6379
"""

SCENARIO_TRIGGER = """#scenario-generate I08-hasher-network-latency

## 장애 모드 정보
- ID: I08-hasher-network-latency
- 이름: Hasher 네트워크 지연
- 카테고리: infrastructure
- 레이어: network
- 설명: hasher 서비스로 향하는 네트워크에 지연을 주입하여 서비스 간 통신 장애 시뮬레이션

## 대상 앱: DockerCoins
- 서비스: hasher, rng, webui, worker, redis
- EKS 클러스터: devops-simulator
- 네임스페이스: dockercoins

## 가용 CloudWatch 알람
- dockercoins-high-latency
- alarm-pipeline-errors

## 가용 FIS 실험 템플릿
- (없음)

## 기존 시나리오
- (없음)
"""


# --- Validators -------------------------------------------------------------

def validate_q1(data):
    errors = []
    for f in ("system_name", "description", "apps"):
        if f not in data:
            errors.append(f"missing top-level field: {f}")
    if "apps" in data:
        for i, app in enumerate(data["apps"]):
            for af in ("id", "name", "description"):
                if af not in app:
                    errors.append(f"apps[{i}] missing: {af}")
    if "app_edges" in data:
        for i, e in enumerate(data["app_edges"]):
            for ef in ("source", "target"):
                if ef not in e:
                    errors.append(f"app_edges[{i}] missing: {ef}")
        # check no bidirectional
        seen = set()
        for e in data["app_edges"]:
            pair = (e.get("source"), e.get("target"))
            reverse = (pair[1], pair[0])
            if reverse in seen:
                errors.append(f"bidirectional edge: {pair[0]} ↔ {pair[1]}")
            seen.add(pair)
    return errors


def validate_q2(data):
    errors = []
    for f in ("app_name", "nodes", "edges"):
        if f not in data:
            errors.append(f"missing top-level field: {f}")
    node_names = set()
    if "nodes" in data:
        for i, n in enumerate(data["nodes"]):
            for nf in ("name", "kind", "service_type", "group"):
                if nf not in n:
                    errors.append(f"nodes[{i}] missing: {nf}")
            if "name" in n:
                node_names.add(n["name"])
            # namespace check
            if "namespace" not in n:
                errors.append(f"nodes[{i}] missing: namespace")
            # labels.role check
            if "labels" not in n or "role" not in n.get("labels", {}):
                errors.append(f"nodes[{i}] missing: labels.role")
    if "edges" in data:
        for i, e in enumerate(data["edges"]):
            for ef in ("source", "target", "description"):
                if ef not in e:
                    errors.append(f"edges[{i}] missing: {ef}")
            if e.get("source") and e["source"] not in node_names:
                errors.append(f"edges[{i}] source '{e['source']}' not in nodes")
            if e.get("target") and e["target"] not in node_names:
                errors.append(f"edges[{i}] target '{e['target']}' not in nodes")
        # bidirectional check
        seen = set()
        for e in data["edges"]:
            pair = (e.get("source"), e.get("target"))
            reverse = (pair[1], pair[0])
            if reverse in seen:
                errors.append(f"bidirectional edge: {pair[0]} ↔ {pair[1]}")
            seen.add(pair)
    # orphan node check
    if "edges" in data and "nodes" in data:
        connected = set()
        for e in data["edges"]:
            connected.add(e.get("source"))
            connected.add(e.get("target"))
        for n in data["nodes"]:
            if n.get("name") not in connected:
                errors.append(f"orphan node: {n.get('name')}")
    if "workflows" in data:
        for i, w in enumerate(data["workflows"]):
            for h in w.get("hops", []):
                if h.get("from") and h["from"] not in node_names:
                    errors.append(f"workflows[{i}] hop from '{h['from']}' not in nodes")
                if h.get("to") and h["to"] not in node_names:
                    errors.append(f"workflows[{i}] hop to '{h['to']}' not in nodes")
    return errors


def validate_k8s_detail(data):
    errors = []
    if "app_name" not in data:
        errors.append("missing top-level field: app_name")
    for f in ("namespaces", "workloads"):
        if f not in data:
            errors.append(f"missing top-level field: {f}")
    if "workloads" in data:
        for i, w in enumerate(data["workloads"]):
            for wf in ("name", "kind", "namespace"):
                if wf not in w:
                    errors.append(f"workloads[{i}] missing: {wf}")
            kind = w.get("kind", "")
            valid_kinds = ("Deployment", "StatefulSet", "DaemonSet", "CronJob", "Job")
            if kind and kind not in valid_kinds:
                errors.append(f"workloads[{i}] invalid kind: {kind}")
            if "containers" in w:
                for j, c in enumerate(w["containers"]):
                    if "name" not in c:
                        errors.append(f"workloads[{i}].containers[{j}] missing: name")
    for section in ("service_accounts", "secrets", "configmaps",
                     "persistent_volume_claims", "network_policies", "ingresses"):
        if section in data and not isinstance(data[section], list):
            errors.append(f"{section} should be list")
    return errors


def validate_scenario(data):
    errors = []
    for f in ("id", "source", "failure_mode_id", "trigger_mode", "name",
              "category", "purpose", "architecture", "trigger", "restore",
              "verification", "evaluation_rubric"):
        if f not in data:
            errors.append(f"missing field: {f}")
    if data.get("source") != "ai-generated":
        errors.append(f"source should be 'ai-generated', got '{data.get('source')}'")
    if "trigger" in data:
        t = data["trigger"]
        if "type" not in t:
            errors.append("trigger missing 'type'")
        elif t["type"] not in ("aws_cli", "fis", "kubectl"):
            errors.append(f"trigger.type invalid: {t['type']}")
        if "command" not in t:
            errors.append("trigger missing 'command'")
        elif isinstance(t["command"], list):
            errors.append("trigger.command must be string, not list")
    if "verification" in data:
        steps = data["verification"].get("steps", [])
        if len(steps) < 3:
            errors.append(f"verification needs ≥3 steps, got {len(steps)}")
    if "evaluation_rubric" in data:
        total = sum(r.get("weight", 0) for r in data["evaluation_rubric"])
        if total != 100:
            errors.append(f"rubric weight sum={total}, expected 100")
    # normal_flow / fault_flow
    for flow in ("normal_flow", "fault_flow"):
        if flow not in data:
            errors.append(f"missing field: {flow}")
    # pre_cleanup
    if "pre_cleanup" not in data:
        errors.append("missing field: pre_cleanup")
    return errors


# --- Test runners -----------------------------------------------------------

def test_arch_q1(client):
    print("\n=== Test: arch-discover Q1 (skill trigger) ===")
    session_id = _create_session(client)
    text = _send(client, session_id, Q1_TRIGGER)

    data = _extract_json(text)
    errors = validate_q1(data) if data else ["no JSON found in response"]
    _save_result("arch-q1", text, data, errors)

    if errors:
        print("FAIL:")
        for e in errors:
            print(f"  - {e}")
        return False

    apps = [a.get("name") for a in data.get("apps", [])]
    print(f"PASS: {len(apps)} apps: {', '.join(apps)}")
    return True


def test_arch_q2(client):
    print("\n=== Test: arch-discover Q2 (skill trigger) ===")
    session_id = _create_session(client)
    text = _send(client, session_id, Q2_TRIGGER)

    data = _extract_json(text)
    errors = validate_q2(data) if data else ["no JSON found in response"]
    _save_result("arch-q2", text, data, errors)

    if errors:
        print("FAIL:")
        for e in errors:
            print(f"  - {e}")
        return False

    nodes = [n.get("name") for n in data.get("nodes", [])]
    print(f"PASS: {len(nodes)} nodes: {', '.join(nodes)}")
    return True


def test_k8s_detail(client):
    print("\n=== Test: k8s-detail (skill trigger) ===")
    session_id = _create_session(client)
    text = _send(client, session_id, K8S_DETAIL_TRIGGER)

    data = _extract_json(text)
    errors = validate_k8s_detail(data) if data else ["no JSON found in response"]
    _save_result("k8s-detail", text, data, errors)

    if errors:
        print("FAIL:")
        for e in errors:
            print(f"  - {e}")
        return False

    workloads = [w.get("name") for w in data.get("workloads", [])]
    print(f"PASS: {len(workloads)} workloads: {', '.join(workloads)}")
    return True


def test_scenario_generate(client):
    print("\n=== Test: scenario-generate (skill trigger) ===")
    session_id = _create_session(client)
    text = _send(client, session_id, SCENARIO_TRIGGER)

    data = _extract_json(text)
    errors = validate_scenario(data) if data else ["no JSON found in response"]
    _save_result("scenario-generate", text, data, errors)

    if errors:
        print("FAIL:")
        for e in errors:
            print(f"  - {e}")
        return False

    print(f"PASS: scenario '{data.get('name')}' / "
          f"{len(data.get('verification', {}).get('steps', []))} steps / "
          f"trigger.type={data.get('trigger', {}).get('type')}")
    return True


# --- Main -------------------------------------------------------------------

TESTS = {
    "arch-q1": test_arch_q1,
    "arch-q2": test_arch_q2,
    "k8s-detail": test_k8s_detail,
    "scenario-generate": test_scenario_generate,
}


def main():
    targets = sys.argv[1:] or list(TESTS.keys())
    invalid = [t for t in targets if t not in TESTS]
    if invalid:
        print(f"Unknown test(s): {', '.join(invalid)}")
        print(f"Available: {', '.join(TESTS.keys())}")
        sys.exit(1)

    print(f"Space: {SPACE_ID}")
    print(f"Profile: {PROFILE}, Region: {REGION}")

    client = _client()
    results = {}
    for name in targets:
        results[name] = TESTS[name](client)

    print("\n=== Summary ===")
    for name, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  {name}: {status}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
