"""2-Phase scenario generation for previously failed FMs.
Phase 1: Generate scenario structure (intents only)
Phase 2: Agent fills commands with live infra lookup
Phase 3: Execute each scenario
"""
import json
import re
import time
import requests

from chat_worker import init_worker

SPACE = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
BASE = "http://localhost:5003"

worker = init_worker(profile="member1-acc", region="us-east-1")
time.sleep(2)

FAILED_SCENARIOS = [
    {
        "fm_id": "FM-02",
        "name": "EKS 노드 종료",
        "description": "FIS로 EKS 워커 노드를 종료하여 Pod 재스케줄링과 서비스 복원 검증",
        "trigger_mechanism": "FIS node-termination experiment",
    },
    {
        "fm_id": "FM-08",
        "name": "ConfigMap 변조",
        "description": "hasher 서비스의 ConfigMap을 변조하여 설정 오류 유도 후 Agent 탐지 검증",
        "trigger_mechanism": "kubectl patch/create configmap",
    },
    {
        "fm_id": "FM-12",
        "name": "Redis 성능 저하",
        "description": "Redis에 리소스 제한을 걸어 성능 저하 유도, 연쇄 지연 발생 검증",
        "trigger_mechanism": "kubectl patch deployment resource limits",
    },
    {
        "fm_id": "FM-19",
        "name": "관측성 사각지대",
        "description": "X-Ray 샘플링 규칙 변경으로 트레이스가 수집되지 않는 상황에서 장애 감지 검증",
        "trigger_mechanism": "AWS X-Ray sampling rule + FIS latency injection",
    },
]


def extract_json(text):
    m = re.search(r"```json\s*\n(.*?)```", text, re.DOTALL)
    if not m:
        return None
    raw = m.group(1)
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def generate_2phase(fm):
    """Generate scenario using 2-phase approach."""
    fm_id = fm["fm_id"]
    print(f"\n{'='*60}")
    print(f"  {fm_id}: {fm['name']}")
    print(f"{'='*60}")

    # Phase 1: Structure only
    phase1 = f"""다음 장애 모드에 대한 시나리오 구조만 만들어줘. 명령어는 비워두고, 각 단계의 의도와 설명만 작성해.

## 장애 모드
- ID: {fm_id}
- 이름: {fm['name']}
- 설명: {fm['description']}
- 트리거 메커니즘: {fm['trigger_mechanism']}

## 대상 인프라
- EKS 클러스터: devops-agent-test-cluster (primary), devops-agent-test-m2-cluster (secondary)
- 네임스페이스: dockercoins
- 서비스: hasher, worker, rng, webui, redis

## 출력
```json 블록으로 응답. 형식:
- trigger: {{"intent": "설명", "command": ""}}
- verification.steps: [{{"intent": "설명", "type": "step_type", "command": ""}}]
- restore: {{"intent": "설명", "command": ""}}

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
        print(f"    {reply1[:300]}")
        return None

    v_steps = structure.get("verification", {}).get("steps", [])
    print(f"    ✓ 구조 생성 ({time.time()-t0:.0f}s): trigger + {len(v_steps)} verification + restore")

    # Phase 2: Fill commands with live infra
    steps_desc = []
    steps_desc.append(f"trigger: {structure.get('trigger',{}).get('intent','')}")
    for i, step in enumerate(v_steps):
        intent = step.get("intent", step.get("name", ""))
        stype = step.get("type", "unknown")
        steps_desc.append(f"verification[{i}] (type={stype}): {intent}")
    steps_desc.append(f"restore: {structure.get('restore',{}).get('intent','')}")

    phase2 = f"""위 시나리오의 각 단계에 대해 실제 실행 가능한 명령어를 만들어줘.

규칙:
- 반드시 실제 인프라를 조회해서 정확한 리소스 ID/이름/태그 확인
- FIS template은 target filter가 현재 노드그룹/서비스와 일치하는 것만 사용
- 알람 이름은 실제 존재하는 것만 사용
- 존재하지 않는 리소스를 참조하지 마
- kubectl 명령은 현재 클러스터에서 검증된 것만

채워야 할 단계:
{chr(10).join(f"  - {s}" for s in steps_desc)}

