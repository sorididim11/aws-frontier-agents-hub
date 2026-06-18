"""Lifecycle management for simulator dependencies: Kubeshark, Chaos Mesh, FIS Agent."""

import json
import subprocess
import sys
from typing import Optional

from simulator.config import SimulatorConfig


def _run(cmd: str, check: bool = True, capture: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["bash", "-c", cmd],
        capture_output=capture,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        print(f"[ERROR] Command failed: {cmd}", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
    return result


def _helm_repo_add(name: str, url: str):
    _run(f"helm repo add {name} {url} 2>/dev/null || true")
    _run("helm repo update")


def _is_release_installed(name: str, namespace: str) -> bool:
    result = _run(f"helm list -n {namespace} -o json 2>/dev/null", check=False)
    if result.returncode != 0:
        return False
    releases = json.loads(result.stdout or "[]")
    return any(r.get("name") == name for r in releases)


def _wait_pods_ready(namespace: str, timeout: int = 120):
    _run(
        f"kubectl wait --for=condition=Ready pods --all -n {namespace} --timeout={timeout}s",
        timeout=timeout + 10,
    )


def install_kubeshark(cfg: SimulatorConfig):
    ns = cfg.kubeshark.namespace
    if _is_release_installed("kubeshark", ns):
        print(f"[OK] Kubeshark already installed in {ns}")
        return

    print("[INSTALL] Kubeshark...")
    _helm_repo_add("kubeshark", "https://helm.kubeshark.co")
    _run(f"kubectl create namespace {ns} --dry-run=client -o yaml | kubectl apply -f -")
    _run(f"helm install kubeshark kubeshark/kubeshark -n {ns} --wait --timeout 3m")
    _wait_pods_ready(ns)
    print("[OK] Kubeshark installed")


def install_chaos_mesh(cfg: SimulatorConfig):
    ns = cfg.chaos_mesh.namespace
    if _is_release_installed("chaos-mesh", ns):
        print(f"[OK] Chaos Mesh already installed in {ns}")
        return

    print("[INSTALL] Chaos Mesh...")
    _helm_repo_add("chaos-mesh", "https://charts.chaos-mesh.org")
    _run(f"kubectl create namespace {ns} --dry-run=client -o yaml | kubectl apply -f -")
    _run(
        f"helm install chaos-mesh chaos-mesh/chaos-mesh -n {ns} "
        f"--set chaosDaemon.runtime={cfg.chaos_mesh.runtime} "
        f"--set chaosDaemon.socketPath={cfg.chaos_mesh.socket_path} "
        f"--set dashboard.securityMode=false "
        f"--wait --timeout 3m"
    )
    _wait_pods_ready(ns)
    print("[OK] Chaos Mesh installed")


def install_all(cfg: SimulatorConfig, kubeshark: bool = True, chaos_mesh: bool = True):
    if kubeshark:
        install_kubeshark(cfg)
    if chaos_mesh:
        install_chaos_mesh(cfg)


def uninstall_kubeshark(cfg: SimulatorConfig):
    ns = cfg.kubeshark.namespace
    if not _is_release_installed("kubeshark", ns):
        print("[OK] Kubeshark not installed, skipping")
        return
    print("[UNINSTALL] Kubeshark...")
    _run(f"helm uninstall kubeshark -n {ns}")
    _run(f"kubectl delete namespace {ns} --ignore-not-found")
    print("[OK] Kubeshark uninstalled")


def uninstall_chaos_mesh(cfg: SimulatorConfig):
    ns = cfg.chaos_mesh.namespace
    if not _is_release_installed("chaos-mesh", ns):
        print("[OK] Chaos Mesh not installed, skipping")
        return
    print("[UNINSTALL] Chaos Mesh...")
    _run(f"helm uninstall chaos-mesh -n {ns}")
    _run(f"kubectl delete crd -l app.kubernetes.io/instance=chaos-mesh 2>/dev/null || true")
    _run(f"kubectl delete namespace {ns} --ignore-not-found")
    print("[OK] Chaos Mesh uninstalled")


def uninstall_all(cfg: SimulatorConfig):
    uninstall_chaos_mesh(cfg)
    uninstall_kubeshark(cfg)


def verify_installation(cfg: SimulatorConfig) -> dict:
    status = {"kubeshark": False, "chaos_mesh": False}

    result = _run(
        f"kubectl get pods -n {cfg.kubeshark.namespace} -o jsonpath='{{.items[*].status.phase}}' 2>/dev/null",
        check=False,
    )
    if result.returncode == 0 and result.stdout:
        phases = result.stdout.strip().split()
        status["kubeshark"] = all(p == "Running" for p in phases) and len(phases) > 0

    result = _run(
        f"kubectl get pods -n {cfg.chaos_mesh.namespace} -o jsonpath='{{.items[*].status.phase}}' 2>/dev/null",
        check=False,
    )
    if result.returncode == 0 and result.stdout:
        phases = result.stdout.strip().split()
        status["chaos_mesh"] = all(p == "Running" for p in phases) and len(phases) > 0

    return status
