"""Core types for the Generation Harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ValidationIssue:
    severity: str  # "error" | "warning"
    code: str
    message: str
    field: str
    fix_hint: str = ""


@dataclass
class ValidationResult:
    valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    auto_fixes: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]


@dataclass
class HarnessResult:
    success: bool
    artifact: dict | str
    rounds: int
    validation_history: list[ValidationResult] = field(default_factory=list)


@dataclass
class GenerationConfig:
    submit_tool_name: str
    submit_tool_schema: dict
    validators: list  # Validator instances (ordered cheap → expensive)
    fixers: list = field(default_factory=list)
    additional_tools: list = field(default_factory=list)
    system_prompt: str = ""
    max_rounds: int = 5
    force_accept_remaining: int = 1
    model_id: str = ""


class Validator(Protocol):
    """Protocol for pluggable validators."""

    stage: str

    def validate(self, artifact: dict, context: dict | None = None) -> ValidationResult:
        ...


class AutoFixer(Protocol):
    """Protocol for mechanical auto-corrections."""

    def fix(self, artifact: dict, context: dict | None = None) -> tuple[dict, list[str]]:
        ...
