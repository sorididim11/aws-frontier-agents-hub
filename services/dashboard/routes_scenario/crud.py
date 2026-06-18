"""Scenario validation helpers, CRUD endpoints, and shared utility functions."""
import json
import os
import re

from flask import jsonify, request

from app_config import (
    _CFG, _cfg_get, AWS_REGION, AGENT_SPACE_ID,
    _req_space_id, _boto_session,
)
from routes_arch import (
    _load_latest_arch, _list_scenarios, _get_scenario, _save_scenario,
    _delete_scenario,
)
from routes_scenario import scenario_bp, _get_space_app_names, _app_matches_space


# ---------------------------------------------------------------------------
# Scenario validation helpers
# ---------------------------------------------------------------------------

SCENARIO_PLACEHOLDER_VARS_GLOBAL = {"PROJECT_NAME", "AWS_ACCOUNT_ID", "AWS_REGION", "ECR_REGISTRY", "NAMESPACE", "FIS_EXPERIMENT_ID"}

SCENARIO_VALIDATION_RULES = """\
- trigger.command는 단일 문자열 (commands 배열 금지, 여러 명령은 && 연결)
- 환경 변수: 글로벌({allowed}) + 시나리오 variables에 선언된 변수만 허용
- trigger가 생성하는 리소스 이름 = verification이 참조하는 이름 (일치 필수)
- trigger에서 kubectl run으로 pod 생성 시, verification의 kubectl_check에서 동일한 pod 이름 사용
- evaluation_rubric weight 합계 = 100
- metric_check dimensions에 PodName, InstanceId 등 변동성 값 사용 금지 (ClusterName, Namespace만 사용)
- kubectl run 일회성 pod의 kubectl_check expected는 Succeeded (Running 아님)""".format(
    allowed=", ".join(f"${{{v}}}" for v in sorted(SCENARIO_PLACEHOLDER_VARS_GLOBAL))
)


def _extract_created_resources(cmd):
    """Extract resource names created/manipulated by a trigger command."""
    names = set()
    if not cmd:
        return names
    for part in re.split(r'\s*&&\s*', cmd):
        tokens = part.split()
        if len(tokens) < 3:
            continue
        if "kubectl" not in tokens[0] and tokens[0] != "kubectl":
            continue
        for i, t in enumerate(tokens):
            if t == "run" and i + 1 < len(tokens):
                name = tokens[i + 1].lstrip("-")
                if name and not name.startswith("-"):
                    names.add(name)
            elif t == "create" and i + 2 < len(tokens):
                name = tokens[i + 2].lstrip("-")
                if name and not name.startswith("-"):
                    names.add(name)
            elif t == "--name" and i + 1 < len(tokens):
                names.add(tokens[i + 1])
    return names


def _extract_referenced_resources(steps):
    """Extract ephemeral resource names that verification expects to exist.

    Includes pod/resource names from kubectl_check steps (trigger에서 만든 리소스 확인용).
    Pre-existing deployments/services (get deploy, get svc)는 제외.
    """
    names = set()
    _skip_resources = {"deploy", "deployment", "deployments", "svc", "service", "services",
                       "node", "nodes", "namespace", "namespaces", "configmap", "secret"}
    for s in (steps or []):
        cfg = s if isinstance(s, dict) else {}
        stype = cfg.get("type", "")
        if stype != "kubectl_check":
            continue
        for field in ("pod", "service"):
            v = cfg.get(field, "")
            if v:
                names.add(v)
        cmd = cfg.get("command", "")
        if cmd:
            tokens = cmd.split()
            for i, t in enumerate(tokens):
                if t in ("get", "describe", "logs", "delete"):
                    if i + 1 < len(tokens) and tokens[i + 1].lower() in _skip_resources:
                        continue
                    if t == "get" and i + 1 < len(tokens) and tokens[i + 1].lower() in ("pod", "pods"):
                        if i + 2 < len(tokens) and not tokens[i + 2].startswith("-"):
                            names.add(tokens[i + 2])
                    elif i + 2 < len(tokens) and not tokens[i + 2].startswith("-"):
                        resource_type = tokens[i + 1] if i + 1 < len(tokens) else ""
                        if resource_type.lower() not in _skip_resources:
                            names.add(tokens[i + 2])
                elif t in ("pods", "pod"):
                    if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                        names.add(tokens[i + 1])
    return names


def _find_undefined_vars(text, scenario_vars=None):
    """Find ${VAR} references not in global + scenario-declared + bash-local variables."""
    if not text:
        return []
    allowed = SCENARIO_PLACEHOLDER_VARS_GLOBAL | set(scenario_vars or [])
    bash_defined = set(re.findall(r'\b([A-Z_][A-Z0-9_]*)=', text))
    allowed = allowed | bash_defined
    found = re.findall(r'\$\{([A-Z_][A-Z0-9_]*)\}', text)
    return [v for v in found if v not in allowed]


