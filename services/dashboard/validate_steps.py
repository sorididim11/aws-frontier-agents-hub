"""
생성된 steps.py 정적 검증.

실행 없이 AST 분석으로 다음을 체크:
1. Python 구문 오류
2. 올바른 import (from scenario_runner import ...)
3. @step 데코레이터 존재 및 순서
4. ctx 메서드가 허용 목록에 있는지
5. ctx._shared["key"] 직접 접근 금지 (ctx._shared.get("key") 필수)
6. StepResult 반환 여부
7. underscore private 메서드 호출 금지 (_shared 제외)

Usage:
    from validate_steps import validate_steps_code
    errors, warnings = validate_steps_code(code_string)
"""
import ast
import re
from typing import Optional

ALLOWED_CTX_METHODS = {
    "kubectl", "alarm_info", "compute_timeouts", "wait_alarm_state",
    "port_forward", "curl", "run_pod", "delete_pod", "wait_pod_running",
    "log", "cleanup",
    "inject_latency", "clear_latency", "send_auxiliary_traffic",
}

ALLOWED_CTX_ATTRIBUTES = {
    "_shared", "alarm_name", "namespace", "aws_profile", "aws_region",
}

REQUIRED_IMPORTS = {"step", "StepResult", "ScenarioContext"}


def validate_steps_code(code: str) -> tuple[list[str], list[str]]:
    """Validate generated steps.py code statically.

    Returns (errors, warnings) where errors are blocking issues
    and warnings are recommendations.
    """
    errors = []
    warnings = []

    # 1. Syntax check
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        errors.append(f"구문 오류: line {e.lineno}: {e.msg}")
        return errors, warnings

    # 2. Import check
    imports_found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "scenario_runner":
            for alias in node.names:
                imports_found.add(alias.name)

    missing_imports = REQUIRED_IMPORTS - imports_found
    if missing_imports:
        errors.append(f"필수 import 누락: {', '.join(sorted(missing_imports))} (from scenario_runner import ...)")

    # 3. @step decorator check
    step_functions = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Name) and deco.func.id == "step":
                    if deco.args and isinstance(deco.args[0], ast.Constant):
                        step_functions.append((deco.args[0].value, node.name, node.lineno))

    if not step_functions:
        errors.append("@step 데코레이터가 있는 함수가 없음")
    else:
        numbers = [s[0] for s in step_functions]
        if numbers != sorted(numbers):
            errors.append(f"step 번호 순서 오류: {numbers} (1부터 순서대로여야 함)")
        if numbers and numbers[0] != 1:
            warnings.append(f"step 시작 번호가 1이 아님: {numbers[0]}")

    # 4. ctx method/attribute validation
    _check_ctx_usage(tree, errors, warnings)

    # 5. _shared["key"] direct access check
    _check_shared_access(tree, errors, warnings)

    # 6. StepResult return check
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            is_step_fn = any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "step"
                for d in node.decorator_list
            )
            if is_step_fn:
                has_return = _has_step_result_return(node)
                if not has_return:
                    warnings.append(f"함수 '{node.name}' (line {node.lineno}): StepResult 반환이 없는 경로가 있을 수 있음")

    # 7. Domain rule check (anti-pattern detection)
    _check_domain_rules(code, errors, warnings)

    return errors, warnings


_BANNED_PATTERNS = [
    (r'\bsubprocess\.(run|call|Popen|check_output)\b', "subprocess 직접 호출 금지 — ctx API 사용"),
    (r'\bos\.(system|popen)\b', "os.system/popen 금지 — ctx API 사용"),
    (r'inject-latency\?seconds=[4-9]', "지연 4초 이상 금지 — 반드시 2초 사용 (worker timeout 방지)"),
    (r'inject-latency\?seconds=\d{2,}', "지연 10초 이상 금지 — 반드시 2초 사용"),
]


def _check_domain_rules(code: str, errors: list, warnings: list):
    """도메인 규칙 위반 감지 (command 복사, 금지 패턴)."""
    lines = code.split('\n')

    for pattern, message in _BANNED_PATTERNS:
        for match in re.finditer(pattern, code):
            line_no = code[:match.start()].count('\n') + 1
            errors.append(f"line {line_no}: {message}")

    # wget: Pod 내부 명령(kubectl run -- wget)은 warning, 그 외는 error
    for match in re.finditer(r'\bwget\b', code):
        line_no = code[:match.start()].count('\n') + 1
        line = lines[line_no - 1]
        if 'ctx.kubectl(' in line or 'run_cmd' in line or 'pod_command' in line.lower():
            warnings.append(f"line {line_no}: Pod 명령에 wget 포함 — curl 기반 이미지 권장")
        else:
            errors.append(f"line {line_no}: wget 사용 금지 — ctx.curl() 사용")


