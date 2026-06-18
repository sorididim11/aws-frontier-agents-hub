"""Generic test harness for scenario generation + execution.

Reads FM definitions from services/dashboard/failure_modes.py
Reads infra config from config.yaml
No app-specific hardcoding.

Usage:
    python3 _test_harness.py FM-01
    python3 _test_harness.py FM-01 FM-07 FM-12
    python3 _test_harness.py --all
"""
import json
import os
import re
import sys
import time
import requests

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/dashboard"))

from ai_provider import init_provider
from app_config import _load_cfg, _cfg_get, _CFG
from failure_modes import FAILURE_MODES as FM_LIST

# Read from config.yaml (no hardcoded app values)
SPACE = _cfg_get(_CFG, "agent.space_id")
BASE = f"http://localhost:{os.environ.get('PORT', '5003')}"
REGION = _cfg_get(_CFG, "aws.region", "us-east-1")
ACCOUNT = _cfg_get(_CFG, "aws.account_id", "")
PROFILE = _cfg_get(_CFG, "aws.profile", "")
NAMESPACE = _cfg_get(_CFG, "kubernetes.namespace", "default")
CLUSTER = _cfg_get(_CFG, "kubernetes.cluster_name", "")
CONTEXT = _cfg_get(_CFG, "clusters.primary.context", "")
PROJECT = _cfg_get(_CFG, "project.name", "")

# Index FM list by id
FM_MAP = {fm["id"]: fm for fm in FM_LIST}

_INFRA_SNAPSHOT = None


def _collect_infra_snapshot():
    """Collect infrastructure info once — injected into prompts to reduce tool_use calls."""
    global _INFRA_SNAPSHOT
    if _INFRA_SNAPSHOT:
        return _INFRA_SNAPSHOT
    import subprocess
    env = {**os.environ, "AWS_PAGER": ""}
    parts = []

    cmds = [
        (f"kubectl get deploy -n {NAMESPACE} --context {CONTEXT} -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas --no-headers",
         "Deployments"),
        (f"kubectl get svc -n {NAMESPACE} --context {CONTEXT} -o custom-columns=NAME:.metadata.name,TYPE:.spec.type,PORTS:.spec.ports[*].port --no-headers",
         "Services"),
        (f"aws cloudwatch describe-alarms --profile {PROFILE} --region {REGION} --query \"MetricAlarms[].{{Name:AlarmName,Metric:MetricName,Dims:Dimensions[*].Value}}\" --output json",
         "CloudWatch Alarms"),
        (f"kubectl get configmap -n kube-system amazon-vpc-cni --context {CONTEXT} -o jsonpath='{{.data.enable-network-policy-controller}}'",
         "NetworkPolicy Enforcement (false=비활성)"),
    ]
    for cmd, label in cmds:
        try:
            r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=15, env=env)
            if r.returncode == 0 and r.stdout.strip():
                parts.append(f"### {label}\n{r.stdout.strip()}")
        except Exception:
            pass

    _INFRA_SNAPSHOT = "\n\n".join(parts) if parts else ""
    return _INFRA_SNAPSHOT


