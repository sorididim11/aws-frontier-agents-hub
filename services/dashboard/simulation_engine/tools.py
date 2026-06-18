"""Simulation Engine v2 — Strands @tool definitions for Generator and Verifier agents.

Generator: read-only + submit_scenario
Verifier: read-only + probe (NO execute capability)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from strands.tools import tool

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from verifier_utils import _run_cmd


@dataclass
class AgentContext:
    """Per-agent execution context bound via closure."""
    kubectl_context: str = ""
    profile: str = ""
    region: str = "us-east-1"
    namespace: str = "default"


# ──────────────────────────────────────────────
# Generator Tools (read-only + submit)
# ──────────────────────────────────────────────


def make_generator_tools(ctx: AgentContext, on_event=None):
    """Generator Agent tools: read-only + submit_scenario."""

    _validated_scenario = [None]

    @tool
    def kubectl_query(command: str) -> str:
        """Execute a read-only kubectl command for environment discovery.

        Args:
            command: Full kubectl command (e.g. 'kubectl get deploy -n coins')
        """
        if not command.startswith("kubectl"):
            return "Error: must start with 'kubectl'"
        allowed = ("kubectl get", "kubectl describe", "kubectl logs", "kubectl top",
                   "kubectl auth", "kubectl api-resources", "kubectl explain")
        if not any(command.startswith(p) for p in allowed):
            return f"Error: read-only only. Allowed prefixes: {', '.join(allowed)}"
        cmd = _inject_kubectl_context(command, ctx)
        if on_event:
            on_event("agent_action", {"agent": "generator", "tool": "kubectl_query", "input": command[:200]})
        ok, stdout, stderr = _run_cmd(cmd, timeout=30)
        result = stdout if ok else f"{stdout}\n[ERROR] {stderr}".strip()
        return result[:3000]

    @tool
    def aws_query(command: str) -> str:
        """Execute a read-only AWS CLI command.

        Args:
            command: Full AWS CLI command (e.g. 'aws cloudwatch describe-alarms')
        """
        if not command.startswith("aws "):
            return "Error: must start with 'aws'"
        allowed = ("aws cloudwatch", "aws logs", "aws eks", "aws sts",
                   "aws ec2 describe", "aws elbv2 describe", "aws fis list", "aws fis get",
                   "aws rds describe", "aws lambda get", "aws lambda list",
                   "aws ecs describe", "aws ecs list", "aws dynamodb describe",
                   "aws elasticache describe", "aws autoscaling describe")
        if not any(command.startswith(p) for p in allowed):
            return f"Error: read-only only. Allowed prefixes: {', '.join(allowed)}"
        cmd = _inject_aws_context(command, ctx)
        if on_event:
            on_event("agent_action", {"agent": "generator", "tool": "aws_query", "input": command[:200]})
        ok, stdout, stderr = _run_cmd(cmd, timeout=30)
        result = stdout if ok else f"{stdout}\n[ERROR] {stderr}".strip()
        return result[:3000]

    @tool
    def submit_scenario(scenario_json: str) -> str:
        """Submit generated scenario JSON for L1-L3 validation.
        Returns validation result — fix errors and resubmit if failed.

        Args:
            scenario_json: Complete scenario JSON as string
        """
        if on_event:
            on_event("agent_action", {"agent": "generator", "tool": "submit_scenario", "input": "scenario submitted"})

        try:
            scenario = json.loads(scenario_json) if isinstance(scenario_json, str) else scenario_json
        except json.JSONDecodeError as e:
            return f"JSON 파싱 실패: {e}"

        errors, warnings = _run_l1_l3_validation(scenario, ctx)

        if errors:
            if on_event:
                on_event("validation", {"passed": False, "layer": errors[0].code[:2] if errors[0].code else "L1", "errors": [e.message for e in errors[:5]]})
            feedback_lines = ["검증 실패. 아래 에러를 수정하고 다시 submit_scenario를 호출하세요:\n"]
            for e in errors:
                feedback_lines.append(f"- [{e.code}] {e.message}")
                if e.fix_hint:
                    feedback_lines.append(f"  힌트: {e.fix_hint}")
            if warnings:
                feedback_lines.append("\n경고 (수정 권장):")
                for w in warnings[:3]:
                    feedback_lines.append(f"- [{w.code}] {w.message}")
            return "\n".join(feedback_lines)

        if on_event:
            on_event("validation", {"passed": True, "layer": "L3", "warnings": len(warnings)})
        _validated_scenario[0] = scenario
        if warnings:
            return f"검증 통과 (경고 {len(warnings)}건). 시나리오가 승인되었습니다.\n\n{json.dumps(scenario, ensure_ascii=False)}"
        return f"검증 통과. 시나리오가 승인되었습니다.\n\n{json.dumps(scenario, ensure_ascii=False)}"

    def get_validated_scenario():
        return _validated_scenario[0]

    return [kubectl_query, aws_query, submit_scenario], get_validated_scenario


# ──────────────────────────────────────────────
# Verifier Tools (read-only + probe, NO execute)
# ──────────────────────────────────────────────


def make_verifier_tools(ctx: AgentContext, policy=None, on_event=None):
    """Verifier Agent tools: read-only + probe. NO execute capability."""

    @tool
    def kubectl_query(command: str) -> str:
        """Execute a read-only kubectl command to observe cluster state.

        Args:
            command: Full kubectl command (e.g. 'kubectl get pod -l app=redis -n coins')
        """
        if not command.startswith("kubectl"):
            return "Error: must start with 'kubectl'"
        allowed = ("kubectl get", "kubectl describe", "kubectl logs", "kubectl top",
                   "kubectl auth", "kubectl api-resources", "kubectl explain")
        if not any(command.startswith(p) for p in allowed):
            return f"Error: read-only only. Allowed prefixes: {', '.join(allowed)}"
        cmd = _inject_kubectl_context(command, ctx)
        if on_event:
            on_event("agent_action", {"agent": "verifier", "tool": "kubectl_query", "input": command[:200]})
        ok, stdout, stderr = _run_cmd(cmd, timeout=30)
        result = stdout if ok else f"{stdout}\n[ERROR] {stderr}".strip()
        return result[:3000]

    @tool
    def aws_query(command: str) -> str:
        """Execute a read-only AWS CLI command to observe AWS state.

        Args:
            command: Full AWS CLI command (e.g. 'aws cloudwatch describe-alarms')
        """
        if not command.startswith("aws "):
            return "Error: must start with 'aws'"
        allowed = ("aws cloudwatch", "aws logs", "aws eks", "aws sts",
                   "aws ec2 describe", "aws elbv2 describe", "aws fis list", "aws fis get",
                   "aws rds describe", "aws lambda get", "aws lambda list",
                   "aws ecs describe", "aws ecs list", "aws dynamodb describe",
                   "aws elasticache describe", "aws autoscaling describe")
        if not any(command.startswith(p) for p in allowed):
            return f"Error: read-only only. Allowed prefixes: {', '.join(allowed)}"
        cmd = _inject_aws_context(command, ctx)
        if on_event:
            on_event("agent_action", {"agent": "verifier", "tool": "aws_query", "input": command[:200]})
        ok, stdout, stderr = _run_cmd(cmd, timeout=30)
        result = stdout if ok else f"{stdout}\n[ERROR] {stderr}".strip()
        return result[:3000]

    @tool
    def probe(command: str) -> str:
        """Execute a non-destructive network probe to verify connectivity or endpoint state.
        Allowed: curl, wget, dig, nslookup, nc -z, ping -c

        Args:
            command: Probe command (e.g. 'curl -s -o /dev/null -w %{http_code} http://svc:8080/health')
        """
        allowed = ("curl ", "wget ", "dig ", "nslookup ", "nc -z", "ping -c", "traceroute ")
        if not any(command.strip().startswith(p) for p in allowed):
            return f"Error: only probe commands allowed. Allowed: {', '.join(allowed)}"
        if policy and not policy.validate_probe(command)[0]:
            return f"Error: {policy.validate_probe(command)[1]}"
        if on_event:
            on_event("agent_action", {"agent": "verifier", "tool": "probe", "input": command[:200]})
        ok, stdout, stderr = _run_cmd(command, timeout=30)
        result = stdout if ok else f"{stdout}\n[ERROR] {stderr}".strip()
        return result[:2000]

    @tool
    def wait_seconds(seconds: int) -> str:
        """Wait for effect propagation before checking state.

        Args:
            seconds: Duration to wait (max 60)
        """
        wait = min(max(seconds, 1), 60)
        if on_event:
            on_event("agent_action", {"agent": "verifier", "tool": "wait_seconds", "input": f"{wait}s"})
        time.sleep(wait)
        return f"Waited {wait}s"

    return [kubectl_query, aws_query, probe, wait_seconds]


# ──────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────


def _inject_kubectl_context(command: str, ctx: AgentContext) -> str:
    if ctx.kubectl_context and "--context" not in command:
        return command.replace("kubectl ", f"kubectl --context {ctx.kubectl_context} ", 1)
    return command


def _inject_aws_context(command: str, ctx: AgentContext) -> str:
    cmd = command
    if ctx.profile and "--profile" not in cmd:
        cmd += f" --profile {ctx.profile}"
    if ctx.region and "--region" not in cmd:
        cmd += f" --region {ctx.region}"
    return cmd


def _run_l1_l3_validation(scenario: dict, ctx: AgentContext):
    """Run L1-L3 validators, return (errors, warnings)."""
    from generation.validators.structural import ScenarioStructuralValidator
    from generation.validators.semantic import ResourceExistenceValidator, CloudWatchDimensionValidator
    from generation.validators.feasibility import TimeoutFeasibilityValidator
    from generation.scenario.fixers import ScenarioAutoFixer

    fixer = ScenarioAutoFixer()
    scenario, _fixes = fixer.fix(scenario, {})

    validators = [
        ScenarioStructuralValidator(),
        ResourceExistenceValidator(),
        CloudWatchDimensionValidator(),
        TimeoutFeasibilityValidator(),
    ]

    all_errors = []
    all_warnings = []
    context = {
        "namespace": ctx.namespace,
        "kubectl_context": ctx.kubectl_context,
        "profile": ctx.profile,
        "region": ctx.region,
    }

    for validator in validators:
        result = validator.validate(scenario, context)
        for issue in result.issues:
            if issue.severity == "error":
                all_errors.append(issue)
            else:
                all_warnings.append(issue)
        if all_errors:
            break

    return all_errors, all_warnings
