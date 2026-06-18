"""2-Phase scenario generation + execution: one FM at a time.

Usage:
    python3 _test_2phase_single.py FM-04
    python3 _test_2phase_single.py FM-07
    python3 _test_2phase_single.py --all

Phase 1: Generate scenario structure (intents only, no commands) via Agent chat
Phase 2: Same session — Agent queries live infra and fills concrete commands
Phase 3: Save to DynamoDB via /api/arch/save-scenario
Phase 4: Execute via /api/scenario-run/{id} and poll status
"""
import json
import re
import sys
import time
import requests

from chat_worker import init_worker

SPACE = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
BASE = "http://localhost:5003"

FAILURE_MODES = {
    "FM-01": {
        "name": "Network Isolation (hasher)",
        "description": "hasher 서비스에 NetworkPolicy로 네트워크 격리 유발, 요청 실패와 Agent 탐지 검증",
        "trigger_mechanism": "kubectl apply NetworkPolicy (deny ingress to hasher)",
        "target_service": "hasher",
        "category": "infrastructure",
        "layer": "network",
    },
    "FM-02": {
        "name": "EKS 노드 종료",
        "description": "FIS 또는 EC2 API로 EKS 워커 노드를 종료하여 Pod 재스케줄링과 서비스 복원 검증",
        "trigger_mechanism": "aws ec2 terminate-instances (EKS worker node)",
        "target_service": "hasher",
        "category": "infrastructure",
        "layer": "compute",
    },
    "FM-03": {
        "name": "Dependency Blackhole (hasher isolation)",
        "description": "hasher에 대한 모든 ingress/egress를 차단하여 의존성 장애 유도",
        "trigger_mechanism": "kubectl apply NetworkPolicy (deny all to/from hasher)",
        "target_service": "hasher",
        "category": "infrastructure",
        "layer": "network",
    },
    "FM-04": {
        "name": "EKS 노드 CPU 스트레스",
        "description": "FIS cpu-stress experiment으로 EKS 노드에 CPU 부하를 주입하여 throttling 유도",
        "trigger_mechanism": "FIS cpu-stress experiment template",
        "target_service": "hasher",
        "category": "infrastructure",
        "layer": "compute",
    },
    "FM-05": {
        "name": "ServiceAccount 권한 제거",
        "description": "EKS 서비스의 ServiceAccount를 제거/변경하여 권한 오류 유도",
        "trigger_mechanism": "kubectl patch serviceaccount",
        "target_service": "hasher",
        "category": "infrastructure",
        "layer": "security",
    },
    "FM-06": {
        "name": "CoreDNS 차단",
        "description": "CoreDNS에 대한 NetworkPolicy로 DNS 해석 실패 유도",
        "trigger_mechanism": "kubectl apply NetworkPolicy (deny egress to kube-dns)",
        "target_service": "hasher",
        "category": "infrastructure",
        "layer": "network",
    },
    "FM-07": {
        "name": "AZ 네트워크 장애",
        "description": "FIS 네트워크 지연/차단 실험으로 AZ 수준 장애 유도",
        "trigger_mechanism": "FIS network-disruption experiment template",
        "target_service": "hasher",
        "category": "infrastructure",
        "layer": "network",
    },
    "FM-08": {
        "name": "ConfigMap 변조",
        "description": "hasher 서비스의 ConfigMap을 변조하여 설정 오류 유도 후 Agent 탐지 검증",
        "trigger_mechanism": "kubectl create/patch configmap + restart pod",
        "target_service": "hasher",
        "category": "application",
        "layer": "config",
    },
    "FM-09": {
        "name": "잘못된 이미지 배포",
        "description": "존재하지 않는 컨테이너 이미지로 배포하여 ImagePullBackOff 유도",
        "trigger_mechanism": "kubectl set image deployment (invalid image tag)",
        "target_service": "hasher",
        "category": "application",
        "layer": "deployment",
    },
    "FM-10": {
        "name": "Endpoint Abuse (hasher)",
        "description": "hasher 서비스에 비정상 요청을 대량 전송하여 에러율 증가 유도",
        "trigger_mechanism": "kubectl run load generator pod",
        "target_service": "hasher",
        "category": "application",
        "layer": "application",
    },
    "FM-12": {
        "name": "Redis 성능 저하",
        "description": "Redis에 CPU/메모리 리소스 제한을 걸어 성능 저하와 연쇄 지연 유도",
        "trigger_mechanism": "kubectl patch deployment redis (resource limits)",
        "target_service": "redis",
        "category": "infrastructure",
        "layer": "data",
    },
    "FM-15": {
        "name": "Redis 재시작 (캐시 무효화)",
        "description": "Redis pod를 강제 삭제하여 캐시 손실 및 cold start 유도",
        "trigger_mechanism": "kubectl delete pod redis",
        "target_service": "redis",
        "category": "infrastructure",
        "layer": "data",
    },
    "FM-19": {
        "name": "관측성 사각지대",
        "description": "X-Ray 샘플링 0%로 설정 + hasher에 지연 주입하여, 트레이스 없이 장애 감지 검증",
        "trigger_mechanism": "aws xray update-sampling-rule (0% rate) + FIS latency injection",
        "target_service": "hasher",
        "category": "infrastructure",
        "layer": "observability",
    },
}


