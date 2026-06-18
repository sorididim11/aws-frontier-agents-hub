"""Steps.py code generation config — submit_steps tool + code validators."""

from __future__ import annotations

import ast
import re
import subprocess
import tempfile
import os

from generation.harness import GenerationHarness
from generation.types import GenerationConfig, ValidationIssue, ValidationResult
from generation.submit_tool import build_submit_tool


SUBMIT_STEPS_TOOL = build_submit_tool(
    name="submit_steps",
    description=(
        "steps.py 코드를 제출합니다. 검증 실패 시 에러가 반환됩니다. "
        "에러를 수정한 후 다시 이 tool을 호출하세요."
    ),
    schema={
        "type": "object",
        "required": ["code"],
        "properties": {
            "code": {
                "type": "string",
                "description": "완전한 steps.py Python 코드",
            },
        },
    },
)


class StepsSyntaxValidator:
    """Python 구문 검증 (ast.parse)."""

    stage = "structural"

    def validate(self, artifact: dict, context: dict | None = None) -> ValidationResult:
        code = artifact.get("code", "")
        issues: list[ValidationIssue] = []

        try:
            ast.parse(code)
        except SyntaxError as e:
            issues.append(ValidationIssue(
                severity="error", code="SYNTAX_ERROR",
                message=f"SyntaxError line {e.lineno}: {e.msg}",
                field="code",
                fix_hint=f"Line {e.lineno} 수정 필요: {e.text.strip() if e.text else ''}",
            ))

        return ValidationResult(valid=not issues, issues=issues)


class StepsImportValidator:
    """필수 import 확인."""

    stage = "structural"

    _REQUIRED_IMPORTS = {"step", "StepResult", "ScenarioContext"}

    def validate(self, artifact: dict, context: dict | None = None) -> ValidationResult:
        code = artifact.get("code", "")
        issues: list[ValidationIssue] = []

        if "from scenario_runner import" not in code:
            issues.append(ValidationIssue(
                severity="error", code="MISSING_IMPORT",
                message="'from scenario_runner import step, StepResult, ScenarioContext' 필수",
                field="code",
                fix_hint="파일 상단에 import 추가.",
            ))
            return ValidationResult(valid=False, issues=issues)

        import_line = ""
        for line in code.split("\n"):
            if "from scenario_runner import" in line:
                import_line = line
                break

        for name in self._REQUIRED_IMPORTS:
            if name not in import_line:
                issues.append(ValidationIssue(
                    severity="error", code="MISSING_IMPORT_NAME",
                    message=f"'{name}' import 누락",
                    field="code",
                    fix_hint=f"from scenario_runner import에 {name} 추가",
                ))

        return ValidationResult(valid=not any(i.severity == "error" for i in issues), issues=issues)


class StepsDecoratorValidator:
    """@step 데코레이터 순서 + ctx API 사용 검증."""

    stage = "structural"

    _ALLOWED_CTX_METHODS = {
        "kubectl", "alarm_info", "run_pod", "get_metric",
        "log_query", "shared", "emit", "aws_cli",
    }

    def validate(self, artifact: dict, context: dict | None = None) -> ValidationResult:
        code = artifact.get("code", "")
        issues: list[ValidationIssue] = []

        step_numbers = re.findall(r'@step\((\d+)', code)
        if not step_numbers:
            issues.append(ValidationIssue(
                severity="error", code="NO_STEPS",
                message="@step 데코레이터가 없음",
                field="code",
                fix_hint="@step(1, '...'), @step(2, '...') 형태로 정의.",
            ))
        else:
            nums = [int(n) for n in step_numbers]
            expected = list(range(1, len(nums) + 1))
            if nums != expected:
                issues.append(ValidationIssue(
                    severity="error", code="STEP_ORDER",
                    message=f"step 번호가 순차적이지 않음: {nums}",
                    field="code",
                    fix_hint=f"1부터 순차 번호: {expected}",
                ))

        # subprocess 사용 금지
        if "subprocess" in code or "os.system" in code:
            issues.append(ValidationIssue(
                severity="error", code="BANNED_IMPORT",
                message="subprocess/os.system 사용 금지 — ctx.kubectl() 또는 ctx.aws_cli() 사용",
                field="code",
            ))

        return ValidationResult(valid=not any(i.severity == "error" for i in issues), issues=issues)


class StepsDryRunValidator:
    """subprocess dry-run 검증."""

    stage = "dry_run"

    def validate(self, artifact: dict, context: dict | None = None) -> ValidationResult:
        code = artifact.get("code", "")
        issues: list[ValidationIssue] = []

        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(code)
                tmp_path = f.name

            env = {**os.environ, "PYTHONPATH": os.path.dirname(os.path.dirname(os.path.dirname(__file__)))}
            result = subprocess.run(
                ["python3", "-c", f"import ast; ast.parse(open('{tmp_path}').read()); print('OK')"],
                capture_output=True, text=True, timeout=10, env=env,
            )
            if result.returncode != 0:
                issues.append(ValidationIssue(
                    severity="error", code="DRY_RUN_FAIL",
                    message=f"Dry-run 실패: {result.stderr[:300]}",
                    field="code",
                ))
        except subprocess.TimeoutExpired:
            issues.append(ValidationIssue(
                severity="warning", code="DRY_RUN_TIMEOUT",
                message="Dry-run 타임아웃 (10s)",
                field="code",
            ))
        except Exception as e:
            issues.append(ValidationIssue(
                severity="warning", code="DRY_RUN_ERROR",
                message=f"Dry-run 오류: {e}",
                field="code",
            ))
        finally:
            try:
                os.unlink(tmp_path)
            except (OSError, UnboundLocalError):
                pass

        return ValidationResult(valid=not any(i.severity == "error" for i in issues), issues=issues)


def create_steps_harness(provider, system_prompt: str = "") -> GenerationHarness:
    """steps.py 생성용 하네스 생성."""
    if not system_prompt:
        from providers.system_prompts import CODE_FIX
        system_prompt = CODE_FIX

    config = GenerationConfig(
        submit_tool_name="submit_steps",
        submit_tool_schema=SUBMIT_STEPS_TOOL,
        validators=[
            StepsSyntaxValidator(),
            StepsImportValidator(),
            StepsDecoratorValidator(),
            StepsDryRunValidator(),
        ],
        fixers=[],
        additional_tools=[],
        system_prompt=system_prompt,
        max_rounds=5,
        force_accept_remaining=1,
    )
    return GenerationHarness(config, provider)