def _check_ctx_usage(tree: ast.AST, errors: list, warnings: list):
    """Check that only allowed methods/attributes are accessed on ctx."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "ctx":
            attr = node.attr
            if attr.startswith("_") and attr != "_shared":
                errors.append(
                    f"line {node.lineno}: ctx.{attr} — private 메서드/속성 사용 금지 "
                    f"(ctx._shared만 예외). ctx.log() 등 public API를 사용하세요"
                )
            elif not attr.startswith("_") and attr not in ALLOWED_CTX_METHODS and attr not in ALLOWED_CTX_ATTRIBUTES:
                warnings.append(
                    f"line {node.lineno}: ctx.{attr} — 알 수 없는 메서드/속성. "
                    f"허용 목록: {', '.join(sorted(ALLOWED_CTX_METHODS))}"
                )


def _check_shared_access(tree: ast.AST, errors: list, warnings: list):
    """Check for ctx._shared["key"] direct subscript access (should use .get())."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            # ctx._shared["key"] pattern
            if (isinstance(node.value, ast.Attribute)
                    and isinstance(node.value.value, ast.Name)
                    and node.value.value.id == "ctx"
                    and node.value.attr == "_shared"):
                # Check if it's in an assignment target (write) vs read
                # Writing ctx._shared["key"] = value is OK
                # Reading ctx._shared["key"] is BAD (should use .get())
                parent = _find_parent(tree, node)
                if isinstance(parent, ast.Assign) and node in _get_targets(parent):
                    pass  # Assignment target, OK
                elif isinstance(parent, ast.AugAssign) and node == parent.target:
                    pass  # Augmented assignment target, OK
                else:
                    if isinstance(node.slice, ast.Constant):
                        key = node.slice.value
                    else:
                        key = "..."
                    errors.append(
                        f"line {node.lineno}: ctx._shared[\"{key}\"] 직접 읽기 — "
                        f"resume 시 KeyError 위험. ctx._shared.get(\"{key}\") 사용 필수"
                    )


def _find_parent(tree: ast.AST, target_node: ast.AST) -> Optional[ast.AST]:
    """Find parent node of target_node in AST."""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            if child is target_node:
                return node
    return None


def _get_targets(assign_node: ast.Assign) -> list:
    """Get all target nodes from an Assign."""
    targets = []
    for t in assign_node.targets:
        if isinstance(t, ast.Tuple):
            targets.extend(t.elts)
        else:
            targets.append(t)
    return targets


def _has_step_result_return(func_node: ast.FunctionDef) -> bool:
    """Check if function has at least one return with StepResult."""
    for node in ast.walk(func_node):
        if isinstance(node, ast.Return) and node.value:
            if isinstance(node.value, ast.Call):
                if isinstance(node.value.func, ast.Name) and node.value.func.id == "StepResult":
                    return True
    return False


def validate_scenario_json(scenario: dict) -> tuple[list[str], list[str]]:
    """Validate scenario JSON structure.

    Returns (errors, warnings).
    """
    errors = []
    warnings = []

    required_fields = ["id", "name", "category", "purpose", "verification"]
    for field in required_fields:
        if field not in scenario:
            errors.append(f"필수 필드 누락: '{field}'")

    verification = scenario.get("verification", {})
    if isinstance(verification, dict):
        alarms = verification.get("alarms", [])
        steps = verification.get("steps", [])

        if not steps:
            warnings.append("verification.steps가 비어있음")
        if len(steps) < 3:
            warnings.append(f"verification.steps가 3개 미만: {len(steps)}개")

        for alarm in alarms:
            if not alarm.get("name"):
                errors.append("verification.alarms에 name이 없는 항목 존재")

    trigger = scenario.get("trigger", {})
    if trigger:
        if not trigger.get("type"):
            errors.append("trigger.type 누락 (aws_cli 또는 fis)")
        if not trigger.get("command"):
            errors.append("trigger.command 누락")

    restore = scenario.get("restore", {})
    if not restore:
        warnings.append("restore 섹션 없음 — 장애 복구 방법 미정의")
    elif not restore.get("command"):
        warnings.append("restore.command 없음")

    rubric = scenario.get("evaluation_rubric", [])
    if rubric:
        total_weight = sum(r.get("weight", 0) for r in rubric if isinstance(r, dict))
        if total_weight != 100:
            warnings.append(f"evaluation_rubric weight 합계: {total_weight} (100이어야 함)")

    # Runtime ID check
    raw = str(scenario)
    if re.search(r'i-[0-9a-f]{8,17}', raw):
        errors.append("런타임 InstanceId 하드코딩 감지 (i-xxx)")
    if re.search(r'\b[a-z]+-[a-z0-9]+-[a-z0-9]{5}\b', raw) and "pod" in raw.lower():
        warnings.append("PodName 하드코딩 가능성 — 동적 생성되는 pod명은 사용 금지")

    # Cross-section variable consistency check
    _check_variable_consistency(scenario, errors, warnings)

    return errors, warnings