def _fix_scenario(scenario):
    """Agent 생성 시나리오 자동 교정. 교정 내역 리스트 반환."""
    fixes = []
    trigger = scenario.get("trigger", {})
    trigger_cmd = trigger.get("command", "") if isinstance(trigger, dict) else ""

    # 1. target_service 자동 추출
    if not scenario.get("target_service", "").strip() and trigger_cmd:
        m = re.search(r'svc/(\w+)', trigger_cmd)
        if not m:
            m = re.search(r'deployment/(\w[\w-]*)', trigger_cmd)
        if not m:
            m = re.search(r'-l\s+app=(\w+)', trigger_cmd)
        if not m:
            m = re.search(r'http://(\w+)[.:/]', trigger_cmd)
        if m:
            scenario["target_service"] = m.group(1)
            fixes.append(f"target_service 자동 추출: {m.group(1)}")
    if not scenario.get("target_service", "").strip():
        ts = scenario.get("variables", {}).get("TARGET_SERVICE", "")
        if not ts:
            for comp in scenario.get("architecture", {}).get("fault_path", []):
                m2 = re.search(r'→\s*(\w+)\s*\(', comp)
                if m2:
                    ts = m2.group(1)
                    break
        if ts:
            scenario["target_service"] = ts
            fixes.append(f"target_service fallback 주입: {ts}")

    # 2. trigger.type 자동 교정
    if isinstance(trigger, dict) and trigger_cmd:
        declared_type = trigger.get("type", "")
        if "kubectl " in trigger_cmd and declared_type != "kubectl":
            trigger["type"] = "kubectl"
            fixes.append(f"trigger.type 교정: {declared_type} -> kubectl")
        elif "aws fis " in trigger_cmd and declared_type != "fis":
            trigger["type"] = "fis"
            fixes.append(f"trigger.type 교정: {declared_type} -> fis")

    # 3. --rm 제거 (kubectl run)
    for field_path in ["trigger.command", "restore.command", "pre_cleanup.command"]:
        parts = field_path.split(".")
        obj = scenario.get(parts[0], {})
        if isinstance(obj, dict) and "--rm" in obj.get(parts[1], ""):
            obj[parts[1]] = obj[parts[1]].replace(" --rm", "")
            fixes.append(f"{field_path}에서 --rm 제거")

    # 4. kubectl_check에 logs 명령 -> 제거하고 pod 상태 확인으로 교정
    v_steps = scenario.get("verification", {}).get("steps", [])
    for step in v_steps:
        if not isinstance(step, dict) or step.get("type") != "kubectl_check":
            continue
        cmd = step.get("command", "")
        if "logs " in cmd or "log " in cmd:
            svc = scenario.get("target_service", "app")
            step["command"] = f"kubectl get pods -l app={svc} -o jsonpath={{.items[0].status.phase}}"
            step["expected"] = "Running"
            step["name"] = f"{svc} pod 정상 상태 확인"
            fixes.append(f"kubectl_check 교정: logs 명령 -> pod 상태 확인")

    # 5. trigger_mode 자동 추론
    if not scenario.get("trigger_mode"):
        has_investigation = any(
            isinstance(s, dict) and s.get("type") == "investigation_event" for s in v_steps)
        has_agent_inv = any(
            isinstance(s, dict) and s.get("type") == "agent_investigation" for s in v_steps)
        if has_investigation:
            scenario["trigger_mode"] = "reactive"
            fixes.append("trigger_mode 자동 추론: reactive (investigation_event 존재)")
        elif has_agent_inv:
            scenario["trigger_mode"] = "proactive"
            fixes.append("trigger_mode 자동 추론: proactive (agent_investigation 존재)")
        else:
            scenario["trigger_mode"] = "reactive"
            fixes.append("trigger_mode 자동 추론: reactive (기본값)")

    # 6. alarm_state timeout 최소값 보장
    for step in v_steps:
        if not isinstance(step, dict) or step.get("type") not in ("alarm_state", "cw_alarm"):
            continue
        expected = step.get("expected", step.get("config", {}).get("expected", ""))
        min_timeout = 300 if expected == "ALARM" else 180
        current = step.get("timeout", 60)
        if current < min_timeout:
            step["timeout"] = min_timeout
            fixes.append(f"alarm_state({expected}) timeout 교정: {current}s -> {min_timeout}s")

    # 7. architecture/normal_flow/fault_flow 자동 생성 (토폴로지 기반)
    target_svc = scenario.get("target_service", "")
    if target_svc and (not scenario.get("architecture") or not scenario.get("normal_flow") or not scenario.get("fault_flow")):
        try:
            space_id = scenario.get("_space_id", "") or AGENT_SPACE_ID
            saved = _load_latest_arch(space_id)
            if saved and saved.get("graph"):
                nodes = saved["graph"].get("nodes", [])
                edges = saved["graph"].get("edges", [])
                node_names = {n["name"] for n in nodes}

                if not scenario.get("architecture") and nodes:
                    components = [{"id": n["name"], "label": n["name"], "type": n.get("service_type", "app")}
                                  for n in nodes if n.get("service_type") != "boundary"]
                    arch_edges = [{"from": e["source"], "to": e["target"],
                                   "label": f"{e.get('protocol', '')}:{e.get('port', '')}" if e.get("protocol") else ""}
                                  for e in edges
                                  if e.get("source") in node_names and e.get("target") in node_names]
                    fault_path = []
                    if target_svc in node_names:
                        dependents = [e["source"] for e in edges if e.get("target") == target_svc]
                        if dependents:
                            fault_path = [dependents[0], target_svc]
                            downstream = [e["target"] for e in edges if e.get("source") == target_svc]
                            if downstream:
                                fault_path.append(downstream[0])
                        else:
                            fault_path = [target_svc]
                    scenario["architecture"] = {
                        "components": components,
                        "edges": arch_edges,
                        "fault_path": fault_path,
                    }
                    fixes.append("architecture 토폴로지 기반 자동 생성")

                if not scenario.get("normal_flow") and edges:
                    flow = []
                    for i, e in enumerate(edges[:8]):
                        proto = e.get("protocol", "")
                        port = e.get("port", "")
                        label = f"{proto}:{port}" if proto and port else (proto or "call")
                        flow.append({"step": f"{i+1}. {e['source']} → {e['target']}", "desc": label})
                    scenario["normal_flow"] = flow
                    fixes.append("normal_flow 토폴로지 기반 자동 생성")

                if not scenario.get("fault_flow") and target_svc:
                    fault_flow = []
                    dependents = [e["source"] for e in edges if e.get("target") == target_svc]
                    downstream = [e["target"] for e in edges if e.get("source") == target_svc]
                    step_num = 1
                    if dependents:
                        for dep in dependents[:3]:
                            fault_flow.append({"step": f"{step_num}. {dep} → {target_svc}", "desc": "장애 발생, 타임아웃/에러"})
                            step_num += 1
                    if downstream:
                        for ds in downstream[:3]:
                            fault_flow.append({"step": f"{step_num}. {target_svc} → {ds}", "desc": "장애 전파, 연쇄 영향"})
                            step_num += 1
                    if not fault_flow:
                        fault_flow = [{"step": f"1. {target_svc}", "desc": "직접 장애 발생"}]
                    scenario["fault_flow"] = fault_flow
                    fixes.append("fault_flow 토폴로지 기반 자동 생성")
        except Exception:
            pass

    # 8. flow 형식 보정: 문자열 배열 → {step, desc} 객체 배열
    for flow_key in ("normal_flow", "fault_flow"):
        flow = scenario.get(flow_key)
        if isinstance(flow, list) and flow and isinstance(flow[0], str):
            scenario[flow_key] = [{"step": f"{i+1}. {s.split('(')[0].strip()}", "desc": s.split('(')[-1].rstrip(')') if '(' in s else ""}
                                  for i, s in enumerate(flow)]
            fixes.append(f"{flow_key} 문자열→객체 변환")

    return fixes


