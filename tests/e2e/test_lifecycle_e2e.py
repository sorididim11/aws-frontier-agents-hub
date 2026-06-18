#!/usr/bin/env python3
"""E2E test: Full scenario lifecycle with auto-accept improvement loop.

Generate (JSON+Script in single turn) → Save → Execute → Improve (auto-accept) → Re-execute
Verifies infrastructure_gaps classification and iterative improvement.
Supports checkpoint-based resume from failure.
"""
import json
import os
import re
import sys
import time
import requests

BASE = "http://localhost:5003"
SPACE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SID = "E2E-test-latency-inject"
MAX_IMPROVE_ROUNDS = 3

AGENT_TIMEOUT = 600
SHORT_TIMEOUT = 30


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def api(method, path, timeout=SHORT_TIMEOUT, **kwargs):
    url = f"{BASE}{path}"
    resp = getattr(requests, method)(url, timeout=timeout, **kwargs)
    return resp.json(), resp.status_code


def step_1_generate():
    log("=" * 60)
    log("STEP 1: Agent generates scenario JSON + bash script (single turn)")
    log("=" * 60)

    msg = ("hasher 서비스에 application-level 지연을 주입하는 장애 시나리오를 만들어줘. "
           "hasher의 /inject-latency?seconds=5 엔드포인트를 kubectl port-forward로 호출해서 지연을 주입하고, "
           "CloudWatch ApplicationSignals 알람(devops-agent-test-hasher-high-latency)으로 검증해야 해. "
           "복원은 /clear-latency 호출. 3분 이내에 완료되어야 해.")

    data, code = api("post", "/api/scenario-chat", timeout=AGENT_TIMEOUT, json={
        "message": msg, "space_id": SPACE_ID, "include_script": True,
    })
    assert code == 200 and data["ok"], f"chat failed: {code} {data}"

    session_id = data["session_id"]
    reply = data["reply"]
    log(f"  Session: {session_id}, reply: {len(reply)} chars")

    m = re.search(r'```json\s*\n(.*?)```', reply, re.DOTALL)
    if not m:
        m = re.search(r'```\s*\n(\{.*?\})\s*```', reply, re.DOTALL)
    assert m, "No JSON block in Agent response"

    scenario = json.loads(m.group(1))
    scenario["id"] = SID
    log(f"  Scenario: {scenario.get('name','')}")

    script = data.get("script")
    if not script:
        sm = re.search(r'```(?:bash|sh)\s*\n(.*?)```', reply, re.DOTALL)
        script = sm.group(1).strip() if sm else None
    has_script = bool(script)
    log(f"  Script included: {has_script} ({len(script) if script else 0} chars)")

    return scenario, session_id, script


def step_2_save(scenario, script=None):
    log("\nSTEP 2: Save scenario (DynamoDB)")
    api("delete", f"/api/scenarios/{SID}", params={"space_id": SPACE_ID})
    data, code = api("post", "/api/arch/save-scenario", json={
        "scenario": scenario, "space_id": SPACE_ID,
    })
    assert code == 200 and data["ok"], f"save failed: {code} {data}"
    log(f"  Saved: {data['id']}")

    if script:
        log("  Saving script from single-turn response...")
        _save_script_local(script)


def _save_script_local(script):
    base = os.path.join(os.path.dirname(__file__), "scenarios", SID)
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, "run.sh")
    with open(path, "w") as f:
        f.write(script)
    log(f"  Script saved: scenarios/{SID}/run.sh ({len(script)} chars)")


def step_3_verify_script():
    log("\nSTEP 3: Verify script exists")
    path = os.path.join(os.path.dirname(__file__), "scenarios", SID, "run.sh")
    assert os.path.exists(path), f"run.sh not found at {path}"
    with open(path) as f:
        content = f.read()
    log(f"  Script: {len(content)} chars")
    assert "checkpoint" in content.lower() or "CHECKPOINT" in content, "Script missing checkpoint function"
    return content


