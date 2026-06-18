"""
Error Response Strategy Matrix.
Maps (step_type, error_category) → ErrorAction for automated failure handling.

Two-layer resolution:
  1. JSON-declared error_handling (from scenario-generate skill) — takes priority
  2. Static matrix fallback — default behavior per step_type × error_category
"""
from enum import Enum


class ErrorAction(str, Enum):
    POLL_CONTINUE = "poll_continue"
    RETRY_BACKOFF = "retry_backoff"
    AGENT_CORRECT = "agent_correct"
    TRIGGER_REINJECT = "trigger_reinject"
    BLOCKED = "blocked"


BACKOFF_CONFIG = {
    "initial_delay": 5,
    "multiplier": 2,
    "max_attempts": 3,
    "max_delay": 40,
}

# JSON error_handling.on_* 값 → ErrorAction 매핑
_ACTION_ALIASES = {
    "poll_continue": ErrorAction.POLL_CONTINUE,
    "retry_backoff": ErrorAction.RETRY_BACKOFF,
    "agent_correct": ErrorAction.AGENT_CORRECT,
    "trigger_reinject": ErrorAction.TRIGGER_REINJECT,
    "blocked": ErrorAction.BLOCKED,
}

# 에러 카테고리 → JSON 필드명 매핑
_CATEGORY_TO_FIELD = {
    "timeout": "on_timeout",
    "command_error": "on_command_error",
    "config_error": "on_config_error",
    "infra_missing": "on_infra_missing",
    "transient": "on_transient",
}

# ── Static fallback matrix ──
# step_type → {error_category → ErrorAction}
_PC = ErrorAction.POLL_CONTINUE
_RB = ErrorAction.RETRY_BACKOFF
_AC = ErrorAction.AGENT_CORRECT
_TR = ErrorAction.TRIGGER_REINJECT
_BL = ErrorAction.BLOCKED

_DEFAULT_ROW = {"timeout": _AC, "command_error": _AC, "config_error": _PC, "infra_missing": _BL, "transient": _RB}

ERROR_RESPONSE_MATRIX = {
    "alarm_state":           {"timeout": _TR, "command_error": _AC, "config_error": _PC, "infra_missing": _BL, "transient": _RB},
    "cw_alarm":              {"timeout": _TR, "command_error": _AC, "config_error": _PC, "infra_missing": _BL, "transient": _RB},
    "metric_check":          {"timeout": _TR, "command_error": _AC, "config_error": _PC, "infra_missing": _BL, "transient": _RB},
    "kubectl_check":         {"timeout": _PC, "command_error": _AC, "config_error": _PC, "infra_missing": _BL, "transient": _RB},
    "pod_status":            {"timeout": _PC, "command_error": _AC, "config_error": _PC, "infra_missing": _BL, "transient": _RB},
    "pod_logs":              {"timeout": _PC, "command_error": _AC, "config_error": _PC, "infra_missing": _BL, "transient": _RB},
    "api_call":              {"timeout": _AC, "command_error": _AC, "config_error": _AC, "infra_missing": _BL, "transient": _RB},
    "log_pattern":           {"timeout": _PC, "command_error": _AC, "config_error": _PC, "infra_missing": _BL, "transient": _RB},
    "xray_trace":            {"timeout": _PC, "command_error": _AC, "config_error": _PC, "infra_missing": _BL, "transient": _RB},
    "xray_latency":          {"timeout": _PC, "command_error": _AC, "config_error": _PC, "infra_missing": _BL, "transient": _RB},
    "lambda_logs":           {"timeout": _PC, "command_error": _AC, "config_error": _PC, "infra_missing": _BL, "transient": _RB},
    "fis_experiment":        {"timeout": _PC, "command_error": _BL, "config_error": _PC, "infra_missing": _BL, "transient": _RB},
    "slack_message":         {"timeout": _PC, "command_error": _BL, "config_error": _PC, "infra_missing": _BL, "transient": _RB},
    "investigation_event":   {"timeout": _PC, "command_error": _BL, "config_error": _PC, "infra_missing": _BL, "transient": _RB},
    "agent_investigation":   {"timeout": _PC, "command_error": _BL, "config_error": _PC, "infra_missing": _BL, "transient": _RB},
}


def get_response_action(step_type: str, error_category: str, error_handling: dict = None) -> ErrorAction:
    """Resolve the appropriate ErrorAction for a failed step.

    Resolution order:
      1. error_handling dict from JSON (scenario-generate skill output)
      2. Static ERROR_RESPONSE_MATRIX fallback
    """
    if error_handling:
        field = _CATEGORY_TO_FIELD.get(error_category)
        if field:
            declared = error_handling.get(field)
            if declared and declared in _ACTION_ALIASES:
                return _ACTION_ALIASES[declared]

    row = ERROR_RESPONSE_MATRIX.get(step_type, _DEFAULT_ROW)
    return row.get(error_category, ErrorAction.AGENT_CORRECT)


def compute_backoff_delay(attempt: int) -> float:
    """Compute delay for a given retry attempt (0-indexed)."""
    delay = BACKOFF_CONFIG["initial_delay"] * (BACKOFF_CONFIG["multiplier"] ** attempt)
    return min(delay, BACKOFF_CONFIG["max_delay"])
