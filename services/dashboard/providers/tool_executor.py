"""Tool executor for Bedrock tool_use loop — kubectl, aws cli, file read."""
import json as _json
import os
import subprocess

MAX_TOOL_RESULT_CHARS = 3000

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

_KUBECTL_PREFIX_WHITELIST = (
    "kubectl get", "kubectl describe", "kubectl logs",
    "kubectl top", "kubectl api-resources", "kubectl explain",
    "kubectl auth",
)

_AWS_PREFIX_WHITELIST = (
    "aws cloudwatch", "aws logs", "aws eks", "aws sts",
    "aws ec2 describe", "aws elbv2 describe", "aws iam list",
    "aws iam get", "aws s3 ls", "aws dynamodb describe",
    "aws dynamodb scan", "aws dynamodb query",
    "aws fis list", "aws fis get",
)

_BLOCKED_PATTERNS = (
    "rm ", "delete", "terminate", "put-", "create-",
    "update-", "modify-", "| bash", "; rm", "&& rm",
)


def _cmd_env():
    env = {**os.environ, "AWS_PAGER": ""}
    path = env.get("PATH", "")
    for p in ("/opt/homebrew/bin", "/usr/local/bin"):
        if p not in path:
            path = p + ":" + path
    env["PATH"] = path
    return env


def _summarize_output(tool_name: str, raw_output: str) -> str:
    if len(raw_output) <= MAX_TOOL_RESULT_CHARS:
        return raw_output
    if tool_name == "kubectl_exec":
        lines = raw_output.split("\n")
        summary = "\n".join(lines[:25])
        if len(lines) > 25:
            summary += f"\n... ({len(lines)} lines total, truncated)"
        return summary[:MAX_TOOL_RESULT_CHARS]
    if tool_name == "aws_cli_exec":
        stripped = raw_output.strip()
        if stripped.startswith("[") or stripped.startswith("{"):
            try:
                data = _json.loads(stripped)
                compact = _json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                return compact[:MAX_TOOL_RESULT_CHARS]
            except _json.JSONDecodeError:
                pass
        return raw_output[:MAX_TOOL_RESULT_CHARS] + "\n... (truncated)"
    return raw_output[:MAX_TOOL_RESULT_CHARS] + "\n... (truncated)"


def execute_tool(tool_name: str, tool_input: dict, context: str = "",
                 profile: str = "", region: str = "") -> str:
    if tool_name == "kubectl_exec":
        raw = _exec_kubectl(tool_input.get("command", ""), context)
    elif tool_name == "aws_cli_exec":
        raw = _exec_aws(tool_input.get("command", ""), profile, region)
    elif tool_name == "read_file":
        raw = _exec_read_file(tool_input.get("path", ""))
    else:
        return f"Unknown tool: {tool_name}"
    return _summarize_output(tool_name, raw)


def _exec_kubectl(command: str, context: str = "") -> str:
    if not command.startswith("kubectl"):
        return "Error: command must start with 'kubectl'"

    if not any(command.startswith(p) for p in _KUBECTL_PREFIX_WHITELIST):
        return f"Error: command not in read-only whitelist. Allowed prefixes: {_KUBECTL_PREFIX_WHITELIST}"

    for pat in _BLOCKED_PATTERNS:
        if pat in command:
            return f"Error: blocked pattern '{pat}' detected"

    if context:
        command = command.replace("kubectl ", f"kubectl --context {context} ", 1)
    else:
        try:
            import sys
            sys.path.insert(0, os.path.join(_PROJECT_ROOT, "services", "dashboard"))
            import cluster_manager
            command = cluster_manager.inject_context(command)
        except (ImportError, Exception):
            pass

    return _run(command)


def _exec_aws(command: str, profile: str = "", region: str = "") -> str:
    if not command.startswith("aws "):
        return "Error: command must start with 'aws'"

    if not any(command.startswith(p) for p in _AWS_PREFIX_WHITELIST):
        return f"Error: command not in read-only whitelist. Allowed prefixes: {_AWS_PREFIX_WHITELIST}"

    for pat in _BLOCKED_PATTERNS:
        if pat in command:
            return f"Error: blocked pattern '{pat}' detected"

    if profile and "--profile" not in command:
        command += f" --profile {profile}"
    if region and "--region" not in command:
        command += f" --region {region}"

    return _run(command)


def _exec_read_file(path: str) -> str:
    if ".." in path or path.startswith("/"):
        return "Error: path must be relative and cannot contain '..'"

    full_path = os.path.join(_PROJECT_ROOT, path)
    if not os.path.isfile(full_path):
        return f"Error: file not found: {path}"

    try:
        with open(full_path, encoding="utf-8") as f:
            content = f.read(50000)
        if len(content) >= 50000:
            content += "\n... (truncated at 50KB)"
        return content
    except Exception as e:
        return f"Error reading file: {e}"


def _run(command: str, timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            ["bash", "-c", command],
            capture_output=True, text=True, timeout=timeout,
            env=_cmd_env(),
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            err = result.stderr.strip()
            output = f"{output}\n[exit_code={result.returncode}] {err}".strip()
        return output[:20000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out ({timeout}s)"
    except Exception as e:
        return f"Error: {e}"
