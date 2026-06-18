"""Collect current environment state via DevOps Agent Chat.

Replaces the K8s-specific enricher.py with a platform-agnostic approach:
Agent uses its own tools (CloudWatch, CloudTrail, X-Ray, Datadog, etc.)
to gather real-time metrics, change history, error rates, and endpoint info.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Optional

from simulator.engine.chat_discovery import AgentChatClient, ChatResponse


STATE_QUESTIONS = [
    {
        "id": "STATE-RESOURCE",
        "question": (
            "각 서비스의 현재 리소스 사용량(CPU, Memory)과 설정된 limit 대비 비율을 알려주세요. "
            "JSON으로 응답해주세요:\n"
            '{{"resources": [{{"service": "이름", "cpu_usage": "50%", "memory_usage": "70%", '
            '"cpu_limit": "200m", "memory_limit": "256Mi", "status": "healthy|warning|critical"}}]}}'
        ),
    },
    {
        "id": "STATE-CHANGES",
        "question": (
            "최근 24시간 CloudTrail에서 인프라 변경 이벤트를 조회해주세요. "
            "특히 Security Group 변경, IAM policy 변경, 배포, 스케일링, 설정 변경 위주로. "
            "JSON으로 응답해주세요:\n"
            '{{"changes": [{{"time": "ISO8601", "event": "이벤트명", '
            '"resource": "리소스", "user": "변경자", "detail": "요약"}}]}}'
        ),
    },
    {
        "id": "STATE-ERRORS",
        "question": (
            "서비스 간 통신 에러율과 p99 지연시간을 알려주세요. 최근 1시간 데이터 기준으로. "
            "JSON으로 응답해주세요:\n"
            '{{"communications": [{{"source": "서비스A", "target": "서비스B", '
            '"error_rate": 0.01, "p99_latency_ms": 200, "request_count": 1000}}]}}'
        ),
    },
    {
        "id": "STATE-ENDPOINTS",
        "question": (
            "외부에서 접근 가능한 엔드포인트(ALB, API Gateway, CloudFront, NLB 등)와 "
            "주요 트래픽 패턴을 알려주세요. 각 엔드포인트의 URL, 주요 경로, 초당 요청 수, "
            "평균 응답 시간을 포함해주세요. "
            "JSON으로 응답해주세요:\n"
            '{{"endpoints": [{{"url": "https://...", "type": "ALB|APIGateway|CloudFront", '
            '"paths": ["/api/..."], "rps": 50, "avg_latency_ms": 100, "error_rate": 0.01}}]}}'
        ),
    },
]


@dataclass
class CurrentState:
    """Aggregated current environment state from Agent responses."""
    resources: dict = field(default_factory=dict)
    changes: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    endpoints: dict = field(default_factory=dict)
    raw_responses: dict = field(default_factory=dict)
    collected_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "resources": self.resources,
            "changes": self.changes,
            "errors": self.errors,
            "endpoints": self.endpoints,
            "collected_at": self.collected_at,
        }

    def to_enrichment(self) -> dict:
        """Convert to enrichment dict compatible with ScenarioRecommender."""
        return {
            "current_state": self.to_dict(),
            "raw_responses": self.raw_responses,
        }


_QUESTION_KEY_MAP = {
    "STATE-RESOURCE": "resources",
    "STATE-CHANGES": "changes",
    "STATE-ERRORS": "errors",
    "STATE-ENDPOINTS": "endpoints",
}


class StateCollector:
    """Collects current environment state by asking DevOps Agent structured questions."""

    def __init__(self, on_progress=None):
        self.on_progress = on_progress
        self.conversation: list = []

    def collect(
        self,
        client: AgentChatClient,
        execution_id: str,
    ) -> CurrentState:
        state = CurrentState(collected_at=time.time())

        for sq in STATE_QUESTIONS:
            qid = sq["id"]
            question = sq["question"]
            key = _QUESTION_KEY_MAP.get(qid, qid)

            print(f"[STATE] Collecting {qid}...")
            if self.on_progress:
                self.on_progress(qid, "asking", {})

            try:
                resp = client.ask(execution_id, question)
                self.conversation.append(resp)

                state.raw_responses[qid] = resp.final_text

                if resp.parsed_json:
                    setattr(state, key, resp.parsed_json)
                    print(f"[STATE]   → {qid}: JSON parsed OK")
                else:
                    setattr(state, key, {"raw_text": resp.final_text})
                    print(f"[STATE]   → {qid}: stored as raw text")

                if self.on_progress:
                    self.on_progress(qid, "done", {
                        "has_json": bool(resp.parsed_json),
                        "text_length": len(resp.final_text),
                    })

            except Exception as e:
                print(f"[STATE]   → {qid}: ERROR {e}")
                state.raw_responses[qid] = f"ERROR: {e}"
                if self.on_progress:
                    self.on_progress(qid, "error", {"error": str(e)})

        return state

    def get_conversation_log(self) -> list:
        return [
            {
                "question": resp.question,
                "answer": resp.final_text,
                "tool_calls": resp.tool_calls,
                "parsed_json": resp.parsed_json,
            }
            for resp in self.conversation
        ]