def extract_json(text):
    """Extract JSON from markdown code block."""
    m = re.search(r"```json\s*\n(.*?)```", text, re.DOTALL)
    if not m:
        return None
    raw = m.group(1)
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def generate_2phase(worker, fm_id, fm):
    """Generate scenario using 2-phase approach via Agent chat."""
    print(f"\n{'='*60}")
    print(f"  {fm_id}: {fm['name']}")
    print(f"{'='*60}")

    # Phase 1: Structure only (intents, no commands)
    phase1 = f"""다음 장애 모드에 대한 시나리오 구조만 만들어줘. 명령어는 비워두고, 각 단계의 의도와 설명만 작성해.

## 장애 모드
- ID: {fm_id}
- 이름: {fm['name']}
- 설명: {fm['description']}
- 트리거 메커니즘: {fm['trigger_mechanism']}

## 대상 인프라
- EKS 클러스터: devops-agent-test-cluster (us-east-1, account 111111111111)
- 네임스페이스: dockercoins
- 서비스: hasher, worker, rng, webui, redis
- 프로젝트명: devops-agent-test

## 출력 형식
```json 블록으로 응답. 형식:
{{
  "trigger": {{"intent": "설명", "command": ""}},
  "pre_cleanup": {{"intent": "이전 실행 잔재 정리", "command": ""}},
  "verification": {{
    "steps": [
      {{"intent": "검증 단계 설명", "type": "step_type", "name": "한국어 단계명", "command": ""}}
    ]
  }},
  "restore": {{"intent": "복원 설명", "command": ""}}
}}

step_type 옵션: alarm_state, kubectl_check, metric_check, investigation_event, fis_experiment, log_pattern, api_call
명령어(command)는 모두 빈 문자열로.
"""

    print("  Phase 1: 구조 생성...", flush=True)
    t0 = time.time()
    resp1 = worker.send_raw(space_id=SPACE, session_id="", prompt=phase1)
    session_id = resp1["session_id"]
    reply1 = resp1["reply"]
    structure = extract_json(reply1)
    if not structure:
        print(f"    ✗ Phase 1 JSON 추출 실패 ({time.time()-t0:.0f}s)")
        print(f"    {reply1[:400]}")
        return None

    v_steps = structure.get("verification", {}).get("steps", [])
    print(f"    ✓ 구조 생성 ({time.time()-t0:.0f}s): trigger + {len(v_steps)} verification + restore")

    # Phase 2: Fill commands with live infra lookup
    steps_desc = []
    if structure.get("pre_cleanup", {}).get("intent"):
        steps_desc.append(f"pre_cleanup: {structure['pre_cleanup']['intent']}")
    steps_desc.append(f"trigger: {structure.get('trigger',{}).get('intent','')}")
    for i, step in enumerate(v_steps):
        intent = step.get("intent", step.get("name", ""))
        stype = step.get("type", "unknown")
        steps_desc.append(f"verification[{i}] (type={stype}): {intent}")
    steps_desc.append(f"restore: {structure.get('restore',{}).get('intent','')}")

    phase2 = f"""위 시나리오의 각 단계에 대해 실제 실행 가능한 명령어를 만들어줘.

규칙:
- 반드시 실제 인프라를 조회해서 정확한 리소스 ID/이름/태그 확인 후 명령어 작성
- FIS template은 현재 존재하는 것만 사용 (aws fis list-experiment-templates로 확인)
- 알람 이름은 실제 존재하는 것만 (aws cloudwatch describe-alarms로 확인)
- kubectl 명령은 현재 클러스터에서 검증된 것만
- pre_cleanup은 멱등성 보장 (이미 없으면 무시: --ignore-not-found, || true 등)
- trigger 명령도 멱등성 고려 (기존 리소스 있으면 삭제 후 생성 또는 update 사용)
- 네임스페이스: dockercoins
- 클러스터 context: arn:aws:eks:us-east-1:111111111111:cluster/devops-agent-test-cluster

## 중요 제약조건
- alarm_state type: 반드시 실제 존재하는 알람의 정확한 이름을 alarm_name에 기재
- metric_check type: dimensions의 Name은 다음만 허용: ClusterName, Environment, Namespace, Operation, Service
  - RemoteService, RemoteOperation 등은 사용 금지!
- investigation_event type: DevOps Agent가 알람 기반으로 자동 조사를 시작/완료하는지 확인하는 타입

채워야 할 단계:
{chr(10).join(f"  - {s}" for s in steps_desc)}

```json 블록으로 응답:
{{
  "pre_cleanup": {{"command": "정리 명령 (없으면 빈 문자열)"}},
  "trigger": {{"command": "단일 bash 문자열"}},
  "verification_commands": [
    {{"command": "step0 명령", "expected": "기대값", "alarm_name": "알람타입이면 실제 알람명", "config": {{}}}}
  ],
  "restore": {{"command": "복원 명령"}}
}}

verification_commands 예시:
- alarm_state: {{"command": "", "expected": "ALARM", "alarm_name": "devops-agent-test-hasher-error-rate"}}
- metric_check: {{"command": "", "config": {{"namespace": "ApplicationSignals", "metric_name": "Latency", "dimensions": [{{"Name": "Service", "Value": "hasher"}}], "statistic": "Average", "period": 60, "threshold": 1000, "comparison": "gt"}}}}
- investigation_event: {{"command": "", "expected": "IN_PROGRESS"}} 또는 {{"expected": "COMPLETED"}}
- kubectl_check: {{"command": "kubectl get ...", "expected": "결과 패턴"}}
- fis_experiment: {{"command": "", "expected": "running"}}
"""

    print("  Phase 2: 인프라 조회 + 명령 생성...", flush=True)
    t1 = time.time()
    resp2 = worker.send_raw(space_id=SPACE, session_id=session_id, prompt=phase2)
    reply2 = resp2["reply"]
    commands = extract_json(reply2)
    if not commands:
        print(f"    ✗ Phase 2 JSON 추출 실패 ({time.time()-t1:.0f}s)")
        print(f"    {reply2[:600]}")
        return None

    trigger_cmd = commands.get("trigger", {}).get("command", "")
    restore_cmd = commands.get("restore", {}).get("command", "")
    pre_cleanup_cmd = commands.get("pre_cleanup", {}).get("command", "")
    v_cmds = commands.get("verification_commands", [])

    print(f"    ✓ 명령 생성 ({time.time()-t1:.0f}s)")
    print(f"      pre_cleanup: {pre_cleanup_cmd[:100]}")
    print(f"      trigger: {trigger_cmd[:100]}")
    print(f"      verify: {len(v_cmds)}개")
    print(f"      restore: {restore_cmd[:100]}")

    # Assemble final scenario with all required fields
    # Use English-safe ID based on fm description keywords
    id_suffix = fm.get("trigger_mechanism", fm["name"]).lower()
    id_suffix = re.sub(r'[^a-z0-9]+', '-', id_suffix).strip('-')[:40]
    scenario_id = f"{fm_id}-{id_suffix}"

    final = {
        "id": scenario_id,
        "source": "ai-generated",
        "skill_version": "2.1",
        "failure_mode_id": fm_id,
        "name": f"{fm['name']}",
        "target_service": fm["target_service"],
        "trigger_mode": "reactive",
        "category": fm["category"],
        "layer": fm["layer"],
        "purpose": fm["description"],
        "namespace": "dockercoins",
        "trigger": {"type": "aws_cli", "command": trigger_cmd},
        "restore": {"command": restore_cmd},
        "verification": {"steps": []},
    }

    if pre_cleanup_cmd:
        final["pre_cleanup"] = {"command": pre_cleanup_cmd, "reset_alarms": [], "wait_ok_timeout": 60}

    # Assemble verification steps
    for i, vc in enumerate(v_cmds):
        if not isinstance(vc, dict):
            continue

        step_type = "kubectl_check"
        step_name = f"Step {i}"
        if i < len(v_steps):
            step_type = v_steps[i].get("type", "kubectl_check")
            step_name = v_steps[i].get("name", v_steps[i].get("intent", f"Step {i}"))

        step = {
            "type": step_type,
            "name": step_name,
            "command": vc.get("command", ""),
            "timeout": 300,
            "poll_interval": 15,
        }

        # Type-specific fields from config
        config = vc.get("config", {})
        if step_type == "alarm_state":
            step["alarm_name"] = vc.get("alarm_name") or config.get("alarm_name", "")
            step["expected"] = vc.get("expected") or config.get("expected", "ALARM")
            step["timeout"] = 360
            step.pop("command", None)  # alarm_state doesn't use command
        elif step_type == "metric_check":
            step["namespace"] = config.get("namespace", "")
            step["metric_name"] = config.get("metric_name", "")
            dims = config.get("dimensions", [])
            # Filter out disallowed dimensions
            allowed_dims = {"Service", "Operation", "Environment", "Namespace", "ClusterName"}
            dims = [d for d in dims if isinstance(d, dict) and d.get("Name") in allowed_dims]
            step["dimensions"] = dims
            step["statistic"] = config.get("statistic", "Average")
            step["period"] = config.get("period", 60)
            step["threshold"] = config.get("threshold", 0)
            step["comparison"] = config.get("comparison", "gt")
            step.pop("command", None)
        elif step_type == "investigation_event":
            step["expected_status"] = vc.get("expected") or config.get("expected_status", "IN_PROGRESS")
            step["timeout"] = 420
            step["poll_interval"] = 20
            step.pop("command", None)
        elif step_type == "kubectl_check":
            step["expected"] = vc.get("expected", "")
        elif step_type == "fis_experiment":
            step["expected_status"] = vc.get("expected") or config.get("expected_status", "running")
            step.pop("command", None)
        elif step_type == "log_pattern":
            step["log_group"] = config.get("log_group", "")
            step["filter_pattern"] = config.get("filter_pattern", vc.get("expected", ""))
            step["minutes"] = config.get("minutes", 5)
            step.pop("command", None)

        final["verification"]["steps"].append(step)

    return final