_GLOBAL_VARS = {"NAMESPACE", "AWS_REGION", "AWS_ACCOUNT_ID", "PROJECT_NAME"}


def _check_variable_consistency(scenario: dict, errors: list, warnings: list):
    """trigger/verification/restore 간 변수 사용 일관성 검증."""
    variables = set(scenario.get("variables", {}).keys())
    all_vars = variables | _GLOBAL_VARS

    def _extract_vars(text: str) -> set:
        if not text:
            return set()
        return set(re.findall(r'\$\{([A-Z_][A-Z0-9_]*)\}', text))

    def _extract_pod_names(text: str) -> set:
        if not text:
            return set()
        names = set()
        for m in re.finditer(r'kubectl\s+(?:run|get\s+pod|delete\s+pod)\s+([a-z][a-z0-9-]+)', text):
            name = m.group(1)
            if not name.startswith("${") and name not in ("pod", "pods", "--"):
                names.add(name)
        return names

    # Collect all commands
    trigger_cmd = scenario.get("trigger", {}).get("command", "") or ""
    restore_cmd = scenario.get("restore", {}).get("command", "") or ""
    cleanup_cmd = scenario.get("pre_cleanup", {}).get("command", "") or ""
    verif_cmds = []
    for step in scenario.get("verification", {}).get("steps", []):
        if step.get("command"):
            verif_cmds.append(step["command"])

    # Check 1: undeclared variables
    all_commands = [trigger_cmd, restore_cmd, cleanup_cmd] + verif_cmds
    for cmd in all_commands:
        for var in _extract_vars(cmd):
            if var not in all_vars:
                errors.append(f"미선언 변수 사용: ${{{var}}} (variables에 정의 필요)")

    # Check 2: trigger에서 생성하는 pod 이름이 verification에서 리터럴로 사용되는지
    trigger_pods = _extract_pod_names(trigger_cmd)
    var_values = set(str(v) for v in scenario.get("variables", {}).values())

    for pod_name in trigger_pods:
        if pod_name in var_values:
            continue  # variables에 선언된 값 — OK (변수로 참조 가능)
        # trigger에 리터럴 pod 이름이 있는데 variables에 없음
        warnings.append(
            f"trigger의 pod '{pod_name}'이 variables에 미선언 — "
            f"verification/restore와 이름 불일치 위험"
        )

    # Check 3: verification에서 리터럴 pod 이름 사용 시, trigger와 일치하는지
    for cmd in verif_cmds:
        verif_pods = _extract_pod_names(cmd)
        for pod_name in verif_pods:
            if pod_name in var_values:
                continue
            if pod_name in trigger_pods:
                continue  # trigger와 같은 리터럴 — 위험하지만 일치는 함
            if trigger_pods:
                errors.append(
                    f"verification의 pod '{pod_name}'이 trigger의 pod "
                    f"{trigger_pods}와 불일치 — 변수 사용 필수"
                )


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python validate_steps.py <steps.py>")
        sys.exit(1)

    filepath = sys.argv[1]
    with open(filepath) as f:
        code = f.read()

    errors, warnings = validate_steps_code(code)

    if errors:
        print(f"ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  ✗ {e}")
    if warnings:
        print(f"WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  ⚠ {w}")
    if not errors and not warnings:
        print("OK: 검증 통과")

    sys.exit(1 if errors else 0)
