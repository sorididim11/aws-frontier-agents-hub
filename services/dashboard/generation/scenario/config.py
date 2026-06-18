"""Scenario generation config — validators, fixers, schema 조립."""

from __future__ import annotations

import json
import os
import subprocess

from generation.harness import GenerationHarness
from generation.types import GenerationConfig
from generation.validators.structural import ScenarioStructuralValidator
from generation.validators.semantic import CloudWatchDimensionValidator, ResourceExistenceValidator
from generation.validators.feasibility import TimeoutFeasibilityValidator
from generation.scenario.schema import SUBMIT_SCENARIO_TOOL
from generation.scenario.fixers import ScenarioAutoFixer


def create_scenario_harness(provider, system_prompt: str = "") -> GenerationHarness:
    """시나리오 생성용 하네스 생성."""
    if not system_prompt:
        from providers.system_prompts import SCENARIO_GEN_TOOL
        system_prompt = SCENARIO_GEN_TOOL

    try:
        from providers.tools import DEVOPS_TOOLS
        additional_tools = DEVOPS_TOOLS
    except ImportError:
        additional_tools = []

    config = GenerationConfig(
        submit_tool_name="submit_scenario",
        submit_tool_schema=SUBMIT_SCENARIO_TOOL,
        validators=[
            ScenarioStructuralValidator(),
            ResourceExistenceValidator(),
            CloudWatchDimensionValidator(),
            TimeoutFeasibilityValidator(),
        ],
        fixers=[ScenarioAutoFixer()],
        additional_tools=additional_tools,
        system_prompt=system_prompt,
        max_rounds=5,
        force_accept_remaining=1,
    )
    return GenerationHarness(config, provider)


def build_generation_context(
    kubectl_context: str = "",
    namespace: str = "dockercoins",
    aws_profile: str = "",
    aws_region: str = "us-east-1",
) -> dict:
    """생성 시작 전 환경 상태 수집 (1회).

    Fallback chain으로 context를 resolve:
    1) 전달받은 kubectl_context 사용
    2) cluster_manager에서 primary cluster resolve
    3) ARN에서 cluster alias 추출하여 재시도
    4) context 없이 current-context 사용 (최종 fallback)
    """
    resolved_context = kubectl_context
    resolved_profile = aws_profile

    try:
        import cluster_manager
        clusters = cluster_manager.get_clusters()
        if clusters:
            primary = clusters[0]
            resolved_context = resolved_context or primary["name"]
            resolved_profile = resolved_profile or primary["profile"]
    except Exception:
        pass

    ctx = {
        "kubectl_context": resolved_context,
        "namespace": namespace,
        "aws_profile": resolved_profile,
        "aws_region": aws_region,
        "available_deployments": [],
        "available_alarms": [],
    }

    env = _build_env(resolved_profile, aws_region)

    # Deployments — fallback chain
    deployments = _discover_deployments(resolved_context, namespace, env)
    if not deployments and resolved_context:
        alias = _extract_cluster_alias(resolved_context)
        if alias != resolved_context:
            deployments = _discover_deployments(alias, namespace, env)
        if not deployments:
            deployments = _discover_deployments("", namespace, env)
    ctx["available_deployments"] = deployments

    # Alarms
    cmd = (
        f"aws cloudwatch describe-alarms --query 'MetricAlarms[*].AlarmName' --output json"
    )
    ok, out = _run(cmd, env)
    if ok and out:
        try:
            ctx["available_alarms"] = json.loads(out)
        except json.JSONDecodeError:
            pass

    return ctx


def _discover_deployments(context: str, namespace: str, env: dict) -> list[str]:
    """kubectl로 deployment 목록 조회. 실패 시 빈 리스트."""
    ctx_flag = f"--context {context} " if context else ""
    cmd = f"kubectl {ctx_flag}-n {namespace} get deploy -o jsonpath='{{.items[*].metadata.name}}'"
    ok, out = _run(cmd, env)
    if ok and out:
        return out.strip("'").split()
    return []


def _extract_cluster_alias(arn_or_context: str) -> str:
    """ARN에서 cluster 이름 추출. ARN이 아니면 원본 반환."""
    if "/" in arn_or_context:
        return arn_or_context.split("/")[-1]
    return arn_or_context


def _build_env(profile: str = "", region: str = "") -> dict:
    env = {**os.environ, "AWS_PAGER": ""}
    path = env.get("PATH", "")
    for p in ("/opt/homebrew/bin", "/usr/local/bin"):
        if p not in path:
            path = p + ":" + path
    env["PATH"] = path
    if profile:
        env["AWS_PROFILE"] = profile
    if region:
        env["AWS_REGION"] = region
    return env


def _run(cmd: str, env: dict, timeout: int = 15) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        return result.returncode == 0, result.stdout.strip()
    except (subprocess.TimeoutExpired, Exception):
        return False, ""