EXPECTED_SKILL_VERSION = "2.1"


def _validate_scenario(scenario):
    """Validate scenario structure and consistency. Returns (errors, warnings)."""
    errors = []
    warnings = []

    sv = scenario.get("skill_version", "")
    if sv and sv != EXPECTED_SKILL_VERSION:
        warnings.append(f"skill_version 불일치: 생성={sv}, 기대={EXPECTED_SKILL_VERSION} — 스킬 업데이트 필요")
    elif not sv:
        warnings.append(f"skill_version 누락 — 구버전 스킬로 생성된 시나리오 (기대: {EXPECTED_SKILL_VERSION})")

    required = ["id", "name", "trigger", "verification", "category", "layer", "trigger_mode"]
    missing = [f for f in required if not scenario.get(f)]
    if missing:
        errors.append(f"필수 필드 누락: {', '.join(missing)}")

    if not scenario.get("target_service", "").strip():
        errors.append("target_service 필수 — 장애 대상 서비스명을 최상위에 기록")

    if not scenario.get("purpose"):
        if scenario.get("description"):
            scenario["purpose"] = scenario.pop("description")
        else:
            errors.append("purpose 필수 — 시나리오 목적을 1~2문장으로 기록 (description이 아닌 purpose 필드 사용)")

    # 장애 흐름 필드 검증
    for flow_field in ("architecture", "normal_flow", "fault_flow"):
        if not scenario.get(flow_field):
            warnings.append(f"{flow_field} 누락 — 장애 전파 시각화에 필요")

    # restore 구조 검증 (rollback → restore 자동 변환)
    if not scenario.get("restore"):
        rollback = scenario.get("rollback")
        if isinstance(rollback, dict):
            steps = rollback.get("steps", [])
            if steps:
                cmds = [s.get("command", "") for s in steps if isinstance(s, dict) and s.get("command")]
                scenario["restore"] = {"command": " && ".join(cmds)}
                scenario.pop("rollback", None)
            elif rollback.get("command"):
                scenario["restore"] = {"command": rollback["command"]}
                scenario.pop("rollback", None)
    restore = scenario.get("restore", {})
    if restore and not isinstance(restore, dict):
        errors.append("restore는 dict여야 합니다: {\"command\": \"복원명령\"}")

    # pre_cleanup 구조 검증 (list → dict 자동 변환)
    pre_cleanup_raw = scenario.get("pre_cleanup")
    if isinstance(pre_cleanup_raw, list):
        cmds = [item.get("command", "") for item in pre_cleanup_raw if isinstance(item, dict) and item.get("command")]
        scenario["pre_cleanup"] = {"command": " && ".join(cmds), "reset_alarms": [], "wait_ok_timeout": 60}

    # verification.steps 구조 검증 (checks → steps 자동 변환)
    verification = scenario.get("verification", {})
    if isinstance(verification, dict):
        if "checks" in verification and "steps" not in verification:
            verification["steps"] = verification.pop("checks")
            scenario["verification"] = verification
        if not verification.get("steps"):
            errors.append("verification.steps 필수 — 검증 단계 배열 필요 (checks가 아닌 steps 사용)")
        # description → name 자동 보정
        for step in verification.get("steps", []):
            if isinstance(step, dict) and "name" not in step and "description" in step:
                step["name"] = step.pop("description")

    trigger = scenario.get("trigger", {})
    if isinstance(trigger, dict):
        if isinstance(trigger.get("commands"), list) and not trigger.get("command"):
            trigger["command"] = " && ".join(trigger.pop("commands"))
        if not trigger.get("command", "").strip():
            errors.append("trigger.command 비어있음")
    else:
        errors.append("trigger는 dict여야 합니다")

    all_commands = []
    if isinstance(trigger, dict):
        all_commands.append(trigger.get("command", ""))
    restore = scenario.get("restore", {})
    if isinstance(restore, dict):
        all_commands.append(restore.get("command", ""))
    pre_cleanup = scenario.get("pre_cleanup", {})
    if isinstance(pre_cleanup, dict):
        all_commands.append(pre_cleanup.get("command", ""))
    v_steps = []
    verification = scenario.get("verification", {})
    if isinstance(verification, dict):
        v_steps = verification.get("steps", [])
    for step in v_steps:
        if isinstance(step, dict):
            all_commands.append(step.get("command", ""))

    scenario_vars = set(scenario.get("variables", {}).keys())
    bad_vars = set()
    for cmd in all_commands:
        bad_vars.update(_find_undefined_vars(cmd, scenario_vars))
    if bad_vars:
        allowed_str = ", ".join(f"${{{v}}}" for v in sorted(SCENARIO_PLACEHOLDER_VARS_GLOBAL | scenario_vars))
        bad_str = ", ".join(f"${{{v}}}" for v in sorted(bad_vars))
        errors.append(f"미허용 변수: {bad_str} — 허용: {allowed_str}")

    try:
        from arch_analysis import VERIFICATION_STEP_TYPES
        valid_types = {s["type"] for s in VERIFICATION_STEP_TYPES}
        for step in v_steps:
            st = step.get("type", "") if isinstance(step, dict) else ""
            if st and st not in valid_types:
                warnings.append(f"미등록 verification type: {st}")
    except ImportError:
        pass

    trigger_cmd = trigger.get("command", "") if isinstance(trigger, dict) else ""
    created = _extract_created_resources(trigger_cmd)
    referenced = _extract_referenced_resources(v_steps)
    if created and referenced:
        # Resolve variables in both sets for comparison
        var_map = scenario.get("variables", {})
        def _resolve_name(name):
            m = re.match(r'^\$\{([A-Z_][A-Z0-9_]*)\}$', name)
            if m and m.group(1) in var_map:
                return str(var_map[m.group(1)])
            return name
        created_resolved = {_resolve_name(n) for n in created}
        referenced_resolved = {_resolve_name(n) for n in referenced}
        orphaned = referenced_resolved - created_resolved
        # Exclude references to existing architecture services (not newly created)
        arch_services = set()
        arch = scenario.get("architecture", {})
        if isinstance(arch, dict):
            for svc in arch.get("services", []):
                if isinstance(svc, dict):
                    arch_services.add(svc.get("name", ""))
                    arch_services.add(svc.get("service", ""))
        orphaned -= arch_services
        # Also exclude kubectl_check with expected="Running" (checking existing deployments)
        existing_checks = set()
        for step in v_steps:
            if isinstance(step, dict) and step.get("type") == "kubectl_check":
                if step.get("expected") == "Running":
                    res_name = step.get("resource", "").split("/")[-1] if step.get("resource") else ""
                    if res_name:
                        existing_checks.add(res_name)
        orphaned -= existing_checks
        kubectl_types = {s.get("type") for s in v_steps if isinstance(s, dict)} & {"kubectl_check"}
        if orphaned and kubectl_types:
            errors.append(
                f"verification이 trigger에서 생성하지 않는 리소스 참조: {', '.join(sorted(orphaned))} "
                f"(trigger 생성: {', '.join(sorted(created_resolved))}) — 변수 사용으로 일치시킬 것"
            )

    allowed_dims = {"Service", "Operation", "Environment", "Namespace", "ClusterName"}
    for step in v_steps:
        if isinstance(step, dict) and step.get("type") == "metric_check":
            for dim in step.get("dimensions", []):
                if isinstance(dim, dict) and dim.get("Name") not in allowed_dims:
                    errors.append(f"metric_check에 변동성 dimension 사용 금지: {dim['Name']}={dim.get('Value','')} — 허용: {', '.join(sorted(allowed_dims))}")

    rubric = scenario.get("evaluation_rubric")
    if isinstance(rubric, dict) and rubric.get("criteria"):
        # Legacy dict format: {criteria: [...]}
        total = sum(c.get("weight", 0) for c in rubric["criteria"] if isinstance(c, dict))
        if total != 100:
            errors.append(f"evaluation_rubric weight 합계 = {total} (100이어야 함)")
    elif isinstance(rubric, list):
        # v2 format: [{criterion, weight, how_to_verify}]
        total = sum(c.get("weight", 0) for c in rubric if isinstance(c, dict))
        if total != 100:
            errors.append(f"evaluation_rubric weight 합계 = {total} (100이어야 함)")

    # Phase 모델 검증 (v2)
    _PHASE_ORDER = ["trigger_active", "effect_observed", "reaction_confirmed"]
    _PHASE_ALLOWED_TYPES = {
        "trigger_active": {"kubectl_check", "fis_experiment", "pod_status"},
        "effect_observed": {"alarm_state", "metric_check", "log_pattern", "xray_trace", "xray_latency", "cw_alarm"},
        "reaction_confirmed": {"investigation_event", "agent_investigation", "slack_message"},
    }
    last_phase_idx = -1
    for step in v_steps:
        if not isinstance(step, dict):
            continue
        phase = step.get("phase")
        if not phase:
            warnings.append(f"step '{step.get('name', '?')}'에 phase 필드 누락")
            continue
        if phase not in _PHASE_ORDER:
            errors.append(f"유효하지 않은 phase '{phase}' — 허용: {_PHASE_ORDER}")
            continue
        phase_idx = _PHASE_ORDER.index(phase)
        if phase_idx < last_phase_idx:
            errors.append(f"phase 순서 역전: '{step.get('name', '?')}'({phase})가 이전 phase보다 앞에 위치")
        last_phase_idx = phase_idx
        step_type = step.get("type", "")
        allowed = _PHASE_ALLOWED_TYPES.get(phase, set())
        if step_type and allowed and step_type not in allowed:
            warnings.append(f"step '{step.get('name', '?')}': type '{step_type}'은 phase '{phase}'에 비표준")

    # reactive 시나리오 investigation 필수 검증
    trigger_mode = scenario.get("trigger_mode", "")
    has_alarm_step = any(
        isinstance(s, dict) and s.get("type") in ("alarm_state", "cw_alarm") and s.get("expected") == "ALARM"
        for s in v_steps
    )
    has_investigation = any(
        isinstance(s, dict) and s.get("type") in ("investigation_event", "agent_investigation")
        for s in v_steps
    )
    if trigger_mode == "reactive" and has_alarm_step and not has_investigation:
        warnings.append("reactive 시나리오에 investigation step 누락 — alarm(ALARM) 포함 시 investigation_event 필수")

    # restore 필수 검증
    if not scenario.get("restore"):
        warnings.append("restore 누락 — 장애 주입 후 원상복구 명령 필수")

    # alarm_spec 유효성 검증 + trigger↔alarm 인과 관계 heuristic
    _REQUIRED_ALARM_SPEC = ["metric_name", "namespace", "statistic", "comparison", "threshold", "period"]
    _TRIGGER_METRIC_MAP = {
        "abuser": ["Error"], "error": ["Error"], "corrupt": ["Error"],
        "5xx": ["Fault"], "fault": ["Fault"],
        "latency": ["Latency"], "delay": ["Latency"], "inject-latency": ["Latency"],
        "stress": ["cpu_utilization", "node_cpu"], "oom": ["memory", "oom"],
    }
    for step in v_steps:
        if not isinstance(step, dict):
            continue
        if step.get("type") not in ("alarm_state", "cw_alarm"):
            continue
        alarm_spec = step.get("alarm_spec")
        alarm_name = step.get("alarm_name") or step.get("alarm")
        if alarm_spec:
            if not isinstance(alarm_spec, dict):
                errors.append("alarm_spec은 dict여야 합니다")
            else:
                missing_spec = [f for f in _REQUIRED_ALARM_SPEC if f not in alarm_spec]
                if missing_spec:
                    errors.append(f"alarm_spec 필수 필드 누락: {', '.join(missing_spec)}")
        elif not alarm_name:
            errors.append(f"alarm_state 스텝 '{step.get('name', '?')}'에 alarm_name 또는 alarm_spec 필수")
        # trigger↔alarm 인과 관계 heuristic warning
        if trigger_cmd:
            trigger_lower = trigger_cmd.lower()
            inferred_metrics = set()
            for kw, metrics in _TRIGGER_METRIC_MAP.items():
                if kw in trigger_lower:
                    inferred_metrics.update(m.lower() for m in metrics)
            if inferred_metrics:
                alarm_metric_hint = ""
                if alarm_spec and isinstance(alarm_spec, dict):
                    alarm_metric_hint = alarm_spec.get("metric_name", "").lower()
                elif alarm_name:
                    alarm_metric_hint = alarm_name.lower()
                if alarm_metric_hint:
                    match_found = any(m in alarm_metric_hint for m in inferred_metrics)
                    if not match_found:
                        warnings.append(
                            f"trigger↔alarm 불일치 가능: trigger에서 {inferred_metrics} 유발 추정, "
                            f"but alarm '{alarm_name or alarm_spec.get('metric_name', '?')}' 참조"
                        )

    return errors, warnings