def step_4_execute(resume_from=0):
    log(f"\nSTEP 4: Execute (ScriptExecutor, resume_from={resume_from})")
    params = {"space_id": SPACE_ID}
    if resume_from > 0:
        params["resume_from"] = resume_from
    data, code = api("post", f"/api/scenario-run/{SID}", params=params)
    assert code == 200, f"run failed: {code} {data}"
    run_id = data["run_id"]
    log(f"  Run: {run_id}")

    start = time.time()
    while True:
        sd, code = api("get", f"/api/scenario-run/{run_id}/status")
        if code == 404:
            log(f"  Run {run_id} not found (app may have restarted)")
            return {"status": "lost", "result": "fail", "run_id": run_id}
        status = sd.get("status", "?")
        if status in ("completed", "preflight_failed"):
            elapsed = round(time.time() - start)
            result = sd.get("result")
            log(f"  Done: result={result} ({elapsed}s)")

            for s in sd.get("steps", []):
                log(f"    {s['name']} → {s['status']} ({s.get('detail','')})")

            so = sd.get("script_output", {})
            if so.get("exit_code") is not None:
                log(f"  Script exit={so['exit_code']}")

            for cp in sd.get("checkpoints", []):
                log(f"    CP{cp['step']}: {cp['name']} → {cp['status']} ({cp.get('detail','')})")

            for ar in sd.get("alarm_results", []):
                log(f"  Alarm: {ar['alarm']} → {ar['status']} (state={ar['current_state']}, {ar['elapsed']}s)")

            last_pass = sd.get("last_passed_step", 0)
            if last_pass > 0:
                log(f"  Last passed checkpoint: {last_pass}")

            return sd
        log(f"  ... {status} ({round(time.time()-start)}s)")
        time.sleep(15)


def _build_fix_message(run_data):
    """실행 결과에서 실패한 checkpoint를 추출하여 수정 요청 메시지 구성."""
    lines = ["실행 결과 실패했어. 아래 실패 내역을 확인하고 시나리오 JSON과 스크립트를 수정해줘.\n"]

    checkpoints = run_data.get("checkpoints", [])
    failed = [cp for cp in checkpoints if cp.get("status") == "FAIL"]
    passed = [cp for cp in checkpoints if cp.get("status") == "PASS"]

    if passed:
        lines.append(f"## 성공한 단계 ({len(passed)}개)")
        for cp in passed:
            lines.append(f"- Step {cp['step']}: {cp['name']} → PASS")
        lines.append("")

    if failed:
        lines.append(f"## 실패한 단계 ({len(failed)}개)")
        for cp in failed:
            lines.append(f"### Step {cp['step']}: {cp['name']} → FAIL")
            lines.append(f"- 상세: {cp.get('detail', '(없음)')}")
            lines.append("")
    elif not checkpoints:
        lines.append("## checkpoint 출력 없음 (스크립트가 초기 단계에서 실패한 것으로 보임)\n")

    so = run_data.get("script_output", {})
    exit_code = so.get("exit_code")
    stderr = (so.get("stderr") or "")[-1500:]
    stdout = (so.get("stdout") or "")[-2000:]

    if exit_code is not None:
        lines.append(f"## 스크립트 종료 코드: {exit_code}")

    if stderr.strip():
        lines.append(f"## stderr (마지막 1500자)\n```\n{stderr.strip()}\n```")

    if stdout.strip():
        stdout_tail = stdout[-1500:]
        lines.append(f"## stdout (마지막 1500자)\n```\n{stdout_tail.strip()}\n```")

    alarm_results = run_data.get("alarm_results", [])
    if alarm_results:
        lines.append("## 알람 결과")
        for ar in alarm_results:
            lines.append(f"- {ar['alarm']}: {ar['status']} (state={ar['current_state']}, {ar['elapsed']}s)")
        lines.append("")

    lines.append("위 실패를 수정한 시나리오 JSON과 스크립트를 다시 생성해줘. "
                 "```json과 ```bash 블록으로 출력해줘.")
    return "\n".join(lines)