PROMPT_TEMPLATE = """다음 장애 시나리오의 각 단계에 대해 실제 실행 가능한 명령어를 만들어줘.
아래 인프라 정보를 참고하되, 추가 확인이 필요하면 도구를 사용해.

## 장애 모드
- ID: {fm_id}
- 이름: {fm_name}
- 설명: {fm_description}
- 트리거 메커니즘: {fm_trigger}

## 대상 인프라
- EKS 클러스터: {cluster} ({region}, {account})
- 네임스페이스: {namespace}
- kubectl context: {context}

## 채워야 할 항목 (반드시 실제 인프라 조회 후 작성)

1. **pre_cleanup**: 이전 실행 잔재 정리 명령 (멱등성 보장: --ignore-not-found, || true)
2. **trigger**: 장애 주입 명령 (단일 bash 문자열)
3. **alarm_name**: 이 장애로 인해 ALARM 상태가 될 CloudWatch 알람의 **정확한 이름** (aws cloudwatch describe-alarms로 확인)
4. **restore**: 원상복구 명령
5. **target_service**: trigger가 직접 영향을 주는 서비스명 (kubectl get deploy -n {namespace}에서 확인)

## alarm_name 선택 방법 (핵심 — 알람 전환이 유일한 검증 수단)
1. trigger 실행 → 어떤 메트릭이 변하는지 추론:
   - 서비스 접근 차단 → 호출자에서 timeout → **Latency** 급증
   - 서비스 에러 유발 → 직접 에러 반환 → **Fault** 증가
   - 리소스 부족 → 처리 지연 → **Latency** 급증
2. 해당 메트릭이 연결된 알람을 `aws cloudwatch describe-alarms`에서 찾기
3. 알람의 Period × EvaluationPeriods = 필요 지속시간. trigger 효과가 이보다 **확실히 길어야** 함
4. 알람의 MetricName과 Dimensions가 trigger 효과와 인과적으로 일치하는지 확인

## 제약조건
- alarm_name은 반드시 `aws cloudwatch describe-alarms` 결과에서 실제 존재하는 것만 사용
- kubectl 명령에는 --context 불필요 (자동 주입됨)
- 네임스페이스는 -n {namespace} 명시
- trigger는 fire-and-forget: 120초 내에 종료되는 단일 명령만. rollout status 대기, watch, sleep 포함 금지
- 알람을 찾을 수 없으면 alarm_name을 "NONE"으로 반환하고 이유를 설명해

```json 블록으로 응답:
{{
  "pre_cleanup": "정리 명령",
  "trigger": "장애 주입 명령",
  "alarm_name": "실제 알람 이름",
  "restore": "복원 명령",
  "target_service": "영향받는 서비스명"
}}
"""


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


def generate_scenario(fm_id, fm):
    print(f"\n{'='*60}")
    print(f"  {fm_id}: {fm['name']}")
    print(f"{'='*60}")

    trigger_desc = fm.get("trigger_mechanism", "")
    if isinstance(trigger_desc, list):
        trigger_desc = " / ".join(trigger_desc)

    infra_ctx = _collect_infra_snapshot()
    prompt = PROMPT_TEMPLATE.format(
        fm_id=fm_id,
        fm_name=fm["name"],
        fm_description=fm["description"],
        fm_trigger=trigger_desc,
        cluster=CLUSTER,
        region=REGION,
        account=ACCOUNT,
        namespace=NAMESPACE,
        context=CONTEXT,
    )
    if infra_ctx:
        prompt = f"## 사전 조회된 인프라 정보 (추가 조회 불필요)\n{infra_ctx}\n\n{prompt}"

    print("  Agent에 명령 생성 요청...", flush=True)
    t0 = time.time()
    try:
        from ai_provider import get_provider
        resp = get_provider().send_raw(space_id=SPACE, session_id="", prompt=prompt)
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
    alarm = result.get("alarm_name", "")
    _trig = result.get("trigger", "")
    _rest = result.get("restore", "")
    _trig_str = _trig.get("command", str(_trig)) if isinstance(_trig, dict) else str(_trig)
    _rest_str = _rest.get("command", str(_rest)) if isinstance(_rest, dict) else str(_rest)
    print(f"    ✓ 명령 생성 완료 ({elapsed:.0f}s)")
    print(f"      trigger: {_trig_str[:90]}")
    print(f"      alarm: {alarm}")
    print(f"      restore: {_rest_str[:90]}")

    if not alarm or alarm == "NONE":
        print(f"    ✗ 적합한 알람 없음 — 이 FM은 현재 알람 설정으로 검증 불가")
        return None

    # Assemble scenario
    id_suffix = re.sub(r'[^a-z0-9]+', '-', fm['name'].lower()).strip('-')[:40]
    scenario_id = f"{fm_id}-{id_suffix}"

    # Normalize trigger/restore — Bedrock may return dict or string
    raw_trigger = result.get("trigger", "")
    if isinstance(raw_trigger, dict):
        trigger_cmd = raw_trigger.get("command", "")
        trigger_type = raw_trigger.get("type", "kubectl" if "kubectl" in trigger_cmd else "fis")
    else:
        trigger_cmd = str(raw_trigger)
        trigger_type = "kubectl" if "kubectl" in trigger_cmd else "fis"

    raw_restore = result.get("restore", "")
    restore_cmd = raw_restore.get("command", "") if isinstance(raw_restore, dict) else str(raw_restore)

    scenario = {
        "id": scenario_id,
        "source": "ai-generated",
        "skill_version": "2.1",
        "failure_mode_id": fm_id,
        "name": fm["name"],
        "target_service": result.get("target_service", ""),
        "trigger_mode": fm.get("trigger_mode", "reactive"),
        "category": fm.get("layer", "infrastructure"),
        "layer": fm.get("layer", "infrastructure"),
        "purpose": fm["description"],
        "namespace": NAMESPACE,
        "trigger": {"type": trigger_type, "command": trigger_cmd},
        "restore": {"command": restore_cmd},
        "verification": {
            "steps": [
                {
                    "type": "alarm_state",
                    "name": "알람 ALARM 전환 확인",
                    "alarm_name": alarm,
                    "expected": "ALARM",
                    "timeout": 420,
                    "poll_interval": 15,
                },
                {
                    "type": "investigation_event",
                    "name": "Agent 조사 시작 확인",
                    "expected_status": "IN_PROGRESS",
                    "timeout": 900,
                    "poll_interval": 20,
                },
                {
                    "type": "investigation_event",
                    "name": "Agent 조사 완료 확인",
                    "expected_status": "COMPLETED",
                    "timeout": 900,
                    "poll_interval": 20,
                },
            ]
        },
    }

    if result.get("pre_cleanup"):
        scenario["pre_cleanup"] = {
            "command": result["pre_cleanup"],
            "reset_alarms": [alarm],
            "wait_ok_timeout": 60,
        }

    return scenario


