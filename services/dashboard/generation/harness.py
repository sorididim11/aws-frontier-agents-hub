"""GenerationHarness — submit_tool 패턴 기반 범용 AI 생성 + 검증 루프.

arch_analysis.py의 submit_analysis 패턴을 일반화:
- LLM이 submit tool 호출 → executor에서 검증
- 실패 → 에러를 tool result로 반환 → LLM 같은 세션에서 재시도
- 성공 → "승인됨" 반환 → 결과 저장
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from generation.types import (
    GenerationConfig,
    HarnessResult,
    ValidationIssue,
    ValidationResult,
)


class GenerationHarness:
    """범용 AI 생성 + 검증 오케스트레이터."""

    def __init__(self, config: GenerationConfig, provider):
        self.config = config
        self.provider = provider
        self._result = None
        self._rounds = 0
        self._history: list[ValidationResult] = []
        self._context: dict | None = None

    def generate(self, prompt: str, context: dict | None = None) -> HarnessResult:
        """메인 엔트리포인트. tool_use 기반 생성 + 검증 루프."""
        self._result = None
        self._rounds = 0
        self._history = []
        self._context = context or {}

        tools = self._build_tools()
        max_rounds = self.config.max_rounds + 5

        t0 = time.time()
        result = self.provider.generate_with_tools(
            prompt=prompt,
            tools=tools,
            tool_executor=self._dispatch,
            system_prompt=self.config.system_prompt,
            max_tokens=16384,
            max_rounds=max_rounds,
        )
        elapsed = time.time() - t0

        print(f"[HARNESS] done: success={self._result is not None}, "
              f"rounds={self._rounds}, elapsed={elapsed:.1f}s")

        return HarnessResult(
            success=self._result is not None,
            artifact=self._result or {},
            rounds=self._rounds,
            validation_history=self._history,
        )

    def validate_only(self, artifact: dict, context: dict | None = None) -> ValidationResult:
        """하네스 없이 검증만 실행 (기존 코드 호환용)."""
        self._context = context or {}
        return self._run_validators(artifact)

    def _build_tools(self) -> list:
        tools = [self.config.submit_tool_schema]
        if self.config.additional_tools:
            tools.extend(self.config.additional_tools)
        return tools

    def _dispatch(self, tool_name: str, tool_input: dict) -> str:
        """Tool executor — submit 도구면 검증, 아니면 기존 executor 위임."""
        if tool_name == self.config.submit_tool_name:
            return self._handle_submit(tool_input)

        from providers.tool_executor import execute_tool
        profile = self._context.get("aws_profile", "")
        region = self._context.get("aws_region", "")
        kubectl_ctx = self._context.get("kubectl_context", "")
        return execute_tool(tool_name, tool_input,
                           context=kubectl_ctx, profile=profile, region=region)

    def _handle_submit(self, tool_input: dict) -> str:
        """검증 → 수락/거부."""
        self._rounds += 1
        artifact = tool_input

        # 1. Auto-fix
        fixes_applied = []
        for fixer in self.config.fixers:
            artifact, fixes = fixer.fix(artifact, self._context)
            fixes_applied.extend(fixes)

        # 2. Progressive validation
        vr = self._run_validators(artifact)
        vr.auto_fixes = fixes_applied
        self._history.append(vr)

        err_codes = [i.code for i in vr.errors]
        print(f"[HARNESS] submit #{self._rounds}: "
              f"errors={len(vr.errors)}, warnings={len(vr.warnings)}, "
              f"fixes={len(fixes_applied)}"
              + (f" → {err_codes}" if err_codes else ""))

        # 3. 강제 수락 (남은 라운드 부족 시)
        remaining = self.config.max_rounds - self._rounds
        if not vr.valid and remaining <= self.config.force_accept_remaining:
            self._result = artifact
            print(f"[HARNESS] force-accept (remaining={remaining})")
            return ("경고와 함께 강제 수락됨. 일부 검증 실패가 있으나 실행 시 확인됩니다.\n"
                    f"경고: {', '.join(i.message for i in vr.errors[:3])}")

        if not vr.valid:
            return self._format_feedback(vr)

        # 4. 수락
        self._result = artifact
        return "검증 통과. 시나리오가 승인되었습니다."

    def _run_validators(self, artifact: dict) -> ValidationResult:
        """Progressive validation — cheap first, stop on first error batch."""
        all_issues: list[ValidationIssue] = []

        for validator in self.config.validators:
            try:
                result = validator.validate(artifact, self._context)
            except Exception as e:
                print(f"[HARNESS] validator {validator.__class__.__name__} error: {e}")
                all_issues.append(ValidationIssue(
                    severity="warning",
                    code="VALIDATOR_ERROR",
                    message=f"Validator {validator.__class__.__name__} 오류: {e}",
                    field="",
                ))
                continue

            all_issues.extend(result.issues)

            if result.errors:
                break

        has_errors = any(i.severity == "error" for i in all_issues)
        return ValidationResult(valid=not has_errors, issues=all_issues)

    def _format_feedback(self, vr: ValidationResult) -> str:
        """LLM이 이해할 수 있는 에러 피드백."""
        lines = ["검증 실패. 아래 문제를 수정 후 다시 submit하세요:\n"]
        for issue in vr.errors:
            lines.append(f"- [{issue.code}] {issue.message}")
            if issue.fix_hint:
                lines.append(f"  힌트: {issue.fix_hint}")
        if vr.warnings:
            lines.append(f"\n경고 {len(vr.warnings)}건 (non-blocking):")
            for w in vr.warnings[:3]:
                lines.append(f"  - {w.message}")
        return "\n".join(lines)