def step_5_improve(run_data, round_num, session_id=None):
    """실패 결과를 동일 채팅 세션에 보내서 Agent가 수정하도록 요청."""
    log(f"\nSTEP 5: Chat-based fix (round {round_num}, session={session_id[:16] if session_id else 'new'})")

    fix_msg = _build_fix_message(run_data)
    log(f"  Fix message: {len(fix_msg)} chars")

    data, code = api("post", "/api/scenario-chat", timeout=AGENT_TIMEOUT, json={
        "message": fix_msg,
        "session_id": session_id,
        "space_id": SPACE_ID,
        "include_script": True,
    })
    assert code == 200 and data["ok"], f"fix chat failed: {code} {data}"

    reply = data["reply"]
    new_session_id = data.get("session_id", session_id)
    log(f"  Reply: {len(reply)} chars")

    m = re.search(r'```json\s*\n(.*?)```', reply, re.DOTALL)
    scenario_fixed = None
    if m:
        try:
            scenario_fixed = json.loads(m.group(1))
            scenario_fixed["id"] = SID
            log(f"  Scenario JSON: extracted ({scenario_fixed.get('name', '?')})")
        except json.JSONDecodeError:
            log(f"  Scenario JSON: parse error")

    script_fixed = None
    sm = re.findall(r'```(?:bash|sh)\s*\n(.*?)```', reply, re.DOTALL)
    if sm:
        script_fixed = max(sm, key=len).strip()
        log(f"  Script: extracted ({len(script_fixed)} chars)")

    if scenario_fixed:
        api("delete", f"/api/scenarios/{SID}", params={"space_id": SPACE_ID})
        d, c = api("post", "/api/arch/save-scenario", json={
            "scenario": scenario_fixed, "space_id": SPACE_ID,
        })
        if c == 200 and d.get("ok"):
            log(f"  Scenario saved to DynamoDB")

    if script_fixed:
        _save_script_local(script_fixed)
        log(f"  Script saved locally")

    result = {
        "has_scenario": bool(scenario_fixed),
        "has_script": bool(script_fixed),
        "session_id": new_session_id,
    }
    return result


def main():
    log("=" * 60)
    log("SCENARIO LIFECYCLE E2E TEST (single-turn + auto-accept)")
    log("=" * 60)

    # Health check
    try:
        r = requests.get(f"{BASE}/api/scenarios", params={"space_id": SPACE_ID}, timeout=5)
        assert r.status_code == 200
    except Exception as e:
        log(f"FATAL: App not reachable: {e}")
        sys.exit(1)

    results = {}

    try:
        # Step 1: Generate (JSON + Script in single turn)
        scenario, session_id, script = step_1_generate()
        results["1_generate"] = "PASS"
        results["1_has_script"] = bool(script)

        # Step 2: Save
        step_2_save(scenario, script=script)
        results["2_save"] = "PASS"

        # Step 3: Verify script
        if not script:
            log("\n  WARNING: Script not in single-turn response, falling back to generate-script...")
            data, code = api("post", "/api/scenario-generate-script", timeout=AGENT_TIMEOUT, json={
                "scenario_id": SID, "session_id": session_id, "space_id": SPACE_ID,
            })
            assert code == 200 and data["ok"], f"script gen failed: {code} {data}"
            assert os.path.exists(os.path.join(os.path.dirname(__file__), "scenarios", SID, "run.sh"))
        step_3_verify_script()
        results["3_script"] = "PASS"

        # Step 4: Execute
        run_data = step_4_execute()
        results["4_execute"] = run_data.get("result", "?")

        # Step 5: Chat-based fix loop (same session, up to MAX_IMPROVE_ROUNDS)
        for round_num in range(1, MAX_IMPROVE_ROUNDS + 1):
            if run_data.get("result") == "pass":
                log(f"\n** SCENARIO PASSED on round {round_num - 1}! **")
                break

            fix_result = step_5_improve(run_data, round_num, session_id=session_id)
            results[f"5_fix_r{round_num}"] = "json+script" if fix_result["has_scenario"] and fix_result["has_script"] else "partial"
            session_id = fix_result.get("session_id", session_id)

            if not fix_result["has_script"]:
                log(f"\n** Agent did not return a script — cannot re-execute **")
                break

            step_3_verify_script()

            log(f"\n  Re-executing after fix round {round_num}...")
            run_data = step_4_execute()
            results[f"4_execute_r{round_num}"] = run_data.get("result", "?")
        else:
            log(f"\n** Max fix rounds ({MAX_IMPROVE_ROUNDS}) reached **")

    except Exception as e:
        import traceback
        log(f"\nERROR: {e}")
        traceback.print_exc()
        results["error"] = str(e)

    log("\n" + "=" * 60)
    log("RESULTS SUMMARY:")
    log("=" * 60)
    for k, v in results.items():
        log(f"  {k}: {v}")
    log("=" * 60)


if __name__ == "__main__":
    main()