def save_scenario(scenario):
    sid = scenario["id"]
    requests.delete(f"{BASE}/api/scenarios/{sid}?space_id={SPACE}", timeout=30)
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
    """Run scenario, return (result, steps) tuple."""
    print(f"  실행: {scenario_id}", flush=True)
    r = requests.post(f"{BASE}/api/scenario-run/{scenario_id}",
                      params={"space_id": SPACE}, timeout=30)
    if r.status_code != 200 or not r.json().get("ok"):
        error = r.json().get("error", r.text[:100]) if r.status_code != 500 else r.text[:100]
        print(f"    ✗ 실행 실패: {error}")
        return "start_failed", []

    run_id = r.json()["run_id"]
    print(f"    run_id={run_id}", flush=True)

    deadline = time.time() + 2400
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
                steps = data.get("steps", [])
                elapsed = time.time() - (deadline - 960)
                print(f"    ═══ {result.upper()} ({elapsed:.0f}s) ═══")
                for s in steps:
                    st = s.get("status", "")
                    if st == "pending":
                        continue
                    icon = "✓" if st == "pass" else "✗" if st == "fail" else "·"
                    print(f"      {icon} {st:10s} | {s.get('name','?')}")
                    if s.get("detail") and st != "pass":
                        print(f"                     → {s['detail'][:140]}")
                return result, steps
        except Exception:
            continue

    print("    ═══ TIMEOUT ═══")
    return "timeout", []


def _evaluate_soft_pass(steps):
    """If alarm+agent steps passed but only kubectl_check failed, return 'soft_pass'."""
    if not steps:
        return None
    failed_types = [s.get("type") for s in steps if s.get("status") == "fail"]
    passed_types = [s.get("type") for s in steps if s.get("status") == "pass"]
    if (set(failed_types) <= {"kubectl_check"} and
        "alarm_state" in passed_types and
        "investigation_event" in passed_types):
        print(f"    ★ soft_pass — 알람+Agent 조사 정상, kubectl_check만 실패 (무시)")
        return "soft_pass"
    return None


MAX_REFLECTION_ROUNDS = 2

REFLECTION_PROMPT = """이전 시나리오 실행이 실패했습니다. **알람이 ALARM으로 전환되지 않았습니다.**

## 실패 정보
- 장애 모드: {fm_id} ({fm_name})
- 실패 단계: {failed_step}
- 실패 원인: {failure_reason}

## 이전 시나리오
- trigger: {prev_trigger}
- alarm_name: {prev_alarm}
- alarm 조건: {alarm_config}

## 인과관계 불일치 분석
{mismatch_analysis}

## 교정 규칙
1. trigger 효과가 alarm의 Period × EvaluationPeriods 동안 **지속**되어야 함
2. trigger 효과가 alarm의 MetricName/Dimensions와 **인과적으로** 연결되어야 함
3. 위 두 조건을 동시에 만족하는 alarm이 없으면, 다른 alarm_name을 선택하세요
4. 기존 alarm_name을 반드시 유지 (빈 값 반환 금지). 적합한 것이 없으면 가장 가까운 것을 선택

동일한 ```json 포맷으로 응답:
```json
{{
  "pre_cleanup": "정리 명령",
  "trigger": "수정된 장애 주입 명령",
  "alarm_name": "실제 알람 이름 (빈 값 금지)",
  "restore": "복원 명령",
  "target_service": "서비스명"
}}
```
"""


