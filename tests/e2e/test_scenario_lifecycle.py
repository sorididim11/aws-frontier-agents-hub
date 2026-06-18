#!/usr/bin/env python3
"""E2E test: Scenario Lifecycle — Generate → Save → Script → Execute → Verify → Improve.

Tests the full lifecycle via HTTP API against running Flask app (localhost:5003).
"""
import json
import os
import sys
import time
import requests

BASE = "http://localhost:5003"
SPACE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
TEST_SCENARIO_ID = "E2E-test-latency-inject"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def api(method, path, **kwargs):
    url = f"{BASE}{path}"
    resp = getattr(requests, method)(url, timeout=120, **kwargs)
    return resp.json(), resp.status_code


# ---------------------------------------------------------------------------
# Test 1: Scenario generation via Agent chat
# ---------------------------------------------------------------------------
def test_scenario_chat():
    log("=== Test 1: Scenario Chat (Agent generates scenario JSON) ===")

    msg = ("hasher 서비스에 네트워크 지연을 주입하는 장애 시나리오를 만들어줘. "
           "3분 이내에 완료되어야 하고, CloudWatch 알람으로 검증해야 해.")

    data, code = api("post", "/api/scenario-chat", json={
        "message": msg,
        "space_id": SPACE_ID,
    })
    assert code == 200, f"chat failed: {code} {data}"
    assert data["ok"], f"chat not ok: {data}"

    session_id = data["session_id"]
    reply = data["reply"]
    log(f"  Session: {session_id}")
    log(f"  Reply length: {len(reply)} chars, has_json={data.get('has_json')}")

    # Extract JSON from reply
    import re
    m = re.search(r'```json\s*\n(.*?)```', reply, re.DOTALL)
    if not m:
        m = re.search(r'```\s*\n(\{.*?\})\s*```', reply, re.DOTALL)
    assert m, "No JSON block in Agent response"

    scenario = json.loads(m.group(1))
    scenario["id"] = TEST_SCENARIO_ID
    log(f"  Scenario: id={scenario['id']}, name={scenario.get('name','')}")

    return scenario, session_id


# ---------------------------------------------------------------------------
# Test 2: Save scenario
# ---------------------------------------------------------------------------
def test_save_scenario(scenario):
    log("=== Test 2: Save Scenario ===")

    # Delete first if exists
    api("delete", f"/api/scenarios/{TEST_SCENARIO_ID}", params={"space_id": SPACE_ID})

    data, code = api("post", "/api/arch/save-scenario", json={
        "scenario": scenario,
        "space_id": SPACE_ID,
    })
    if code == 409:
        log("  Scenario already exists, deleting and retrying...")
        api("delete", f"/api/scenarios/{TEST_SCENARIO_ID}", params={"space_id": SPACE_ID})
        data, code = api("post", "/api/arch/save-scenario", json={
            "scenario": scenario,
            "space_id": SPACE_ID,
        })

    assert code == 200, f"save failed: {code} {data}"
    assert data["ok"], f"save not ok: {data}"
    log(f"  Saved: id={data['id']}, space={data['space_id']}")

    # Verify local directory
    scen_dir = os.path.join(os.path.dirname(__file__), "scenarios", TEST_SCENARIO_ID)
    scen_json_path = os.path.join(scen_dir, "scenario.json")
    assert os.path.exists(scen_json_path), f"scenario.json not found: {scen_json_path}"
    log(f"  Local dir exists: {scen_dir}")

    return data


# ---------------------------------------------------------------------------
# Test 3: Script generation (Turn 2)
# ---------------------------------------------------------------------------
def test_generate_script(session_id):
    log("=== Test 3: Generate Script (Agent Turn 2) ===")

    data, code = api("post", "/api/scenario-generate-script", json={
        "scenario_id": TEST_SCENARIO_ID,
        "session_id": session_id,
        "space_id": SPACE_ID,
    })
    assert code == 200, f"script gen failed: {code} {data}"
    assert data["ok"], f"script gen not ok: {data}"

    script = data["script"]
    log(f"  Script generated: {data['length']} chars")
    log(f"  First line: {script.split(chr(10))[0][:80]}")

    # Verify script saved locally
    script_path = os.path.join(os.path.dirname(__file__), "scenarios", TEST_SCENARIO_ID, "run.sh")
    assert os.path.exists(script_path), f"run.sh not found: {script_path}"
    log(f"  run.sh saved: {script_path}")

    # Verify GET endpoint
    data2, code2 = api("get", f"/api/scenario-script/{TEST_SCENARIO_ID}")
    assert code2 == 200 and data2["ok"], f"script GET failed: {data2}"
    log(f"  GET /api/scenario-script: {len(data2['script'])} chars")

    return script