```json 블록으로 응답:
{{
  "trigger": {{"command": "단일 bash 문자열"}},
  "verification_commands": [
    {{"command": "step0 명령", "expected": "기대값"}},
    ...
  ],
  "restore": {{"command": "복원 명령"}}
}}
"""

    print("  Phase 2: 인프라 조회 + 명령 생성...", flush=True)
    t1 = time.time()
    resp2 = worker.send_raw(space_id=SPACE, session_id=session_id, prompt=phase2)
    reply2 = resp2["reply"]
    commands = extract_json(reply2)
    if not commands:
        print(f"    ✗ Phase 2 JSON 추출 실패 ({time.time()-t1:.0f}s)")
        print(f"    {reply2[:500]}")
        return None

    trigger_cmd = commands.get("trigger", {}).get("command", "")
    restore_cmd = commands.get("restore", {}).get("command", "")
    v_cmds = commands.get("verification_commands", [])

    print(f"    ✓ 명령 생성 ({time.time()-t1:.0f}s)")
    print(f"      trigger: {trigger_cmd[:100]}")
    print(f"      verify: {len(v_cmds)}개")
    print(f"      restore: {restore_cmd[:100]}")

    # Assemble final scenario
    final = {
        "id": f"{fm_id}-2phase-test",
        "source": "ai-generated",
        "failure_mode_id": fm_id,
        "name": f"{fm['name']} (2-phase)",
        "target_service": "hasher",
        "trigger_mode": "reactive",
        "category": "infrastructure",
        "layer": "compute",
        "purpose": fm["description"],
        "trigger": {"type": "aws_cli", "command": trigger_cmd},
        "restore": {"command": restore_cmd},
        "verification": {"steps": []},
    }

    for i, vc in enumerate(v_cmds):
        step = {}
        if isinstance(vc, dict):
            step = {
                "type": v_steps[i].get("type", "kubectl_check") if i < len(v_steps) else "kubectl_check",
                "name": v_steps[i].get("intent", f"Step {i}") if i < len(v_steps) else f"Step {i}",
                "command": vc.get("command", ""),
                "expected": vc.get("expected", ""),
                "timeout": 300,
                "poll_interval": 15,
            }
        elif isinstance(vc, str):
            step = {
                "type": v_steps[i].get("type", "kubectl_check") if i < len(v_steps) else "kubectl_check",
                "name": v_steps[i].get("intent", f"Step {i}") if i < len(v_steps) else f"Step {i}",
                "command": vc,
                "timeout": 300,
                "poll_interval": 15,
            }
        final["verification"]["steps"].append(step)

    return final


def save_and_run(scenario):
    """Save scenario and execute it."""
    sid = scenario["id"]

    # Save to DynamoDB via API
    r = requests.post(f"{BASE}/api/arch/save-scenario",
                      json={"scenario": scenario, "space_id": SPACE}, timeout=30)
    if r.status_code != 200:
        print(f"    ✗ 저장 실패: {r.text[:100]}")
        return None

    print(f"  실행 시작: {sid}", flush=True)
    r = requests.post(f"{BASE}/api/scenario-run/{sid}",
                      params={"space_id": SPACE}, timeout=30)
    if r.status_code != 200 or not r.json().get("ok"):
        print(f"    ✗ 실행 시작 실패: {r.json().get('error', r.text[:100])}")
        return None

    run_id = r.json()["run_id"]
    print(f"    run_id={run_id}", flush=True)

    deadline = time.time() + 660
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
                print(f"    → {last_step[:55]}", flush=True)

            if status in ("completed", "done", "fail", "error",
                         "interrupted", "cancelled", "preflight_failed"):
                result = data.get("result", "?")
                print(f"    ═══ {result.upper()} ═══")
                for s in data.get("steps", []):
                    if s.get("status") != "pending":
                        icon = "✓" if s["status"] == "pass" else "✗" if s["status"] == "fail" else "·"
                        print(f"      {icon} {s['status']:10s} | {s.get('name','?')[:50]}")
                        if s.get("detail") and s["status"] != "pass":
                            print(f"                     → {s['detail'][:120]}")
                return result
        except Exception:
            continue

    print("    ═══ TIMEOUT ═══")
    return "timeout"


# ====== Main ======
print("2-Phase 시나리오 생성 + 실행 테스트")
print("=" * 60)

results = []
for fm in FAILED_SCENARIOS:
    scenario = generate_2phase(fm)
    if scenario:
        result = save_and_run(scenario)
        results.append({"fm": fm["fm_id"], "result": result})
    else:
        results.append({"fm": fm["fm_id"], "result": "generation_failed"})

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
