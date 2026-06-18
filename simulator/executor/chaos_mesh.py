"""Chaos Mesh CRD lifecycle: apply, delete, status check."""

import json
import subprocess
import tempfile
from typing import Optional


def _run(cmd: str, timeout: int = 30) -> tuple:
    try:
        result = subprocess.run(
            ["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", "timeout"
    except Exception as e:
        return False, "", str(e)


def apply(yaml_str: str) -> tuple:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_str)
        f.flush()
        ok, stdout, stderr = _run(f"kubectl apply -f {f.name}")
    return ok, stdout, stderr


def delete(kind: str, name: str, namespace: str) -> tuple:
    return _run(
        f"kubectl delete {kind.lower()} {name} -n {namespace} --ignore-not-found"
    )


def status(kind: str, name: str, namespace: str) -> Optional[str]:
    ok, stdout, _ = _run(
        f"kubectl get {kind.lower()} {name} -n {namespace} "
        f"-o jsonpath='{{.status.conditions[-1:].type}}' 2>/dev/null"
    )
    if ok and stdout:
        return stdout.strip("'\"")

    ok, stdout, _ = _run(
        f"kubectl get {kind.lower()} {name} -n {namespace} "
        f"-o jsonpath='{{.status.experiment.desiredPhase}}' 2>/dev/null"
    )
    if ok and stdout:
        phase = stdout.strip("'\"")
        if phase == "Run":
            return "Injected"
        return phase

    return None


def list_experiments(namespace: str, label_selector: str = "") -> list:
    label_flag = f"-l {label_selector}" if label_selector else ""
    kinds = ["networkchaos", "podchaos", "stresschaos", "httpchaos", "iochaos", "dnschaos"]

    experiments = []
    for kind in kinds:
        ok, stdout, _ = _run(
            f"kubectl get {kind} -n {namespace} {label_flag} -o json 2>/dev/null"
        )
        if ok and stdout:
            try:
                data = json.loads(stdout)
                for item in data.get("items", []):
                    experiments.append({
                        "kind": kind,
                        "name": item["metadata"]["name"],
                        "namespace": item["metadata"]["namespace"],
                        "created": item["metadata"].get("creationTimestamp", ""),
                    })
            except (json.JSONDecodeError, KeyError):
                pass

    return experiments


def cleanup_all(namespace: str):
    kinds = ["networkchaos", "podchaos", "stresschaos", "httpchaos", "iochaos", "dnschaos"]
    for kind in kinds:
        _run(f"kubectl delete {kind} --all -n {namespace} --ignore-not-found")
