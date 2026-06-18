"""Fixed-structure scenario generation + execution.

우리가 구조를 정의하고, Agent에게 명령어/알람명만 채우라고 요청.
Phase 1 불필요 — 표준 5-step 구조 고정:
  1. trigger 효과 확인 (kubectl_check 또는 fis_experiment)
  2. 알람 ALARM 전환 확인 (alarm_state)
  3. Agent 조사 시작 (investigation_event IN_PROGRESS)
  4. Agent 조사 완료 (investigation_event COMPLETED)

Usage:
    python3 _test_fixed_structure.py FM-07
    python3 _test_fixed_structure.py FM-01 FM-04
    python3 _test_fixed_structure.py --all
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
        "description": "hasher 서비스에 NetworkPolicy로 네트워크 격리 유발",
        "trigger_mechanism": "kubectl apply NetworkPolicy (deny ingress to hasher)",
        "target_service": "hasher",
        "category": "infrastructure",
        "layer": "network",
        "effect_description": "worker→hasher 요청이 timeout됨 → hasher의 Latency 메트릭 급증 → Latency 기반 알람이 발동",
    },
    "FM-03": {
        "name": "Dependency Blackhole (hasher)",
        "description": "hasher에 대한 모든 ingress를 차단하여 의존성 장애 유도",
        "trigger_mechanism": "kubectl apply NetworkPolicy (deny all ingress to hasher)",
        "target_service": "hasher",
        "category": "infrastructure",
        "layer": "network",
        "effect_description": "worker→hasher 요청이 timeout됨 → hasher의 Latency 메트릭 급증 → Latency 기반 알람이 발동",
    },
    "FM-04": {
        "name": "EKS Node CPU Stress",
        "description": "FIS cpu-stress experiment으로 EKS 노드에 CPU 부하 주입",
        "trigger_mechanism": "FIS cpu-stress experiment template",
        "target_service": "hasher",
        "category": "infrastructure",
        "layer": "compute",
        "effect_description": "노드 CPU 포화 → hasher 컨테이너 throttling → hasher Latency 메트릭 급증 → Latency 기반 알람 발동",
    },
    "FM-06": {
        "name": "CoreDNS 차단",
        "description": "hasher에서 DNS 조회를 차단하여 서비스 간 통신 실패 유도",
        "trigger_mechanism": "kubectl apply NetworkPolicy (deny egress to kube-dns from hasher)",
        "target_service": "hasher",
        "category": "infrastructure",
        "layer": "network",
        "effect_description": "hasher의 DNS 해석 실패 → 외부 의존 호출 실패 → hasher Fault 메트릭 증가 → Fault 기반 알람 발동",
    },
    "FM-07": {
        "name": "AZ 네트워크 장애",
        "description": "FIS 네트워크 지연 실험으로 AZ 수준 네트워크 장애 유도",
        "trigger_mechanism": "FIS network-disruption experiment template",
        "target_service": "hasher",
        "category": "infrastructure",
        "layer": "network",
        "effect_description": "Pod 네트워크에 지연 주입 → hasher 응답 시간 증가 → hasher Latency 메트릭 급증 → Latency 기반 알람 발동",
    },
    "FM-08": {
        "name": "ConfigMap 변조",
        "description": "hasher에 잘못된 환경변수를 주입하여 에러 유도",
        "trigger_mechanism": "kubectl create configmap (잘못된 값) + patch deployment (envFrom 추가) → pod restart",
        "target_service": "hasher",
        "category": "application",
        "layer": "config",
        "effect_description": "hasher가 잘못된 설정으로 시작 → 요청 처리 시 에러 반환 → hasher Fault 메트릭 증가 → Fault 기반 알람 발동",
    },
    "FM-09": {
        "name": "잘못된 이미지 배포",
        "description": "존재하지 않는 컨테이너 이미지로 배포하여 ImagePullBackOff 유도",
        "trigger_mechanism": "kubectl set image deployment (invalid image tag)",
        "target_service": "hasher",
        "category": "application",
        "layer": "deployment",
        "effect_description": "pod ImagePullBackOff → hasher 인스턴스 없음 → 요청 실패 → hasher Fault 메트릭 증가 → Fault 기반 알람 발동",
    },
    "FM-12": {
        "name": "Redis 성능 저하",
        "description": "Redis에 CPU/메모리 리소스 제한을 걸어 성능 저하 유도",
        "trigger_mechanism": "kubectl patch deployment redis (resource limits: cpu=10m, memory=32Mi)",
        "target_service": "redis",
        "category": "infrastructure",
        "layer": "data",
        "effect_description": "Redis 처리 지연 → worker가 Redis 대기 → worker→hasher 전체 체인 Latency 증가 → hasher Latency 기반 알람 발동",
    },
    "FM-15": {
        "name": "Redis 재시작 (캐시 무효화)",
        "description": "Redis pod를 강제 삭제하여 캐시 손실 및 cold start 유도",
        "trigger_mechanism": "kubectl delete pod (redis)",
        "target_service": "redis",
        "category": "infrastructure",
        "layer": "data",
        "effect_description": "Redis 재시작 → worker→Redis 연결 순단 → worker 요청 지연/실패 → hasher Latency 메트릭 급증 → Latency 기반 알람 발동",
    },
}


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


def generate_scenario(worker, fm_id, fm):
    """Send single prompt: fixed structure + ask Agent to fill commands."""
    print(f"\n{'='*60}")
    print(f"  {fm_id}: {fm['name']}")
    print(f"{'='*60}")

    prompt = f"""다음 장애 시나리오의 각 단계에 대해 실제 실행 가능한 명령어를 만들어줘.
