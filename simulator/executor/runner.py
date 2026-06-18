"""Scenario execution engine: trigger → verify → restore."""

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

from simulator.executor import chaos_mesh, fis


@dataclass
class VerificationResult:
    name: str
    status: str = "pending"  # pending, checking, pass, fail, skipped
    detail: str = ""
    elapsed: float = 0.0


@dataclass
class RunResult:
    scenario_id: str
    status: str = "pending"  # pending, running, completed
    result: str = ""         # pass, fail, partial
    trigger_output: str = ""
    verification_results: list = field(default_factory=list)
    started_at: float = 0.0
    completed_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "status": self.status,
            "result": self.result,
            "trigger_output": self.trigger_output,
            "duration_seconds": round(self.completed_at - self.started_at, 1) if self.completed_at else 0,
            "verification": [
                {"name": v.name, "status": v.status, "detail": v.detail, "elapsed": v.elapsed}
                for v in self.verification_results
            ],
        }


def _run_cmd(cmd: str, timeout: int = 30) -> tuple:
    try:
        result = subprocess.run(
            ["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", "timeout"
    except Exception as e:
        return False, "", str(e)


# ── Verification handlers ──

def _check_chaos_status(step: dict) -> tuple:
    kind = step.get("kind", "")
    name = step.get("name_", "")
    namespace = step.get("namespace", "")
    expected = step.get("expected", "Injected")

    current = chaos_mesh.status(kind, name, namespace)
    if current and expected.lower() in current.lower():
        return True, f"Status: {current}"
    return False, f"Current: {current}, expected: {expected}"


def _check_pod_logs(step: dict) -> tuple:
    deployment = step.get("deployment", "")
    namespace = step.get("namespace", "")
    pattern = step.get("pattern", "")
    tail = step.get("tail", 100)

    ok, stdout, _ = _run_cmd(
        f"kubectl logs -n {namespace} -l app={deployment} --tail={tail} 2>/dev/null"
    )
    if ok and stdout and pattern:
        if re.search(pattern, stdout, re.IGNORECASE):
            match = re.findall(pattern, stdout, re.IGNORECASE)
            return True, f"Pattern matched ({len(match)} hits)"
    return False, "Pattern not found in logs"


def _check_pod_status(step: dict) -> tuple:
    deployment = step.get("deployment", "")
    namespace = step.get("namespace", "")
    expected = step.get("expected", "")

    ok, stdout, _ = _run_cmd(
        f"kubectl get pods -n {namespace} -l app={deployment} "
        f"-o jsonpath='{{.items[0].status.containerStatuses[0].state}}' 2>/dev/null"
    )
    if ok and stdout:
        if expected.lower() in stdout.lower():
            return True, f"Pod status contains: {expected}"

    ok, stdout, _ = _run_cmd(
        f"kubectl get pods -n {namespace} -l app={deployment} "
        f"-o jsonpath='{{.items[0].status.phase}}' 2>/dev/null"
    )
    if ok and stdout and expected.lower() in stdout.lower():
        return True, f"Pod phase: {stdout}"

    return False, f"Expected {expected}, got: {stdout}"


def _check_http(step: dict) -> tuple:
    url = step.get("url", "")
    expected_status = step.get("expected_status", 200)
    max_latency_ms = step.get("max_latency_ms", 0)

    start = time.time()
    ok, stdout, _ = _run_cmd(
        f"kubectl exec -n {step.get('namespace', 'default')} "
        f"deploy/{step.get('from_deployment', '')} -- "
        f"curl -s -o /dev/null -w '%{{http_code}} %{{time_total}}' {url} 2>/dev/null",
        timeout=30,
    )
    if ok and stdout:
        parts = stdout.split()
        if len(parts) >= 2:
            status_code = int(parts[0])
            latency_s = float(parts[1])
            if max_latency_ms and latency_s * 1000 > max_latency_ms:
                return True, f"Latency {latency_s*1000:.0f}ms > {max_latency_ms}ms threshold"
            if status_code == expected_status:
                return True, f"HTTP {status_code}, latency {latency_s*1000:.0f}ms"
    return False, f"HTTP check failed: {stdout}"


def _check_fis_status(step: dict) -> tuple:
    experiment_id = step.get("experiment_id", "")
    expected = step.get("expected", "running")
    region = step.get("region", "us-east-1")

    current = fis.status(experiment_id, region=region)
    if current and expected.lower() in current.lower():
        return True, f"FIS status: {current}"
    return False, f"Expected {expected}, got: {current}"


VERIFIERS = {
    "chaos_status": _check_chaos_status,
    "pod_logs": _check_pod_logs,
    "pod_status": _check_pod_status,
    "http_check": _check_http,
    "fis_status": _check_fis_status,
}


# ── Main execution ──

def _trigger(scenario: dict) -> tuple:
    trigger = scenario.get("trigger", {})
    trigger_type = trigger.get("type", "kubectl")

    if trigger_type == "chaos_mesh":
        yaml_str = trigger.get("yaml", "")
        if not yaml_str:
            return False, "No YAML in trigger"
        ok, stdout, stderr = chaos_mesh.apply(yaml_str)
        return ok, stdout if ok else stderr

    elif trigger_type == "fis":
        template_id = trigger.get("template_id", "")
        region = trigger.get("region", "us-east-1")
        ok, exp_id, detail = fis.start(template_id, region=region)
        if ok:
            scenario["_fis_experiment_id"] = exp_id
        return ok, exp_id if ok else detail

    elif trigger_type in ("kubectl", "aws_cli"):
        cmd = trigger.get("command", "")
        ok, stdout, stderr = _run_cmd(cmd, timeout=120)
        return ok, stdout if ok else stderr

    return False, f"Unknown trigger type: {trigger_type}"


def _restore(scenario: dict):
    restore = scenario.get("restore", {})
    restore_type = restore.get("type", "")

    if restore_type == "chaos_mesh_delete":
        kind = restore.get("kind", "")
        name = restore.get("name", "")
        namespace = restore.get("namespace", "")
        chaos_mesh.delete(kind, name, namespace)

    elif restore_type == "fis_stop":
        exp_id = scenario.get("_fis_experiment_id", restore.get("experiment_id", ""))
        region = restore.get("region", "us-east-1")
        if exp_id:
            fis.stop(exp_id, region=region)

    elif restore_type == "command":
        cmd = restore.get("command", "")
        if cmd:
            _run_cmd(cmd, timeout=60)


def run_scenario(scenario: dict, auto_restore: bool = True) -> RunResult:
    run = RunResult(
        scenario_id=scenario["id"],
        started_at=time.time(),
    )

    for step in scenario.get("verification", []):
        run.verification_results.append(VerificationResult(name=step.get("name", "")))

    # Trigger
    run.status = "running"
    ok, output = _trigger(scenario)
    run.trigger_output = output

    if not ok:
        run.status = "completed"
        run.result = "fail"
        run.completed_at = time.time()
        print(f"[FAIL] Trigger failed: {output}")
        if auto_restore:
            _restore(scenario)
        return run

    print(f"[OK] Trigger applied: {output}")

    # Verification loop
    all_passed = True
    for i, step in enumerate(scenario.get("verification", [])):
        vr = run.verification_results[i]
        vr.status = "checking"

        verifier = VERIFIERS.get(step.get("type"))
        if not verifier:
            vr.status = "skipped"
            vr.detail = f"Unknown verification type: {step.get('type')}"
            continue

        timeout = step.get("timeout", 120)
        poll_interval = step.get("poll_interval", 10)
        deadline = time.time() + timeout

        passed = False
        while time.time() < deadline:
            try:
                ok, detail = verifier(step)
                if ok:
                    vr.status = "pass"
                    vr.detail = detail
                    vr.elapsed = time.time() - run.started_at
                    passed = True
                    print(f"  [PASS] {vr.name}: {detail}")
                    break
            except Exception as e:
                vr.detail = str(e)

            time.sleep(poll_interval)

        if not passed:
            vr.status = "fail"
            vr.elapsed = time.time() - run.started_at
            all_passed = False
            print(f"  [FAIL] {vr.name}: {vr.detail} (timeout {timeout}s)")

    # Result
    run.status = "completed"
    run.completed_at = time.time()
    passed_count = sum(1 for v in run.verification_results if v.status == "pass")
    total = len(run.verification_results)

    if all_passed:
        run.result = "pass"
    elif passed_count > 0:
        run.result = "partial"
    else:
        run.result = "fail"

    print(f"[DONE] {scenario['id']}: {run.result} ({passed_count}/{total} steps passed)")

    # Restore
    if auto_restore:
        print("[RESTORE] Cleaning up...")
        _restore(scenario)
        print("[RESTORE] Done")

    return run
