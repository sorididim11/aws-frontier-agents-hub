#!/usr/bin/env python3
"""시나리오 생성 통합 테스트 — 실제 Agent 채팅 경로 사용.

실제 사용자 플로우를 테스트:
  POST /api/scenario-chat → ChatWorker → Agent Space → 시나리오 JSON

각 failure mode에 대해:
1. /api/scenario-chat으로 시나리오 생성 요청
2. 응답에서 시나리오 JSON 추출 + 구조 검증
3. Infra 검증 (FIS 존재, 알람 존재, kubectl context)
4. Feasibility 검증 (산술적 실현 가능성)
5. 결과 리포트

Usage:
    python3 test_scenario_generation.py [--modes FM-04,FM-08] [--save]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/dashboard"))

from failure_modes import FAILURE_MODES

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_URL = "http://localhost:5003"
SPACE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
AWS_REGION = "us-east-1"

CLUSTER_INFO = {
    "primary": {
        "context": "arn:aws:eks:us-east-1:111111111111:cluster/devops-agent-test-cluster",
        "name": "devops-agent-test-cluster",
        "profile": "member1-acc",
        "account_id": "111111111111",
    },
    "member2": {
        "context": "devops-agent-test-m2-cluster",
        "name": "devops-agent-test-m2-cluster",
        "profile": "member2-acc",
        "account_id": "222222222222",
    },
}

ACTIVE_CLUSTER = CLUSTER_INFO["member2"]

TARGET_MAP = {
    "FM-01": {"service": "hasher", "message": "hasher 서비스의 Security Group에서 worker→hasher 포트를 차단하여 네트워크 격리 시나리오를 생성해줘"},
    "FM-02": {"service": "worker", "message": "EKS 노드 하나를 종료하여 worker pod의 재스케줄링을 검증하는 시나리오를 생성해줘"},
    "FM-03": {"service": "rng", "message": "rng 서비스가 의존하는 RDS 접근을 차단하여 DB 의존성 장애를 시뮬레이션하는 시나리오를 생성해줘"},
    "FM-04": {"service": "worker", "message": "EKS 노드에 CPU stress를 주입하여 리소스 압박 시나리오를 생성해줘. FIS CPU stress 템플릿을 사용해"},
    "FM-05": {"service": "worker", "message": "worker의 IRSA role에 explicit deny를 추가하여 IAM 권한 박탈 시나리오를 생성해줘"},
    "FM-06": {"service": "hasher", "message": "hasher NLB의 Route53 레코드를 잘못된 IP로 변경하여 DNS 장애 시나리오를 생성해줘"},
    "FM-07": {"service": "worker", "message": "FIS 네트워크 disruption으로 AZ 장애를 시뮬레이션하는 시나리오를 생성해줘"},
    "FM-08": {"service": "hasher", "message": "hasher deployment의 환경변수를 잘못된 값으로 변조하여 설정 오류 시나리오를 생성해줘"},
    "FM-09": {"service": "hasher", "message": "hasher를 존재하지 않는 이미지 태그로 업데이트하여 배포 실패 시나리오를 생성해줘"},
    "FM-10": {"service": "hasher", "message": "hasher 엔드포인트에 비정상 요청을 대량 전송하여 에러율 상승 시나리오를 생성해줘"},
    "FM-12": {"service": "rng", "message": "RDS 파라미터 그룹의 max_connections를 극단적으로 축소하여 DB 성능 저하 시나리오를 생성해줘"},
    "FM-15": {"service": "worker", "message": "Redis FLUSHALL로 캐시를 무효화하여 처리량 변화를 관찰하는 시나리오를 생성해줘"},
    "FM-18": {"service": "worker", "message": "EKS nodegroup을 0으로 축소하여 auto-scaling 설정 오류 시나리오를 생성해줘"},
    "FM-19": {"service": "hasher", "message": "CloudWatch agent DaemonSet을 삭제하여 관측성 사각지대 시나리오를 생성해줘"},
    "FM-21": {"service": "worker", "message": "노드 디스크를 dd로 가득 채워 스토리지 압박 시나리오를 생성해줘"},
    "FM-22": {"service": "worker", "message": "rng 서비스를 graceful shutdown하여 worker의 회로 차단기 동작을 검증하는 시나리오를 생성해줘"},
}


@dataclass
class ValidationResult:
    check: str
    passed: bool
    detail: str = ""


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def _run_cmd(cmd, profile=None, timeout=15):
    env = {**os.environ, "AWS_PAGER": ""}
    if profile:
        env["AWS_PROFILE"] = profile
    env["AWS_REGION"] = AWS_REGION
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, env=env, timeout=timeout)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", "timeout"


def validate_structure(sc) -> list[ValidationResult]:
    results = []
    required = ["id", "name", "trigger", "verification", "category", "layer", "trigger_mode"]
    missing = [f for f in required if not sc.get(f)]
    results.append(ValidationResult("required_fields", not missing, f"missing: {missing}" if missing else "OK"))

    trigger = sc.get("trigger", {})
    results.append(ValidationResult("trigger_type", trigger.get("type") in ("aws_cli", "fis"), f"type={trigger.get('type')}"))
    results.append(ValidationResult("trigger_command_single_string", isinstance(trigger.get("command"), str), ""))

    all_cmds = " ".join([
        trigger.get("command", ""),
        sc.get("restore", {}).get("command", ""),
        sc.get("pre_cleanup", {}).get("command", ""),
    ])
    found_vars = set(re.findall(r'\$\{(\w+)\}', all_cmds))
    allowed = {"PROJECT_NAME", "AWS_ACCOUNT_ID", "AWS_REGION", "FIS_EXPERIMENT_ID", "ECR_REGISTRY", "NAMESPACE"}
    declared = set(sc.get("variables", {}).keys()) if sc.get("variables") else set()
    bad_vars = [v for v in found_vars if v not in allowed and v not in declared]
    results.append(ValidationResult("no_undefined_vars", not bad_vars, f"bad: {bad_vars}" if bad_vars else "OK"))

    steps = sc.get("verification", {}).get("steps", [])
    results.append(ValidationResult("min_3_steps", len(steps) >= 3, f"count={len(steps)}"))

    for s in steps:
        dims = s.get("dimensions", {})
        if isinstance(dims, dict):
            bad_dims = [k for k in dims if k in ("PodName", "InstanceId", "NodeName")]
        elif isinstance(dims, list):
            bad_dims = [d.get("Name") for d in dims if d.get("Name") in ("PodName", "InstanceId", "NodeName")]
        else:
            bad_dims = []
        if bad_dims:
            results.append(ValidationResult("no_runtime_dimensions", False, f"step '{s.get('name')}' uses {bad_dims}"))
            break
    else:
        results.append(ValidationResult("no_runtime_dimensions", True, "OK"))

    rubric = sc.get("evaluation_rubric", {})
    if rubric:
        weights = [c.get("weight", 0) for c in rubric.get("criteria", rubric.get("items", []))]
        total = sum(weights)
        results.append(ValidationResult("rubric_weight_100", total == 100, f"total={total}"))
    else:
        results.append(ValidationResult("rubric_weight_100", True, "no rubric (optional)"))

    return results


def validate_infra(sc) -> list[ValidationResult]:
    results = []
    trigger = sc.get("trigger", {})

    if trigger.get("type") == "fis":
        cmd_str = trigger.get("command", "")
        fis_id_match = re.search(r'--experiment-template-id\s+(EXT\w+)', cmd_str)
        if fis_id_match:
            tid = fis_id_match.group(1)
            found = False
            for c in CLUSTER_INFO.values():
                ok, stdout, _ = _run_cmd(
                    f"aws fis get-experiment-template --id {tid} --query 'experimentTemplate.id' --output text",
                    profile=c["profile"]
                )
                if ok and tid in stdout:
                    found = True
                    break
            results.append(ValidationResult("fis_template_exists", found, f"id={tid}"))
        else:
            tag_match = re.search(r"tags\.Name=='([^']+)'", cmd_str)
            if tag_match:
                tag_name = tag_match.group(1)
                found = False
                for c in CLUSTER_INFO.values():
                    ok, stdout, _ = _run_cmd(
                        f"aws fis list-experiment-templates --query \"experimentTemplates[?tags.Name=='{tag_name}'].id|[0]\" --output text",
                        profile=c["profile"]
                    )
                    if ok and stdout and stdout != "None":
                        found = True
                        break
                results.append(ValidationResult("fis_template_tag_exists", found, f"tag={tag_name}"))
            else:
                results.append(ValidationResult("fis_template_parseable", False, "cannot parse FIS ID or tag from command"))

    steps = sc.get("verification", {}).get("steps", [])
    alarm_names = set()
    for s in steps:
        if s.get("type") in ("alarm_state", "cw_alarm"):
            an = s.get("alarm_name") or s.get("alarm", "")
            if an:
                alarm_names.add(an)

    for alarm_name in list(alarm_names)[:3]:
        resolved = alarm_name.replace("${PROJECT_NAME}", "devops-agent-test")
        found = False
        for c in CLUSTER_INFO.values():
            ok, stdout, _ = _run_cmd(
                f"aws cloudwatch describe-alarms --alarm-names '{resolved}' --query 'MetricAlarms[0].AlarmName' --output text",
                profile=c["profile"]
            )
            if ok and stdout and stdout != "None":
                found = True
                break
        results.append(ValidationResult(f"alarm_exists:{resolved[:40]}", found, f"found={found}"))

    kubectl_ctx = ACTIVE_CLUSTER["context"]
    ok, stdout, _ = _run_cmd(f"kubectl --context {kubectl_ctx} get nodes --no-headers 2>/dev/null | wc -l")
    node_count = int(stdout.strip()) if ok and stdout.strip().isdigit() else 0
    results.append(ValidationResult("kubectl_context_works", node_count > 0, f"nodes={node_count}"))

    return results


def validate_feasibility(sc) -> Optional[str]:
    trigger = sc.get("trigger", {})
    if trigger.get("type") != "fis":
        return None

    cmd_str = trigger.get("command", "")
    fis_id_match = re.search(r'--experiment-template-id\s+(EXT\w+)', cmd_str)
    if not fis_id_match:
        return None

    tid = fis_id_match.group(1)

    ok, stdout, _ = _run_cmd(
        f"aws fis get-experiment-template --id {tid} --query 'experimentTemplate.{{targets:targets,actions:actions}}' --output json",
        profile=ACTIVE_CLUSTER["profile"]
    )
    if not ok or not stdout:
        # Try other account
        for c in CLUSTER_INFO.values():
            ok, stdout, _ = _run_cmd(
                f"aws fis get-experiment-template --id {tid} --query 'experimentTemplate.{{targets:targets,actions:actions}}' --output json",
                profile=c["profile"]
            )
            if ok and stdout:
                break
    if not ok or not stdout:
        return "FIS 템플릿 조회 실패"

    fis_detail = json.loads(stdout)
    targets = fis_detail.get("targets", {})
    actions = fis_detail.get("actions", {})

    selection_mode = "UNKNOWN"
    for t_val in targets.values():
        selection_mode = t_val.get("selectionMode", "UNKNOWN")
        break

    fis_duration = 0
    for a_val in actions.values():
        params = a_val.get("parameters", {})
        doc_params = params.get("documentParameters", "")
        if doc_params:
            try:
                dp = json.loads(doc_params)
                fis_duration = max(fis_duration, int(dp.get("DurationSeconds", 0)))
            except (json.JSONDecodeError, ValueError):
                pass
        dur_str = params.get("duration", "")
        if not fis_duration and dur_str:
            m = re.search(r'PT(\d+)M', dur_str)
            if m:
                fis_duration = int(m.group(1)) * 60

    steps = sc.get("verification", {}).get("steps", [])
    alarm_name_for_check = None
    for s in steps:
        if s.get("type") in ("alarm_state", "cw_alarm"):
            alarm_name_for_check = s.get("alarm_name") or s.get("alarm", "")
            break

    if alarm_name_for_check:
        resolved = alarm_name_for_check.replace("${PROJECT_NAME}", "devops-agent-test")
        for c in CLUSTER_INFO.values():
            ok2, stdout2, _ = _run_cmd(
                f"aws cloudwatch describe-alarms --alarm-names '{resolved}' --query 'MetricAlarms[0].{{Threshold:Threshold,Period:Period,EvaluationPeriods:EvaluationPeriods,MetricName:MetricName,Namespace:Namespace,Dimensions:Dimensions}}' --output json",
                profile=c["profile"]
            )
            if ok2 and stdout2 and stdout2 != "null":
                break
        else:
            return "알람 조회 실패"

        spec = json.loads(stdout2)
        threshold = spec.get("Threshold")
        period = spec.get("Period", 300)
        eval_periods = spec.get("EvaluationPeriods", 1)
        required_duration = period * eval_periods
        metric_name = spec.get("MetricName", "")
        namespace_cw = spec.get("Namespace", "")
        dimensions = spec.get("Dimensions", [])

        issues = []

        if fis_duration > 0 and fis_duration < required_duration:
            issues.append(f"Duration부족: FIS {fis_duration}s < 알람요구 {required_duration}s")

        is_node_metric = namespace_cw == "ContainerInsights" and "node_" in metric_name
        is_cluster_avg = any(d.get("Name") == "ClusterName" for d in dimensions) and \
                         not any(d.get("Name") == "NodeName" for d in dimensions)

        if is_node_metric and is_cluster_avg:
            kubectl_ctx = ACTIVE_CLUSTER["context"]
            ok3, stdout3, _ = _run_cmd(f"kubectl --context {kubectl_ctx} get nodes --no-headers 2>/dev/null | wc -l")
            node_count = int(stdout3.strip()) if ok3 and stdout3.strip().isdigit() else 0

            if node_count > 0:
                target_count = node_count
                m = re.search(r'COUNT\((\d+)\)', selection_mode)
                if m:
                    target_count = int(m.group(1))
                max_achievable = (target_count * 98 + (node_count - target_count) * 5) / node_count

                if max_achievable < threshold:
                    issues.append(f"불가능: {target_count}/{node_count}노드 stress → 최대 {max_achievable:.0f}% < threshold {threshold}%")
                else:
                    return f"✓ 가능: {selection_mode}, {node_count}노드, 최대 {max_achievable:.0f}% vs threshold {threshold}%"

        if issues:
            return f"✗ 불가능: {'; '.join(issues)}"
        return f"✓ 가능: duration {fis_duration}s >= {required_duration}s, selection={selection_mode}"

    return None


# ---------------------------------------------------------------------------
# Generator — 실제 채팅 경로 사용
# ---------------------------------------------------------------------------

def generate_scenario_via_chat(fm: dict) -> tuple[Optional[dict], str, str]:
    """POST /api/scenario-chat → Agent Space 채팅으로 시나리오 생성.

    Returns: (scenario_dict or None, session_id, raw_reply)
    """
    import requests

    fm_id = fm["id"]
    target_info = TARGET_MAP.get(fm_id, {"service": "worker", "message": f"{fm['name']} 시나리오를 생성해줘"})

    resp = requests.post(f"{BASE_URL}/api/scenario-chat", json={
        "message": target_info["message"],
        "space_id": SPACE_ID,
        "template_id": fm_id,
        "app_name": "DockerCoins",
        "include_script": False,
    }, timeout=660)

    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

    d = resp.json()
    if not d.get("ok"):
        raise RuntimeError(d.get("error", "unknown"))

    session_id = d.get("session_id", "")
    reply = d.get("reply", "")
    scenario = d.get("scenario")

    return scenario, session_id, reply


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

@dataclass
class ScenarioTestResult:
    fm_id: str
    fm_name: str
    generated: bool = False
    generation_error: str = ""
    scenario_id: str = ""
    trigger_type: str = ""
    trigger_cmd: str = ""
    session_id: str = ""
    validations: list = field(default_factory=list)
    feasibility: Optional[str] = None
    elapsed_sec: float = 0
    raw_reply: str = ""

    @property
    def all_passed(self):
        return self.generated and all(v.passed for v in self.validations)


def test_one_fm(fm: dict) -> ScenarioTestResult:
    result = ScenarioTestResult(fm_id=fm["id"], fm_name=fm["name"])
    t0 = time.time()

    try:
        sc, session_id, reply = generate_scenario_via_chat(fm)
        result.session_id = session_id
        result.raw_reply = reply[:500]

        if not sc:
            result.generated = False
            result.generation_error = "Agent 응답에 시나리오 JSON 없음"
        else:
            result.generated = True
            result.scenario_id = sc.get("id", "")
            result.trigger_type = sc.get("trigger", {}).get("type", "")
            result.trigger_cmd = sc.get("trigger", {}).get("command", "")[:150]

            result.validations.extend(validate_structure(sc))
            result.validations.extend(validate_infra(sc))
            result.feasibility = validate_feasibility(sc)

            # Save for inspection
            out_dir = os.path.join(os.path.dirname(__file__), "_test_scenarios")
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, f"{fm['id']}.json"), "w") as f:
                json.dump(sc, f, ensure_ascii=False, indent=2)

    except Exception as e:
        result.generated = False
        result.generation_error = f"{type(e).__name__}: {str(e)[:300]}"

    result.elapsed_sec = time.time() - t0
    return result


def run_all(mode_ids: list[str] = None, save: bool = False):
    modes = FAILURE_MODES
    if mode_ids:
        modes = [fm for fm in FAILURE_MODES if fm["id"] in mode_ids]

    print(f"\n{'='*70}")
    print(f"  시나리오 생성 통합 테스트 — Agent 채팅 경로")
    print(f"  {len(modes)} failure modes | 순차 실행 (Agent 채팅)")
    print(f"  클러스터: {ACTIVE_CLUSTER['name']} ({ACTIVE_CLUSTER['account_id']})")
    print(f"{'='*70}\n")

    results: list[ScenarioTestResult] = []

    for fm in modes:
        print(f"  [{fm['id']}] {fm['name']} ... ", end="", flush=True)
        r = test_one_fm(fm)
        status = "✓" if r.all_passed else ("⚠" if r.generated else "✗")
        print(f"{status} ({r.elapsed_sec:.0f}s) {r.scenario_id or r.generation_error[:50]}")
        results.append(r)

    # Summary report
    results.sort(key=lambda r: r.fm_id)
    print(f"\n{'='*70}")
    print(f"  결과 요약")
    print(f"{'='*70}")
    print(f"{'FM':<6} {'Name':<30} {'Gen':>3} {'Valid':>5} {'Infra':>5} {'Feasibility':<30} {'Time':>5}")
    print(f"{'-'*6} {'-'*30} {'-'*3} {'-'*5} {'-'*5} {'-'*30} {'-'*5}")

    pass_count = 0
    for r in results:
        gen = "✓" if r.generated else "✗"
        struct_checks = [v for v in r.validations if v.check in ("required_fields", "trigger_type", "trigger_command_single_string", "no_undefined_vars", "min_3_steps", "no_runtime_dimensions", "rubric_weight_100")]
        infra_checks = [v for v in r.validations if v not in struct_checks]
        valid_ok = "✓" if all(v.passed for v in struct_checks) else ("✗" if struct_checks else "-")
        infra_ok = "✓" if all(v.passed for v in infra_checks) else ("✗" if infra_checks else "-")
        feas = (r.feasibility or "-")[:30]
        print(f"{r.fm_id:<6} {r.fm_name:<30} {gen:>3} {valid_ok:>5} {infra_ok:>5} {feas:<30} {r.elapsed_sec:>4.0f}s")
        if r.all_passed:
            pass_count += 1

    print(f"\n총 {len(results)}개 중 {pass_count}개 통과")

    # Detail failures
    failures = [r for r in results if not r.all_passed]
    if failures:
        print(f"\n{'='*70}")
        print(f"  실패 상세")
        print(f"{'='*70}")
        for r in failures:
            print(f"\n[{r.fm_id}] {r.fm_name}")
            if not r.generated:
                print(f"  생성 실패: {r.generation_error}")
            else:
                failed_v = [v for v in r.validations if not v.passed]
                for v in failed_v:
                    print(f"  ✗ {v.check}: {v.detail}")
                if r.feasibility and r.feasibility.startswith("✗"):
                    print(f"  ✗ feasibility: {r.feasibility}")

    # Save report
    report_path = os.path.join(os.path.dirname(__file__), "_test_scenarios", "report.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    report_data = []
    for r in results:
        report_data.append({
            "fm_id": r.fm_id,
            "fm_name": r.fm_name,
            "generated": r.generated,
            "generation_error": r.generation_error,
            "scenario_id": r.scenario_id,
            "trigger_type": r.trigger_type,
            "trigger_cmd": r.trigger_cmd,
            "session_id": r.session_id,
            "validations": [{"check": v.check, "passed": v.passed, "detail": v.detail} for v in r.validations],
            "feasibility": r.feasibility,
            "elapsed_sec": r.elapsed_sec,
            "all_passed": r.all_passed,
        })
    with open(report_path, "w") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)
    print(f"\n리포트 저장: {report_path}")

    if save and pass_count > 0:
        print(f"\n--- 통과 시나리오 DynamoDB 저장 ---")
        import requests
        for r in results:
            if r.all_passed:
                sc_path = os.path.join(os.path.dirname(__file__), "_test_scenarios", f"{r.fm_id}.json")
                with open(sc_path) as f:
                    sc = json.load(f)
                sc.setdefault("target_service", TARGET_MAP.get(r.fm_id, {}).get("service", ""))
                sc.setdefault("skill_version", "2.1")
                resp = requests.post(f"{BASE_URL}/api/arch/save-scenario", json={
                    "scenario": sc, "space_id": SPACE_ID
                })
                d = resp.json()
                print(f"  {'✓' if d.get('ok') else '✗'} {r.fm_id}: {d.get('id', d.get('error','')[:50])}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="시나리오 생성 통합 테스트 (Agent 채팅)")
    parser.add_argument("--modes", type=str, help="테스트할 FM ID (쉼표 구분, 예: FM-04,FM-08)")
    parser.add_argument("--save", action="store_true", help="통과 시나리오를 DynamoDB에 저장")
    args = parser.parse_args()

    mode_ids = args.modes.split(",") if args.modes else None
    run_all(mode_ids=mode_ids, save=args.save)
