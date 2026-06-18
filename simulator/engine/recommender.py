"""Scenario recommender: Bedrock Claude analyzes architecture → recommends specific chaos scenarios.

Flow:
1. Load abstract scenario templates (layer-level chaos categories)
2. Convert topology graph to architecture summary
3. Send both to Bedrock Claude for architecture-specific recommendations
4. Optionally collect additional data via Agent chat for enrichment
5. Return ranked recommendations with rationale
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional

import boto3
from botocore.config import Config as BotoConfig

from simulator.config import SimulatorConfig
from simulator.engine.topology import ServiceGraph


def _extract_recommendation_json(text: str) -> Optional[dict]:
    """Extract JSON from Bedrock response — handles markdown fences, trailing commas, truncation."""
    import re

    # Try markdown fences first
    for pattern in [r"```json\s*\n(.*?)\n```", r"```\s*\n(.*?)\n```"]:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            content = match.group(1)
            parsed = _try_parse_json(content)
            if parsed:
                return parsed

    # Try raw braces
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        parsed = _try_parse_json(match.group(0))
        if parsed:
            return parsed

    return None


def _try_parse_json(content: str) -> Optional[dict]:
    """Try to parse JSON, fixing common LLM output issues."""
    import re

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Fix trailing commas before ] or }
    fixed = re.sub(r",\s*([}\]])", r"\1", content)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # Truncated response — try closing open structures
    for suffix in ["]}}", "]}", "}", "]}]}}"]:
        try:
            return json.loads(fixed + suffix)
        except json.JSONDecodeError:
            continue

    return None


from simulator.engine.failure_modes import get_failure_modes


RECOMMEND_PROMPT = """당신은 카오스 엔지니어링 전문가입니다. 애플리케이션 아키텍처와 장애 모드 목록이 주어지면,
이 특정 아키텍처에 가장 가치 있는 장애 시나리오를 추천하세요.

**중요**: 플랫폼(EKS, ECS, EC2, Lambda 등)에 종속되지 않는 추천을 해주세요.
트리거는 AWS API와 FIS만 사용 가능합니다. 앱 코드를 수정하거나 내부 endpoint에 접근할 수 없습니다.

## 아키텍처 (JSON)
{architecture_json}

## 현재 환경 상태
{current_state_json}

## 장애 모드 (10종)
{failure_modes_json}

## 지시사항
1. 아키텍처를 분석하세요: 서비스, 통신 패턴, 의존성, 단일 장애점(SPOF), 컴퓨트 플랫폼
2. 각 추천 시나리오에 대해 이 특정 아키텍처에 왜 가치 있는지 설명하세요
3. 가치 순으로 5-8개 시나리오를 추천하세요 (가장 영향력 있는 것 먼저)
4. 각 시나리오의 정확한 대상 서비스/리소스를 지정하세요
5. trigger_mode를 지정하세요: reactive(알람 트리거), proactive(Agent 질문 트리거), either(둘 다)
6. proactive 시나리오는 investigation_prompt(Agent에게 보낼 조사 질문)을 포함하세요

**응답은 반드시 한국어로 작성하세요.**