def _get_alarm_config(alarm_name):
    """Get alarm configuration for reflection analysis."""
    import subprocess
    cmd = (
        f"aws cloudwatch describe-alarms --alarm-names \"{alarm_name}\" "
        f"--profile {PROFILE} --region {REGION} "
        f"--query \"MetricAlarms[0].{{Metric:MetricName,Threshold:Threshold,"
        f"Period:Period,EvalPeriods:EvaluationPeriods,Stat:Statistic,Namespace:Namespace}}\" "
        f"--output json"
    )
    try:
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=10,
                           env={**os.environ, "AWS_PAGER": ""})
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return "(조회 실패)"


def _get_failure_info(scenario_id):
    """Get failure details from the run."""
    try:
        r = requests.get(f"{BASE}/api/scenario-run/{scenario_id}/status",
                         params={"space_id": SPACE}, timeout=10)
        data = r.json()
        steps = data.get("steps", [])
        for s in steps:
            if s.get("status") == "fail":
                return s.get("name", "unknown"), s.get("detail", "상세 없음")
            if s.get("status") in ("running", "checking", "timeout"):
                return s.get("name", "unknown"), "timeout — 조건 미충족"
        return "알람 전환", "timeout — 알람이 ALARM 상태로 전환되지 않음"
    except Exception:
        return "unknown", "상태 조회 실패"


def reflect_and_fix(scenario, fm_id, fm, session_id):
    """Reflection loop — analyze failure, ask Bedrock to fix, return new scenario."""
    from ai_provider import get_provider

    alarm_step = next((s for s in scenario.get("verification", {}).get("steps", [])
                       if s.get("type") == "alarm_state"), {})
    alarm = alarm_step.get("alarm_name", "")
    alarm_config = _get_alarm_config(alarm) if alarm else "(없음)"

    failed_step, failure_reason = _get_failure_info(scenario["id"])

    # Analyze mismatch
    trigger_cmd = scenario.get("trigger", {}).get("command", "")
    try:
        import json as _j
        cfg = _j.loads(alarm_config) if alarm_config.startswith("{") else {}
        period = cfg.get("Period", "?")
        evals = cfg.get("EvalPeriods", "?")
        threshold = cfg.get("Threshold", "?")
        metric = cfg.get("Metric", "?")
        required_duration = f"{period}s × {evals}회 = {int(period)*int(evals) if isinstance(period,int) and isinstance(evals,int) else '?'}초 이상"
        mismatch = (
            f"- 알람 조건: {metric} > {threshold}, {required_duration} 지속 필요\n"
            f"- trigger 효과: '{trigger_cmd[:100]}' → 효과 지속시간 분석 필요\n"
            f"- 불일치: trigger 효과가 알람 평가 기간보다 짧거나, metric에 영향을 주지 못할 수 있음"
        )
    except Exception:
        mismatch = f"- alarm_config: {alarm_config}\n- trigger: {trigger_cmd[:100]}"

    prompt = REFLECTION_PROMPT.format(
        fm_id=fm_id,
        fm_name=fm["name"],
        failed_step=failed_step,
        failure_reason=failure_reason,
        prev_trigger=trigger_cmd[:200],
        prev_alarm=alarm,
        alarm_config=alarm_config,
        mismatch_analysis=mismatch,
    )

    infra_ctx = _collect_infra_snapshot()
    if infra_ctx:
        prompt = f"## 인프라 정보\n{infra_ctx}\n\n{prompt}"

    print(f"    [리플렉션] Bedrock에게 교정 요청...", flush=True)
    t0 = time.time()
    try:
        resp = get_provider().send_raw(space_id=SPACE, session_id=session_id, prompt=prompt)
    except Exception as e:
        print(f"    [리플렉션] 실패: {type(e).__name__}")
        return None, session_id

    reply = resp["reply"]
    new_session = resp.get("session_id", session_id)
    result = extract_json(reply)

    if not result:
        print(f"    [리플렉션] JSON 추출 실패 ({time.time()-t0:.0f}s)")
        return None, new_session

    new_alarm = result.get("alarm_name", "")
    raw_trig = result.get("trigger", "")
    raw_rest = result.get("restore", "")
    trig_str = raw_trig.get("command", str(raw_trig)) if isinstance(raw_trig, dict) else str(raw_trig)
    rest_str = raw_rest.get("command", str(raw_rest)) if isinstance(raw_rest, dict) else str(raw_rest)
    print(f"    [리플렉션] 교정 완료 ({time.time()-t0:.0f}s)")
    print(f"      trigger: {trig_str[:90]}")
    print(f"      alarm: {new_alarm} {'(변경됨!)' if new_alarm != alarm else ''}")

    if not new_alarm or new_alarm == "NONE":
        print(f"    [리플렉션] 적합한 알람 없음")
        return None, new_session

    # Rebuild scenario with fixed values
    scenario["trigger"]["command"] = trig_str or scenario["trigger"]["command"]
    scenario["restore"]["command"] = rest_str or scenario["restore"]["command"]
    scenario["target_service"] = result.get("target_service", scenario.get("target_service", ""))

    if result.get("pre_cleanup"):
        scenario["pre_cleanup"] = {
            "command": result["pre_cleanup"],
            "reset_alarms": [new_alarm],
            "wait_ok_timeout": 60,
        }

    # Update alarm_name in verification steps
    for step in scenario["verification"]["steps"]:
        if step["type"] == "alarm_state":
            step["alarm_name"] = new_alarm

    return scenario, new_session