반드시 실제 인프라를 조회해서 정확한 리소스 확인 후 작성해.

## 장애 모드
- ID: {fm_id}
- 이름: {fm['name']}
- 설명: {fm['description']}
- 트리거: {fm['trigger_mechanism']}

## 대상 인프라
- EKS 클러스터: devops-agent-test-cluster (us-east-1, 111111111111)
- 네임스페이스: dockercoins
- 대상 서비스: {fm['target_service']}
- kubectl context: arn:aws:eks:us-east-1:111111111111:cluster/devops-agent-test-cluster

## 채워야 할 항목 (반드시 실제 인프라 조회 후 작성)

1. **pre_cleanup**: 이전 실행 잔재 정리 명령 (멱등성 보장: --ignore-not-found, || true)
2. **trigger**: 장애 주입 명령 (단일 bash 문자열)
3. **trigger_check**: trigger 효과가 적용되었는지 확인하는 kubectl 명령 + 기대값
4. **alarm_name**: 이 장애로 인해 ALARM 상태가 될 CloudWatch 알람의 **정확한 이름** (aws cloudwatch describe-alarms로 확인)
5. **restore**: 원상복구 명령

## alarm_name 선택 방법 (중요)
1. 먼저 이 trigger가 실행되면 어떤 메트릭이 변하는지 추론해:
   - 서비스 접근 차단 → 호출자에서 timeout → **Latency** 급증
   - 서비스 에러 유발 → 직접 에러 반환 → **Fault** 증가
   - 리소스 부족 → 처리 지연 → **Latency** 급증
2. 그 메트릭이 연결된 알람을 `aws cloudwatch describe-alarms`에서 찾아
3. 알람의 MetricName과 Dimensions가 trigger 효과와 인과적으로 일치하는지 확인

## 제약조건
- alarm_name은 반드시 `aws cloudwatch describe-alarms` 결과에서 실제 존재하는 것만 사용
- kubectl 명령에는 --context 불필요 (자동 주입됨)
- 네임스페이스는 -n dockercoins 명시
- trigger는 fire-and-forget: 120초 내에 종료되는 단일 명령만. rollout status 대기, watch, sleep 포함 금지
- trigger의 효과가 나타나는 건 verification이 확인하므로, trigger에서 결과를 기다리지 마