def save_scenario(scenario):
    """Save scenario to DynamoDB via API."""
    sid = scenario["id"]
    r = requests.post(f"{BASE}/api/arch/save-scenario",
                      json={"scenario": scenario, "space_id": SPACE}, timeout=30)
    if r.status_code == 409:
        # Already exists — delete and retry
        requests.delete(f"{BASE}/api/scenarios/{sid}?space_id={SPACE}", timeout=10)
        r = requests.post(f"{BASE}/api/arch/save-scenario",
                          json={"scenario": scenario, "space_id": SPACE}, timeout=30)

    if r.status_code != 200:
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        errors = data.get("validation_errors", [])
        print(f"    ✗ 저장 실패 ({r.status_code}): {errors or r.text[:200]}")
        return False

    result = r.json()
    if result.get("fixes"):
        print(f"    ⚡ auto-fix: {result['fixes']}")
    if result.get("warnings"):
        for w in result["warnings"][:3]:
            print(f"    ⚠ {w[:80]}")
    return True


def run_scenario(scenario_id):
    """Execute scenario and poll for result."""
    print(f"  실행 시작: {scenario_id}", flush=True)
    r = requests.post(f"{BASE}/api/scenario-run/{scenario_id}",
                      params={"space_id": SPACE}, timeout=30)
    if r.status_code != 200 or not r.json().get("ok"):
        error = r.json().get("error", r.text[:100]) if r.status_code != 500 else r.text[:100]
        print(f"    ✗ 실행 시작 실패: {error}")
        return "start_failed"

    run_id = r.json()["run_id"]
    print(f"    run_id={run_id}", flush=True)

    deadline = time.time() + 720  # 12min max
    last_step = ""
    while time.time() < deadline:
        time.sleep(10)
        try:
            pr = requests.get(f"{BASE}/api/scenario-run/{run_id}/status",
                              params={"space_id": SPACE}, timeout=10)
            data = pr.json()
            status = data.get("status", "")

            current = next((s for s in data.get("steps", [])
                            if s.get("status") in ("running", "checking")), None)
            if current and current.get("name", "") != last_step:
                last_step = current.get("name", "")
                print(f"    → {last_step[:60]}", flush=True)

            if status in ("completed", "done", "fail", "error",
                          "interrupted", "cancelled", "preflight_failed"):
                result = data.get("result", "?")
                elapsed = time.time() - (deadline - 720)
                print(f"    ═══ {result.upper()} ({elapsed:.0f}s) ═══")
                for s in data.get("steps", []):
                    st = s.get("status", "")
                    if st == "pending":
                        continue
                    icon = "✓" if st == "pass" else "✗" if st == "fail" else "·"
                    print(f"      {icon} {st:10s} | {s.get('name','?')[:55]}")
                    if s.get("detail") and st != "pass":
                        print(f"                     → {s['detail'][:130]}")
                return result
        except Exception as e:
            continue

    print("    ═══ TIMEOUT ═══")
    return "timeout"


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 _test_2phase_single.py FM-04 [FM-07 ...]")
        print("       python3 _test_2phase_single.py --all")
        print(f"\nAvailable: {', '.join(sorted(FAILURE_MODES.keys()))}")
        sys.exit(1)

    if sys.argv[1] == "--all":
        targets = sorted(FAILURE_MODES.keys())
    else:
        targets = [arg.upper() for arg in sys.argv[1:]]
        invalid = [t for t in targets if t not in FAILURE_MODES]
        if invalid:
            print(f"Unknown FM IDs: {invalid}")
            sys.exit(1)

    worker = init_worker(profile="member1-acc", region="us-east-1")
    time.sleep(2)

    print(f"2-Phase 시나리오 생성 + 실행 ({len(targets)}개)")
    print("=" * 60)

    results = []
    for fm_id in targets:
        fm = FAILURE_MODES[fm_id]
        scenario = generate_2phase(worker, fm_id, fm)
        if not scenario:
            results.append({"fm": fm_id, "result": "generation_failed"})
            print(f"  ... 10s cooldown")
            time.sleep(10)
            continue

        # Debug: print generated JSON
        print(f"\n  Generated scenario JSON:")
        print(f"    id: {scenario['id']}")
        print(f"    trigger.command: {scenario['trigger']['command'][:100]}")
        print(f"    verification steps: {len(scenario['verification']['steps'])}")
        print(f"    restore.command: {scenario['restore']['command'][:100]}")

        if not save_scenario(scenario):
            results.append({"fm": fm_id, "result": "save_failed"})
            print(f"  ... 10s cooldown")
            time.sleep(10)
            continue

        result = run_scenario(scenario["id"])
        results.append({"fm": fm_id, "id": scenario["id"], "result": result})

        print(f"  ... 30s cooldown")
        time.sleep(30)

    # Summary
    print()
    print("=" * 60)
    print("최종 결과")
    print("=" * 60)
    passed = [r for r in results if r["result"] == "pass"]
    print(f"  {len(passed)}/{len(results)} pass")
    for r in results:
        icon = "✓" if r["result"] == "pass" else "✗"
        print(f"  {icon} {r['fm']}: {r['result']}")


if __name__ == "__main__":
    main()
