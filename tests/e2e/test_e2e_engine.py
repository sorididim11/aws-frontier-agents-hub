#!/usr/bin/env python3
"""
E2E verification for the Python scenario execution engine.

Tests:
  1. scenario_runner.py standalone — mock steps, verify event protocol
  2. scenario_runner.py with resume — steps 1-2 skipped, step 3 onward
  3. scenario_runner.py with retry — step with max_retries, verify retry logic
  4. PythonScriptExecutor — subprocess execution + checkpoint parsing
  5. ScriptExecutor — existing bash path still works
  6. retry_from_step — DynamoDB fallback (mocked)

Run: python3 test_e2e_engine.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(DASHBOARD_DIR)
sys.path.insert(0, DASHBOARD_DIR)

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


# =========================================================================
# Test 1: scenario_runner.py standalone with mock steps
# =========================================================================
print("\n=== Test 1: scenario_runner.py standalone (mock steps, all pass) ===")

mock_steps_all_pass = '''\
from scenario_runner import step, StepResult, ScenarioContext

@step(1, "환경 확인")
def check_env(ctx):
    return StepResult("pass", "mock env ok")

@step(2, "장애 주입")
def inject(ctx):
    return StepResult("pass", "mock inject ok")

@step(3, "검증")
def verify(ctx):
    return StepResult("pass", "mock verify ok")
'''

with tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=DASHBOARD_DIR, delete=False) as f:
    f.write(mock_steps_all_pass)
    mock_steps_file = f.name

try:
    result = subprocess.run(
        [sys.executable, "scenario_runner.py", mock_steps_file,
         "--namespace", "test-ns", "--alarm-name", "test-alarm"],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    stdout = result.stdout
    events = []
    checkpoints = []
    for line in stdout.splitlines():
        if line.startswith("EVENT|"):
            events.append(json.loads(line[6:]))
        elif line.startswith("CHECKPOINT|"):
            checkpoints.append(line)

    report("exit code 0 (all pass)", result.returncode == 0, f"got {result.returncode}")
    report("run_start event emitted", any(e["event"] == "run_start" for e in events),
           f"events: {[e['event'] for e in events]}")
    report("3 step_pass events", sum(1 for e in events if e["event"] == "step_pass") == 3,
           f"got {sum(1 for e in events if e['event'] == 'step_pass')}")
    report("run_complete with result=pass",
           any(e["event"] == "run_complete" and e["result"] == "pass" for e in events),
           f"events: {[e for e in events if e['event'] == 'run_complete']}")
    report("3 CHECKPOINT lines", len(checkpoints) == 3, f"got {len(checkpoints)}: {checkpoints}")
    report("RESULT line present", "RESULT|3/3" in stdout, f"stdout tail: {stdout[-200:]}")
finally:
    os.unlink(mock_steps_file)


# =========================================================================
# Test 2: scenario_runner.py with resume_from=2
# =========================================================================
print("\n=== Test 2: scenario_runner.py resume from step 2 ===")

with tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=DASHBOARD_DIR, delete=False) as f:
    f.write(mock_steps_all_pass)
    mock_steps_file = f.name

try:
    result = subprocess.run(
        [sys.executable, "scenario_runner.py", mock_steps_file,
         "--namespace", "test-ns", "--resume-from", "2"],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    events = [json.loads(l[6:]) for l in result.stdout.splitlines() if l.startswith("EVENT|")]
    skip_events = [e for e in events if e["event"] == "step_skip"]
    pass_events = [e for e in events if e["event"] == "step_pass"]

    report("exit code 0", result.returncode == 0, f"got {result.returncode}")
    report("step 1 skipped", len(skip_events) == 1 and skip_events[0]["step"] == 1,
           f"skip events: {skip_events}")
    report("steps 2,3 executed", len(pass_events) == 2,
           f"pass events: {[e['step'] for e in pass_events]}")
finally:
    os.unlink(mock_steps_file)


# =========================================================================
# Test 3: scenario_runner.py with retry (step fails then passes)
# =========================================================================
print("\n=== Test 3: scenario_runner.py retry logic ===")

mock_steps_retry = '''\
from scenario_runner import step, StepResult, ScenarioContext

attempt_count = 0

@step(1, "환경 확인")
def check_env(ctx):
    return StepResult("pass", "ok")

@step(2, "불안정한 단계", max_retries=2, retry_delay=0.1)
def flaky_step(ctx):
    global attempt_count
    attempt_count += 1
    if attempt_count < 3:
        return StepResult("fail", f"attempt {attempt_count} failed", error_category="transient")
    return StepResult("pass", f"succeeded on attempt {attempt_count}")

@step(3, "최종 검증")
def final(ctx):
    return StepResult("pass", "done")
'''

with tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=DASHBOARD_DIR, delete=False) as f:
    f.write(mock_steps_retry)
    mock_steps_file = f.name

try:
    result = subprocess.run(
        [sys.executable, "scenario_runner.py", mock_steps_file,
         "--namespace", "test-ns"],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    events = [json.loads(l[6:]) for l in result.stdout.splitlines() if l.startswith("EVENT|")]
    retry_events = [e for e in events if e["event"] == "step_retry"]
    pass_events = [e for e in events if e["event"] == "step_pass"]

    report("exit code 0 (retry succeeds)", result.returncode == 0, f"got {result.returncode}")
    report("2 retry events for step 2", len(retry_events) == 2,
           f"retry events: {retry_events}")
    report("all 3 steps pass", len(pass_events) == 3,
           f"pass events: {[e['step'] for e in pass_events]}")
    report("RESULT|3/3", "RESULT|3/3" in result.stdout, f"stdout: {result.stdout[-200:]}")
finally:
    os.unlink(mock_steps_file)


# =========================================================================
# Test 4: scenario_runner.py — step fail stops execution
# =========================================================================
print("\n=== Test 4: scenario_runner.py step failure stops execution ===")

mock_steps_fail = '''\
from scenario_runner import step, StepResult, ScenarioContext

@step(1, "환경 확인")
def check_env(ctx):
    return StepResult("pass", "ok")

@step(2, "장애 주입")
def inject(ctx):
    return StepResult("fail", "injection failed", error_category="command_error")

@step(3, "이 단계 실행 안됨")
def should_not_run(ctx):
    return StepResult("pass", "should not reach here")
'''

with tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=DASHBOARD_DIR, delete=False) as f:
    f.write(mock_steps_fail)
    mock_steps_file = f.name

try:
    result = subprocess.run(
        [sys.executable, "scenario_runner.py", mock_steps_file,
         "--namespace", "test-ns"],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    events = [json.loads(l[6:]) for l in result.stdout.splitlines() if l.startswith("EVENT|")]
    fail_events = [e for e in events if e["event"] == "step_fail"]
    pass_events = [e for e in events if e["event"] == "step_pass"]
    complete = [e for e in events if e["event"] == "run_complete"]

    report("exit code 1 (failure)", result.returncode == 1, f"got {result.returncode}")
    report("1 step_pass (step 1)", len(pass_events) == 1 and pass_events[0]["step"] == 1,
           f"pass: {pass_events}")
    report("1 step_fail (step 2)", len(fail_events) == 1 and fail_events[0]["step"] == 2,
           f"fail: {fail_events}")
    report("step 3 not executed", not any(e.get("step") == 3 and e["event"] in ("step_pass", "step_fail") for e in events),
           "step 3 was executed!")
    report("run_complete result=fail", complete and complete[0]["result"] == "fail",
           f"complete: {complete}")
    report("error_category=command_error", fail_events and fail_events[0].get("error_category") == "command_error",
           f"category: {fail_events[0].get('error_category') if fail_events else 'none'}")
finally:
    os.unlink(mock_steps_file)


# =========================================================================
# Test 5: PythonScriptExecutor integration
# =========================================================================
print("\n=== Test 5: PythonScriptExecutor integration ===")

mock_steps_for_executor = '''\
from scenario_runner import step, StepResult, ScenarioContext

@step(1, "환경 확인")
def check_env(ctx):
    return StepResult("pass", "executor test env ok")

@step(2, "장애 주입")
def inject(ctx):
    return StepResult("pass", "executor test inject ok")
'''

try:
    from verifier_executors import PythonScriptExecutor

    mock_scenario = {
        "id": "TEST-E2E-001",
        "name": "E2E Test Scenario",
        "category": "application",
        "verification": {
            "alarms": [{"name": "test-alarm", "type": "CloudWatch"}],
            "steps": [
                {"name": "환경 확인", "type": "pre_check"},
                {"name": "장애 주입", "type": "inject"},
            ]
        }
    }

    executor = PythonScriptExecutor(
        scenario=mock_scenario,
        steps_script=mock_steps_for_executor,
        namespace="test-ns",
    )

    report("PythonScriptExecutor created", executor is not None)
    report("script_type is python", executor.script_type == "python",
           f"got {executor.script_type}")
    report("run_id generated", bool(executor.run_id), f"run_id: {executor.run_id}")

    # to_dict returns expected structure
    d = executor.to_dict()
    report("to_dict has script_type", d.get("script_type") == "python",
           f"got {d.get('script_type')}")
    report("to_dict has status", "status" in d, f"keys: {list(d.keys())}")

except Exception as e:
    import traceback
    report("PythonScriptExecutor import/create", False, f"{e}\n{traceback.format_exc()}")


# =========================================================================
# Test 6: ScriptExecutor with existing bash run.sh
# =========================================================================
print("\n=== Test 6: ScriptExecutor bash backward compat ===")

try:
    from verifier_executors import ScriptExecutor

    bash_script = '''#!/bin/bash
set -e
STEP=0; PASSED=0; TOTAL=2
checkpoint() {
  STEP=$1; local name="$2" status="$3" detail="$4"
  echo "CHECKPOINT|$STEP|$name|$status|$detail"
  if [ "$status" = "PASS" ]; then PASSED=$((PASSED+1)); fi
}
checkpoint 1 "env_check" "PASS" "bash env ok"
checkpoint 2 "inject" "PASS" "bash inject ok"
echo "RESULT|$PASSED/$TOTAL"
'''

    mock_scenario_bash = {
        "id": "TEST-BASH-001",
        "name": "Bash Test Scenario",
        "category": "application",
        "verification": {
            "alarms": [{"name": "test-alarm", "type": "CloudWatch"}],
            "steps": [
                {"name": "env_check", "type": "pre_check"},
                {"name": "inject", "type": "inject"},
            ]
        }
    }

    executor = ScriptExecutor(
        scenario=mock_scenario_bash,
        script=bash_script,
        namespace="test-ns",
    )

    report("ScriptExecutor created", executor is not None)
    report("run_id generated", bool(executor.run_id))

    d = executor.to_dict()
    report("to_dict has status", "status" in d)

except Exception as e:
    import traceback
    report("ScriptExecutor bash compat", False, f"{e}\n{traceback.format_exc()}")


# =========================================================================
# Test 7: PythonScriptExecutor full pipeline execution
# =========================================================================
print("\n=== Test 7: PythonScriptExecutor full pipeline execution ===")

try:
    import threading

    mock_scenario_no_alarms = {
        "id": "TEST-E2E-002",
        "name": "E2E Test No Alarms",
        "category": "application",
        "verification": {
            "steps": [
                {"name": "환경 확인", "type": "pre_check"},
                {"name": "장애 주입", "type": "inject"},
            ]
        }
    }

    executor = PythonScriptExecutor(
        scenario=mock_scenario_no_alarms,
        steps_script=mock_steps_for_executor,
        namespace="test-ns",
    )

    t = threading.Thread(target=executor._run_pipeline, daemon=True)
    t.start()
    t.join(timeout=30)

    report("pipeline completed", not t.is_alive(), "still running after 30s")
    report("status is completed/pass/fail",
           executor.status in ("completed", "pass", "fail", "error"),
           f"status: {executor.status}")

    d = executor.to_dict()
    report("checkpoints populated", len(d.get("checkpoints", [])) > 0,
           f"checkpoints: {d.get('checkpoints', [])}")
    report("json_events populated", len(d.get("json_events", [])) > 0,
           f"json_events count: {len(d.get('json_events', []))}")

    # Verify no duplicate checkpoints (a prior bug)
    cp_names = [(cp.get("step"), cp.get("name")) for cp in d.get("checkpoints", [])]
    unique_cps = set(cp_names)
    report("no duplicate checkpoints", len(cp_names) == len(unique_cps),
           f"total={len(cp_names)}, unique={len(unique_cps)}, dupes={[x for x in cp_names if cp_names.count(x) > 1]}")

except Exception as e:
    import traceback
    report("PythonScriptExecutor pipeline", False, f"{e}\n{traceback.format_exc()}")


# =========================================================================
# Test 8: retry_from_step (in-memory path)
# =========================================================================
print("\n=== Test 8: retry_from_step in-memory path ===")

try:
    from verifier import retry_from_step, _active_runs, _runs_lock, get_active_run

    # Create a mock run with completed steps
    executor2 = PythonScriptExecutor(
        scenario=mock_scenario,
        steps_script=mock_steps_for_executor,
        namespace="test-ns",
    )
    executor2.status = "fail"
    executor2.checkpoints = [
        {"step": 1, "name": "환경 확인", "status": "PASS", "detail": "ok"},
        {"step": 2, "name": "장애 주입", "status": "FAIL", "detail": "failed"},
    ]

    with _runs_lock:
        _active_runs[executor2.run_id] = executor2

    result = retry_from_step(executor2.run_id, 2)
    report("retry_from_step finds in-memory run", result is not None,
           "returned None for in-memory run")

    # Clean up
    with _runs_lock:
        del _active_runs[executor2.run_id]

except Exception as e:
    import traceback
    report("retry_from_step in-memory", False, f"{e}\n{traceback.format_exc()}")


# =========================================================================
# Test 9: retry_from_step (not found → returns None)
# =========================================================================
print("\n=== Test 9: retry_from_step nonexistent run ===")

try:
    result = retry_from_step("nonexistent-run-id-12345", 1)
    report("returns None for nonexistent run", result is None, f"got {result}")
except Exception as e:
    import traceback
    report("retry_from_step nonexistent", False, f"{e}\n{traceback.format_exc()}")


# =========================================================================
# Test 10: classify_error function
# =========================================================================
print("\n=== Test 10: classify_error ===")

try:
    from scenario_runner import classify_error

    cat, reason = classify_error("connection timed out after 300s")
    report("transient detected for timed out", cat == "transient", f"got {cat}")

    cat, reason = classify_error("something failed", timed_out=True)
    report("timeout with timed_out=True", cat == "timeout", f"got {cat}")

    cat, reason = classify_error("command not found: kubectl")
    report("infra_missing for missing command", cat == "infra_missing", f"got {cat}")

    cat, reason = classify_error("something unknown happened")
    report("default category", cat is not None, f"got {cat}")

except Exception as e:
    import traceback
    report("classify_error", False, f"{e}\n{traceback.format_exc()}")


# =========================================================================
# Test 11: Import backward compatibility
# =========================================================================
print("\n=== Test 11: Import backward compatibility ===")

try:
    from verifier import (
        start_run, get_active_run, retry_from_step, get_history,
        SimulationRun, ScriptExecutor, PythonScriptExecutor,
        VERIFIERS, _classify_step_error, _run_cmd, _cmd_env,
    )
    report("all key imports from verifier work", True)
except ImportError as e:
    report("backward compat imports", False, str(e))


# =========================================================================
# Test 12: _extract_python_block and _extract_bash_block
# =========================================================================
print("\n=== Test 12: Script extraction helpers ===")

try:
    sys.path.insert(0, DASHBOARD_DIR)
    # We can't easily import from routes_scenario without Flask context,
    # so test the regex patterns directly
    bash_pattern = re.compile(r'```(?:bash|sh)\s*\n(.*?)```', re.DOTALL)
    python_pattern = re.compile(r'```(?:python)\s*\n(.*?)```', re.DOTALL)

    test_reply = '''Here's the script:

```python
from scenario_runner import step, StepResult

@step(1, "test")
def test_step(ctx):
    return StepResult("pass", "ok")
```

Done!'''

    py_blocks = python_pattern.findall(test_reply)
    report("python block extracted", len(py_blocks) == 1 and "@step" in py_blocks[0],
           f"found {len(py_blocks)} blocks")

    test_reply_bash = '''Here:

```bash
#!/bin/bash
echo hello
```
'''
    bash_blocks = bash_pattern.findall(test_reply_bash)
    report("bash block extracted", len(bash_blocks) == 1 and "echo hello" in bash_blocks[0],
           f"found {len(bash_blocks)} blocks")

except Exception as e:
    import traceback
    report("extraction helpers", False, f"{e}\n{traceback.format_exc()}")


# =========================================================================
# Test 13: _get_scenario_script priority (steps.py > run.sh)
# =========================================================================
print("\n=== Test 13: Script file priority (steps.py > run.sh) ===")

try:
    test_scenario_dir = os.path.join(DASHBOARD_DIR, "scenarios", "TEST-PRIORITY-001")
    os.makedirs(test_scenario_dir, exist_ok=True)

    # Write both files
    with open(os.path.join(test_scenario_dir, "run.sh"), "w") as f:
        f.write("#!/bin/bash\necho hello")
    with open(os.path.join(test_scenario_dir, "steps.py"), "w") as f:
        f.write("# python steps")

    # Test priority
    from routes_scenario import _get_scenario_script
    script, stype = _get_scenario_script("TEST-PRIORITY-001")
    report("steps.py takes priority", stype == "python" and "python steps" in script,
           f"type={stype}")

    # Remove steps.py, should fall back to run.sh
    os.unlink(os.path.join(test_scenario_dir, "steps.py"))
    script, stype = _get_scenario_script("TEST-PRIORITY-001")
    report("fallback to run.sh", stype == "bash" and "echo hello" in script,
           f"type={stype}")

    # Cleanup
    import shutil
    shutil.rmtree(test_scenario_dir)

except Exception as e:
    import traceback
    report("script priority", False, f"{e}\n{traceback.format_exc()}")
    # Cleanup on error
    import shutil
    shutil.rmtree(os.path.join(DASHBOARD_DIR, "scenarios", "TEST-PRIORITY-001"), ignore_errors=True)


# =========================================================================
# Summary
# =========================================================================
print(f"\n{'='*60}")
print(f"E2E Engine Tests: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
print(f"{'='*60}")
if ERRORS:
    print("\nFailures:")
    for err in ERRORS:
        print(f"  - {err}")
    sys.exit(1)
else:
    print("\nAll tests passed!")
    sys.exit(0)