```json 블록으로 응답:
{{
  "pre_cleanup": "정리 명령",
  "trigger": "장애 주입 명령",
  "trigger_check": {{"command": "kubectl ...", "expected": "기대 패턴"}},
  "alarm_name": "실제 알람 이름",
  "restore": "복원 명령"
}}
"""

    print("  Agent에 명령 생성 요청...", flush=True)
    t0 = time.time()
    try:
        resp = worker.send_raw(space_id=SPACE, session_id="", prompt=prompt)
    except Exception as e:
        print(f"    ✗ Agent 호출 실패 ({time.time()-t0:.0f}s): {type(e).__name__}")
        return None
    reply = resp["reply"]
    result = extract_json(reply)

    if not result:
        print(f"    ✗ JSON 추출 실패 ({time.time()-t0:.0f}s)")
        print(f"    {reply[:500]}")
        return None

    elapsed = time.time() - t0
    print(f"    ✓ 명령 생성 완료 ({elapsed:.0f}s)")
    print(f"      trigger: {result.get('trigger','')[:90]}")
    print(f"      alarm: {result.get('alarm_name','')}")
    print(f"      restore: {result.get('restore','')[:90]}")

    # Assemble scenario with fixed structure
    id_suffix = re.sub(r'[^a-z0-9]+', '-', fm['name'].lower()).strip('-')[:40]
    scenario_id = f"{fm_id}-{id_suffix}"

    trigger_check = result.get("trigger_check", {})
    if isinstance(trigger_check, str):
        trigger_check = {"command": trigger_check, "expected": ""}

    scenario = {
        "id": scenario_id,
        "source": "ai-generated",
        "skill_version": "2.1",
        "failure_mode_id": fm_id,
        "name": fm["name"],
        "target_service": fm["target_service"],
        "trigger_mode": "reactive",
        "category": fm["category"],
        "layer": fm["layer"],
        "purpose": fm["description"],
        "namespace": "dockercoins",
        "trigger": {"type": "kubectl" if "kubectl" in result.get("trigger", "") else "fis",
                    "command": result.get("trigger", "")},
        "restore": {"command": result.get("restore", "")},
        "verification": {
            "steps": [
                {
                    "type": "kubectl_check",
                    "name": "Trigger 효과 확인",
                    "command": trigger_check.get("command", ""),
                    "expected": trigger_check.get("expected", ""),
                    "timeout": 120,
                    "poll_interval": 10,
                },
                {
                    "type": "alarm_state",
                    "name": f"{fm['target_service']} 알람 ALARM 전환 확인",
                    "alarm_name": result.get("alarm_name", ""),
                    "expected": "ALARM",
                    "timeout": 360,
                    "poll_interval": 15,
                },
                {
                    "type": "investigation_event",
                    "name": "Agent 조사 시작 확인",
                    "expected_status": "IN_PROGRESS",
                    "timeout": 420,
                    "poll_interval": 20,
                },
                {
                    "type": "investigation_event",
                    "name": "Agent 조사 완료 확인",
                    "expected_status": "COMPLETED",
                    "timeout": 420,
                    "poll_interval": 20,
                },
            ]
        },
    }

    if result.get("pre_cleanup"):
        scenario["pre_cleanup"] = {
            "command": result["pre_cleanup"],
            "reset_alarms": [result.get("alarm_name", "")],
            "wait_ok_timeout": 60,
        }

    return scenario


def save_scenario(scenario):
    sid = scenario["id"]
    # Delete if exists
    requests.delete(f"{BASE}/api/scenarios/{sid}?space_id={SPACE}", timeout=10)

    r = requests.post(f"{BASE}/api/arch/save-scenario",
                      json={"scenario": scenario, "space_id": SPACE}, timeout=30)
    if r.status_code != 200:
        data = r.json() if "json" in r.headers.get("content-type", "") else {}
        errors = data.get("validation_errors", [])
        print(f"    ✗ 저장 실패 ({r.status_code}): {errors or r.text[:200]}")
        return False

    result = r.json()
    if result.get("fixes"):
        print(f"    ⚡ auto-fix: {result['fixes']}")
    return True


def run_scenario(scenario_id):
    print(f"  실행: {scenario_id}", flush=True)
    r = requests.post(f"{BASE}/api/scenario-run/{scenario_id}",
                      params={"space_id": SPACE}, timeout=30)
    if r.status_code != 200 or not r.json().get("ok"):
        error = r.json().get("error", r.text[:100]) if r.status_code != 500 else r.text[:100]
        print(f"    ✗ 실행 실패: {error}")
        return "start_failed"

    run_id = r.json()["run_id"]
    print(f"    run_id={run_id}", flush=True)

    deadline = time.time() + 960
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
                print(f"    → {last_step}", flush=True)

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
                    print(f"      {icon} {st:10s} | {s.get('name','?')}")
                    if s.get("detail") and st != "pass":
                        print(f"                     → {s['detail'][:140]}")
                return result
        except Exception:
            continue

    print("    ═══ TIMEOUT ═══")
    return "timeout"


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 _test_fixed_structure.py FM-07 [FM-01 ...]")
        print("       python3 _test_fixed_structure.py --all")
        print(f"\nAvailable: {', '.join(sorted(FAILURE_MODES.keys()))}")
        sys.exit(1)

    if sys.argv[1] == "--all":
        targets = sorted(FAILURE_MODES.keys())
    else:
        targets = [arg.upper() for arg in sys.argv[1:]]
        invalid = [t for t in targets if t not in FAILURE_MODES]
        if invalid:
            print(f"Unknown: {invalid}")
            sys.exit(1)

    worker = init_worker(profile="member1-acc", region="us-east-1")
    time.sleep(2)

    print(f"고정 구조 시나리오 생성 + 실행 ({len(targets)}개)")
    print("=" * 60)

    results = []
    for fm_id in targets:
        fm = FAILURE_MODES[fm_id]
        scenario = generate_scenario(worker, fm_id, fm)
        if not scenario:
            results.append({"fm": fm_id, "result": "generation_failed"})
            time.sleep(10)
            continue

        if not save_scenario(scenario):
            results.append({"fm": fm_id, "result": "save_failed"})
            time.sleep(10)
            continue

        result = run_scenario(scenario["id"])
        results.append({"fm": fm_id, "id": scenario["id"], "result": result})
        print(f"  ... 30s cooldown")
        time.sleep(30)

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
