#!/usr/bin/env python3
"""
Full E2E live test for 2 scenarios:
  1. Generate Python steps (Agent call)
  2. Execute (PythonScriptExecutor)
  3. Verify checkpoints + events
  4. Retry → new_run response
  5. Resume from failed step → verify skip behavior

Requires: app running on port 5003, valid AWS SSO session.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

BASE = "http://localhost:5003"
SPACE = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

SCENARIOS = [
    "SCN-HASHER-APP-LATENCY-001",
    "SCN-HASHER-ENDPOINT-ABUSE-001",
]

PASS = 0
FAIL = 0
ERRORS = []


def report(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        ERRORS.append(f"{name}: {detail}")
        print(f"  FAIL  {name} — {detail}")


def api_post(path, data=None):
    url = f"{BASE}{path}"
    body = json.dumps(data or {}).encode()
    req = urllib.request.Request(url, method="POST",
                                headers={"Content-Type": "application/json"}, data=body)
    try:
        with urllib.request.urlopen(req, timeout=700) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def api_get(path):
    url = f"{BASE}{path}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"status": "not_found", "error": "404"}
        raise


def poll_run(run_id, max_wait=660):
    """Poll until completed, return final status dict."""
    start = time.time()
    last_print = 0
    while time.time() - start < max_wait:
        d = api_get(f"/api/scenario-run/{run_id}/status?space_id={SPACE}")
        status = d.get("status")
        if status in ("completed", "pass", "fail", "error"):
            return d
        elapsed = time.time() - start
        if elapsed - last_print >= 30:
            cps = d.get("checkpoints", [])
            trigger = d.get("trigger_output", "")[:60]
            cp_str = ",".join(f"s{c['step']}:{c['status']}" for c in cps)
            print(f"    [{elapsed:.0f}s] status={status} cps=[{cp_str}] {trigger}")
            last_print = elapsed
        time.sleep(10)
    return {"status": "timeout", "error": f"Exceeded {max_wait}s"}


# =========================================================================
for scenario_id in SCENARIOS:
    print(f"\n{'='*70}")
    print(f"  SCENARIO: {scenario_id}")
    print(f"{'='*70}")

    # --- Phase 1: Generate Python steps ---
    print(f"\n--- Phase 1: Generate Python steps ---")
    code, resp = api_post("/api/scenario-generate-script", {
        "scenario_id": scenario_id,
        "space_id": SPACE,
        "script_type": "python",
    })
    report(f"[{scenario_id}] generation API 200", code == 200, f"got {code}")
    report(f"[{scenario_id}] script generated", resp.get("ok") is True, resp.get("error", ""))
    report(f"[{scenario_id}] script_type=python", resp.get("script_type") == "python",
           f"got {resp.get('script_type')}")

    script = resp.get("script", "")
    report(f"[{scenario_id}] script has @step", "@step(" in script, "no @step decorator found")
    report(f"[{scenario_id}] script has _shared.get (resume-safe)",
           "_shared.get(" in script or '_shared.get("' in script,
           "missing _shared.get — not resume-safe")

    # Check file saved
    steps_path = os.path.join(os.path.dirname(__file__), "scenarios", scenario_id, "steps.py")
    report(f"[{scenario_id}] steps.py saved to disk", os.path.exists(steps_path),
           f"not found at {steps_path}")

    # --- Phase 2: Execute ---
    print(f"\n--- Phase 2: Execute scenario ---")
    code, resp = api_post(f"/api/scenario-run/{scenario_id}?space_id={SPACE}", {"space_id": SPACE})
    report(f"[{scenario_id}] run started", resp.get("ok") is True, resp.get("error", ""))
    run_id = resp.get("run_id", "")
    print(f"    run_id={run_id}")

    # --- Phase 3: Wait for completion ---
    print(f"\n--- Phase 3: Poll until completion ---")
    final = poll_run(run_id)
    status = final.get("status")
    result = final.get("result")
    cps = final.get("checkpoints", [])
    events = final.get("json_events", [])

    report(f"[{scenario_id}] completed (not hung)", status == "completed",
           f"status={status}")
    report(f"[{scenario_id}] script_type=python in result", final.get("script_type") == "python",
           f"got {final.get('script_type')}")
    report(f"[{scenario_id}] has checkpoints", len(cps) > 0,
           f"count={len(cps)}")
    report(f"[{scenario_id}] has json_events", len(events) > 0,
           f"count={len(events)}")

    # At minimum steps 1-3 should pass (env check, pre-cleanup, inject)
    pass_steps = [cp for cp in cps if cp.get("status") == "PASS"]
    fail_steps = [cp for cp in cps if cp.get("status") == "FAIL"]
    report(f"[{scenario_id}] steps 1-3 PASS", len(pass_steps) >= 3,
           f"only {len(pass_steps)} passed: {[c['step'] for c in pass_steps]}")

    print(f"    Result: {result}, Passed: {len(pass_steps)}, Failed: {len(fail_steps)}")
    for cp in cps:
        print(f"      [{cp.get('step')}] {cp.get('name')}: {cp.get('status')} | {str(cp.get('detail',''))[:80]}")

    # No duplicate checkpoints
    cp_keys = [(cp.get("step"), cp.get("name")) for cp in cps]
    report(f"[{scenario_id}] no duplicate checkpoints", len(cp_keys) == len(set(cp_keys)),
           f"dupes found")

    # --- Phase 4: Retry ---
    print(f"\n--- Phase 4: Retry from failed step ---")
    if fail_steps:
        fail_step_num = fail_steps[0].get("step", 4)
    else:
        fail_step_num = len(cps)

    code, retry_resp = api_post(f"/api/scenario-run/{run_id}/retry/{fail_step_num}?space_id={SPACE}", {})
    report(f"[{scenario_id}] retry returns new_run",
           retry_resp.get("action") == "new_run",
           f"got {retry_resp}")
    report(f"[{scenario_id}] retry scenario_id correct",
           retry_resp.get("scenario_id") == scenario_id,
           f"got {retry_resp.get('scenario_id')}")
    report(f"[{scenario_id}] retry resume_from correct",
           retry_resp.get("resume_from") == fail_step_num,
           f"got {retry_resp.get('resume_from')}")

    # --- Phase 5: Resume execution from failed step ---
    print(f"\n--- Phase 5: Resume from step {fail_step_num} ---")
    code, resume_resp = api_post(
        f"/api/scenario-run/{scenario_id}?space_id={SPACE}&resume_from={fail_step_num}",
        {"space_id": SPACE},
    )
    report(f"[{scenario_id}] resume run started", resume_resp.get("ok") is True,
           resume_resp.get("error", ""))
    resume_run_id = resume_resp.get("run_id", "")
    print(f"    resume_run_id={resume_run_id}")

    # Poll resumed run
    resume_final = poll_run(resume_run_id, max_wait=400)
    resume_events = resume_final.get("json_events", [])
    skip_events = [e for e in resume_events if e.get("event") == "step_skip"]
    resume_cps = resume_final.get("checkpoints", [])

    report(f"[{scenario_id}] resume completed", resume_final.get("status") == "completed",
           f"status={resume_final.get('status')}")
    report(f"[{scenario_id}] skipped {fail_step_num - 1} steps",
           len(skip_events) == fail_step_num - 1,
           f"expected {fail_step_num - 1} skips, got {len(skip_events)}: {[e.get('step') for e in skip_events]}")

    # The resumed step should have executed (pass or fail)
    executed_steps = [cp for cp in resume_cps if cp.get("status") in ("PASS", "FAIL")]
    report(f"[{scenario_id}] resumed step executed", len(executed_steps) >= 1,
           f"no executed steps in resume")

    # No KeyError in resumed step (the _shared.get fix)
    resume_fail_detail = ""
    for cp in resume_cps:
        if cp.get("status") == "FAIL":
            resume_fail_detail = cp.get("detail", "")
            break
    has_keyerror = "KeyError" in resume_fail_detail or "'timeouts'" in resume_fail_detail or "'alarm_name'" in resume_fail_detail
    report(f"[{scenario_id}] no KeyError in resume (shared.get fix)",
           not has_keyerror,
           f"KeyError found: {resume_fail_detail[:100]}")

    print(f"    Resume result: {resume_final.get('result')}")
    for cp in resume_cps:
        print(f"      [{cp.get('step')}] {cp.get('name')}: {cp.get('status')} | {str(cp.get('detail',''))[:80]}")


# =========================================================================
print(f"\n{'='*70}")
print(f"  FINAL SUMMARY: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
print(f"{'='*70}")
if ERRORS:
    print("\nFailures:")
    for err in ERRORS:
        print(f"  - {err}")
    sys.exit(1)
else:
    print("\nAll E2E live tests passed!")
    sys.exit(0)
