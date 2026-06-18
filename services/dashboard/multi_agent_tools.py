"""Multi-Agent Engine tools — @tool definitions for Generator and Verifier agents."""
import os
import sys
import time
import threading

sys.path.insert(0, os.path.dirname(__file__))

from strands.tools import tool
from verifier_utils import _run_cmd

_lock = threading.Lock()
_context = {"kubectl_context": "", "profile": "", "region": "us-east-1", "namespace": "default"}


def configure(kubectl_context: str = "", profile: str = "", region: str = "us-east-1",
              namespace: str = "default"):
    with _lock:
        _context["kubectl_context"] = kubectl_context
        _context["profile"] = profile
        _context["region"] = region
        _context["namespace"] = namespace


def _ctx():
    with _lock:
        return dict(_context)


@tool
def kubectl_query(command: str) -> str:
    """Execute a read-only kubectl command. Returns stdout/stderr.

    Args:
        command: Full kubectl command (e.g. 'kubectl get pods -n dockercoins')
    """
    if not command.startswith("kubectl"):
        return "Error: must start with 'kubectl'"
    allowed = ("kubectl get", "kubectl describe", "kubectl logs", "kubectl top",
               "kubectl auth", "kubectl api-resources", "kubectl explain")
    if not any(command.startswith(p) for p in allowed):
        return f"Error: read-only only. Allowed: {allowed}"
    ctx = _ctx()
    if ctx["kubectl_context"]:
        command = command.replace("kubectl ", f"kubectl --context {ctx['kubectl_context']} ", 1)
    ok, stdout, stderr = _run_cmd(command, timeout=30)
    result = stdout if ok else f"{stdout}\n[ERROR] {stderr}".strip()
    return result[:3000]


@tool
def aws_query(command: str) -> str:
    """Execute a read-only AWS CLI command. Returns stdout/stderr.

    Args:
        command: Full AWS CLI command (e.g. 'aws cloudwatch describe-alarms')
    """
    if not command.startswith("aws "):
        return "Error: must start with 'aws'"
    allowed = ("aws cloudwatch", "aws logs", "aws eks", "aws sts",
               "aws ec2 describe", "aws elbv2 describe", "aws fis list", "aws fis get")
    if not any(command.startswith(p) for p in allowed):
        return f"Error: read-only only. Allowed: {allowed}"
    ctx = _ctx()
    if ctx["profile"] and "--profile" not in command:
        command += f" --profile {ctx['profile']}"
    if ctx["region"] and "--region" not in command:
        command += f" --region {ctx['region']}"
    ok, stdout, stderr = _run_cmd(command, timeout=30)
    result = stdout if ok else f"{stdout}\n[ERROR] {stderr}".strip()
    return result[:3000]


@tool
def check_command(command: str, expected: str) -> str:
    """Execute a command and check if output contains expected pattern.

    Args:
        command: Shell command to execute
        expected: Substring expected in stdout (pipe-separated for OR: 'error|timeout')
    """
    ctx = _ctx()
    if ctx["kubectl_context"] and "kubectl" in command and "--context" not in command:
        command = command.replace("kubectl ", f"kubectl --context {ctx['kubectl_context']} ", 1)
    if ctx["profile"] and "aws " in command and "--profile" not in command:
        command += f" --profile {ctx['profile']}"
    ok, stdout, stderr = _run_cmd(command, timeout=30)
    output = stdout if ok else f"{stdout} {stderr}"
    patterns = [p.strip() for p in expected.split("|")]
    for p in patterns:
        if p and p in output:
            return f"MATCH: found '{p}' in output. Full: {output[:500]}"
    return f"NO_MATCH: expected='{expected}' actual={output[:500]}"


@tool
def wait_seconds(seconds: int) -> str:
    """Wait for a specified duration.

    Args:
        seconds: How long to wait (max 60)
    """
    wait = min(max(seconds, 1), 60)
    time.sleep(wait)
    return f"Waited {wait}s"


# Tool sets
GENERATOR_TOOLS = [kubectl_query, aws_query]
VERIFIER_TOOLS = [kubectl_query, aws_query, check_command, wait_seconds]
