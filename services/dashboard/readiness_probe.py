"""
Readiness Probe — 시나리오 생성 전 인프라 검증.

시나리오 JSON에서 참조하는 리소스(알람, 서비스, 엔드포인트, 메트릭)를
실제로 조회하여 사용 가능/불가를 판별한다.

결과는 Agent에게 전달할 markdown 테이블로 렌더링되어,
Agent가 "사용 가능" 리소스만 참조하도록 구조적으로 제약한다.
"""
import dataclasses
import json
import os
import subprocess
import time
from typing import Optional


@dataclasses.dataclass
class ResourceStatus:
    resource_type: str  # "alarm" | "service" | "endpoint" | "metric"
    name: str
    ok: bool
    detail: str
    fix_hint: Optional[str] = None


@dataclasses.dataclass
class ReadinessReport:
    ready: bool
    resources: list[ResourceStatus] = dataclasses.field(default_factory=list)
    summary_table: str = ""


def _build_env(aws_profile: str = "", aws_region: str = "us-east-1") -> dict:
    env = {**os.environ, "AWS_PAGER": ""}
    path = env.get("PATH", "")
    for p in ("/opt/homebrew/bin", "/usr/local/bin"):
        if p not in path:
            path = p + ":" + path
    env["PATH"] = path
    if aws_profile:
        env["AWS_PROFILE"] = aws_profile
    if aws_region:
        env["AWS_REGION"] = aws_region
    return env


def _run(cmd: str, env: dict, timeout: int = 15) -> tuple[bool, str, str]:
    try:
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True, timeout=timeout,
            env=env,
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out"
    except Exception as e:
        return False, "", str(e)


def _probe_alarm(alarm_name: str, env: dict) -> ResourceStatus:
    """알람 존재 + dimensions + 최근 5분 datapoints 검증."""
    cmd = (
        f"aws cloudwatch describe-alarms --alarm-names '{alarm_name}'"
        f" --query 'MetricAlarms[0].{{Namespace:Namespace,MetricName:MetricName,"
        f"Dimensions:Dimensions,Statistic:Statistic,State:StateValue,"
        f"Threshold:Threshold,Period:Period}}'"
        f" --output json"
    )
    ok, stdout, _ = _run(cmd, env)
    if not ok or not stdout or stdout == "null":
        return ResourceStatus(
            resource_type="alarm", name=alarm_name, ok=False,
            detail="알람 존재하지 않음",
            fix_hint=f"CloudWatch 알람 '{alarm_name}' 생성 필요",
        )

    alarm_detail = json.loads(stdout)
    ns = alarm_detail.get("Namespace", "")
    metric = alarm_detail.get("MetricName", "")
    dims = alarm_detail.get("Dimensions", [])
    state = alarm_detail.get("State", "UNKNOWN")
    threshold = alarm_detail.get("Threshold", "?")
    period = alarm_detail.get("Period", "?")

    dim_str = ", ".join(f"{d['Name']}={d['Value']}" for d in dims)
    dim_args = " ".join(f"'Name={d['Name']},Value={d['Value']}'" for d in dims)

    cmd2 = (
        f"aws cloudwatch get-metric-statistics"
        f" --namespace '{ns}' --metric-name '{metric}'"
        f" --dimensions {dim_args}"
        f" --start-time $(date -u -v-5M '+%Y-%m-%dT%H:%M:%S' 2>/dev/null || date -u -d '5 minutes ago' '+%Y-%m-%dT%H:%M:%S')"
        f" --end-time $(date -u '+%Y-%m-%dT%H:%M:%S')"
        f" --period 60 --statistics Average"
        f" --query 'length(Datapoints)' --output text"
    )
    ok2, stdout2, _ = _run(cmd2, env)
    datapoints = int(stdout2.strip()) if ok2 and stdout2.strip().isdigit() else 0

    detail = f"{metric}, Dims=[{dim_str}], State={state}, Threshold={threshold}, Period={period}s, Datapoints(5min)={datapoints}"

    if datapoints == 0:
        target_svc = ""
        for d in dims:
            if d["Name"] == "Service":
                target_svc = d["Value"]
                break
        fix = "kubectl rollout restart deployment/" + (target_svc or "TARGET") + " (OTEL 미계측 또는 트래픽 없음)"
        return ResourceStatus(
            resource_type="alarm", name=alarm_name, ok=False,
            detail=detail, fix_hint=fix,
        )

    return ResourceStatus(resource_type="alarm", name=alarm_name, ok=True, detail=detail)


