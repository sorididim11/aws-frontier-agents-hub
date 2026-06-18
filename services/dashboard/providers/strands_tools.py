"""Strands @tool wrappers — delegates to tool_executor.py safety whitelist.

Two modes:
  1. make_tools(ctx: AccountContext) — per-agent isolated tools (preferred)
  2. configure() + READONLY_TOOLS — legacy global context (deprecated)
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from strands.tools import tool
from providers.tool_executor import execute_tool


# ---------------------------------------------------------------------------
# NEW: Per-agent tool factory (AccountContext-based isolation)
# ---------------------------------------------------------------------------

def make_tools(ctx: "AccountContext") -> list:
    """Create tool instances with AccountContext bound via closure.

    Each agent gets its own tool set — no shared mutable state.
    """
    @tool
    def kubectl_exec(command: str) -> str:
        """Execute a read-only kubectl command against the EKS cluster. Returns stdout/stderr.

        Use for querying deployments, pods, services, configmaps, logs, etc.
        Allowed: kubectl get, describe, logs, top, api-resources, explain, auth.

        Args:
            command: Full kubectl command (e.g. 'kubectl get pods -n dockercoins -o wide')
        """
        return execute_tool("kubectl_exec", {"command": command},
                            context=ctx.kubectl_context,
                            profile=ctx.profile,
                            region=ctx.region)

    @tool
    def aws_cli_exec(command: str) -> str:
        """Execute a read-only AWS CLI command. Returns stdout/stderr.

        Use for querying CloudWatch alarms, metrics, logs, EKS info, etc.
        Allowed: aws cloudwatch, logs, eks, sts, ec2 describe, elbv2 describe, iam list/get, s3 ls, dynamodb describe/scan/query, fis list/get.

        Args:
            command: Full AWS CLI command (e.g. 'aws cloudwatch describe-alarms --alarm-names my-alarm --region us-east-1')
        """
        return execute_tool("aws_cli_exec", {"command": command},
                            context=ctx.kubectl_context,
                            profile=ctx.profile,
                            region=ctx.region)

    @tool
    def read_file(path: str) -> str:
        """Read the contents of a file from the project directory.

        Use for reading scenario definitions, configuration files, source code, etc.

        Args:
            path: Relative path from the project root (e.g. 'services/dashboard/failure_modes.py')
        """
        return execute_tool("read_file", {"path": path})

    return [kubectl_exec, aws_cli_exec, read_file]


# ---------------------------------------------------------------------------
# LEGACY: Global context (deprecated — use make_tools instead)
# Kept for backward compatibility during migration.
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_context = {"kubectl_context": "", "profile": "", "region": "us-east-1"}


def configure(kubectl_context: str = "", profile: str = "", region: str = "us-east-1"):
    """Set runtime context for tool execution. Thread-safe.

    DEPRECATED: Use make_tools(AccountContext(...)) for per-agent isolation.
    """
    with _lock:
        _context["kubectl_context"] = kubectl_context
        _context["profile"] = profile
        _context["region"] = region


def _ctx():
    with _lock:
        return dict(_context)


@tool
def kubectl_exec(command: str) -> str:
    """Execute a read-only kubectl command against the EKS cluster. Returns stdout/stderr.

    Use for querying deployments, pods, services, configmaps, logs, etc.
    Allowed: kubectl get, describe, logs, top, api-resources, explain, auth.

    Args:
        command: Full kubectl command (e.g. 'kubectl get pods -n dockercoins -o wide')
    """
    ctx = _ctx()
    return execute_tool("kubectl_exec", {"command": command},
                        context=ctx["kubectl_context"],
                        profile=ctx["profile"],
                        region=ctx["region"])


@tool
def aws_cli_exec(command: str) -> str:
    """Execute a read-only AWS CLI command. Returns stdout/stderr.

    Use for querying CloudWatch alarms, metrics, logs, EKS info, etc.
    Allowed: aws cloudwatch, logs, eks, sts, ec2 describe, elbv2 describe, iam list/get, s3 ls, dynamodb describe/scan/query, fis list/get.

    Args:
        command: Full AWS CLI command (e.g. 'aws cloudwatch describe-alarms --alarm-names my-alarm --region us-east-1')
    """
    ctx = _ctx()
    return execute_tool("aws_cli_exec", {"command": command},
                        context=ctx["kubectl_context"],
                        profile=ctx["profile"],
                        region=ctx["region"])


@tool
def read_file(path: str) -> str:
    """Read the contents of a file from the project directory.

    Use for reading scenario definitions, configuration files, source code, etc.

    Args:
        path: Relative path from the project root (e.g. 'services/dashboard/failure_modes.py')
    """
    return execute_tool("read_file", {"path": path})


READONLY_TOOLS = [kubectl_exec, aws_cli_exec, read_file]
