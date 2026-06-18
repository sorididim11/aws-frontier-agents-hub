"""Simulation Engine v2 — Data contracts between Generator, Verifier, and Orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SimulationStatus(str, Enum):
    CREATED = "created"
    GENERATING = "generating"
    VALIDATING = "validating"
    TRIGGERING = "triggering"
    EXECUTING = "executing"
    OBSERVING = "observing"
    IMPROVING = "improving"
    ESCALATING = "escalating"
    RESTORING = "restoring"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VerdictErrorLayer(int, Enum):
    L1_STRUCTURAL = 1
    L2_SEMANTIC = 2
    L3_FEASIBILITY = 3
    L4_EXECUTION = 4


# ──────────────────────────────────────────────
# Request (입력)
# ──────────────────────────────────────────────


@dataclass
class SimulationRequest:
    """사용자가 '생성 & 실행'을 클릭할 때 전달되는 요청."""

    failure_mode_id: str
    target_service: str
    namespace: str
    space_id: str
    architecture_json: dict = field(default_factory=dict)
    recommendation: dict = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    max_rounds: int = 3
    # 재실행 시 기존 시나리오를 전달
    existing_scenario: dict | None = None


# ──────────────────────────────────────────────
# Artifact (Generator → Verifier)
# ──────────────────────────────────────────────


@dataclass
class ArtifactMetadata:
    attempt: int = 1
    strategy: str = ""
    constraints: list[str] = field(default_factory=list)
    reasoning: str = ""
    generation_time_ms: int = 0


@dataclass
class Artifact:
    """Generator Agent의 출력 — 검증 통과된 시나리오 JSON."""

    scenario_json: dict
    metadata: ArtifactMetadata = field(default_factory=ArtifactMetadata)


# ──────────────────────────────────────────────
# Verdict (Verifier → Orchestrator → Generator)
# ──────────────────────────────────────────────


@dataclass
class StepEvidence:
    """단일 verification step의 실행 결과."""

    name: str
    passed: bool
    command: str = ""
    expected: str = ""
    actual: str = ""
    detail: str = ""
    elapsed_seconds: float = 0.0


@dataclass
class ExecutionEvidence:
    """L4 실행 전체의 관찰 결과."""

    trigger_command: str = ""
    trigger_output: str = ""
    trigger_success: bool = False
    steps: list[StepEvidence] = field(default_factory=list)
    observed_state: dict = field(default_factory=dict)
    elapsed_seconds: float = 0.0


@dataclass
class VerdictError:
    """검증 실패 항목 하나."""

    layer: VerdictErrorLayer
    code: str
    message: str
    field: str = ""
    fix_hint: str = ""


@dataclass
class Verdict:
    """Verifier의 최종 판정."""

    passed: bool
    layer_reached: VerdictErrorLayer = VerdictErrorLayer.L4_EXECUTION
    errors: list[VerdictError] = field(default_factory=list)
    execution_evidence: ExecutionEvidence | None = None
    failure_reason: str = ""
    fix_hint: str = ""
    quality_score: float = 0.0
    verdict_time_ms: int = 0


# ──────────────────────────────────────────────
# Strategy (Escalation)
# ──────────────────────────────────────────────


class EscalationAction(str, Enum):
    SWITCH_APPROACH = "switch_approach"
    SWITCH_FM = "switch_fm"
    GIVE_UP = "give_up"


@dataclass
class Strategy:
    """에스컬레이션 판단 결과."""

    action: EscalationAction
    reason: str = ""
    new_constraints: list[str] = field(default_factory=list)
    suggested_approach: str = ""


# ──────────────────────────────────────────────
# RunResult (최종 출력)
# ──────────────────────────────────────────────


@dataclass
class RoundRecord:
    """한 라운드의 기록."""

    round_num: int
    artifact: Artifact | None = None
    verdict: Verdict | None = None
    strategy: Strategy | None = None


@dataclass
class RunResult:
    """Orchestrator의 최종 반환값."""

    run_id: str = ""
    success: bool = False
    rounds_used: int = 0
    final_artifact: Artifact | None = None
    final_verdict: Verdict | None = None
    history: list[RoundRecord] = field(default_factory=list)
    reason: str = ""


# ──────────────────────────────────────────────
# SSE Event
# ──────────────────────────────────────────────


@dataclass
class SimulationEvent:
    """SSE로 전달되는 이벤트."""

    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