JSON으로만 응답하세요:
{{
  "recommendations": [
    {{
      "failure_mode_id": "FM-01",
      "name": "이 아키텍처에 특화된 시나리오 이름",
      "target": {{"service": "대상 서비스", "resource": "SG ID 또는 리소스 식별자"}},
      "priority": "high|medium|low",
      "trigger_mode": "reactive|proactive|either",
      "rationale": "이 시나리오가 이 아키텍처에 가치 있는 이유",
      "expected_impact": "무엇이 깨지고 어떻게 전파되는지",
      "detection_challenge": "Agent가 진단하기 어려운 이유",
      "investigation_prompt": "proactive인 경우 Agent에게 보낼 조사 질문",
      "additional_data_needed": []
    }}
  ],
  "architecture_analysis": {{
    "critical_path": "핵심 데이터 흐름 설명",
    "single_points_of_failure": ["서비스 이름"],
    "risk_areas": ["간략한 설명"]
  }}
}}"""


@dataclass
class Recommendation:
    failure_mode_id: str
    name: str
    target: dict
    priority: str
    trigger_mode: str  # reactive | proactive | either
    rationale: str
    expected_impact: str
    detection_challenge: str
    investigation_prompt: str = ""
    additional_data_needed: list = field(default_factory=list)
    # Legacy alias
    @property
    def template_id(self) -> str:
        return self.failure_mode_id


@dataclass
class RecommendationResult:
    recommendations: list = field(default_factory=list)
    architecture_analysis: dict = field(default_factory=dict)
    raw_response: str = ""

    def to_dict(self) -> dict:
        return {
            "recommendations": [
                {
                    "failure_mode_id": r.failure_mode_id,
                    "name": r.name,
                    "target": r.target,
                    "priority": r.priority,
                    "trigger_mode": r.trigger_mode,
                    "rationale": r.rationale,
                    "expected_impact": r.expected_impact,
                    "detection_challenge": r.detection_challenge,
                    "investigation_prompt": r.investigation_prompt,
                    "additional_data_needed": r.additional_data_needed,
                }
                for r in self.recommendations
            ],
            "architecture_analysis": self.architecture_analysis,
        }


class ScenarioRecommender:
    """Recommends chaos scenarios by sending architecture + templates to Bedrock Claude."""

    def __init__(self, cfg: SimulatorConfig, model_id: str = "us.anthropic.claude-opus-4-6-v1"):
        self.cfg = cfg
        self.model_id = model_id
        self._client = None

    @property
    def bedrock(self):
        if self._client is None:
            session_kwargs = {"region_name": self.cfg.chat.region}
            if self.cfg.chat.profile:
                session_kwargs["profile_name"] = self.cfg.chat.profile
            session = boto3.Session(**session_kwargs)
            self._client = session.client(
                "bedrock-runtime",
                config=BotoConfig(read_timeout=300),
            )
        return self._client

    def recommend(self, graph: ServiceGraph, enrichment: dict = None) -> RecommendationResult:
        """Analyze architecture and recommend chaos scenarios.

        Args:
            graph: Discovered service topology
            enrichment: Optional dict — may contain current_state from StateCollector
        """
        arch_json = self._build_architecture_summary(graph, enrichment)
        failure_modes = get_failure_modes()
        fm_for_prompt = [
            {k: v for k, v in fm.items()
             if k in ("id", "name", "layer", "description", "trigger_mode",
                       "applicable_when", "detection_challenge", "proactive_question")}
            for fm in failure_modes
        ]

        current_state = {}
        if enrichment and "current_state" in enrichment:
            current_state = enrichment["current_state"]

        prompt = RECOMMEND_PROMPT.format(
            architecture_json=json.dumps(arch_json, indent=2, ensure_ascii=False),
            failure_modes_json=json.dumps(fm_for_prompt, indent=2, ensure_ascii=False),
            current_state_json=json.dumps(current_state, indent=2, ensure_ascii=False) if current_state else "수집되지 않음",
        )

        response = self.bedrock.invoke_model(
            modelId=self.model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 8192,
                "messages": [{"role": "user", "content": prompt}],
            }),
        )

        body = json.loads(response["body"].read())
        text = body["content"][0]["text"]
        stop_reason = body.get("stop_reason", "")

        if stop_reason == "max_tokens":
            print("[RECOMMEND] WARNING: Response truncated — increase max_tokens or simplify prompt")

        return self._parse_response(text)

    def recommend_with_enrichment(
        self, graph: ServiceGraph, chat_client=None, exec_id: str = None
    ) -> RecommendationResult:
        """Two-pass recommendation: first pass recommends, then collects additional data
        via Agent chat for scenarios that need it, and re-ranks.

        Args:
            graph: Discovered service topology
            chat_client: Optional AgentChatClient for follow-up questions
            exec_id: Chat session execution ID
        """
        result = self.recommend(graph)

        needs_data = [
            r for r in result.recommendations if r.additional_data_needed
        ]
        if not needs_data or not chat_client or not exec_id:
            return result

        enrichment = self._collect_additional_data(
            chat_client, exec_id, graph.namespace, needs_data
        )

        if enrichment:
            return self.recommend(graph, enrichment=enrichment)
        return result

    def _build_architecture_summary(self, graph: ServiceGraph, enrichment: dict = None) -> dict:
        nodes = []
        for n in graph.nodes:
            node_info = {
                "name": n.name,
                "namespace": n.namespace,
                "type": n.service_type,
                "ports": n.ports,
                "compute_type": n.compute_type or n.kind,
            }
            if n.group:
                node_info["group"] = n.group
            if enrichment and n.name in enrichment:
                node_info["enrichment"] = enrichment[n.name]
            nodes.append(node_info)

        edges = []
        for e in graph.edges:
            edge_info = {
                "source": e.source,
                "target": e.target,
                "protocol": e.protocol,
                "port": e.port,
            }
            if e.paths:
                edge_info["paths"] = e.paths
            if e.methods:
                edge_info["methods"] = e.methods
            edges.append(edge_info)

        callers_map = {}
        for e in graph.edges:
            callers_map.setdefault(e.target, []).append(e.source)

        return {
            "namespace": graph.namespace,
            "services": nodes,
            "communications": edges,
            "dependency_summary": {
                target: callers for target, callers in callers_map.items()
            },
        }

    def _parse_response(self, text: str) -> RecommendationResult:
        data = _extract_recommendation_json(text)
        result = RecommendationResult(raw_response=text)

        if not data:
            print("[RECOMMEND] WARNING: Could not parse JSON from Bedrock response")
            return result

        for rec in data.get("recommendations", []):
            fm_id = rec.get("failure_mode_id", "") or rec.get("template_id", "")
            result.recommendations.append(Recommendation(
                failure_mode_id=fm_id,
                name=rec.get("name", ""),
                target=rec.get("target", {}),
                priority=rec.get("priority", "medium"),
                trigger_mode=rec.get("trigger_mode", "reactive"),
                rationale=rec.get("rationale", ""),
                expected_impact=rec.get("expected_impact", ""),
                detection_challenge=rec.get("detection_challenge", ""),
                investigation_prompt=rec.get("investigation_prompt", ""),
                additional_data_needed=rec.get("additional_data_needed", []),
            ))

        result.architecture_analysis = data.get("architecture_analysis", {})
        return result

    def _collect_additional_data(self, chat_client, exec_id, namespace, needs: list) -> dict:
        """Ask Agent for additional data needed by recommendations."""
        data_types = set()
        for rec in needs:
            data_types.update(rec.additional_data_needed)

        enrichment = {}

        if "resource_limits" in data_types or "probe_config" in data_types:
            from simulator.engine.chat_discovery import Q_ENRICHMENT
            print("[RECOMMEND] Collecting enrichment data from Agent...")
            resp = chat_client.ask(exec_id, Q_ENRICHMENT)
            data = resp.parsed_json
            if data and "enrichment" in data:
                enrichment = data["enrichment"]
                print(f"[RECOMMEND]   → Got enrichment for {len(enrichment)} services")

        return enrichment

    def save(self, result: RecommendationResult, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"[OK] Recommendations saved to {path}")