@scenario_bp.route("/api/validate-scenario", methods=["POST"])
def api_validate_scenario():
    """Validate scenario JSON without saving. Returns errors and warnings."""
    body = request.json or {}
    scenario = body.get("scenario")
    if not scenario or not isinstance(scenario, dict):
        return jsonify({"ok": False, "errors": ["scenario object required"], "warnings": []}), 400
    scenario["_space_id"] = body.get("space_id", "") or _req_space_id("json")
    fixes = _fix_scenario(scenario)
    scenario.pop("_space_id", None)
    errors, warnings = _validate_scenario(scenario)
    return jsonify({"ok": len(errors) == 0, "errors": errors, "warnings": warnings, "fixes": fixes})


@scenario_bp.route("/api/arch/save-scenario", methods=["POST"])
def api_arch_save_scenario():
    """Save a generated scenario JSON to DynamoDB, scoped by space_id."""
    body = request.json or {}
    scenario = body.get("scenario")
    if not scenario or not isinstance(scenario, dict):
        return jsonify({"ok": False, "error": "scenario object required"}), 400

    sid = scenario.get("id", "").strip()
    if not sid:
        return jsonify({"ok": False, "error": "scenario.id required"}), 400
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_\-]{2,80}$', sid):
        return jsonify({"ok": False, "error": "invalid scenario ID: 영문/숫자/하이픈만 허용 (3~81자)"}), 400

    space_id = _req_space_id("json")

    # Multi Agent 모듈: 검증/fix 없이 저장 (Agent가 이미 환경 확인 완료)
    executor = scenario.get("executor", "")
    if executor == "multi_agent":
        if _get_scenario(space_id, sid):
            return jsonify({"ok": False, "error": f"Scenario '{sid}' already exists"}), 409
        _save_scenario(space_id, scenario)
        return jsonify({"ok": True, "id": sid, "space_id": space_id})

    # Rule Engine 모듈: 기존 검증 + auto-fix
    scenario["_space_id"] = space_id
    fixes = _fix_scenario(scenario)
    scenario.pop("_space_id", None)
    errors, warnings = _validate_scenario(scenario)
    if errors:
        return jsonify({"ok": False, "error": "시나리오 검증 실패", "validation_errors": errors, "warnings": warnings}), 400

    if _get_scenario(space_id, sid):
        return jsonify({"ok": False, "error": f"Scenario '{sid}' already exists"}), 409

    _save_scenario(space_id, scenario)
    result = {"ok": True, "id": sid, "space_id": space_id}
    if fixes:
        result["fixes"] = fixes
        print(f"[save-scenario] auto-fix: {fixes}")
    if warnings:
        result["warnings"] = warnings
    return jsonify(result)


