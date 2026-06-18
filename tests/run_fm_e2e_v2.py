"""E2E test v2: FM-03 + FM-01 with reinforced infra_state signal prompt."""
import json
import time
import sys
import urllib.request

BASE = "http://localhost:5003"

SCENARIOS = [
    {
        "failure_mode": "FM-03",
        "message": "Redis dependency blackhole 시나리오 생성. Redis를 scale 0으로 중단하여 Worker의 의존성 장애 유발. effect_observed는 반드시 Worker에서 Redis로의 연결 시도 실패를 직접 확인해야 한다 (exec로 nc/redis-cli 시도).",
        "app_name": "dockercoins",
    },
    {
        "failure_mode": "FM-01",
        "message": "Hasher network isolation 시나리오 생성. Worker에서 Hasher로의 네트워크 연결을 차단. effect_observed는 반드시 Worker pod에서 Hasher로의 연결 시도(wget/curl)가 실패하는지 직접 확인해야 한다.",
        "app_name": "dockercoins",
    },
]


def api_post(path, data):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}{path}", data=body, headers={"Content-Type": "application/json"}
    )
    resp = urllib.request.urlopen(req, timeout=300)
    return json.loads(resp.read().decode("utf-8"))


def api_get(path):
    req = urllib.request.Request(f"{BASE}{path}")
    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read().decode("utf-8"))


def generate_and_save(spec):
    fm = spec["failure_mode"]
    print(f"\n{'='*60}")
    print(f"{fm}")
    print(f"{'='*60}")

    print("  생성 중...")
    result = api_post("/api/scenario-generate-v2", spec)
    if not result.get("ok"):
        print(f"  생성 실패: {result.get('error', result.get('validation_errors', ''))}")
        return None

    scenario = result["scenario"]
    sid = scenario.get("id", "unknown")
    rounds = result.get("rounds", "?")
    print(f"  생성 OK (id={sid}, rounds={rounds})")

    trigger = scenario.get("trigger", {}).get("command", "")[:120]
    print(f"    trigger: {trigger}")
    steps = scenario.get("verification", {}).get("steps", [])
    for i, s in enumerate(steps):
        cmd = (s.get("command") or "")[:100]
        exp = s.get("expected", s.get("expected_status", ""))[:60]
        print(f"    [{i}] {s.get('type')}/{s.get('phase')} expected={exp} cmd={cmd}")

    if scenario.get("infrastructure_gaps"):
        for g in scenario["infrastructure_gaps"]:
            print(f"    [gap] {g.get('ideal','')[:50]} → {g.get('workaround','')[:50]}")

    # Validate: effect_observed step should NOT use CrashLoopBackOff/restart
    for s in steps:
        if s.get("phase") == "effect_observed":
            exp = s.get("expected", "")
            cmd = s.get("command", "")
            if "CrashLoopBackOff" in exp or "restartCount" in cmd or "restart" in exp.lower():
                print(f"  ⚠️  WARNING: effect_observed 여전히 crash 가정! expected={exp}")
            if "exec" in cmd or "wget" in cmd or "curl" in cmd or "nc " in cmd:
                print(f"  ✓ effect_observed가 연결 테스트 사용 (infra_state)")

    # Save
    try:
        save_result = api_post("/api/arch/save-scenario", {"scenario": scenario})
        print(f"  저장 OK")
    except Exception as e:
        print(f"  저장 실패: {e}")
        return None

    return sid


def run_scenario(scenario_id):
    print(f"  실행 시작 (scenario_id={scenario_id})")
    try:
        result = api_post(f"/api/scenario-run/{scenario_id}", {})
    except Exception as e:
        print(f"  실행 시작 실패: {e}")
        return None

    if not result.get("ok"):
        print(f"  실행 실패: {result.get('error', '')}")
        return None

    run_id = result["run_id"]
    print(f"  run_id={run_id}")
    return run_id


def poll_run(run_id, timeout=400):
    start = time.time()
    last_status = ""
    while time.time() - start < timeout:
        try:
            status = api_get(f"/api/scenario-run/{run_id}/status")
        except Exception:
            time.sleep(10)
            continue

        cur = status.get("status", "unknown")
        step_idx = status.get("current_step_index", -1)
        step_name = ""
        steps = status.get("steps", [])
        if 0 <= step_idx < len(steps):
            step_name = steps[step_idx].get("name", steps[step_idx].get("type", ""))

        elapsed = int(time.time() - start)
        passes = sum(1 for s in steps if s.get("result") == "pass")
        fails = sum(1 for s in steps if s.get("result") == "fail")

        info = f"  [{elapsed}s] {cur} pass={passes} fail={fails}"
        if step_name:
            info += f" → {step_name}"

        if info != last_status:
            print(info)
            last_status = info

        if cur in ("completed", "failed", "timeout", "cancelled"):
            break
        time.sleep(10)

    # Final result
    try:
        final = api_get(f"/api/scenario-run/{run_id}/status")
    except Exception:
        final = {"status": "unknown"}

    result_val = final.get("result", final.get("status", "unknown"))
    print(f"  ─── 완료: {final.get('status')} result={result_val} ───")

    steps = final.get("steps", [])
    for s in steps:
        r = s.get("result", "?").upper()
        name = s.get("name", s.get("type", "?"))
        detail = (s.get("detail") or "")[:100]
        icon = "PASS    " if r == "PASS" else "FAIL    " if r == "FAIL" else "SKIPPED "
        print(f"    [{icon}] {name}: {detail}")

    return result_val


def main():
    results = {}
    for spec in SCENARIOS:
        fm = spec["failure_mode"]
        sid = generate_and_save(spec)
        if not sid:
            results[fm] = "generate_failed"
            continue

        run_id = run_scenario(sid)
        if not run_id:
            results[fm] = "run_start_failed"
            print("  restore 대기 20s...")
            time.sleep(20)
            continue

        result = poll_run(run_id, timeout=400)
        results[fm] = result

        print("  restore 대기 20s...")
        time.sleep(20)

    print(f"\n\n{'='*60}")
    print("최종 결과")
    print(f"{'='*60}")
    for fm, r in results.items():
        icon = "PASS" if r == "pass" else "FAIL"
        print(f"  [{icon}] {fm}: {r}")


if __name__ == "__main__":
    main()