# ---------------------------------------------------------------------------
# Test 4: Execute scenario (ScriptExecutor)
# ---------------------------------------------------------------------------
def test_execute_scenario():
    log("=== Test 4: Execute Scenario (ScriptExecutor) ===")

    data, code = api("post", f"/api/scenario-run/{TEST_SCENARIO_ID}",
                      params={"space_id": SPACE_ID})
    assert code == 200, f"run failed: {code} {data}"

    run_id = data["run_id"]
    log(f"  Run started: {run_id}")

    # Poll for completion
    start = time.time()
    timeout = 360  # 6 minutes max
    while time.time() - start < timeout:
        status_data, _ = api("get", f"/api/scenario-run/{run_id}/status")
        status = status_data.get("status", "unknown")
        result = status_data.get("result")

        if status in ("completed", "preflight_failed"):
            elapsed = round(time.time() - start, 1)
            log(f"  Completed: status={status}, result={result}, elapsed={elapsed}s")

            steps = status_data.get("steps", [])
            for s in steps:
                log(f"    Step {s.get('index')}: {s['name']} → {s['status']} ({s.get('detail','')})")

            script_out = status_data.get("script_output", {})
            if script_out:
                log(f"  Script exit_code={script_out.get('exit_code')}")
                if script_out.get("stderr"):
                    log(f"  stderr: {script_out['stderr'][:200]}")

            alarm_results = status_data.get("alarm_results", [])
            for ar in alarm_results:
                log(f"  Alarm: {ar['alarm']} → {ar['status']} (state={ar['current_state']}, {ar['elapsed']}s)")

            return status_data

        log(f"  Polling... status={status} ({round(time.time()-start)}s)")
        time.sleep(15)

    log(f"  TIMEOUT after {timeout}s")
    return {"status": "timeout", "result": "fail"}


# ---------------------------------------------------------------------------
# Test 5: Improvement (if failed)
# ---------------------------------------------------------------------------
def test_improvement(run_data):
    log("=== Test 5: Improvement Loop ===")

    if run_data.get("result") == "pass":
        log("  Scenario passed — no improvement needed")
        return None

    run_id = run_data.get("run_id", "")
    data, code = api("post", "/api/scenario-improvements", json={
        "scenario_id": TEST_SCENARIO_ID,
        "run_id": run_id,
        "space_id": SPACE_ID,
    })
    assert code == 200, f"improvements failed: {code} {data}"
    assert data["ok"], f"improvements not ok: {data}"

    improvements = data.get("improvements", {})
    log(f"  Diagnosis: {improvements.get('diagnosis', 'N/A')}")
    log(f"  Confidence: {improvements.get('confidence', 'N/A')}")
    log(f"  Script fix: {'Yes' if improvements.get('script_fix') else 'No'}")
    log(f"  Scenario fixes: {len(improvements.get('scenario_fixes', []))}")
    log(f"  Prompt rules: {len(improvements.get('prompt_rules', []))}")

    # Accept improvements
    accept_body = {
        "scenario_id": TEST_SCENARIO_ID,
        "space_id": SPACE_ID,
        "prompt_rules": improvements.get("prompt_rules", []),
        "scenario_fixes": improvements.get("scenario_fixes", []),
    }
    if improvements.get("script_fix"):
        accept_body["script_fix"] = improvements["script_fix"]

    data2, code2 = api("post", "/api/scenario-improvements/accept", json=accept_body)
    assert code2 == 200 and data2["ok"], f"accept failed: {data2}"
    log(f"  Accepted: rules={data2.get('rules_added',0)}, fixes={data2.get('fixes_applied',0)}, script={data2.get('script_updated',False)}")

    return improvements


# ---------------------------------------------------------------------------
# Test 6: Cleanup
# ---------------------------------------------------------------------------
def test_cleanup():
    log("=== Test 6: Cleanup ===")
    data, code = api("delete", f"/api/scenarios/{TEST_SCENARIO_ID}",
                      params={"space_id": SPACE_ID})
    log(f"  Delete: code={code}, ok={data.get('ok')}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    log("=" * 60)
    log("Scenario Lifecycle E2E Test")
    log("=" * 60)

    try:
        # Health check
        r = requests.get(f"{BASE}/api/scenarios", params={"space_id": SPACE_ID}, timeout=5)
        assert r.status_code == 200, f"App not running on {BASE}"
        log(f"App running. {len(r.json().get('scenarios',[]))} existing scenarios.\n")
    except Exception as e:
        log(f"FATAL: App not reachable at {BASE}: {e}")
        sys.exit(1)

    results = {}

    try:
        # Test 1: Generate scenario via Agent
        scenario, session_id = test_scenario_chat()
        results["chat"] = "PASS"
        log("")

        # Test 2: Save scenario
        test_save_scenario(scenario)
        results["save"] = "PASS"
        log("")

        # Test 3: Generate script
        script = test_generate_script(session_id)
        results["script_gen"] = "PASS"
        log("")

        # Test 4: Execute
        run_data = test_execute_scenario()
        results["execute"] = "PASS" if run_data.get("result") == "pass" else "FAIL (scenario failed)"
        log("")

        # Test 5: Improvement (only if failed)
        if run_data.get("result") != "pass":
            improvements = test_improvement(run_data)
            results["improve"] = "PASS"

            # Re-execute after improvement
            log("\n=== Test 5b: Re-execute after improvement ===")
            run_data2 = test_execute_scenario()
            results["re_execute"] = "PASS" if run_data2.get("result") == "pass" else "FAIL"
        else:
            results["improve"] = "SKIP (passed first time)"
        log("")

    except Exception as e:
        import traceback
        log(f"\nERROR: {e}")
        traceback.print_exc()
        results["error"] = str(e)
    finally:
        # Cleanup (optional — keep for inspection)
        # test_cleanup()
        pass

    log("=" * 60)
    log("RESULTS:")
    for k, v in results.items():
        log(f"  {k}: {v}")
    log("=" * 60)


if __name__ == "__main__":
    main()