# ===================================================================
# Scenario Tab — List, Detail, Run Proxy, Chat
# ===================================================================

SCENARIO_CATEGORY_META = {
    "infrastructure": {"label": "Infrastructure", "color": "#f97316", "order": 0},
    "application":    {"label": "Application",    "color": "#ef4444", "order": 1},
    "composite":      {"label": "Composite",      "color": "#06b6d4", "order": 2},
    "multi-service":  {"label": "Multi Service",  "color": "#a855f7", "order": 3},
    "single-service": {"label": "Single Service", "color": "#ef4444", "order": 4},
    "kubernetes":     {"label": "Kubernetes",      "color": "#3b82f6", "order": 5},
    "aws":            {"label": "AWS Infrastructure", "color": "#f97316", "order": 6},
    "cleanup":        {"label": "Cleanup / Restore",  "color": "#64748b", "order": 7},
    "data":           {"label": "Data Layer",          "color": "#10b981", "order": 8},
    "observability":  {"label": "Observability",       "color": "#8b5cf6", "order": 9},
    "security":       {"label": "Security",            "color": "#dc2626", "order": 10},
}


@scenario_bp.route("/api/scenarios")
def api_scenario_list():
    space_id = _req_space_id()
    filter_space = request.args.get("filter_space_id", "")
    effective_space = filter_space.strip() if filter_space.strip() else space_id

    scenarios = _list_scenarios(effective_space)

    # Space에 속한 앱 목록 (보안 시나리오 필터용)
    space_apps = _get_space_app_names(effective_space)

    try:
        from security_scenarios import scenario_engine as _sec_engine
        for sec in _sec_engine.list_registered_scenarios():
            sec_space = sec.get("devops_space_id", "")
            sec_app = sec.get("app_name", sec.get("target_service", ""))
            if sec_space and sec_space != effective_space:
                continue
            if not sec_space:
                if space_apps and sec_app and not _app_matches_space(sec_app, space_apps):
                    continue
            cat = sec.get("category", "security")
            if cat == "security" and sec.get("target_service"):
                cat = f"security-{sec['target_service']}"
            scenarios.append({
                "id": sec.get("id", ""),
                "name": sec.get("name", ""),
                "description": sec.get("description", ""),
                "category": cat,
                "app_name": sec_app,
                "layer": sec.get("layer", "app"),
                "target_service": sec.get("target_service", ""),
                "failure_mode": sec.get("failure_mode", ""),
                "purpose": sec.get("purpose", ""),
                "expected_root_cause": sec.get("expected_root_cause", ""),
                "verification_count": len(sec.get("verification", {}).get("steps", [])),
                "source": "security-agent",
                "risk_level": sec.get("risk_level", ""),
                "finding_id": sec.get("finding_id", ""),
                "last_result": sec.get("last_result"),
            })
    except Exception:
        pass

    # security-{app} 카테고리 동적 생성
    categories = dict(SCENARIO_CATEGORY_META)
    for s in scenarios:
        cat = s.get("category", "")
        if cat.startswith("security-") and cat not in categories:
            app = cat.replace("security-", "", 1)
            categories[cat] = {"label": f"Security — {app}", "color": "#dc2626", "order": 10}

    return jsonify({"ok": True, "scenarios": scenarios, "categories": categories,
                    "space_id": effective_space})