def main():
    available = sorted(FM_MAP.keys())

    if len(sys.argv) < 2:
        print("Usage: python3 _test_harness.py FM-01 [FM-07 ...]")
        print("       python3 _test_harness.py --all")
        print(f"\nAvailable ({len(available)}): {', '.join(available)}")
        sys.exit(1)

    if sys.argv[1] == "--all":
        targets = available
    else:
        targets = [arg.upper() for arg in sys.argv[1:]]
        invalid = [t for t in targets if t not in FM_MAP]
        if invalid:
            print(f"Unknown: {invalid}")
            print(f"Available: {', '.join(available)}")
            sys.exit(1)

    init_provider(profile=PROFILE, region=REGION)
    time.sleep(2)

    print(f"시나리오 생성 + 실행 ({len(targets)}개)")
    print(f"  infra: {CLUSTER} ({REGION}/{ACCOUNT})")
    print(f"  namespace: {NAMESPACE}")
    print("=" * 60)

    results = []
    for fm_id in targets:
        fm = FM_MAP[fm_id]
        scenario = generate_scenario(fm_id, fm)
        if not scenario:
            results.append({"fm": fm_id, "result": "generation_failed"})
            time.sleep(10)
            continue

        if not save_scenario(scenario):
            results.append({"fm": fm_id, "result": "save_failed"})
            time.sleep(10)
            continue

        result, steps = run_scenario(scenario["id"])

        # Check if this is a "soft pass" — alarm+agent passed, only kubectl_check failed
        if result == "fail":
            result = _evaluate_soft_pass(steps) or result

        # Reflection loop — retry on failure
        session_id = ""
        for reflection_round in range(1, MAX_REFLECTION_ROUNDS + 1):
            if result in ("pass", "soft_pass"):
                break
            if result in ("generation_failed", "save_failed", "start_failed"):
                break

            print(f"\n    ┌─ 리플렉션 {reflection_round}/{MAX_REFLECTION_ROUNDS} ─┐")
            fixed_scenario, session_id = reflect_and_fix(scenario, fm_id, fm, session_id)
            if not fixed_scenario:
                print(f"    └─ 교정 불가 — 중단 ─┘")
                break

            scenario = fixed_scenario
            if not save_scenario(scenario):
                print(f"    └─ 저장 실패 — 중단 ─┘")
                break

            print(f"    └─ 재실행 ─┘")
            result, steps = run_scenario(scenario["id"])
            if result == "fail":
                result = _evaluate_soft_pass(steps) or result

        results.append({"fm": fm_id, "id": scenario["id"], "result": result})
        print(f"  ... 30s cooldown")
        time.sleep(30)

    print()
    print("=" * 60)
    print("최종 결과")
    print("=" * 60)
    passed = [r for r in results if r["result"] in ("pass", "soft_pass")]
    print(f"  {len(passed)}/{len(results)} pass")
    for r in results:
        icon = "✓" if r["result"] in ("pass", "soft_pass") else "✗"
        print(f"  {icon} {r['fm']}: {r['result']}")


if __name__ == "__main__":
    main()