def _probe_service(service_name: str, namespace: str, kubectl_context: str, env: dict) -> ResourceStatus:
    """서비스 Pod Running 확인."""
    ctx_flag = f"--context {kubectl_context} " if kubectl_context else ""
    cmd = f"kubectl {ctx_flag}-n {namespace} get pods -l app={service_name} -o jsonpath='{{.items[*].status.phase}}'"
    ok, stdout, stderr = _run(cmd, env, timeout=10)

    if not ok:
        return ResourceStatus(
            resource_type="service", name=service_name, ok=False,
            detail=f"kubectl 실패: {stderr}",
            fix_hint=f"kubectl {ctx_flag}-n {namespace} get pods 확인",
        )

    if "Running" not in stdout:
        return ResourceStatus(
            resource_type="service", name=service_name, ok=False,
            detail=f"Pod 상태: {stdout or '없음'}",
            fix_hint=f"kubectl {ctx_flag}-n {namespace} rollout restart deployment/{service_name}",
        )

    pod_count = len([p for p in stdout.split() if p == "Running"])
    return ResourceStatus(
        resource_type="service", name=service_name, ok=True,
        detail=f"Running ({pod_count} pod{'s' if pod_count > 1 else ''})",
    )


def _probe_endpoint(service_name: str, namespace: str, kubectl_context: str,
                    port: int, env: dict) -> ResourceStatus:
    """port-forward + curl로 endpoint 도달 확인."""
    ctx_flag = f"--context {kubectl_context} " if kubectl_context else ""
    local_port = 28000 + abs(hash(service_name)) % 1000

    pf_cmd = ["kubectl"]
    if kubectl_context:
        pf_cmd.extend(["--context", kubectl_context])
    pf_cmd.extend(["-n", namespace, "port-forward", f"svc/{service_name}", f"{local_port}:{port}"])

    pf_proc = None
    try:
        pf_proc = subprocess.Popen(
            pf_cmd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        # port-forward가 연결될 때까지 대기 (최대 5초)
        for _ in range(10):
            time.sleep(0.5)
            if pf_proc.poll() is not None:
                break
            # 연결 시도
            curl_cmd = f"curl -s -o /dev/null -w '%{{http_code}}' --connect-timeout 1 http://localhost:{local_port}/"
            ok, stdout_c, _ = _run(curl_cmd, env, timeout=3)
            code = stdout_c.strip().strip("'") if ok else "000"
            if code != "000":
                break
        else:
            code = "000"

        if pf_proc.poll() is not None:
            _, stderr_bytes = pf_proc.communicate(timeout=1)
            return ResourceStatus(
                resource_type="endpoint", name=f"{service_name}:{port}", ok=False,
                detail=f"port-forward 실패: {stderr_bytes.decode()[:100]}",
                fix_hint=f"kubectl {ctx_flag}-n {namespace} get svc/{service_name} 확인",
            )

        if code == "000":
            return ResourceStatus(
                resource_type="endpoint", name=f"{service_name}:{port}", ok=False,
                detail="endpoint 도달 불가 (connection refused)",
                fix_hint=f"Pod가 port {port}에서 listen 중인지 확인",
            )

        # /inject-latency 지원 여부 확인
        has_inject = False
        inject_cmd = f"curl -s -o /dev/null -w '%{{http_code}}' --connect-timeout 2 http://localhost:{local_port}/inject-latency?seconds=0"
        ok_inj, stdout_inj, _ = _run(inject_cmd, env, timeout=5)
        inj_code = stdout_inj.strip().strip("'") if ok_inj else "000"
        has_inject = inj_code not in ("000", "404")

        detail = f"HTTP {code}, /inject-latency={'지원' if has_inject else '미지원'}"
        return ResourceStatus(
            resource_type="endpoint", name=f"{service_name}:{port}", ok=True,
            detail=detail,
        )
    except Exception as e:
        return ResourceStatus(
            resource_type="endpoint", name=f"{service_name}:{port}", ok=False,
            detail=f"probe 오류: {str(e)[:80]}",
            fix_hint="port-forward 또는 curl 실행 오류",
        )
    finally:
        if pf_proc and pf_proc.poll() is None:
            pf_proc.terminate()
            try:
                pf_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pf_proc.kill()


def _render_summary_table(resources: list[ResourceStatus]) -> str:
    """Agent에게 전달할 markdown 테이블 생성."""
    available = [r for r in resources if r.ok]
    blocked = [r for r in resources if not r.ok]

    lines = ["## 검증된 인프라 리소스\n"]

    if available:
        alarm_avail = [r for r in available if r.resource_type == "alarm"]
        svc_avail = [r for r in available if r.resource_type in ("service", "endpoint")]

        if alarm_avail:
            lines.append("### 알람 (사용 가능 ✓)")
            lines.append("| 알람명 | 상세 |")
            lines.append("|--------|------|")
            for r in alarm_avail:
                lines.append(f"| {r.name} | {r.detail} |")
            lines.append("")

        if svc_avail:
            lines.append("### 서비스/엔드포인트 (사용 가능 ✓)")
            lines.append("| 이름 | 유형 | 상태 |")
            lines.append("|------|------|------|")
            for r in svc_avail:
                lines.append(f"| {r.name} | {r.resource_type} | {r.detail} |")
            lines.append("")

    if blocked:
        lines.append("### 사용 불가 ✗ (아래 리소스는 steps.py에서 참조 금지)")
        lines.append("| 이름 | 유형 | 문제 | Fix |")
        lines.append("|------|------|------|-----|")
        for r in blocked:
            lines.append(f"| {r.name} | {r.resource_type} | {r.detail} | {r.fix_hint or '-'} |")
        lines.append("")

    if not available and not blocked:
        lines.append("(검증 대상 리소스 없음)")

    return "\n".join(lines)


@dataclasses.dataclass
class FeasibilityResult:
    feasible: bool
    reason: str
    max_achievable: Optional[float] = None
    threshold: Optional[float] = None
    suggestion: Optional[str] = None


def _probe_feasibility(scenario: dict, env: dict, kubectl_context: str) -> Optional[FeasibilityResult]:
    """알람+FIS 조합의 실현 가능성 산술 검증."""
    trigger = scenario.get("trigger", {})
    if trigger.get("type") != "fis":
        return None

    # FIS 템플릿 ID 추출
    cmd_str = trigger.get("command", "")
    import re as _re
    m = _re.search(r'--experiment-template-id\s+(EXT\w+)', cmd_str)
    if not m:
        return None
    template_id = m.group(1)

    # 알람 이름 추출
    alarms = scenario.get("verification", {}).get("alarms", [])
    if not alarms:
        return None
    alarm_name = alarms[0].get("name", "") if isinstance(alarms[0], dict) else alarms[0]
    if not alarm_name:
        return None

    # 1. 알람 full spec
    cmd = (
        f"aws cloudwatch describe-alarms --alarm-names '{alarm_name}'"
        f" --query 'MetricAlarms[0].{{Namespace:Namespace,MetricName:MetricName,"
        f"Dimensions:Dimensions,Threshold:Threshold,Period:Period,"
        f"EvaluationPeriods:EvaluationPeriods,ComparisonOperator:ComparisonOperator}}'"
        f" --output json"
    )
    ok, stdout, _ = _run(cmd, env)
    if not ok or not stdout or stdout == "null":
        return None
    alarm_spec = json.loads(stdout)

    threshold = float(alarm_spec.get("Threshold", 0))
    period = int(alarm_spec.get("Period", 300))
    eval_periods = int(alarm_spec.get("EvaluationPeriods", 1))
    dimensions = alarm_spec.get("Dimensions", [])
    metric_name = alarm_spec.get("MetricName", "")
    namespace_cw = alarm_spec.get("Namespace", "")

    required_duration = period * eval_periods

    # 2. FIS 템플릿 상세
    cmd2 = (
        f"aws fis get-experiment-template --id {template_id}"
        f" --query 'experimentTemplate.{{targets:targets,actions:actions}}'"
        f" --output json"
    )
    ok2, stdout2, _ = _run(cmd2, env, timeout=15)
    if not ok2 or not stdout2 or stdout2 == "null":
        return FeasibilityResult(
            feasible=False, reason=f"FIS 템플릿 '{template_id}' 조회 실패",
            suggestion="FIS 템플릿 ID 확인 필요")

    fis_detail = json.loads(stdout2)
    targets = fis_detail.get("targets", {})
    actions = fis_detail.get("actions", {})

    # SelectionMode
    selection_mode = "UNKNOWN"
    for t_val in targets.values():
        selection_mode = t_val.get("selectionMode", "UNKNOWN")
        break

    # Action duration (ISO 8601 duration → seconds)
    fis_duration = 0
    for a_val in actions.values():
        params = a_val.get("parameters", {})
        dur_str = params.get("duration", "")
        doc_params = params.get("documentParameters", "")
        if doc_params:
            try:
                dp = json.loads(doc_params)
                fis_duration = max(fis_duration, int(dp.get("DurationSeconds", 0)))
            except (json.JSONDecodeError, ValueError):
                pass
        if not fis_duration and dur_str:
            m2 = _re.search(r'PT(\d+)M', dur_str)
            if m2:
                fis_duration = int(m2.group(1)) * 60

    # 3. 노드 수 (ContainerInsights 메트릭인 경우)
    node_count = 0
    is_node_metric = namespace_cw == "ContainerInsights" and "node_" in metric_name
    is_cluster_avg = any(d.get("Name") == "ClusterName" for d in dimensions) and \
                     not any(d.get("Name") == "NodeName" for d in dimensions)

    if is_node_metric and is_cluster_avg and kubectl_context:
        cmd3 = f"kubectl --context {kubectl_context} get nodes --no-headers 2>/dev/null | wc -l"
        ok3, stdout3, _ = _run(cmd3, env, timeout=10)
        if ok3 and stdout3.strip().isdigit():
            node_count = int(stdout3.strip())

    # 4. 산술 검증
    issues = []

    # Duration 검증
    if fis_duration > 0 and fis_duration < required_duration:
        issues.append(
            f"FIS duration({fis_duration}s) < 알람 evaluation window({required_duration}s = {period}s×{eval_periods})")

    # 노드 평균 vs target count 검증
    max_achievable = None
    if is_node_metric and is_cluster_avg and node_count > 0:
        target_count = node_count  # default: ALL
        m3 = _re.search(r'COUNT\((\d+)\)', selection_mode)
        if m3:
            target_count = int(m3.group(1))
        elif "PERCENT" in selection_mode:
            m4 = _re.search(r'PERCENT\((\d+)\)', selection_mode)
            if m4:
                target_count = max(1, node_count * int(m4.group(1)) // 100)

        idle_cpu = 5.0
        stressed_cpu = 98.0
        max_achievable = (target_count * stressed_cpu + (node_count - target_count) * idle_cpu) / node_count

        if max_achievable < threshold:
            issues.append(
                f"노드 {node_count}개 중 {target_count}개 stress → 클러스터 평균 최대 {max_achievable:.0f}% < threshold {threshold}%")

    if issues:
        suggestion_parts = []
        if max_achievable is not None and max_achievable < threshold:
            suggestion_parts.append(f"FIS SelectionMode=ALL 또는 알람 threshold를 {max_achievable - 5:.0f}% 이하로 조정")
        if fis_duration > 0 and fis_duration < required_duration:
            suggestion_parts.append(f"FIS duration을 {required_duration + 120}초 이상으로 증가")

        return FeasibilityResult(
            feasible=False,
            reason="; ".join(issues),
            max_achievable=max_achievable,
            threshold=threshold,
            suggestion=" / ".join(suggestion_parts) if suggestion_parts else None,
        )

    return FeasibilityResult(feasible=True, reason="산술 검증 통과", max_achievable=max_achievable, threshold=threshold)


def _render_feasibility(result: Optional[FeasibilityResult]) -> str:
    if result is None:
        return ""
    lines = ["\n## 실현 가능성 분석\n"]
    status = "✓ 가능" if result.feasible else "✗ 불가능"
    lines.append(f"| 항목 | 값 |")
    lines.append(f"|------|---|")
    lines.append(f"| **판정** | {status} |")
    lines.append(f"| **이유** | {result.reason} |")
    if result.max_achievable is not None:
        lines.append(f"| 달성 가능 최대 | {result.max_achievable:.0f}% |")
    if result.threshold is not None:
        lines.append(f"| Threshold | {result.threshold}% |")
    if result.suggestion:
        lines.append(f"| **대안** | {result.suggestion} |")
    lines.append("")
    if not result.feasible:
        lines.append("**⚠️ 이 시나리오는 현재 구성으로는 물리적으로 실현 불가능합니다. steps.py 생성을 거부하거나 대안을 적용하세요.**\n")
    return "\n".join(lines)


def probe_scenario_readiness(
    scenario: dict,
    aws_profile: str = "",
    aws_region: str = "us-east-1",
    namespace: str = "dockercoins",
    kubectl_context: str = "",
    service_port: int = 80,
) -> ReadinessReport:
    """시나리오 JSON에서 참조하는 리소스를 추출하고 각각 검증."""
    env = _build_env(aws_profile, aws_region)
    resources: list[ResourceStatus] = []

    # 1. 알람 검증
    alarms = []
    verification = scenario.get("verification", {})
    for alarm_def in verification.get("alarms", []):
        name = alarm_def.get("name", "")
        if name:
            alarms.append(name)

    for alarm_name in alarms:
        resources.append(_probe_alarm(alarm_name, env))

    # 2. 서비스 검증
    target_service = scenario.get("target_service", "")
    if target_service:
        resources.append(_probe_service(target_service, namespace, kubectl_context, env))
        resources.append(_probe_endpoint(target_service, namespace, kubectl_context, service_port, env))

    # 3. Feasibility 분석
    feasibility = _probe_feasibility(scenario, env, kubectl_context)

    # 4. 결과 집계
    all_ok = all(r.ok for r in resources) if resources else True
    if feasibility and not feasibility.feasible:
        all_ok = False
    summary = _render_summary_table(resources) + _render_feasibility(feasibility)

    return ReadinessReport(ready=all_ok, resources=resources, summary_table=summary)