@scenario_bp.route("/api/scenarios/<scenario_id>")
def api_scenario_detail(scenario_id):
    space_id = _req_space_id()
    scenario = _get_scenario(space_id, scenario_id)
    if not scenario and scenario_id.startswith("SEC-"):
        try:
            from security_scenarios import scenario_engine as _sec_engine
            for sec in _sec_engine.list_registered_scenarios():
                if sec.get("id") == scenario_id:
                    if space_id and sec.get("devops_space_id") and sec["devops_space_id"] != space_id:
                        continue
                    scenario = sec
                    break
        except Exception:
            pass
    if not scenario:
        return jsonify({"ok": False, "error": "Scenario not found"}), 404
    return jsonify({"ok": True, "scenario": scenario})


@scenario_bp.route("/api/scenarios/<scenario_id>", methods=["DELETE"])
def api_scenario_delete(scenario_id):
    space_id = _req_space_id()
    if _delete_scenario(space_id, scenario_id):
        return jsonify({"ok": True, "id": scenario_id})
    return jsonify({"ok": False, "error": "Scenario not found"}), 404


# ---------------------------------------------------------------------------
# Scenario loading helpers (shared with verifier)
# ---------------------------------------------------------------------------
def _scenario_placeholder_values():
    """Build placeholder->value map for global variables."""
    _account_id = _cfg_get(_CFG, "aws.account_id", os.environ.get("AWS_ACCOUNT_ID", ""))
    _project_name = _cfg_get(_CFG, "project.name", os.environ.get("PROJECT_NAME", ""))
    _namespace = _cfg_get(_CFG, "kubernetes.namespace", os.environ.get("K8S_NAMESPACE", ""))
    return {
        "${PROJECT_NAME}": _project_name,
        "${AWS_REGION}": AWS_REGION,
        "${ECR_REGISTRY}": os.environ.get("ECR_REGISTRY", f"{_account_id}.dkr.ecr.{AWS_REGION}.amazonaws.com"),
        "${AWS_ACCOUNT_ID}": _account_id,
        "${NAMESPACE}": _namespace,
    }


