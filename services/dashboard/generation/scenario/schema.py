"""Scenario submit tool schema — Bedrock tool_use inputSchema."""

from generation.submit_tool import build_submit_tool

SUBMIT_SCENARIO_TOOL = build_submit_tool(
    name="submit_scenario",
    description=(
        "시나리오 JSON을 제출합니다. 검증 실패 시 에러 메시지가 반환됩니다. "
        "에러를 수정한 후 다시 이 tool을 호출하세요."
    ),
    schema={
        "type": "object",
        "required": ["id", "name", "target_service", "skill_version",
                     "category", "layer", "trigger_mode", "purpose",
                     "trigger", "verification", "restore"],
        "properties": {
            "id": {"type": "string", "description": "시나리오 고유 ID (예: FM-21-worker-oom)"},
            "name": {"type": "string", "description": "시나리오 표시명"},
            "target_service": {"type": "string", "description": "장애 대상 deployment 또는 서비스 이름"},
            "namespace": {"type": "string", "description": "K8s namespace — target_service가 EKS에서 실행되면 필수. 네가 모니터링하는 환경에서 직접 확인하여 채워라."},
            "cluster_name": {"type": "string", "description": "EKS 클러스터 이름 — target_service가 EKS에서 실행되면 필수. 네가 모니터링하는 환경에서 직접 확인하여 채워라."},
            "skill_version": {"type": "string", "enum": ["2.1"]},
            "category": {"type": "string", "enum": ["infrastructure", "application", "composite"]},
            "layer": {"type": "string", "enum": ["network", "compute", "storage", "application"]},
            "trigger_mode": {"type": "string", "enum": ["reactive", "proactive"]},
            "purpose": {"type": "string", "description": "시나리오 목적 1-2문장"},
            "trigger": {
                "type": "object",
                "required": ["type", "command"],
                "properties": {
                    "type": {"type": "string", "enum": ["kubectl", "aws", "fis"]},
                    "command": {"type": "string", "description": "fire-and-forget 단일 명령"},
                },
            },
            "verification": {
                "type": "object",
                "required": ["steps"],
                "properties": {
                    "alarm_name": {"type": "string", "description": "기존 알람 이름 (optional)"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["type", "phase"],
                            "properties": {
                                "type": {"type": "string"},
                                "phase": {
                                    "type": "string",
                                    "enum": ["trigger_active", "effect_observed", "reaction_confirmed"],
                                },
                                "timeout": {"type": "integer"},
                                "poll_interval": {"type": "integer"},
                                "expected": {"type": "string"},
                                "expected_status": {"type": "string"},
                                "command": {"type": "string"},
                                "alarm_name": {"type": "string"},
                                "alarm_spec": {"type": "object"},
                                "namespace": {"type": "string"},
                                "metric_name": {"type": "string"},
                                "dimensions": {"type": "array"},
                                "statistic": {"type": "string"},
                                "comparison": {"type": "string"},
                                "threshold": {"type": "number"},
                            },
                        },
                    },
                },
            },
            "restore": {
                "type": "object",
                "required": ["command"],
                "properties": {
                    "command": {"type": "string", "description": "원래 상태 복원 명령"},
                },
            },
            "architecture": {
                "type": "object",
                "properties": {
                    "components": {"type": "array"},
                    "edges": {"type": "array"},
                    "fault_path": {"type": "array"},
                },
            },
            "normal_flow": {"type": "array", "items": {"type": "object"}},
            "fault_flow": {"type": "array", "items": {"type": "object"}},
            "infrastructure_gaps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ideal": {"type": "string"},
                        "current": {"type": "string"},
                        "action": {"type": "string"},
                        "package": {"type": "string"},
                        "workaround": {"type": "string"},
                    },
                },
            },
            "evaluation_rubric": {"type": "object"},
            "variables": {"type": "object"},
            "pre_cleanup": {"type": "object"},
        },
    },
)