def _ensure_evaluation_rubric(scenario):
    """evaluation_rubric이 없으면 verification steps 기반으로 자동 생성."""
    rubric = scenario.get("evaluation_rubric")
    if isinstance(rubric, dict) and rubric.get("criteria"):
        return
    if isinstance(rubric, list) and rubric:
        return

    steps = scenario.get("verification", {}).get("steps", [])
    criteria = []
    detection_steps = [s for s in steps if s.get("type") in ("alarm_state", "cw_alarm", "metric_check")]
    analysis_steps = [s for s in steps if s.get("type") in ("agent_investigation", "investigation_event")]
    observation_steps = [s for s in steps if s not in detection_steps and s not in analysis_steps]

    if detection_steps:
        w = 40 // len(detection_steps)
        for s in detection_steps:
            criteria.append({"id": f"detect_{len(criteria)}", "criteria": s.get("name", "장애 감지 정확도"), "weight": w})
    if analysis_steps:
        w = 30 // len(analysis_steps)
        for s in analysis_steps:
            criteria.append({"id": f"analysis_{len(criteria)}", "criteria": s.get("name", "Agent 조사 품질"), "weight": w})
    if observation_steps:
        w = 30 // len(observation_steps)
        for s in observation_steps:
            criteria.append({"id": f"observe_{len(criteria)}", "criteria": s.get("name", "상태 관찰 정확성"), "weight": w})

    if not criteria:
        criteria = [
            {"id": "root_cause", "criteria": "근본 원인을 정확히 식별했는가", "weight": 40},
            {"id": "impact", "criteria": "영향 범위를 올바르게 분석했는가", "weight": 30},
            {"id": "timeline", "criteria": "타임라인을 논리적으로 재구성했는가", "weight": 30},
        ]

    total = sum(c["weight"] for c in criteria)
    if criteria and total != 100:
        criteria[-1]["weight"] += (100 - total)

    scenario["evaluation_rubric"] = {"criteria": criteria, "passing_score": 6}


def _load_scenario_by_id(scenario_id, space_id=None):
    """Load a scenario from DynamoDB with placeholder substitution."""
    space_id = space_id or AGENT_SPACE_ID
    sc = _get_scenario(space_id, scenario_id)
    if not sc:
        return None
    subs = _scenario_placeholder_values()
    if sc.get("namespace"):
        subs["${NAMESPACE}"] = sc["namespace"]
    raw = json.dumps(sc, ensure_ascii=False)
    for placeholder, value in subs.items():
        if not value:
            continue
        raw = raw.replace(placeholder, value)
    return json.loads(raw)


def _resolve_namespace(scenario, space_id):
    """Determine namespace: scenario-level > arch analysis > config.yaml > default."""
    ns = scenario.get("namespace")
    if ns:
        return ns
    try:
        arch = _load_latest_arch(space_id)
        if arch and arch.get("graph", {}).get("namespace"):
            return arch["graph"]["namespace"]
    except Exception:
        pass
    return _cfg_get(_CFG, "kubernetes.namespace", "default")
