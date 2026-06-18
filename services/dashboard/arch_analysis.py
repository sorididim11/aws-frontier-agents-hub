"""Architecture analysis module — Bedrock agent-based discovery + recommendation.

Standalone module with no simulator dependency. Accepts boto3 Session directly.
Shared by both simulator web UI and Frontier Agent Hub (space.html).

Core classes:
  - ArchitectAgent: Bedrock converse agent that interviews DevOps Agent Chat
  - ArchitectureAgentDiscoverer: Orchestrates app + infra agents
  - ArchitectureRecommender: Bedrock recommendation engine
  - ArchitectureDiscoverer: (deprecated) hardcoded Q&A pipeline
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

import prompts as _prompts


# ══════════════════════════════════════════════════════════════════
# Question Loader
# ══════════════════════════════════════════════════════════════════

_QUESTIONS_CACHE = None


def load_questions(path: str = None) -> dict:
    global _QUESTIONS_CACHE
    if _QUESTIONS_CACHE:
        return _QUESTIONS_CACHE
    if not path:
        path = os.path.join(os.path.dirname(__file__), "arch_questions.json")
    with open(path, encoding="utf-8") as f:
        _QUESTIONS_CACHE = json.load(f)
    return _QUESTIONS_CACHE


# ══════════════════════════════════════════════════════════════════
# Data Models
# ══════════════════════════════════════════════════════════════════

@dataclass
class ServiceNode:
    name: str
    namespace: str
    kind: str = "Deployment"
    labels: dict = field(default_factory=dict)
    ports: list = field(default_factory=list)
    service_type: str = "app"  # app, cache, db, gateway, queue
    group: str = ""

    def label_selector(self) -> dict:
        return self.labels if self.labels else {"app": self.name}


@dataclass
class ServiceEdge:
    source: str
    target: str
    protocol: str = "tcp"
    port: int = 0
    paths: list = field(default_factory=list)
    methods: list = field(default_factory=list)
    description: str = ""

    @property
    def is_http(self) -> bool:
        return self.protocol in ("http", "grpc")


@dataclass
class ServiceGraph:
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    namespace: str = ""
    discovered_at: float = 0.0

    def get_node(self, name: str) -> Optional[ServiceNode]:
        return next((n for n in self.nodes if n.name == name), None)

    def get_callers(self, target: str) -> list:
        return [e.source for e in self.edges if e.target == target]

    def get_callees(self, source: str) -> list:
        return [e.target for e in self.edges if e.source == source]

    def to_dict(self) -> dict:
        return {
            "namespace": self.namespace,
            "discovered_at": self.discovered_at,
            "nodes": [
                {"name": n.name, "namespace": n.namespace, "kind": n.kind,
                 "labels": n.labels, "ports": n.ports, "service_type": n.service_type,
                 "group": n.group}
                for n in self.nodes
            ],
            "edges": [
                {"source": e.source, "target": e.target, "protocol": e.protocol,
                 "port": e.port, "paths": e.paths, "methods": e.methods,
                 "description": e.description}
                for e in self.edges
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ServiceGraph":
        nodes = [
            ServiceNode(
                name=n["name"], namespace=n.get("namespace", ""),
                kind=n.get("kind", "Deployment"), labels=n.get("labels", {}),
                ports=n.get("ports", []), service_type=n.get("service_type", "app"),
                group=n.get("group", ""),
            )
            for n in data.get("nodes", [])
        ]
        edges = [
            ServiceEdge(
                source=e["source"], target=e["target"],
                protocol=e.get("protocol", "tcp"), port=e.get("port", 0),
                paths=e.get("paths", []), methods=e.get("methods", []),
                description=e.get("description", ""),
            )
            for e in data.get("edges", [])
        ]
        return cls(
            nodes=nodes, edges=edges,
            namespace=data.get("namespace", ""),
            discovered_at=data.get("discovered_at", 0.0),
        )


# ══════════════════════════════════════════════════════════════════
# Questions are loaded from arch_questions.json at runtime.
# ══════════════════════════════════════════════════════════════════

def _get_phase_question(phase_id: str) -> str:
    """Get question text for a phase from the JSON config."""
    q = load_questions()
    for p in q.get("phases", []):
        if p["id"] == phase_id:
            return p["question"]
    raise ValueError(f"Unknown phase: {phase_id}")


def _get_phase_name(phase_id: str) -> str:
    q = load_questions()
    for p in q.get("phases", []):
        if p["id"] == phase_id:
            return p["name"]
    return phase_id


# ══════════════════════════════════════════════════════════════════
# Chat Client
# ══════════════════════════════════════════════════════════════════

@dataclass
class ChatBlock:
    index: int
    block_type: str
    text: str
    block_id: str = ""


@dataclass
class ChatResponse:
    question: str
    blocks: list = field(default_factory=list)
    parsed_json: dict = field(default_factory=dict)
    raw_text: str = ""
    session_id: str = ""

    @property
    def final_text(self) -> str:
        for b in reversed(self.blocks):
            if b.block_type == "final_response" and b.text:
                return b.text
        for b in reversed(self.blocks):
            if b.block_type == "text" and b.text:
                return b.text
        return self.raw_text

    @property
    def tool_calls(self) -> list:
        return [b.text for b in self.blocks if b.block_type == "tool_summary"]

    @property
    def reasoning(self) -> list:
        return [b.text for b in self.blocks if b.block_type == "text"]


_chat_sessions = {}  # {space_id: executionId} — Space당 분석용 채팅 1개 유지


class AgentChatClient:
    """Wrapper that delegates all Agent chat calls to the shared ChatWorker.

    All send_message calls go through one queue → one thread, preventing
    concurrent API calls against the same Space.
    """

    def __init__(self, space_id: str, session=None):
        self.space_id = space_id
        self._last_session_reused = False

    def get_or_create_session(self) -> str:
        cached = _chat_sessions.get(self.space_id)
        if cached:
            print(f"[CHAT] Space {self.space_id[:8]} 기존 채팅 재사용: {cached}")
            self._last_session_reused = True
            return cached
        self._last_session_reused = False
        return self.create_session()

    def create_session(self, max_retries: int = 3) -> str:
        _chat_sessions.pop(self.space_id, None)
        exec_id = "NEW"
        _chat_sessions[self.space_id] = exec_id
        print(f"[CHAT] Space {self.space_id[:8]} 새 채팅 예약 (첫 ask에서 생성)")
        return exec_id

    def invalidate_session(self):
        _chat_sessions.pop(self.space_id, None)

    def ask(self, execution_id: str, question: str,
            max_retries: int = 3) -> ChatResponse:
        from chat_worker import init_worker, get_worker
        from app_config import _profile_for_space, AWS_REGION
        profile = _profile_for_space(self.space_id)
        init_worker(profile=profile, region=AWS_REGION)
        session_id = execution_id if execution_id != "NEW" else None
        t0 = time.time()
        print(f"[CHAT] Agent Space 전송, 질문 길이={len(question)} chars, "
              f"session={'신규' if not session_id else session_id[:16]}")
        raw = get_worker(profile).send_raw(
            space_id=self.space_id,
            session_id=session_id or "",
            prompt=question,
            user_id="arch-analysis",
        )
        elapsed = time.time() - t0
        resp = ChatResponse(
            question=question,
            raw_text=raw.get("reply", ""),
            session_id=raw.get("session_id", session_id or ""),
        )
        print(f"[CHAT] Agent Space 완료: {len(resp.raw_text)} chars, {elapsed:.1f}초")
        if resp.session_id and execution_id == "NEW":
            _chat_sessions[self.space_id] = resp.session_id
            print(f"[CHAT] Space {self.space_id[:8]} 세션 확정: {resp.session_id[:16]}")
        resp.parsed_json = _extract_json(resp.raw_text) or {}
        return resp


def _extract_json(text: str) -> Optional[dict]:
    for pattern in [r"```json\s*\n(.*?)\n```", r"```\s*\n(.*?)\n```"]:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _try_parse_json(content: str) -> Optional[dict]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    fixed = re.sub(r",\s*([}\]])", r"\1", content)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    for suffix in ["]}}", "]}", "}", "]}]}}"]:
        try:
            return json.loads(fixed + suffix)
        except json.JSONDecodeError:
            continue
    return None


def _extract_recommendation_json(text: str) -> Optional[dict]:
    for pattern in [r"```json\s*\n(.*?)\n```", r"```\s*\n(.*?)\n```"]:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            parsed = _try_parse_json(match.group(1))
            if parsed:
                return parsed
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        parsed = _try_parse_json(match.group(0))
        if parsed:
            return parsed
    return None


# ══════════════════════════════════════════════════════════════════
# Architecture Discoverer
# ══════════════════════════════════════════════════════════════════

@dataclass
class AnalysisResult:
    """Full analysis output — business, app, infra, risk layers."""
    system_name: str = ""
    description: str = ""
    workflows: list = field(default_factory=list)
    taxonomy: list = field(default_factory=list)
    graph: ServiceGraph = field(default_factory=ServiceGraph)
    compute: list = field(default_factory=list)
    managed_services: list = field(default_factory=list)
    spof: list = field(default_factory=list)
    blast_radius: list = field(default_factory=list)
    external_deps: list = field(default_factory=list)
    observability_gaps: list = field(default_factory=list)
    conversations: dict = field(default_factory=dict)
    k8s_detail: dict = field(default_factory=dict)

    def to_dict(self, include_conversations=False) -> dict:
        d = {
            "system_name": self.system_name,
            "description": self.description,
            "workflows": self.workflows,
            "taxonomy": self.taxonomy,
            "graph": self.graph.to_dict(),
            "compute": self.compute,
            "managed_services": self.managed_services,
            "spof": self.spof,
            "blast_radius": self.blast_radius,
            "external_deps": self.external_deps,
            "observability_gaps": self.observability_gaps,
        }
        if include_conversations:
            d["conversations"] = self.conversations
        if self.k8s_detail:
            d["k8s_detail"] = self.k8s_detail
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AnalysisResult":
        graph_data = d.get("graph", {})
        def _make_node(n):
            n.setdefault("namespace", "")
            n.setdefault("kind", "Deployment")
            return ServiceNode(**n)

        graph = ServiceGraph(
            nodes=[_make_node(n) if isinstance(n, dict) else n for n in graph_data.get("nodes", [])],
            edges=[ServiceEdge(**e) if isinstance(e, dict) else e for e in graph_data.get("edges", [])],
            namespace=graph_data.get("namespace", ""),
            discovered_at=graph_data.get("discovered_at", 0.0),
        )
        return cls(
            system_name=d.get("system_name", ""),
            description=d.get("description", ""),
            workflows=d.get("workflows", []),
            taxonomy=d.get("taxonomy", []),
            graph=graph,
            compute=d.get("compute", []),
            managed_services=d.get("managed_services", []),
            spof=d.get("spof", []),
            blast_radius=d.get("blast_radius", []),
            external_deps=d.get("external_deps", []),
            observability_gaps=d.get("observability_gaps", []),
            conversations=d.get("conversations", {}),
            k8s_detail=d.get("k8s_detail", {}),
        )


class ArchitectureDiscoverer:
    """Top-down architecture discovery by chatting with DevOps Agent.

    Phase 1: 비즈니스 이해 — 시스템 목적, 워크플로우, 서비스 목록
    Phase 2: 서비스 통신 — 서비스 간 호출 관계
    Phase 3: 인프라 매핑 — 각 서비스의 실행 환경 (K8s, Lambda, EC2 등)
    Phase 4: 의존성·위험 — 외부 의존성, SPOF, blast radius
    """

    def __init__(self, space_id: str, session, on_progress=None):
        self.space_id = space_id
        self.session = session
        self.on_progress = on_progress
        self.conversation: list = []
        self._cache: Optional[AnalysisResult] = None

    def _emit(self, phase, question, resp, result=None):
        if self.on_progress:
            self.on_progress(phase, question, {
                "answer": resp.final_text,
                "tool_calls": resp.tool_calls,
                "parsed_json": resp.parsed_json,
                "result": result or {},
            })

    def discover(self, force: bool = False,
                 bedrock_client=None) -> AnalysisResult:
        if self._cache and not force:
            if time.time() - self._cache.graph.discovered_at < 300:
                return self._cache

        client = AgentChatClient(self.space_id, self.session)
        exec_id = client.get_or_create_session()
        print(f"[ARCH] Session: {exec_id}")

        result = AnalysisResult()
        result.graph.discovered_at = time.time()

        q_config = load_questions()
        phases = sorted(q_config.get("phases", []), key=lambda p: p["order"])
        for phase in phases:
            self._run_phase(client, exec_id, phase["id"], result)

        self._validate(result)

        if bedrock_client:
            self._evaluate_and_refine(client, exec_id, result, bedrock_client, q_config)

        self._cache = result
        return result

    def _run_phase(self, client, exec_id, phase_id: str, result: AnalysisResult):
        """Run a single phase question and parse results into AnalysisResult."""
        name = _get_phase_name(phase_id)
        question = _get_phase_question(phase_id)
        print(f"[ARCH] Phase: {name}...")
        resp = client.ask(exec_id, question)
        self.conversation.append(resp)
        data = resp.parsed_json

        handler = getattr(self, f"_parse_{phase_id}", None)
        parsed_summary = {}
        if data and handler:
            parsed_summary = handler(data, result)
        elif not data:
            print(f"[ARCH]   → WARNING: {name} 파싱 실패")

        self._emit(phase_id, question, resp, parsed_summary)

    def _parse_business(self, data: dict, result: AnalysisResult) -> dict:
        result.system_name = data.get("system_name", "")
        result.description = data.get("description", "")
        result.workflows = data.get("workflows", [])
        for svc in data.get("services", []):
            result.graph.nodes.append(ServiceNode(
                name=svc["name"],
                namespace="",
                kind="Service",
                labels={"role": svc.get("role", "")},
                service_type=svc.get("service_type", "app"),
                group=svc.get("group", ""),
            ))
        print(f"[ARCH]   → {result.system_name}: {len(result.graph.nodes)} 서비스")
        return {"system_name": result.system_name,
                "count": len(result.graph.nodes),
                "names": [n.name for n in result.graph.nodes]}

    def _parse_communication(self, data: dict, result: AnalysisResult) -> dict:
        if "edges" not in data:
            return {"count": 0}
        for e in data["edges"]:
            result.graph.edges.append(ServiceEdge(
                source=e["source"], target=e["target"],
                protocol=e.get("protocol", "tcp"),
                port=e.get("port", 0),
                paths=e.get("paths") or [],
                methods=e.get("methods") or [],
                description=e.get("description", ""),
            ))
        print(f"[ARCH]   → {len(result.graph.edges)} 통신 경로")
        return {"count": len(result.graph.edges),
                "edges": [f"{e.source}→{e.target}" for e in result.graph.edges]}

    def _parse_infra(self, data: dict, result: AnalysisResult) -> dict:
        result.compute = data.get("compute", [])
        for c in result.compute:
            node = result.graph.get_node(c.get("service", ""))
            if node:
                node.kind = c.get("platform", "unknown")
                detail = c.get("detail", {})
                if isinstance(detail, dict):
                    node.namespace = detail.get("namespace", "")
                    node.ports = c.get("ports", node.ports)

        for ms in data.get("managed_services", []):
            name = ms.get("name", "")
            short = name
            if ".rds.amazonaws.com" in name:
                short = name.split(".")[0]
            elif "arn:aws:" in name:
                short = name.split(":")[-1].split("/")[-1]
            elif "." in name and len(name) > 30:
                short = name.split(".")[0]

            svc_type_map = {"rds": "db", "elasticache": "cache", "dynamodb": "db",
                            "s3": "db", "sqs": "queue", "sns": "queue"}
            svc_type = svc_type_map.get(ms.get("type", ""), "app")

            if not result.graph.get_node(short):
                inherited_group = ""
                for user in ms.get("used_by", []):
                    user_node = result.graph.get_node(user)
                    if user_node and user_node.group:
                        inherited_group = user_node.group
                        break
                result.graph.nodes.append(ServiceNode(
                    name=short, namespace="managed",
                    kind=ms.get("type", "managed"), service_type=svc_type,
                    group=inherited_group,
                ))
            for user in ms.get("used_by", []):
                result.graph.edges.append(ServiceEdge(
                    source=user, target=short, protocol="tcp",
                    description=ms.get("description", ""),
                ))
            result.managed_services.append(ms)

        print(f"[ARCH]   → {len(result.compute)} compute, "
              f"{len(result.managed_services)} managed services")
        return {"compute": len(result.compute),
                "managed": len(result.managed_services)}

    def _parse_dependencies(self, data: dict, result: AnalysisResult) -> dict:
        for dep in data.get("external_deps", []):
            target = dep.get("target", "")
            short = target
            if "." in target and len(target) > 30:
                short = target.split(".")[0]
            if not result.graph.get_node(short):
                dep_type = dep.get("type", "external")
                svc_type = {"db": "db", "cache": "cache",
                            "api": "gateway"}.get(dep_type, "app")
                source_node = result.graph.get_node(dep.get("source", ""))
                inherited_group = source_node.group if source_node else ""
                result.graph.nodes.append(ServiceNode(
                    name=short, namespace="external",
                    kind="ExternalService", service_type=svc_type,
                    group=inherited_group,
                ))
            result.graph.edges.append(ServiceEdge(
                source=dep.get("source", ""), target=short,
                protocol=dep.get("protocol", "tcp"),
                port=dep.get("port", 0),
                description=dep.get("description", ""),
            ))
            result.external_deps.append(dep)

        result.spof = data.get("spof", [])
        result.blast_radius = data.get("blast_radius", [])
        result.observability_gaps = data.get("observability_gaps", [])
        print(f"[ARCH]   → {len(result.external_deps)} 외부 의존, "
              f"{len(result.spof)} SPOF, "
              f"{len(result.blast_radius)} blast radius")
        return {"external": len(result.external_deps),
                "spof": len(result.spof),
                "blast_radius": len(result.blast_radius)}

    def _validate(self, result: AnalysisResult):
        known = {n.name for n in result.graph.nodes}
        for e in result.graph.edges:
            if e.source and e.source not in known:
                result.graph.nodes.append(ServiceNode(
                    name=e.source, namespace="", kind="Service",
                ))
                known.add(e.source)
            if e.target and e.target not in known:
                result.graph.nodes.append(ServiceNode(
                    name=e.target, namespace="", kind="Service",
                ))
                known.add(e.target)
        print(f"[ARCH] 최종: {len(result.graph.nodes)} 노드, "
              f"{len(result.graph.edges)} 엣지")

    def _evaluate_and_refine(self, client, exec_id, result: AnalysisResult,
                             bedrock_client, q_config: dict):
        """Bedrock evaluation loop — score the analysis, ask followup questions."""
        eval_cfg = q_config.get("evaluation", {})
        prompt_template = eval_cfg.get("prompt", "")
        threshold = eval_cfg.get("pass_threshold", 70)
        max_iter = eval_cfg.get("max_iterations", 2)

        if not prompt_template:
            return

        for iteration in range(max_iter):
            print(f"[ARCH] 평가 루프 {iteration + 1}/{max_iter}...")

            analysis_json = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
            prompt = prompt_template.replace("{analysis_json}", analysis_json)

            try:
                resp = bedrock_client.invoke_model(
                    modelId="us.anthropic.claude-sonnet-4-6",
                    contentType="application/json",
                    accept="application/json",
                    body=json.dumps({
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 4096,
                        "messages": [{"role": "user", "content": prompt}],
                    }),
                )
                body = json.loads(resp["body"].read())
                text = body["content"][0]["text"]
                eval_data = _extract_recommendation_json(text)
            except Exception as e:
                print(f"[ARCH]   → 평가 실패: {e}")
                break

            if not eval_data:
                print("[ARCH]   → 평가 JSON 파싱 실패")
                break

            score = eval_data.get("score", 0)
            verdict = eval_data.get("verdict", "needs_refinement")
            issues = eval_data.get("issues", [])
            followups = eval_data.get("followup_questions", [])

            print(f"[ARCH]   → 점수: {score}/100, 판정: {verdict}, "
                  f"이슈: {len(issues)}, 추가 질문: {len(followups)}")

            self._emit("evaluation", f"평가 루프 #{iteration + 1}", type("R", (), {
                "final_text": text, "tool_calls": [], "parsed_json": eval_data,
                "blocks": [], "question": ""})(), {
                "score": score, "verdict": verdict,
                "issues": issues, "followups": followups,
                "iteration": iteration + 1,
            })

            if score >= threshold or verdict == "pass":
                print(f"[ARCH]   → 평가 통과 (score={score})")
                break

            if not followups:
                print("[ARCH]   → 추가 질문 없음, 종료")
                break

            for fq in followups:
                q_text = fq.get("question", "")
                phase_id = fq.get("phase", "")
                if not q_text:
                    continue
                print(f"[ARCH]   → 추가 질문 ({phase_id}): {q_text[:80]}...")
                fq_resp = client.ask(exec_id, q_text)
                self.conversation.append(fq_resp)
                if fq_resp.parsed_json and phase_id:
                    handler = getattr(self, f"_parse_{phase_id}", None)
                    if handler:
                        handler(fq_resp.parsed_json, result)
                self._emit(f"followup_{phase_id}", q_text, fq_resp, {
                    "iteration": iteration + 1,
                    "phase": phase_id,
                })

    def get_conversation_log(self) -> list:
        log = []
        for resp in self.conversation:
            log.append({
                "question": resp.question,
                "answer": resp.final_text,
                "tool_calls": resp.tool_calls,
                "reasoning_steps": resp.reasoning,
                "parsed_json": resp.parsed_json,
            })
        return log


# ══════════════════════════════════════════════════════════════════
# Bedrock Agent — converse API + tool_use
# ══════════════════════════════════════════════════════════════════


def _load_agent_config() -> dict:
    """Load agent configuration from arch_questions.json."""
    cfg = load_questions()
    return cfg.get("agents", {}), cfg.get("tools", {})


def _build_tool_config(tools_cfg: dict) -> dict:
    """Build toolConfig for Bedrock converse API from JSON config."""
    tools = []
    for _name, spec in tools_cfg.items():
        tools.append(spec)
    return {"tools": tools}


class ArchitectAgent:
    """Bedrock converse agent that parses DevOps Agent answers into structured output.

    Fixed-question architecture: code sends Q1 to DevOps Agent, Sonnet parses
    the answer. Falls back to tool_use loop only for follow-up questions.
    """

    def __init__(self, agent_type: str, bedrock_client, chat_client: AgentChatClient,
                 execution_id: str, on_event=None,
                 model_id: str = "us.anthropic.claude-sonnet-4-6",
                 max_turns: int = 10, quality_threshold: int = 75,
                 system_prompt: str = None,
                 tagged_resources: str = None,
                 cancel_event=None):
        self.agent_type = agent_type
        self.bedrock = bedrock_client
        self.chat_client = chat_client
        self.execution_id = execution_id
        self.on_event = on_event
        self.model_id = model_id
        self.max_turns = max_turns
        self.quality_threshold = quality_threshold
        self._system_prompt_override = system_prompt
        self._tagged_resources = tagged_resources
        self._cancel = cancel_event
        self._final_result = None
        self._turn = 0
        self.interview_log: list = []

    def _emit(self, event: dict):
        if self.on_event:
            self.on_event(event)

    def run(self, context: dict = None, devops_answer: str = None) -> dict:
        """Run the agent. If devops_answer is provided, skip Q1 and parse directly.

        Fixed-question flow (devops_answer provided):
          1. Feed the pre-collected DevOps Agent answer to Sonnet for parsing
          2. Sonnet calls submit_analysis with structured output
          3. If validation fails, Sonnet can use ask_devops_agent for 1 follow-up

        Legacy flow (devops_answer=None):
          Falls back to the original autonomous interview loop.
        """
        agents_cfg, tools_cfg = _load_agent_config()
        agent_cfg = agents_cfg.get(self.agent_type, {})
        system_prompt = self._system_prompt_override or agent_cfg.get("system_prompt", "You are an architect.")
        if self._tagged_resources:
            system_prompt += "\n\n## 사전 정보 — 알려진 AWS 리소스\n" + self._tagged_resources
        tool_config = _build_tool_config(tools_cfg)

        self._emit({"type": "phase_start", "agent": self.agent_type,
                     "label": agent_cfg.get("display_name", self.agent_type) + " 분석 시작"})

        messages = []
        if context:
            messages.append({
                "role": "user",
                "content": [{"text": (
                    "선행 분석 결과가 있습니다. 이를 참고하여 분석하세요:\n\n"
                    + json.dumps(context, indent=2, ensure_ascii=False)
                )}],
            })
            messages.append({
                "role": "assistant",
                "content": [{"text": "선행 분석 결과를 확인했습니다. 이를 기반으로 분석을 시작하겠습니다."}],
            })

        if devops_answer:
            messages.append({
                "role": "user",
                "content": [{"text": (
                    "DevOps Agent에게 질문하여 아래 답변을 받았습니다. "
                    "이 답변을 파싱하여 submit_analysis로 구조화된 결과를 제출하세요. "
                    "답변 내용이 부족한 경우에만 ask_devops_agent로 최대 1회 추가 질문하세요.\n\n"
                    "--- DevOps Agent 답변 ---\n" + devops_answer
                )}],
            })
        else:
            messages.append({
                "role": "user",
                "content": [{"text": "분석을 시작하세요. ask_devops_agent 도구로 DevOps Agent에게 질문하고, "
                                      "충분한 정보를 수집하면 submit_analysis로 결과를 제출하세요."}],
            })

        for turn in range(self.max_turns):
            if self._cancel and self._cancel.is_set():
                print(f"[ARCH-AGENT] {self.agent_type}: 취소됨")
                return self._final_result or {}
            self._turn = turn + 1

            if turn == self.max_turns - 2:
                messages.append({
                    "role": "user",
                    "content": [{"text": (
                        "남은 턴이 2턴뿐입니다. 지금까지 수집한 정보로 즉시 submit_analysis를 호출하세요. "
                        "완벽하지 않아도 됩니다. 추가 질문 없이 바로 submit하세요."
                    )}],
                })

            response = None
            for attempt in range(3):
                try:
                    t0 = time.time()
                    print(f"[ARCH-AGENT] {self.agent_type} turn {turn+1}: converse 호출 "
                          f"(messages={len(messages)}, attempt={attempt+1})")
                    response = self.bedrock.converse(
                        modelId=self.model_id,
                        messages=messages,
                        system=[{"text": system_prompt}],
                        toolConfig=tool_config,
                        inferenceConfig={"maxTokens": 16384},
                    )
                    print(f"[ARCH-AGENT] {self.agent_type} turn {turn+1}: converse 완료 "
                          f"{time.time()-t0:.1f}초, stop={response.get('stopReason','?')}")
                    break
                except Exception as e:
                    elapsed = time.time() - t0
                    wait = 2 ** attempt
                    print(f"[ARCH-AGENT] converse 실패 (시도 {attempt+1}/3): "
                          f"{elapsed:.1f}초 후 {type(e).__name__}: {e}, "
                          f"{wait}초 후 재시도")
                    if attempt < 2:
                        time.sleep(wait)
                    else:
                        self._emit({"type": "error", "error": f"Bedrock converse 오류 (3회 실패): {e}"})
            if response is None:
                break

            stop_reason = response.get("stopReason", "end_turn")
            output_msg = response.get("output", {}).get("message", {})
            content_blocks = output_msg.get("content", [])

            messages.append({"role": "assistant", "content": content_blocks})

            tool_results = []
            pending_tools = []
            for block in content_blocks:
                if "text" in block:
                    text = block["text"]
                    if text.strip():
                        self._emit({"type": "agent_thinking", "agent": self.agent_type,
                                     "thought": text[:500], "turn": self._turn})
                elif "toolUse" in block:
                    tu = block["toolUse"]
                    pending_tools.append((tu["name"], tu.get("input", {}), tu["toolUseId"]))

            ask_tools = [t for t in pending_tools if t[0] == "ask_devops_agent"]
            other_tools = [t for t in pending_tools if t[0] != "ask_devops_agent"]

            if len(ask_tools) > 1:
                print(f"[ARCH-AGENT] {self.agent_type} turn {turn+1}: "
                      f"{len(ask_tools)}개 ask_devops_agent 병렬 실행")
                futures = {}
                with ThreadPoolExecutor(max_workers=len(ask_tools)) as pool:
                    for name, inp, tid in ask_tools:
                        futures[pool.submit(self._dispatch_tool, name, inp)] = tid
                    for fut in as_completed(futures):
                        tid = futures[fut]
                        result_text = fut.result()
                        tool_results.append({
                            "toolResult": {
                                "toolUseId": tid,
                                "content": [{"text": result_text[:8000]}],
                            }
                        })
            else:
                for name, inp, tid in ask_tools:
                    result_text = self._dispatch_tool(name, inp)
                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tid,
                            "content": [{"text": result_text[:8000]}],
                        }
                    })

            for name, inp, tid in other_tools:
                result_text = self._dispatch_tool(name, inp)
                tool_results.append({
                    "toolResult": {
                        "toolUseId": tid,
                        "content": [{"text": result_text[:8000]}],
                    }
                })

            if self._final_result is not None:
                self._emit({"type": "phase_complete", "agent": self.agent_type,
                             "result": self._final_result})
                return self._final_result

            if tool_results:
                messages.append({"role": "user", "content": tool_results})
                continue

            if stop_reason == "end_turn":
                for block in content_blocks:
                    if "text" in block:
                        parsed = _extract_json(block["text"])
                        if parsed:
                            self._final_result = parsed
                            self._emit({"type": "phase_complete", "agent": self.agent_type,
                                         "result": parsed})
                            return parsed
                print(f"[ARCH-AGENT] {self.agent_type}: end_turn but no parseable result")
                break

            if stop_reason == "max_tokens":
                print(f"[ARCH-AGENT] {self.agent_type} turn {turn+1}: max_tokens 도달, 이어서 진행")
                messages.append({
                    "role": "user",
                    "content": [{"text": "응답이 잘렸습니다. 이어서 완성하세요. "
                                          "submit_analysis를 아직 호출하지 않았다면 지금 바로 호출하세요."}],
                })
                continue

            if stop_reason not in ("tool_use", "end_turn"):
                print(f"[ARCH-AGENT] {self.agent_type}: unexpected stopReason={stop_reason}")
                break

        print(f"[ARCH-AGENT] {self.agent_type}: 최대 턴({self.max_turns}) 도달")
        self._emit({"type": "agent_evaluation", "agent": self.agent_type,
                     "score": 0, "verdict": "incomplete"})
        return self._final_result or {}

    def _validate_analysis(self, analysis: dict) -> list:
        """코드 검증 — 에이전트 출력의 구조적 완전성을 검사."""
        errors = []

        if self.agent_type == "L1":
            services = analysis.get("services", [])
            edges = analysis.get("edges", [])
            taxonomy = analysis.get("taxonomy", [])

            if not services:
                errors.append("services가 비어있습니다")
                return errors
            if not taxonomy:
                errors.append("taxonomy가 비어있습니다 — 앱 그룹 분류 체계가 필요합니다")
            no_group = [s.get("name") for s in services if not s.get("group")]
            if no_group:
                errors.append(f"group이 없는 서비스 {len(no_group)}개: {', '.join(no_group[:5])}")
            missing_3field = [s.get("name") for s in services
                              if not s.get("service") or not s.get("resource_type")]
            if missing_3field:
                errors.append(f"service 또는 resource_type이 없는 서비스 {len(missing_3field)}개: "
                              f"{', '.join(missing_3field[:5])} — 3단 분리(service, resource_type, resource_name) 필수")
            if not edges:
                errors.append("edges가 비어있습니다 — 서비스 간 통신 흐름이 필요합니다")

            connected = set()
            for e in edges:
                connected.add(e.get("source", ""))
                connected.add(e.get("target", ""))
            orphans = [s.get("name") for s in services
                       if s.get("name") not in connected
                       and s.get("service_type") not in ("db", "cache", "queue")]
            if orphans:
                errors.append(f"edge가 없는 서비스 (orphan) {len(orphans)}개: {', '.join(orphans[:5])} "
                              "— 설정 부속품(Parameter Group, Subnet Group 등)이면 services에서 제거하고, "
                              "실제 서비스이면 edge를 추가하세요. 추가 질문 없이 수정 후 다시 submit하세요.")

        elif self.agent_type == "L2":
            compute = analysis.get("compute", [])
            managed = analysis.get("managed_services", [])
            if not compute and not managed:
                errors.append("compute와 managed_services가 모두 비어있습니다")
                return errors

            no_group_compute = [c.get("name", c.get("service", "?")) for c in compute if not c.get("group")]
            no_group_managed = [m.get("name", "?") for m in managed if not m.get("group")]
            if no_group_compute:
                errors.append(f"compute에서 group 없음: {', '.join(no_group_compute[:5])}")
            if no_group_managed:
                errors.append(f"managed_services에서 group 없음: {', '.join(no_group_managed[:5])}")
            missing_3f_compute = [c.get("name", "?") for c in compute
                                  if not c.get("service") or not c.get("resource_type")]
            missing_3f_managed = [m.get("name", "?") for m in managed
                                  if not m.get("service") or not m.get("resource_type")]
            if missing_3f_compute:
                errors.append(f"compute에서 service/resource_type 없음: {', '.join(missing_3f_compute[:5])}")
            if missing_3f_managed:
                errors.append(f"managed_services에서 service/resource_type 없음: {', '.join(missing_3f_managed[:5])}")

        return errors

    def _dispatch_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "ask_devops_agent":
            question = tool_input.get("question", "")
            self._emit({"type": "agent_question", "agent": self.agent_type,
                         "question": question, "turn": self._turn})

            try:
                resp = self.chat_client.ask(self.execution_id, question)
                answer = resp.final_text
                self._emit({
                    "type": "agent_answer", "agent": self.agent_type,
                    "answer": answer[:800], "turn": self._turn,
                    "tool_calls": resp.tool_calls[:5],
                    "has_json": bool(resp.parsed_json),
                })
                self.interview_log.append({
                    "turn": self._turn, "question": question,
                    "answer": answer[:2000],
                    "tool_calls": [str(tc)[:200] for tc in resp.tool_calls[:5]],
                })
                return answer
            except Exception as e:
                error_msg = f"DevOps Agent 응답 오류: {e}"
                self._emit({"type": "agent_answer", "agent": self.agent_type,
                             "answer": error_msg, "turn": self._turn,
                             "tool_calls": [], "has_json": False})
                return error_msg

        elif tool_name == "submit_analysis":
            score = tool_input.get("quality_score", 0)
            analysis = tool_input.get("analysis", {})

            remaining_turns = self.max_turns - self._turn
            force_accept = remaining_turns <= 2

            errors = self._validate_analysis(analysis)

            if errors and not force_accept:
                feedback = "코드 검증 실패. 아래 문제를 추가 질문으로 보완한 뒤 다시 submit_analysis하세요:\n"
                for e in errors:
                    feedback += f"- {e}\n"
                print(f"[ARCH-AGENT] {self.agent_type}: 검증 실패 ({len(errors)}건)")
                for e in errors:
                    print(f"  - {e}")
                self._emit({"type": "agent_evaluation", "agent": self.agent_type,
                             "score": score, "verdict": "validation_failed",
                             "errors": errors})
                return feedback

            passed = score >= self.quality_threshold or force_accept
            verdict = "pass" if passed else "needs_refinement"
            if force_accept and (score < self.quality_threshold or errors):
                verdict = "force_accepted"
                print(f"[ARCH-AGENT] {self.agent_type}: 남은 턴 {remaining_turns}, 강제 수락"
                      f" (score={score}, errors={len(errors)})")

            self._emit({"type": "agent_evaluation", "agent": self.agent_type,
                         "score": score, "verdict": verdict})

            if passed:
                self._final_result = analysis
                return "분석 결과가 승인되었습니다. 세션을 종료합니다."
            else:
                return (f"점수 {score}은(는) 기준 {self.quality_threshold} 미만입니다. "
                        "부족한 영역을 추가 질문으로 보완한 뒤 다시 submit_analysis를 호출하세요.")

        return f"Unknown tool: {tool_name}"


class ArchitectureAgentDiscoverer:
    """Orchestrates L1→L2→L3 layer-by-layer architecture discovery.

    Each layer runs as a separate ArchitectAgent:
      L1 (서비스): business purpose, service list, communication edges, workflows
      L2 (컴포넌트): K8s details, managed services, ports/protocols
      L3 (인프라): external deps, SPOF, blast radius, observability gaps

    After each layer completes, a `layer_complete` event is emitted with the
    cumulative AnalysisResult so the frontend can render the diagram immediately.
    """

    LAYERS = ["L1", "L2"]

    def __init__(self, space_id: str, session, on_event=None,
                 model_id: str = "us.anthropic.claude-sonnet-4-6",
                 prompt_overrides: dict = None,
                 tagged_resources: dict = None,
                 app_gate=None,
                 app_selection_ref: dict = None,
                 app_name: str = None,
                 force_new_session: bool = False,
                 is_boundary: bool = False):
        self.space_id = space_id
        self.session = session
        self.on_event = on_event
        self.model_id = model_id
        self.prompt_overrides = prompt_overrides or {}
        self.tagged_resources = tagged_resources or {}
        self.app_gate = app_gate
        self.app_selection_ref = app_selection_ref if app_selection_ref is not None else {}
        self._app_name_override = app_name
        self._force_new_session = force_new_session
        self._is_boundary = is_boundary

    def _emit(self, event: dict):
        if self.on_event:
            self.on_event(event)

    def _compute_tag_coverage(self, apps: list) -> dict:
        if not self.tagged_resources:
            return {a["name"]: False for a in apps}
        tagged_names = set()
        for acct_data in self.tagged_resources.values():
            if not acct_data.get("ok"):
                continue
            for svc_items in acct_data.get("by_service", {}).values():
                for item in svc_items:
                    tagged_names.add(item.get("name", "").lower())
        return {app["name"]: any(app["name"].lower() in tn for tn in tagged_names)
                for app in apps}

    @staticmethod
    def _summarize_tagged(tagged: dict) -> str:
        if not tagged:
            return ""
        lines = []
        for acct_id, acct_data in tagged.items():
            if not acct_data.get("ok"):
                continue
            by_svc = acct_data.get("by_service", {})
            if not by_svc:
                continue
            # Group by App tag first
            by_app: dict = {}
            for svc, items in by_svc.items():
                for it in items:
                    app = it.get("app", "Untagged")
                    by_app.setdefault(app, {}).setdefault(svc, []).append(it)
            lines.append(f"Account {acct_id} ({acct_data.get('total', 0)} tagged resources):")
            for app_name in sorted(by_app.keys()):
                app_items = by_app[app_name]
                total_in_app = sum(len(v) for v in app_items.values())
                lines.append(f"  [App={app_name}] ({total_in_app} resources):")
                for svc in sorted(app_items.keys()):
                    items = app_items[svc]
                    names = [it.get("name", "?") for it in items[:8]]
                    names_str = ", ".join(names)
                    if len(items) > 8:
                        names_str += f" ... (+{len(items) - 8})"
                    lines.append(f"    - {svc}: {names_str}")
        return "\n".join(lines) if lines else ""

    def _build_question(self, layer: str, tagged_summary: str,
                         prev_result: dict = None) -> Optional[str]:
        """Build skill-trigger or legacy question depending on layer.

        Q1/Q2/Q2_K8S → lightweight skill triggers (#arch-q1, #arch-q2, #k8s-detail).
        L1/L2 (Bedrock agent) → legacy question_template from config.
        """
        if layer == "Q1":
            return self._build_q1_trigger(tagged_summary)

        q_config = load_questions()
        substitutions = {"tagged_resources_list": tagged_summary or "(없음)"}

        agents_cfg = q_config.get("agents", {})
        agent_cfg = agents_cfg.get(layer, {})
        template = agent_cfg.get("question_template", "")
        if not template:
            return None

        if layer == "L2" and prev_result:
            services = prev_result.get("services", [])
            lines = ["L1에서 식별된 서비스 목록:"]
            for svc in services:
                lines.append(f"  - {svc.get('name')}: {svc.get('service', '?')} "
                             f"{svc.get('resource_type', '?')} — {svc.get('role', '?')} "
                             f"(group: {svc.get('group', '?')})")
            substitutions["l1_services_summary"] = "\n".join(lines)

        result = template
        for key, val in substitutions.items():
            result = result.replace("{" + key + "}", val)
        return result

    def _build_q1_trigger(self, tagged_summary: str) -> str:
        """Build #arch-q1 skill trigger with tagged resources for app discovery."""
        parts = ["#arch-q1"]
        if tagged_summary:
            parts.append(f"\n## 알려진 AWS 리소스 (App 태그 포함)\n{tagged_summary}")
        return "\n".join(parts)

    def _build_q2_question(self, app_id: int, app_name: str,
                            tagged_summary: str = "",
                            q1_data: dict = None, all_apps: list = None) -> str:
        """Build #arch-q2 skill trigger with app id/name, boundary rule, and E2E workflow context."""
        parts = [f"#arch-q2 {app_id} {app_name}"]
        parts.append(f"\n이 앱의 App 태그: App={app_name}")
        parts.append(f"App={app_name} 태그가 붙은 리소스가 이 앱 소속입니다. 다른 App 태그를 가진 리소스는 경계 노드로 표현하세요.")

        if q1_data:
            relevant_workflows = []
            for w in q1_data.get("app_workflows", []):
                if app_name in w.get("hops", []):
                    relevant_workflows.append(
                        f"- \"{w['name']}\": {' → '.join(w['hops'])}")
            if relevant_workflows:
                parts.append(f"\n## 참고 E2E 흐름\n이 앱이 참여하는 워크플로우:")
                parts.extend(relevant_workflows)
                parts.append("이 흐름을 기준으로 컴포넌트를 찾고 연결하세요.")

        if all_apps:
            other = [a["name"] for a in all_apps if a["name"] != app_name]
            if other:
                parts.append(f"\n## 중복 금지\n다른 앱({', '.join(other)})의 컴포넌트는 찾지 마세요. boundary_node로 연결만 하세요.")

        return "\n".join(parts)

    def _build_q2k8s_question(self, app_name: str,
                               graph: "ArchGraph" = None) -> str:
        """Build #k8s-detail skill trigger with known service list."""
        parts = [f"#k8s-detail {app_name}"]
        if graph:
            svc_lines = []
            for n in graph.nodes:
                if n.group == app_name and "EKS" in (n.kind or ""):
                    port_str = f", port {n.ports[0]}" if n.ports else ""
                    kind_short = (n.kind or "").replace("Amazon EKS ", "")
                    svc_lines.append(f"  - {n.name}: {kind_short}{port_str}")
            if svc_lines:
                parts.append("\n## 알려진 서비스\n" + "\n".join(svc_lines))
        return "\n".join(parts)

    @staticmethod
    def _has_k8s_workloads(result: "AnalysisResult") -> list:
        """Return app group names that have K8s-based workloads."""
        k8s_apps = set()
        for n in result.graph.nodes:
            kind = n.kind or ""
            if "EKS" in kind or kind in ("Deployment", "StatefulSet", "DaemonSet", "CronJob", "Job"):
                if n.group:
                    k8s_apps.add(n.group)
        return sorted(k8s_apps)

    @staticmethod
    def _apply_k8s_enrichment(result: "AnalysisResult", data: dict, app_name: str):
        """Merge K8s supplementary data into existing graph nodes."""
        for w in data.get("workloads", []):
            node = result.graph.get_node(w.get("name", ""))
            if not node:
                continue
            k8s_kind = w.get("kind", "")
            if k8s_kind:
                node.kind = f"Amazon EKS {k8s_kind}"
            if w.get("namespace"):
                node.namespace = w["namespace"]
            if w.get("replicas"):
                node.labels["replicas"] = w["replicas"]
            if w.get("service_account"):
                node.labels["service_account"] = w["service_account"]
            if w.get("hpa"):
                node.labels["hpa"] = True

        result.k8s_detail[app_name] = data

    @staticmethod
    def _validate_q2k8s(data: dict) -> list:
        """Validate Q2_K8S response structure."""
        errors = []
        if not data.get("app_name"):
            errors.append("app_name 누락")
        if not data.get("workloads"):
            errors.append("workloads 누락 또는 빈 배열")
        for w in data.get("workloads", []):
            if not w.get("name"):
                errors.append("workload에 name 누락")
            if not w.get("kind"):
                errors.append(f"workload '{w.get('name', '?')}'에 kind 누락")
            if not w.get("namespace"):
                errors.append(f"workload '{w.get('name', '?')}'에 namespace 누락")
        return errors

    @staticmethod
    def _parse_agent_json(answer: str) -> Optional[dict]:
        """Extract JSON from Agent answer — delegates to robust _extract_recommendation_json."""
        parsed = _extract_recommendation_json(answer)
        if parsed:
            return parsed
        print(f"[ARCH-AGENT] JSON 추출 실패 — 답변 앞 200자: {answer[:200]}")
        return None

    @staticmethod
    def _validate_q1(data: dict) -> list:
        """Validate Q1 (app-level) response."""
        errors = []
        if not data.get("apps"):
            errors.append("apps 배열이 비어있습니다")
        for app in data.get("apps", []):
            if not app.get("name"):
                errors.append(f"앱에 name이 없습니다: {app}")
            if not app.get("id"):
                errors.append(f"앱에 id가 없습니다: {app.get('name', '?')}")
        edge_apps = set()
        for e in data.get("app_edges", []):
            edge_apps.add(e.get("source"))
            edge_apps.add(e.get("target"))
        app_names = {a.get("name") for a in data.get("apps", [])}
        invalid = edge_apps - app_names - {None}
        if invalid:
            errors.append(f"app_edges에 존재하지 않는 앱 참조: {invalid}")
        return errors

    @staticmethod
    def _validate_q2(data: dict) -> list:
        """Validate Q2 (service-level) response."""
        errors = []
        if not data.get("nodes"):
            errors.append("nodes 배열이 비어있습니다")
        node_names = {n.get("name") for n in data.get("nodes", [])}
        # Check orphan nodes
        connected = set()
        for e in data.get("edges", []):
            connected.add(e.get("source"))
            connected.add(e.get("target"))
            if e.get("source") not in node_names:
                errors.append(f"edge source '{e.get('source')}'가 nodes에 없습니다")
            if e.get("target") not in node_names:
                errors.append(f"edge target '{e.get('target')}'가 nodes에 없습니다")
        orphans = node_names - connected
        if orphans and len(orphans) > len(node_names) * 0.3:
            errors.append(f"orphan 노드가 너무 많습니다 ({len(orphans)}개): {orphans}")
        # Check bidirectional edges
        edge_set = set()
        for e in data.get("edges", []):
            key = (e.get("source"), e.get("target"))
            rev = (e.get("target"), e.get("source"))
            if rev in edge_set:
                errors.append(f"양방향 edge: {key[0]} ↔ {key[1]}")
            edge_set.add(key)
        # Check required node fields
        for n in data.get("nodes", []):
            if not n.get("kind"):
                errors.append(f"노드 '{n.get('name')}'에 kind가 없습니다")
            if not n.get("group"):
                errors.append(f"노드 '{n.get('name')}'에 group이 없습니다")
        return errors

    def discover(self, checkpoint: dict = None, cancel_event=None) -> AnalysisResult:
        """Top-down architecture discovery: Q1(apps) → Q2(services per app).

        No Bedrock — DevOps Agent returns render-ready JSON directly.
        Falls back to legacy Bedrock pipeline if new questions config is absent.
        """
        q_config = load_questions()
        has_topdown = "questions" in q_config and "Q1" in q_config["questions"]

        if has_topdown:
            return self._discover_topdown(checkpoint, cancel_event)
        else:
            return self._discover_legacy(checkpoint, cancel_event)

    def _detect_single_app_mode(self) -> bool:
        """app_name이 지정되었거나 tag scope가 존재하면 single-app mode."""
        if self._app_name_override:
            return True
        if not self.tagged_resources:
            return False
        total = sum(d.get("total", 0) for d in self.tagged_resources.values() if d.get("ok"))
        return total > 0

    def _infer_app_name_from_tags(self) -> str:
        """앱 이름 결정: 사용자 설정 > tagged_resources에서 가장 큰 App 그룹 > 기본값."""
        if self._app_name_override:
            return self._app_name_override
        if self.tagged_resources:
            app_counts: dict = {}
            for acct_data in self.tagged_resources.values():
                if not acct_data.get("ok"):
                    continue
                for items in acct_data.get("by_service", {}).values():
                    for it in items:
                        app = it.get("app", "")
                        if app:
                            app_counts[app] = app_counts.get(app, 0) + 1
            if app_counts:
                return max(app_counts, key=app_counts.get)
        return "Application"

    def _build_single_app_question(self, app_name: str, is_boundary: bool = False) -> str:
        """Single-app mode: #arch-q2 스킬 트리거 + 컨텍스트."""
        parts = [f"#arch-q2 1 {app_name}"]
        parts.append(f"\n이 앱의 App 태그: App={app_name}")
        parts.append("App 태그가 이 앱인 리소스가 소속입니다. 태그 없어도 흐름에서 도달 가능하면 이 앱에 포함하세요.")

        if is_boundary:
            known_apps = self._get_known_apps(app_name)
            if known_apps:
                parts.append(f"\n## 중복 금지\n이미 식별된 앱: {', '.join(known_apps)}")
            known_nodes = self._get_known_nodes(app_name)
            if known_nodes:
                parts.append("\n## 이미 발견된 리소스 (다른 앱 소속)")
                parts.append("아래 리소스는 이미 다른 앱에서 발견됨. 동일 물리적 리소스를 다른 이름으로 중복 나열 금지:")
                parts.append(", ".join(known_nodes))
        else:
            known_apps = self._get_known_apps(app_name)
            if known_apps:
                apps_list = ", ".join(known_apps)
                parts.append(f"\n## 이미 발견된 앱 목록")
                parts.append(f"이 환경에는 다음 앱이 이미 식별되어 있습니다: {apps_list}")
                parts.append("이 앱들은 boundary_nodes로 추가하지 마세요. 이미 별도 분석이 완료된 앱입니다.")

        return "\n".join(parts)

    def _get_known_nodes(self, current_app: str) -> list:
        """DDB에서 이미 분석된 노드 중 현재 앱이 아닌 것들의 이름+kind 목록."""
        try:
            from routes_arch import _load_latest_arch
            saved = _load_latest_arch(self.space_id)
            if saved:
                nodes = saved.get("graph", {}).get("nodes", [])
                result = []
                for n in nodes:
                    if (n.get("group") != current_app
                            and n.get("service_type") != "boundary"
                            and n.get("name")):
                        entry = n["name"]
                        kind = n.get("kind", "")
                        role = (n.get("labels") or {}).get("role", "")
                        if kind:
                            entry += f" ({kind})"
                        if role and len(role) < 80:
                            entry += f" — {role}"
                        result.append(entry)
                return result
        except Exception:
            pass
        return []

    def _get_known_apps(self, current_app: str) -> list:
        """DDB에서 이미 분석된 앱 group 목록을 가져옴 (현재 앱 제외)."""
        try:
            from routes_arch import _load_latest_arch
            saved = _load_latest_arch(self.space_id)
            if saved:
                nodes = saved.get("graph", {}).get("nodes", [])
                groups = {n.get("group") for n in nodes
                          if n.get("group") and n.get("service_type") != "boundary"}
                groups.discard(current_app)
                return sorted(groups)
        except Exception:
            pass
        return []

    def _discover_topdown(self, checkpoint: dict = None, cancel_event=None) -> AnalysisResult:
        """Top-down discovery: Q1 → L1, Q2 per app → L2."""

        # ── Single-app mode detection ──
        if self._detect_single_app_mode():
            # Check if checkpoint was from a multi-app run (has Q1 data)
            if checkpoint and checkpoint.get("mode") != "single_app":
                if "Q1" in checkpoint.get("layer_results", {}):
                    checkpoint = None
            return self._discover_single_app(checkpoint, cancel_event)

        chat_client = AgentChatClient(self.space_id, self.session)
        tagged_summary = self._summarize_tagged(self.tagged_resources)
        if tagged_summary:
            print(f"[ARCH] 사전 정보: {len(tagged_summary)} chars")

        result = AnalysisResult()
        result.graph.discovered_at = time.time()
        interview_log = []

        exec_id = chat_client.get_or_create_session()
        print(f"[ARCH] 채팅: {exec_id}")

        # ── Resume from checkpoint: skip Q1 if L1 already done ──
        if checkpoint and "L1" in checkpoint.get("completed_layers", []):
            q1_data = checkpoint.get("layer_results", {}).get("Q1", {})
            apps = q1_data.get("apps", checkpoint.get("apps", []))
            cp_exec_id = checkpoint.get("exec_ids", {}).get("Q1")
            if cp_exec_id:
                exec_id = cp_exec_id
            if apps:
                print(f"[ARCH] 체크포인트에서 복원: {len(apps)} 앱")
                result.system_name = q1_data.get("system_name", "")
                result.description = q1_data.get("description", "")
                result.taxonomy = [
                    {"group": a["name"], "description": a.get("description", ""),
                     "classification_criteria": a.get("classification_criteria", "")}
                    for a in apps
                ]
                for app in apps:
                    result.graph.nodes.append(ServiceNode(
                        name=app["name"], namespace="", kind="Application",
                        service_type="app", group=app["name"],
                        labels={"role": app.get("description", "")},
                    ))
                for e in q1_data.get("app_edges", []):
                    result.graph.edges.append(ServiceEdge(
                        source=e.get("source", ""), target=e.get("target", ""),
                        description=e.get("description", ""),
                    ))
                self._emit({
                    "type": "layer_complete", "layer": "L1",
                    "analysis": result.to_dict(),
                    "restored": True,
                })
                # Use pre-selected apps from checkpoint if available
                pre_sel = checkpoint.get("selected_apps")
                return self._topdown_after_l1(
                    result, apps, q1_data, exec_id, chat_client,
                    interview_log, cancel_event, tagged_summary,
                    pre_selected_apps=pre_sel)

        # ── Q1: 앱 레벨 ──
        if cancel_event and cancel_event.is_set():
            return result

        q1 = self._build_question("Q1", tagged_summary)
        if not q1:
            print("[ARCH] ERROR: Q1 질문 생성 실패")
            return result

        self._emit({"type": "phase_start", "phase": "Q1", "description": "앱 식별"})
        self._emit({"type": "agent_question", "agent": "Q1",
                     "question": q1[:500], "turn": 0, "fixed_question": True})
        print(f"[ARCH] Q1 전송 ({len(q1)} chars)...")

        _AGENT_ERROR_PHRASES = (
            "I encountered an issue",
            "Could you rephrase",
            "please try a new chat session",
            "I'm sorry, I cannot",
        )

        q1_answer = None
        for q1_attempt in range(2):
            try:
                t0 = time.time()
                resp = chat_client.ask(exec_id, q1)
                if resp.session_id and exec_id == "NEW":
                    exec_id = resp.session_id
                q1_answer = resp.final_text
                elapsed = time.time() - t0
                print(f"[ARCH] Q1 답변: {len(q1_answer)} chars, {elapsed:.1f}초")

                if any(p in q1_answer for p in _AGENT_ERROR_PHRASES):
                    print(f"[ARCH] Q1 Agent 내부 에러 감지 (시도 {q1_attempt+1})")
                    if q1_attempt == 0:
                        self._emit({"type": "agent_answer", "agent": "Q1",
                                     "answer": q1_answer[:500] + "\n\n⟳ 새 채팅으로 재시도...", "turn": 0})
                        chat_client.invalidate_session()
                        exec_id = chat_client.create_session()
                        print(f"[ARCH] 새 채팅: {exec_id}")
                        q1_answer = None
                        continue
                break
            except Exception as e:
                print(f"[ARCH] Q1 실패 (시도 {q1_attempt+1}): {e}")
                if q1_attempt == 0:
                    chat_client.invalidate_session()
                    exec_id = chat_client.create_session()
                    continue
                self._emit({"type": "error", "error": f"Q1 전송 실패: {e}"})
                return result

        if not q1_answer:
            self._emit({"type": "error", "error": "Agent가 반복적으로 내부 에러를 반환합니다. 잠시 후 다시 시도하세요."})
            return result

        self._emit({"type": "agent_answer", "agent": "Q1",
                     "answer": q1_answer[:800], "turn": 0})
        interview_log.append({"turn": 1, "question": q1,
                               "answer": q1_answer, "phase": "Q1"})

        q1_data = self._parse_agent_json(q1_answer)
        if not q1_data:
            print("[ARCH] Q1 JSON 파싱 실패 — 답변에 JSON 블록 없음")
            self._emit({"type": "error",
                         "error": "Q1 답변에서 JSON을 추출할 수 없습니다. Agent 답변을 확인하세요.",
                         "raw_answer": q1_answer[:2000]})
            return result

        q1_errors = self._validate_q1(q1_data)
        if q1_errors:
            print(f"[ARCH] Q1 검증 실패: {q1_errors}")
            # Try one fix question
            fix_q = "제공한 JSON에 다음 문제가 있습니다:\n" + \
                    "\n".join(f"- {e}" for e in q1_errors) + \
                    "\n\n수정하여 올바른 JSON 블록을 다시 제공해주세요."
            try:
                resp2 = chat_client.ask(exec_id, fix_q)
                q1_data_fixed = self._parse_agent_json(resp2.final_text)
                if q1_data_fixed:
                    q1_data = q1_data_fixed
                    interview_log.append({"turn": 2, "question": fix_q,
                                           "answer": resp2.final_text[:2000], "phase": "Q1_fix"})
            except Exception as e:
                print(f"[ARCH] Q1 수정 질문 실패: {e}")

        # Build L1 data from Q1 apps
        apps = q1_data.get("apps", [])
        result.system_name = q1_data.get("system_name", "")
        result.description = q1_data.get("description", "")
        result.taxonomy = [
            {"group": a["name"], "description": a.get("description", ""),
             "classification_criteria": a.get("classification_criteria", "")}
            for a in apps
        ]

        # Emit L1 partial result (app-level graph from app_edges)
        for app in apps:
            result.graph.nodes.append(ServiceNode(
                name=app["name"], namespace="", kind="Application",
                service_type="app", group=app["name"],
                labels={"role": app.get("description", "")},
            ))
        for e in q1_data.get("app_edges", []):
            result.graph.edges.append(ServiceEdge(
                source=e.get("source", ""), target=e.get("target", ""),
                description=e.get("description", ""),
            ))
        result.workflows = [
            {"name": w.get("name", ""), "hops": [
                {"from": w["hops"][i], "to": w["hops"][i+1]}
                for i in range(len(w.get("hops", [])) - 1)
            ]}
            for w in q1_data.get("app_workflows", [])
        ]

        self._emit({
            "type": "layer_complete", "layer": "L1",
            "analysis": result.to_dict(),
            "checkpoint": {
                "completed_layers": ["L1"],
                "exec_ids": {"Q1": exec_id},
                "layer_results": {"Q1": q1_data},
                "apps": apps,
            },
        })
        print(f"[ARCH] L1 완료: {len(apps)} 앱, {len(result.graph.edges)} 엣지")

        return self._topdown_after_l1(
            result, apps, q1_data, exec_id, chat_client,
            interview_log, cancel_event, tagged_summary)

    def _topdown_after_l1(self, result, apps, q1_data, exec_id, chat_client,
                          interview_log, cancel_event, tagged_summary,
                          pre_selected_apps=None):
        """Continue topdown discovery after L1 is complete (app selection + Q2).

        pre_selected_apps: if provided, skip app selection gate and use these directly.
        """
        # ── App Selection Gate ──
        if pre_selected_apps:
            selected_names = set(pre_selected_apps)
            apps_to_analyze = [a for a in apps if a["name"] in selected_names]
            if not apps_to_analyze:
                apps_to_analyze = apps
            print(f"[ARCH] 체크포인트에서 복원된 앱 선택: {[a['name'] for a in apps_to_analyze]}")
            self._emit({
                "type": "app_selection_confirmed",
                "selected": [a["name"] for a in apps_to_analyze],
                "unselected": [a["name"] for a in apps if a["name"] not in selected_names],
                "total": len(apps),
            })
        elif self.app_gate:
            tag_coverage = self._compute_tag_coverage(apps)
            self._emit({
                "type": "app_list",
                "apps": [
                    {
                        "id": a.get("id", 0),
                        "name": a["name"],
                        "description": a.get("description", ""),
                        "has_tag_coverage": tag_coverage.get(a["name"], False),
                    }
                    for a in apps
                ],
            })
            print(f"[ARCH] 앱 선택 대기 중 ({len(apps)}개 앱)...")

            while not self.app_gate.is_set():
                if cancel_event and cancel_event.is_set():
                    return result
                self.app_gate.wait(timeout=2)

            raw_selection = self.app_selection_ref.get(self.space_id)
            selected_names = set(raw_selection if raw_selection is not None
                                 else [a["name"] for a in apps])
            apps_to_analyze = [a for a in apps if a["name"] in selected_names]
            print(f"[ARCH] 선택된 앱: {[a['name'] for a in apps_to_analyze]}")

            self._emit({
                "type": "app_selection_confirmed",
                "selected": [a["name"] for a in apps_to_analyze],
                "unselected": [a["name"] for a in apps if a["name"] not in selected_names],
                "total": len(apps),
            })
        else:
            apps_to_analyze = apps

        # ── Q2: 앱별 서비스 레벨 ──
        if cancel_event and cancel_event.is_set():
            return result

        self._emit({"type": "phase_start", "phase": "Q2",
                     "description": f"앱별 서비스 상세 ({len(apps_to_analyze)}/{len(apps)}개)"})

        # Reset graph for L2 — rebuild with service-level detail
        result.graph.nodes = []
        result.graph.edges = []
        all_workflows = []

        for app in apps_to_analyze:
            if cancel_event and cancel_event.is_set():
                break

            app_id = app.get("id", 0)
            app_name = app["name"]
            q2 = self._build_q2_question(app_id, app_name, tagged_summary,
                                          q1_data=q1_data, all_apps=apps)

            print(f"[ARCH] Q2 전송: 앱 #{app_id} {app_name} ({len(q2)} chars)...")
            self._emit({"type": "agent_question", "agent": "Q2",
                         "question": q2[:300], "turn": app_id,
                         "app_name": app_name})

            try:
                t0 = time.time()
                resp = chat_client.ask(exec_id, q2)
                q2_answer = resp.final_text
                elapsed = time.time() - t0
                print(f"[ARCH] Q2 답변 ({app_name}): {len(q2_answer)} chars, {elapsed:.1f}초")
                self._emit({"type": "agent_answer", "agent": "Q2",
                             "answer": q2_answer[:800], "turn": app_id,
                             "app_name": app_name})
                interview_log.append({"turn": len(interview_log) + 1,
                                       "question": q2, "answer": q2_answer,
                                       "phase": "Q2", "app_name": app_name})
            except Exception as e:
                print(f"[ARCH] Q2 실패 ({app_name}): {e}")
                self._emit({"type": "error", "error": f"Q2 {app_name} 실패: {e}"})
                continue

            q2_data = self._parse_agent_json(q2_answer)
            if not q2_data:
                print(f"[ARCH] Q2 JSON 파싱 실패 ({app_name})")
                continue

            q2_errors = self._validate_q2(q2_data)
            if q2_errors:
                print(f"[ARCH] Q2 검증 경고 ({app_name}): {q2_errors[:3]}")

            # Apply Q2 nodes/edges directly to graph
            for n in q2_data.get("nodes", []):
                node_name = n.get("name", "")
                existing = result.graph.get_node(node_name)
                agent_group = n.get("group", "")
                effective_group = agent_group if agent_group else app_name
                if existing:
                    if not existing.group and effective_group:
                        existing.group = effective_group
                    continue
                kind = n.get("kind", "Service")
                ns = n.get("namespace", "")
                svc_type = n.get("service_type", "app")
                if not ns and svc_type in ("managed", "platform"):
                    ns = svc_type
                result.graph.nodes.append(ServiceNode(
                    name=node_name,
                    namespace=ns,
                    kind=kind,
                    service_type=svc_type,
                    group=effective_group,
                    labels=n.get("labels", {}),
                    ports=n.get("ports", []),
                ))

            # Boundary nodes (외부 앱/시스템 연결점)
            for bn in q2_data.get("boundary_nodes", []):
                bn_name = bn.get("name", "")
                if not bn_name or result.graph.get_node(bn_name):
                    continue
                ext_app = bn.get("app_name", bn_name)
                result.graph.nodes.append(ServiceNode(
                    name=bn_name, namespace="external", kind="External App",
                    service_type="boundary", group=ext_app,
                    labels=bn.get("labels", {}), ports=[],
                ))

            existing_edges = {(ex.source, ex.target) for ex in result.graph.edges}
            for e in q2_data.get("edges", []):
                edge_key = (e.get("source", ""), e.get("target", ""))
                if edge_key in existing_edges:
                    continue
                existing_edges.add(edge_key)
                result.graph.edges.append(ServiceEdge(
                    source=e.get("source", ""), target=e.get("target", ""),
                    protocol=e.get("protocol", "tcp"), port=e.get("port", 0),
                    paths=e.get("paths", []), methods=e.get("methods", []),
                    description=e.get("description", ""),
                ))

            all_workflows.extend(q2_data.get("workflows", []))

            # Emit incremental progress
            self._emit({
                "type": "layer_progress", "layer": "L2",
                "app_name": app_name,
                "nodes_count": len(result.graph.nodes),
                "edges_count": len(result.graph.edges),
            })

        result.workflows = all_workflows
        self._fill_missing_nodes(result)
        result.conversations["Q1"] = [interview_log[0]] if interview_log else []
        result.conversations["Q2"] = interview_log[1:] if len(interview_log) > 1 else []

        # ── Q2-K8s: K8s 플랫폼 보충 (조건부) ──
        k8s_apps = self._has_k8s_workloads(result)
        if k8s_apps:
            self._emit({"type": "phase_start", "phase": "Q2_K8S",
                         "description": f"K8s 리소스 보충 ({len(k8s_apps)}개 앱)"})
            k8s_log = []
            for app_name in k8s_apps:
                if cancel_event and cancel_event.is_set():
                    break
                q2k = self._build_q2k8s_question(app_name, graph=result.graph)
                if not q2k:
                    continue
                print(f"[ARCH] Q2-K8s 전송: {app_name} ({len(q2k)} chars)...")
                self._emit({"type": "agent_question", "agent": "Q2_K8S",
                             "question": q2k[:300], "app_name": app_name})
                try:
                    t0 = time.time()
                    resp = chat_client.ask(exec_id, q2k)
                    k8s_answer = resp.final_text
                    elapsed = time.time() - t0
                    print(f"[ARCH] Q2-K8s 답변 ({app_name}): {len(k8s_answer)} chars, {elapsed:.1f}초")
                    self._emit({"type": "agent_answer", "agent": "Q2_K8S",
                                 "answer": k8s_answer[:800], "app_name": app_name})
                    k8s_log.append({"turn": len(k8s_log) + 1,
                                    "question": q2k, "answer": k8s_answer,
                                    "phase": "Q2_K8S", "app_name": app_name})
                    k8s_data = self._parse_agent_json(k8s_answer)
                    if k8s_data:
                        k8s_errors = self._validate_q2k8s(k8s_data)
                        if k8s_errors:
                            print(f"[ARCH] Q2-K8s 검증 경고 ({app_name}): {k8s_errors}")
                        self._apply_k8s_enrichment(result, k8s_data, app_name)
                        self._emit({"type": "layer_progress", "layer": "L2",
                                     "app_name": app_name,
                                     "nodes_count": len(result.graph.nodes),
                                     "edges_count": len(result.graph.edges),
                                     "k8s_enriched": True})
                except Exception as e:
                    print(f"[ARCH] Q2-K8s 실패 ({app_name}): {e}")
                    self._emit({"type": "warning", "phase": "Q2_K8S",
                                 "message": f"Q2-K8s {app_name} 실패: {e}"})
            if k8s_log:
                result.conversations["Q2_K8S"] = k8s_log

        self._emit({
            "type": "layer_complete", "layer": "L2",
            "analysis": result.to_dict(),
        })

        print(f"[ARCH] 전체 완료: {len(result.graph.nodes)} 노드, "
              f"{len(result.graph.edges)} 엣지, {len(all_workflows)} 워크플로우")
        return result

    def _discover_single_app(self, checkpoint: dict = None, cancel_event=None) -> AnalysisResult:
        """Single-app discovery: Q1 한 턴으로 서비스 토폴로지 완성.

        app_name이 지정되면 Q1 한 턴(서비스 + boundary 포함)으로 전송. Agent가 직접 탐색.
        """
        chat_client = AgentChatClient(self.space_id, self.session)

        result = AnalysisResult()
        result.graph.discovered_at = time.time()
        interview_log = []

        app_name = self._infer_app_name_from_tags()
        result.system_name = app_name
        result.description = f"{app_name} 환경 서비스 토폴로지"
        result.taxonomy = [{"group": app_name, "description": f"{app_name} 앱",
                            "classification_criteria": "tag scope 내 모든 리소스"}]

        # 체크포인트 재개가 아닌 새 분석은 항상 새 세션 사용.
        # 기존 세션 재사용 시 이전 대화 맥락(옛 응답 포맷)에 Agent가 고착되어
        # edge/노드 포맷이 깨지는 문제가 있어, 1회성 분석은 깨끗한 세션으로 시작한다.
        resuming = bool(checkpoint and checkpoint.get("mode") == "single_app"
                        and "L1" in (checkpoint.get("completed_layers") or []))
        if self._force_new_session or not resuming:
            chat_client.invalidate_session()
            exec_id = chat_client.create_session()
            fresh = True
        else:
            exec_id = chat_client.get_or_create_session()
            fresh = False
        print(f"[ARCH] [single-app] 채팅: {exec_id}, 앱: {app_name}, new_session={fresh}")

        self._emit({"type": "mode", "mode": "single_app", "app_name": app_name})

        # ── Resume from checkpoint ──
        if checkpoint and checkpoint.get("mode") == "single_app":
            if "L1" in checkpoint.get("completed_layers", []):
                q1_data = (checkpoint.get("layer_results", {}).get("Q1")
                           or checkpoint.get("layer_results", {}).get("Q2", {}))
                cp_exec_id = (checkpoint.get("exec_ids", {}).get("Q1")
                              or checkpoint.get("exec_ids", {}).get("Q2"))
                if cp_exec_id:
                    exec_id = cp_exec_id
                if q1_data:
                    print(f"[ARCH] [single-app] 체크포인트 복원: L1(서비스 토폴로지)")
                    self._apply_q2_to_result(result, q1_data, app_name)
                    self._emit({
                        "type": "layer_complete", "layer": "L1",
                        "analysis": result.to_dict(),
                        "restored": True,
                    })
                    return self._single_app_after_l1(
                        result, app_name, exec_id, chat_client,
                        interview_log, cancel_event)

        # ── Q1: 서비스 토폴로지 ──
        if cancel_event and cancel_event.is_set():
            return result

        q = self._build_single_app_question(app_name, is_boundary=self._is_boundary)
        if not q:
            self._emit({"type": "error", "error": "질문 생성 실패"})
            return result

        self._emit({"type": "phase_start", "phase": "Q1",
                     "description": f"서비스 토폴로지 분석 ({app_name})"})
        self._emit({"type": "agent_question", "agent": "Q1",
                     "question": q[:500], "turn": 0, "fixed_question": True})
        print(f"[ARCH] [single-app] Q1 전송 ({len(q)} chars)...")

        _AGENT_ERROR_PHRASES = (
            "I encountered an issue",
            "Could you rephrase",
            "please try a new chat session",
            "I'm sorry, I cannot",
        )

        self._emit({"type": "phase_start", "phase": "Q1_wait",
                     "description": f"Agent 응답 대기 중 (최대 5분 소요)..."})

        q1_answer = None
        for attempt in range(2):
            try:
                t0 = time.time()
                resp = chat_client.ask(exec_id, q)
                if resp.session_id and exec_id == "NEW":
                    exec_id = resp.session_id
                q1_answer = resp.final_text
                elapsed = time.time() - t0
                print(f"[ARCH] [single-app] Q1 답변: {len(q1_answer)} chars, {elapsed:.1f}초")

                if any(p in q1_answer for p in _AGENT_ERROR_PHRASES):
                    print(f"[ARCH] [single-app] Agent 에러 감지 (시도 {attempt+1})")
                    if attempt == 0:
                        self._emit({"type": "agent_answer", "agent": "Q1",
                                     "answer": q1_answer[:500] + "\n\n⟳ 재시도...", "turn": 0})
                        chat_client.invalidate_session()
                        exec_id = chat_client.create_session()
                        q1_answer = None
                        continue
                break
            except Exception as e:
                print(f"[ARCH] [single-app] Q1 실패 (시도 {attempt+1}): {e}")
                if attempt == 0:
                    self._emit({"type": "agent_thinking", "agent": "Q1",
                                 "thought": f"Agent 응답 실패 — 새 세션으로 재시도 중..."})
                    chat_client.invalidate_session()
                    exec_id = chat_client.create_session()
                    continue
                self._emit({"type": "error", "error": f"Q1 전송 실패: {e}"})
                return result

        if not q1_answer:
            self._emit({"type": "error", "error": "Agent가 반복적으로 에러를 반환합니다."})
            return result

        self._emit({"type": "agent_answer", "agent": "Q1",
                     "answer": q1_answer[:800], "turn": 0})
        interview_log.append({"turn": 1, "question": q,
                               "answer": q1_answer, "phase": "Q1"})

        q1_data = self._parse_agent_json(q1_answer)
        if not q1_data:
            print("[ARCH] [single-app] Q1 JSON 파싱 실패")
            self._emit({"type": "error",
                         "error": "Q1 답변에서 JSON을 추출할 수 없습니다.",
                         "raw_answer": q1_answer[:2000]})
            return result

        q1_errors = self._validate_q2(q1_data)
        if q1_errors:
            print(f"[ARCH] [single-app] Q1 검증 경고: {q1_errors[:3]}")

        self._apply_q2_to_result(result, q1_data, app_name)

        self._emit({
            "type": "layer_complete", "layer": "L1",
            "analysis": result.to_dict(),
            "checkpoint": {
                "mode": "single_app",
                "completed_layers": ["L1"],
                "exec_ids": {"Q1": exec_id},
                "layer_results": {"Q1": q1_data},
                "app_name": app_name,
            },
        })
        print(f"[ARCH] [single-app] L1 완료: {len(result.graph.nodes)} 노드, "
              f"{len(result.graph.edges)} 엣지")

        return self._single_app_after_l1(
            result, app_name, exec_id, chat_client,
            interview_log, cancel_event)

    def _apply_q2_to_result(self, result: AnalysisResult, q2_data: dict, app_name: str):
        """Apply Q2 JSON data to AnalysisResult graph."""
        for n in q2_data.get("nodes", []):
            node_name = n.get("name", "")
            existing = result.graph.get_node(node_name)
            agent_group = n.get("group", "")
            effective_group = agent_group if agent_group else app_name
            if existing:
                if not existing.group and effective_group:
                    existing.group = effective_group
                continue
            kind = n.get("kind", "Service")
            ns = n.get("namespace", "")
            svc_type = n.get("service_type", "app")
            if not ns and svc_type in ("managed", "platform"):
                ns = svc_type
            result.graph.nodes.append(ServiceNode(
                name=node_name, namespace=ns, kind=kind,
                service_type=svc_type, group=effective_group,
                labels=n.get("labels", {}), ports=n.get("ports", []),
            ))

        for bn in q2_data.get("boundary_nodes", []):
            bn_name = bn.get("name", "")
            if not bn_name or result.graph.get_node(bn_name):
                continue
            ext_app = bn.get("app_name", bn_name)
            result.graph.nodes.append(ServiceNode(
                name=bn_name, namespace="external", kind="External App",
                service_type="boundary", group=ext_app,
                labels=bn.get("labels", {}), ports=[],
            ))

        existing_edges = {(ex.source, ex.target) for ex in result.graph.edges}
        for e in q2_data.get("edges", []):
            edge_key = (e.get("source", ""), e.get("target", ""))
            if edge_key in existing_edges:
                continue
            existing_edges.add(edge_key)
            result.graph.edges.append(ServiceEdge(
                source=e.get("source", ""), target=e.get("target", ""),
                protocol=e.get("protocol", "tcp"), port=e.get("port", 0),
                paths=e.get("paths", []), methods=e.get("methods", []),
                description=e.get("description", ""),
            ))

        result.workflows = q2_data.get("workflows", [])
        self._fill_missing_nodes(result)

    def _single_app_after_l1(self, result, app_name, exec_id, chat_client,
                              interview_log, cancel_event):
        """Single-app mode: L1 서비스 토폴로지 완료 후 최종 결과 반환."""
        result.conversations["Q2"] = interview_log

        print(f"[ARCH] [single-app] 전체 완료: {len(result.graph.nodes)} 노드, "
              f"{len(result.graph.edges)} 엣지")
        return result

    def _discover_legacy(self, checkpoint: dict = None, cancel_event=None) -> AnalysisResult:
        """Legacy Bedrock-based discovery (L1/L2 agents). Kept for backwards compat."""
        from botocore.config import Config as BotoConfig

        chat_client = AgentChatClient(self.space_id, self.session)

        completed = set()
        layer_results = {}
        checkpoint_exec_ids = {}
        if checkpoint and checkpoint.get("completed_layers"):
            completed = set(checkpoint["completed_layers"])
            layer_results = checkpoint.get("layer_results", {})
            checkpoint_exec_ids = checkpoint.get("exec_ids", {})
            print(f"[ARCH-AGENT] 체크포인트 복원: {sorted(completed)}")

        bedrock = self.session.client(
            "bedrock-runtime",
            config=BotoConfig(read_timeout=300, connect_timeout=10),
        )

        tagged_summary = self._summarize_tagged(self.tagged_resources)
        if tagged_summary:
            print(f"[ARCH-AGENT] 사전 정보: {len(tagged_summary)} chars")

        result = AnalysisResult()
        result.graph.discovered_at = time.time()
        exec_ids = dict(checkpoint_exec_ids)

        for layer in self.LAYERS:
            if cancel_event and cancel_event.is_set():
                print(f"[ARCH-AGENT] 취소됨 — {layer} 시작 전")
                break

            if layer in completed:
                self._apply_layer(layer, layer_results.get(layer, {}), result)
                self._emit({
                    "type": "layer_complete",
                    "layer": layer,
                    "analysis": result.to_dict(),
                    "restored": True,
                })
                continue

            exec_id = chat_client.create_session()
            exec_ids[layer] = exec_id
            print(f"[ARCH-AGENT] {layer} 새 세션: {exec_id}")

            prev_context = layer_results.get(self.LAYERS[self.LAYERS.index(layer) - 1]) \
                if self.LAYERS.index(layer) > 0 else None

            devops_answer = None
            fixed_question = self._build_question(layer, tagged_summary, prev_context)
            if fixed_question:
                self._emit({"type": "agent_question", "agent": layer,
                             "question": fixed_question[:500], "turn": 0,
                             "fixed_question": True})
                print(f"[ARCH-AGENT] {layer} Q1 전송 ({len(fixed_question)} chars)...")
                try:
                    t0 = time.time()
                    resp = chat_client.ask(exec_id, fixed_question)
                    if resp.session_id and exec_id == "NEW":
                        exec_id = resp.session_id
                        exec_ids[layer] = exec_id
                    devops_answer = resp.final_text
                    elapsed = time.time() - t0
                    print(f"[ARCH-AGENT] {layer} Q1 답변 수신: {len(devops_answer)} chars, {elapsed:.1f}초")
                    self._emit({"type": "agent_answer", "agent": layer,
                                 "answer": devops_answer[:800], "turn": 0,
                                 "tool_calls": resp.tool_calls[:5],
                                 "has_json": bool(resp.parsed_json),
                                 "fixed_question": True})
                except Exception as e:
                    print(f"[ARCH-AGENT] {layer} Q1 실패: {e}")
                    self._emit({"type": "error", "error": f"{layer} Q1 전송 실패: {e}"})

            layer_cfg = self.prompt_overrides.get(layer, {})
            agent = ArchitectAgent(
                agent_type=layer, bedrock_client=bedrock,
                chat_client=chat_client, execution_id=exec_id,
                on_event=self.on_event, model_id=self.model_id,
                max_turns=layer_cfg.get("max_turns", 10),
                quality_threshold=layer_cfg.get("quality_threshold", 75),
                system_prompt=layer_cfg.get("system_prompt"),
                tagged_resources=tagged_summary,
                cancel_event=cancel_event,
            )

            if fixed_question and devops_answer:
                agent.interview_log.append({
                    "turn": 0, "question": fixed_question,
                    "answer": devops_answer[:2000],
                    "fixed_question": True,
                })

            layer_result = agent.run(context=prev_context, devops_answer=devops_answer)
            if not layer_result:
                print(f"[ARCH-AGENT] WARNING: {layer} returned empty result")
                self._emit({"type": "error", "error": f"{layer} 분석 결과가 비어있습니다"})

            layer_results[layer] = layer_result or {}
            self._apply_layer(layer, layer_results[layer], result)
            result.conversations[layer] = agent.interview_log

            self._emit({
                "type": "layer_complete",
                "layer": layer,
                "analysis": result.to_dict(),
                "checkpoint": {
                    "exec_ids": exec_ids,
                    "completed_layers": [l for l in self.LAYERS if l in layer_results],
                    "layer_results": layer_results,
                },
            })

        print(f"[ARCH-AGENT] 전체 완료: {len(result.graph.nodes)} 노드, "
              f"{len(result.graph.edges)} 엣지")
        return result

    def _apply_layer(self, layer: str, data: dict, result: AnalysisResult):
        """Merge a single layer's agent output into the cumulative result."""
        if layer == "L1":
            self._apply_L1(data, result)
        elif layer == "L2":
            self._apply_L2(data, result)
        self._fill_missing_nodes(result)

    def _apply_L1(self, data: dict, result: AnalysisResult):
        result.system_name = data.get("system_name", "")
        result.description = data.get("description", "")
        result.workflows = data.get("workflows", [])
        result.taxonomy = data.get("taxonomy", [])

        for svc in data.get("services", []):
            if not result.graph.get_node(svc.get("name", "")):
                aws_svc = svc.get("service", "")
                res_type = svc.get("resource_type", "")
                kind = f"{aws_svc} {res_type}".strip() if aws_svc else "Service"
                svc_type = svc.get("service_type", "app")
                ns = "managed" if svc_type == "managed" else ""
                result.graph.nodes.append(ServiceNode(
                    name=svc.get("name", ""),
                    namespace=ns,
                    kind=kind,
                    labels={"role": svc.get("role", "")},
                    service_type=svc_type,
                    group=svc.get("group", ""),
                ))

        for e in data.get("edges", []):
            result.graph.edges.append(ServiceEdge(
                source=e.get("source", ""), target=e.get("target", ""),
                protocol=e.get("protocol", "tcp"), port=e.get("port", 0),
                paths=e.get("paths", []), methods=e.get("methods", []),
                description=e.get("description", ""),
            ))

        groups = {}
        for n in result.graph.nodes:
            if n.group:
                groups.setdefault(n.group, []).append(n.name)
        print(f"[ARCH-AGENT] L1: {len(result.graph.nodes)} 서비스, "
              f"{len(result.graph.edges)} 엣지, "
              f"taxonomy: {list(groups.keys())}")

    def _apply_L2(self, data: dict, result: AnalysisResult):
        result.compute = data.get("compute", [])
        for c in result.compute:
            node = result.graph.get_node(c.get("service", ""))
            if node:
                node.kind = c.get("platform", "unknown")
                detail = c.get("detail", {})
                if isinstance(detail, dict):
                    node.namespace = detail.get("namespace", "")
                    node.ports = c.get("ports", node.ports)
                if c.get("group") and not node.group:
                    node.group = c["group"]

        for ms in data.get("managed_services", []):
            name = ms.get("name", "")
            short = name
            if ".rds.amazonaws.com" in name:
                short = name.split(".")[0]
            elif "arn:aws:" in name:
                short = name.split(":")[-1].split("/")[-1]
            svc_type_map = {"rds": "db", "elasticache": "cache", "dynamodb": "db",
                            "s3": "db", "sqs": "queue", "sns": "queue"}
            svc_type = svc_type_map.get(ms.get("type", ""), "app")
            if not result.graph.get_node(short):
                agent_group = ms.get("group", "")
                if not agent_group:
                    for user in ms.get("used_by", []):
                        user_node = result.graph.get_node(user)
                        if user_node and user_node.group:
                            agent_group = user_node.group
                            break
                result.graph.nodes.append(ServiceNode(
                    name=short, namespace="managed",
                    kind=ms.get("type", "managed"), service_type=svc_type,
                    group=agent_group,
                ))
            for user in ms.get("used_by", []):
                result.graph.edges.append(ServiceEdge(
                    source=user, target=short, protocol="tcp",
                    description=ms.get("description", ""),
                ))
            result.managed_services.append(ms)

        for dep in data.get("external_deps", []):
            target = dep.get("target", "")
            short = target.split(".")[0] if "." in target and len(target) > 30 else target
            if not result.graph.get_node(short):
                dep_type = dep.get("type", "external")
                svc_type = {"db": "db", "cache": "cache", "api": "gateway"}.get(dep_type, "app")
                agent_group = dep.get("group", "")
                if not agent_group:
                    source_node = result.graph.get_node(dep.get("source", ""))
                    agent_group = source_node.group if source_node else ""
                result.graph.nodes.append(ServiceNode(
                    name=short, namespace="external",
                    kind="ExternalService", service_type=svc_type,
                    group=agent_group,
                ))
            result.graph.edges.append(ServiceEdge(
                source=dep.get("source", ""), target=short,
                protocol=dep.get("protocol", "tcp"), port=dep.get("port", 0),
                description=dep.get("description", ""),
            ))
            result.external_deps.append(dep)

        print(f"[ARCH-AGENT] L2: {len(result.compute)} compute, "
              f"{len(result.managed_services)} managed, "
              f"{len(result.external_deps)} external")

    @staticmethod
    def _fill_missing_nodes(result: AnalysisResult):
        known = {n.name for n in result.graph.nodes}
        for e in result.graph.edges:
            if e.source and e.source not in known:
                result.graph.nodes.append(ServiceNode(name=e.source, namespace=""))
                known.add(e.source)
            if e.target and e.target not in known:
                result.graph.nodes.append(ServiceNode(name=e.target, namespace=""))
                known.add(e.target)


# ══════════════════════════════════════════════════════════════════
# Architecture Recommender (Bedrock)
# ══════════════════════════════════════════════════════════════════

def _load_failure_modes():
    """Load platform-agnostic failure modes from simulator engine."""
    try:
        import sys, os
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from simulator.engine.failure_modes import get_failure_modes
        return get_failure_modes()
    except (ImportError, ModuleNotFoundError):
        return []


FAILURE_MODES = _load_failure_modes()

# Legacy alias
ABSTRACT_TEMPLATES = FAILURE_MODES


RECOMMEND_PROMPT = _prompts.load("recommend")


@dataclass
class Recommendation:
    template_id: str
    name: str
    target: dict
    priority: str
    rationale: str
    expected_impact: str
    detection_challenge: str
    additional_data_needed: list = field(default_factory=list)


@dataclass
class RecommendationResult:
    recommendations: list = field(default_factory=list)
    architecture_analysis: dict = field(default_factory=dict)
    raw_response: str = ""

    def to_dict(self) -> dict:
        return {
            "recommendations": [
                {
                    "template_id": r.template_id,
                    "name": r.name,
                    "target": r.target,
                    "priority": r.priority,
                    "rationale": r.rationale,
                    "expected_impact": r.expected_impact,
                    "detection_challenge": r.detection_challenge,
                    "additional_data_needed": r.additional_data_needed,
                }
                for r in self.recommendations
            ],
            "architecture_analysis": self.architecture_analysis,
        }


class ArchitectureRecommender:
    """Recommends chaos scenarios by sending architecture to Bedrock Claude."""

    def __init__(self, bedrock_client, model_id: str = "us.anthropic.claude-opus-4-6-v1"):
        self.bedrock = bedrock_client
        self.model_id = model_id

    def recommend(self, graph: ServiceGraph, enrichment: dict = None) -> RecommendationResult:
        arch_json = self._build_architecture_summary(graph, enrichment)
        fm_for_prompt = [
            {k: v for k, v in fm.items()
             if k in ("id", "name", "layer", "description", "trigger_mode",
                       "applicable_when", "detection_challenge", "proactive_question")}
            for fm in FAILURE_MODES
        ]
        templates_json = json.dumps(fm_for_prompt, indent=2, ensure_ascii=False)

        prompt = RECOMMEND_PROMPT.format(
            architecture_json=json.dumps(arch_json, indent=2, ensure_ascii=False),
            templates_json=templates_json,
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
            print("[RECOMMEND] WARNING: 응답 잘림 — max_tokens 증가 필요")

        return self._parse_response(text)

    def _build_architecture_summary(self, graph, enrichment=None):
        nodes = []
        for n in graph.nodes:
            node_info = {"name": n.name, "namespace": n.namespace,
                         "type": n.service_type, "ports": n.ports,
                         "compute_type": getattr(n, "compute_type", "") or n.kind}
            if getattr(n, "group", ""):
                node_info["group"] = n.group
            if enrichment and n.name in enrichment:
                node_info["enrichment"] = enrichment[n.name]
            nodes.append(node_info)

        edges = []
        for e in graph.edges:
            edge_info = {"source": e.source, "target": e.target,
                         "protocol": e.protocol, "port": e.port}
            if e.paths:
                edge_info["paths"] = e.paths
            if e.methods:
                edge_info["methods"] = e.methods
            if e.description:
                edge_info["description"] = e.description
            edges.append(edge_info)

        callers_map = {}
        for e in graph.edges:
            callers_map.setdefault(e.target, []).append(e.source)

        return {
            "namespace": graph.namespace,
            "services": nodes,
            "communications": edges,
            "dependency_summary": callers_map,
        }

    def _parse_response(self, text):
        data = _extract_recommendation_json(text)
        result = RecommendationResult(raw_response=text)

        if not data:
            print("[RECOMMEND] WARNING: Bedrock 응답에서 JSON 파싱 실패")
            return result

        for rec in data.get("recommendations", []):
            result.recommendations.append(Recommendation(
                template_id=rec.get("template_id", ""),
                name=rec.get("name", ""),
                target=rec.get("target", {}),
                priority=rec.get("priority", "medium"),
                rationale=rec.get("rationale", ""),
                expected_impact=rec.get("expected_impact", ""),
                detection_challenge=rec.get("detection_challenge", ""),
                additional_data_needed=rec.get("additional_data_needed", []),
            ))

        result.architecture_analysis = data.get("architecture_analysis", {})
        return result


# ══════════════════════════════════════════════════════════════════
# Scenario Generator (Bedrock)
# ══════════════════════════════════════════════════════════════════

EXEMPLAR_MAP = {
    # Failure mode ID prefix → exemplar scenario IDs
    "FM": ["I02-sg-block", "I08-hasher-network-latency", "C07-corrupted-data"],
    # Legacy prefix support
    "NET": ["I08-hasher-network-latency", "I02-sg-block"],
    "APP": ["A01-oom", "A02-latency"],
    "K8S": ["K01-imagepull", "K02-crashloop"],
    "AWS": ["I02-sg-block", "I08-hasher-network-latency"],
    "CMP": ["C07-corrupted-data", "A01-oom"],
}

VERIFICATION_STEP_TYPES = [
    # 모든 step 공통 필수: name(한국어 라벨), type. description 사용 금지 — 반드시 name.
    # Platform-agnostic (AWS-level)
    {"type": "metric_check", "fields": "name, namespace, metric_name, dimensions, statistic, period, threshold, comparison(gt|lt|eq), timeout, poll_interval",
     "desc": "CloudWatch 메트릭 임계값 확인 (플랫폼 무관)"},
    {"type": "log_pattern", "fields": "name, log_group, filter_pattern, minutes, timeout, poll_interval",
     "desc": "CloudWatch Logs 패턴 검색 (플랫폼 무관)"},
    {"type": "alarm_state", "fields": "name, alarm_name, expected(ALARM|OK|INSUFFICIENT_DATA), timeout, poll_interval",
     "desc": "CloudWatch 알람 상태 확인"},
    {"type": "api_call", "fields": "name, service, action, parameters, jmespath, expected, timeout, poll_interval",
     "desc": "AWS API 호출 결과 확인 (범용). service는 boto3 서비스명(ec2, rds 등). kubectl 사용 금지"},
    {"type": "kubectl_check", "fields": "name, command, expected, pod(대상 서비스명, context 자동주입용), timeout, poll_interval",
     "desc": "kubectl 명령 실행 + 결과 검증 (K8s 리소스 확인용). command에 전체 kubectl 명령 기재"},
    {"type": "agent_investigation", "fields": "name, prompt, expected_findings, observation_window, timeout, poll_interval",
     "desc": "DevOps Agent에게 조사 질문 후 결과 확인 (proactive 시나리오용)"},
    {"type": "fis_experiment", "fields": "name, expected_status(running|completed), timeout, poll_interval",
     "desc": "FIS 실험 상태 모니터링"},
    {"type": "investigation_event", "fields": "name, expected_status(IN_PROGRESS|COMPLETED), timeout, poll_interval",
     "desc": "DevOps Agent 조사 태스크 상태 추적 (reactive 시나리오용)"},
    # Legacy (K8s-specific, backward compatible)
    {"type": "pod_logs", "fields": "name, pod, pattern, tail(opt), timeout, poll_interval",
     "desc": "Pod 로그에서 패턴 매칭 (K8s 전용)"},
    {"type": "pod_status", "fields": "name, pod, expected(OOMKilled|CrashLoopBackOff|ImagePullBackOff|Running), timeout, poll_interval",
     "desc": "Pod 상태 확인 (K8s 전용)"},
    {"type": "cw_alarm", "fields": "name, alarm(${PROJECT_NAME}-xxx), expected(ALARM|OK), timeout, poll_interval",
     "desc": "CloudWatch 알람 상태 확인 (alarm_state 별칭)"},
    {"type": "xray_trace", "fields": "name, filter(X-Ray filter expr), minutes, timeout, poll_interval",
     "desc": "X-Ray 에러/장애 트레이스 검색"},
    {"type": "xray_latency", "fields": "name, service, min_latency_ms, minutes, timeout, poll_interval",
     "desc": "X-Ray 고지연 트레이스 검색"},
    {"type": "lambda_logs", "fields": "name, function(opt), pattern, minutes, timeout, poll_interval",
     "desc": "Lambda 함수 로그 검색"},
    {"type": "slack_message", "fields": "name, channel(opt), pattern, minutes, timeout, poll_interval",
     "desc": "Slack 채널 메시지 검색"},
    {"type": "manual", "fields": "name, timeout",
     "desc": "수동 확인 대기"},
]

GENERATE_SCENARIO_PROMPT = _prompts.load("generate_scenario")


class ScenarioGenerator:
    """Generates executable scenario JSON from architecture recommendations."""

    def __init__(self, bedrock_client, model_id: str = "us.anthropic.claude-opus-4-6-v1"):
        self.bedrock = bedrock_client
        self.model_id = model_id

    def generate(self, recommendation: dict, graph: ServiceGraph,
                 context: dict) -> dict:
        arch_json = self._build_arch_summary(graph)
        fm_id = recommendation.get("failure_mode_id", "") or recommendation.get("template_id", "")
        template = self._find_template(fm_id)
        scenario_id = context.get("scenario_id", "G01-generated")

        exemplar_ids = self._select_exemplars(fm_id)
        exemplars = []
        for eid in exemplar_ids:
            ex = context.get("scenarios", {}).get(eid)
            if ex:
                exemplars.append(ex)

        prompt = GENERATE_SCENARIO_PROMPT.format(
            template_id=fm_id,
            template_name=template.get("name", "") if template else "",
            rec_name=recommendation.get("name", ""),
            target_json=json.dumps(recommendation.get("target", {}), ensure_ascii=False),
            trigger_mode=recommendation.get("trigger_mode", "reactive"),
            rationale=recommendation.get("rationale", ""),
            expected_impact=recommendation.get("expected_impact", ""),
            investigation_prompt=recommendation.get("investigation_prompt", ""),
            architecture_json=json.dumps(arch_json, indent=2, ensure_ascii=False),
            alarms_json=json.dumps(context.get("alarms", []), indent=2, ensure_ascii=False),
            fis_templates_json=json.dumps(context.get("fis_templates", []), indent=2, ensure_ascii=False),
            step_types_json=json.dumps(VERIFICATION_STEP_TYPES, indent=2, ensure_ascii=False),
            exemplars_json=json.dumps(exemplars, indent=2, ensure_ascii=False),
            scenario_id=scenario_id,
        )

        response = self.bedrock.invoke_model(
            modelId=self.model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 16384,
                "messages": [{"role": "user", "content": prompt}],
            }),
        )

        body = json.loads(response["body"].read())
        text = body["content"][0]["text"]
        stop_reason = body.get("stop_reason", "")

        if stop_reason == "max_tokens":
            print("[SCENARIO-GEN] WARNING: 응답 잘림 — max_tokens 증가 필요")

        scenario = _extract_recommendation_json(text)
        if not scenario:
            raise ValueError("Bedrock 응답에서 시나리오 JSON 파싱 실패")

        scenario["source"] = "ai-generated"
        if "id" not in scenario:
            scenario["id"] = scenario_id

        # --- Post-processing Fix 1: Auto-generate evaluation_rubric if missing ---
        if not scenario.get("evaluation_rubric") or not scenario.get("evaluation_rubric", {}).get("criteria"):
            steps = scenario.get("verification", {}).get("steps", [])
            criteria = []
            detection_steps = [s for s in steps if s.get("type") in ("alarm_state", "cw_alarm", "metric_check")]
            analysis_steps = [s for s in steps if s.get("type") in ("agent_investigation", "investigation_event")]
            observation_steps = [s for s in steps if s not in detection_steps and s not in analysis_steps]

            if detection_steps:
                w = 40 // len(detection_steps)
                for s in detection_steps:
                    criteria.append({"name": s.get("name", ""), "weight": w, "type": "detection"})
            if analysis_steps:
                w = 30 // len(analysis_steps)
                for s in analysis_steps:
                    criteria.append({"name": s.get("name", ""), "weight": w, "type": "analysis"})
            if observation_steps:
                w = 30 // len(observation_steps)
                for s in observation_steps:
                    criteria.append({"name": s.get("name", ""), "weight": w, "type": "observation"})

            # Adjust to sum to 100
            total = sum(c["weight"] for c in criteria)
            if criteria and total != 100:
                criteria[-1]["weight"] += (100 - total)

            scenario["evaluation_rubric"] = {"criteria": criteria}

        # --- Post-processing Fix 2: Validate variables in commands ---
        allowed_globals = {"PROJECT_NAME", "AWS_ACCOUNT_ID", "AWS_REGION", "FIS_EXPERIMENT_ID"}
        declared_vars = set(scenario.get("variables", {}).keys()) if scenario.get("variables") else set()
        all_allowed = allowed_globals | declared_vars

        commands_to_check = []
        trigger_cmd = scenario.get("trigger", {}).get("command", "")
        restore_cmd = scenario.get("restore", {}).get("command", "")
        pre_cleanup_cmd = scenario.get("pre_cleanup", {}).get("command", "")
        if trigger_cmd:
            commands_to_check.append(trigger_cmd)
        if restore_cmd:
            commands_to_check.append(restore_cmd)
        if pre_cleanup_cmd:
            commands_to_check.append(pre_cleanup_cmd)

        undefined_vars = set()
        for cmd in commands_to_check:
            found = set(re.findall(r'\$\{([A-Z_][A-Z0-9_]*)\}', cmd))
            undefined_vars.update(found - all_allowed)

        if undefined_vars:
            if "variables" not in scenario:
                scenario["variables"] = {}
            for var in undefined_vars:
                scenario["variables"][var] = {"discovery": f"echo 'TODO: provide discovery command for {var}'"}
            print(f"[SCENARIO-GEN] WARNING: 미정의 변수 발견, variables에 추가: {undefined_vars}")

        return scenario

    def _build_arch_summary(self, graph: ServiceGraph) -> dict:
        nodes = []
        for n in graph.nodes:
            nodes.append({"name": n.name, "namespace": n.namespace,
                          "type": n.service_type, "ports": n.ports, "kind": n.kind})
        edges = []
        for e in graph.edges:
            edge_info = {"source": e.source, "target": e.target,
                         "protocol": e.protocol, "port": e.port}
            if e.paths:
                edge_info["paths"] = e.paths
            if e.methods:
                edge_info["methods"] = e.methods
            edges.append(edge_info)
        return {"namespace": graph.namespace, "services": nodes, "communications": edges}

    def _find_template(self, fm_id: str) -> dict:
        for t in FAILURE_MODES:
            if t["id"] == fm_id:
                return t
        return {}

    def _select_exemplars(self, template_id: str) -> list:
        prefix = template_id.split("-")[0] if "-" in template_id else template_id[:3]
        return EXEMPLAR_MAP.get(prefix, ["A01-oom", "I08-hasher-network-latency"])

    @staticmethod
    def next_generated_id(existing_ids: list) -> str:
        max_num = 0
        for sid in existing_ids:
            m = re.match(r"G(\d+)-", sid)
            if m:
                max_num = max(max_num, int(m.group(1)))
        return f"G{max_num + 1:02d}"


# ═══════════════════════════════════════════════════════════════
# ServiceCodeAnalyzer — per-service code analysis via Agent + GitHub
# ═══════════════════════════════════════════════════════════════

class ServiceCodeAnalyzer:
    """Analyzes a single service's source code via DevOps Agent + GitHub repo."""

    DIAGRAM_TYPES = ["component", "dynamic"]

    def __init__(self, space_id: str, service_name: str,
                 on_event=None, cancel_event=None):
        self.space_id = space_id
        self.service_name = service_name
        self.on_event = on_event or (lambda e: None)
        self.cancel_event = cancel_event
        self._client = AgentChatClient(space_id)
        self._questions = self._load_questions()

    def _load_questions(self) -> dict:
        qpath = os.path.join(os.path.dirname(__file__), "arch_questions.json")
        try:
            with open(qpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("service_analysis", {})
        except Exception:
            return {}

    def analyze(self, diagram_types: list = None) -> dict:
        """Run requested diagram analyses, emitting events per diagram."""
        types = diagram_types or self.DIAGRAM_TYPES
        results = {}
        exec_id = self._client.get_or_create_session()

        for dtype in types:
            if self.cancel_event and self.cancel_event.is_set():
                break
            diagram_key = f"service/{self.service_name}/{dtype}"
            self.on_event({"type": "diagram_start", "key": diagram_key})

            try:
                if dtype == "component":
                    data = self._analyze_component(exec_id)
                elif dtype == "dynamic":
                    data = self._analyze_dynamic(exec_id)
                else:
                    continue

                errors = self._validate(dtype, data)
                if errors and dtype in self._questions:
                    print(f"[SVC-ANALYSIS] {diagram_key} 검증 실패: {errors}")
                    if dtype == "dynamic":
                        exec_id = self._client.create_session()
                        data = self._analyze_dynamic(exec_id)

                results[dtype] = data
                self.on_event({"type": "diagram_done", "key": diagram_key, "data": data})
            except Exception as e:
                print(f"[SVC-ANALYSIS] {diagram_key} 실패: {e}")
                self.on_event({"type": "diagram_error", "key": diagram_key, "error": str(e)})
                results[dtype] = None

        return results

    def _ask(self, exec_id: str, question: str) -> dict:
        resp = self._client.ask(exec_id, question)
        return resp.parsed_json or {}

    def _analyze_component(self, exec_id: str) -> dict:
        tmpl = self._questions.get("component", {}).get("question_template", "")
        question = tmpl.replace("{service_name}", self.service_name)
        data = self._ask(exec_id, question)
        if data and data.get("components"):
            data = self._verify_component(exec_id, data)
            if not data.get("components"):
                data = self._retry_with_feedback(exec_id, data)
        return data

    def _verify_component(self, exec_id: str, data: dict) -> dict:
        """Ask Agent to verify — challenge fabricated components/interfaces."""
        self.on_event({"type": "verify_start", "service": self.service_name})
        comp_names = [c.get("name", "") for c in data.get("components", [])]
        iface_names = []
        for c in data.get("components", []):
            for ri in c.get("required_interfaces", []):
                iface_names.append(ri.get("name") or ri.get("target", ""))
        verify_q = (
            f"방금 {self.service_name} 분석에서 아래 컴포넌트와 인터페이스를 보고했어:\n"
            f"- 컴포넌트: {', '.join(comp_names)}\n"
            f"- required 인터페이스: {', '.join(iface_names)}\n\n"
            f"각각에 대해 질문할게:\n"
            f"1. 이 이름을 코드 어디서 봤어? 정확한 파일명과 줄번호, 클래스명 또는 함수명을 대.\n"
            f"2. 만약 코드에 그 이름이 정확히 없고 네가 역할을 추상화해서 붙인 이름이라면 솔직히 'fabricated'라고 말해.\n\n"
            f"규칙:\n"
            f"- 코드에 실제 클래스/함수로 존재 → exists (파일:줄번호 증거 필수)\n"
            f"- 코드에 없고 네가 만든 이름 → fabricated (왜 만들었는지 이유)\n"
            f"- 애매하면 fabricated로 분류\n\n"
            f"```json\n{{\n  \"components\": [\n"
            f"    {{\"name\": \"컴포넌트명\", \"verdict\": \"exists|fabricated\", \"evidence\": \"파일:줄번호 또는 만든 이유\"}}\n"
            f"  ],\n  \"interfaces\": [\n"
            f"    {{\"name\": \"인터페이스명\", \"verdict\": \"exists|fabricated\", \"evidence\": \"파일:줄번호 또는 만든 이유\"}}\n"
            f"  ]\n}}\n```"
        )
        try:
            verify_resp = self._ask(exec_id, verify_q)
            if not verify_resp:
                return data
            # Remove fabricated components
            fab_comps = set()
            for v in verify_resp.get("components", []):
                if v.get("verdict") == "fabricated":
                    fab_comps.add(v.get("name", ""))
            fab_ifaces = set()
            for v in verify_resp.get("interfaces", []):
                if v.get("verdict") == "fabricated":
                    fab_ifaces.add(v.get("name", ""))

            if fab_comps:
                print(f"[SVC-VERIFY] {self.service_name}: fabricated components removed: {fab_comps}")
                data["components"] = [c for c in data["components"] if c.get("name") not in fab_comps]
                data["relationships"] = [r for r in data.get("relationships", [])
                                         if r.get("source") not in fab_comps and r.get("target") not in fab_comps]
            if fab_ifaces:
                print(f"[SVC-VERIFY] {self.service_name}: fabricated interfaces removed: {fab_ifaces}")
                for c in data.get("components", []):
                    c["required_interfaces"] = [ri for ri in c.get("required_interfaces", [])
                                                if (ri.get("name") or ri.get("target", "")) not in fab_ifaces]
            data["_verification"] = verify_resp
            return data
        except Exception as e:
            print(f"[SVC-VERIFY] {self.service_name} verification failed: {e}")
            return data

    def _retry_with_feedback(self, exec_id: str, data: dict) -> dict:
        """Same session retry — Agent sees its own mistakes and corrects."""
        verification = data.get("_verification", {})
        fab_list = [v.get("name") for v in verification.get("components", [])
                    if v.get("verdict") == "fabricated"]
        fab_reason = "; ".join(
            f"{v.get('name')}: {v.get('evidence', '?')}"
            for v in verification.get("components", [])
            if v.get("verdict") == "fabricated"
        )
        retry_q = (
            f"방금 너의 분석을 검증했더니 모든 컴포넌트가 fabricated였어:\n"
            f"{fab_reason}\n\n"
            f"코드에 없는 추상화를 만들지 마. 실제 있는 것만 보고해.\n"
            f"- 클래스가 없으면 '서비스 전체가 1개 컴포넌트'로 보고해도 됨\n"
            f"- 컴포넌트 이름은 실제 파일명 또는 진입 함수명 기반\n"
            f"- required_interfaces는 코드에서 실제 import하거나 호출하는 외부 서비스만\n\n"
            f"다시 동일한 JSON 형식으로 {self.service_name}을 보고해줘."
        )
        print(f"[SVC-RETRY] {self.service_name}: 피드백과 함께 재시도")
        return self._ask(exec_id, retry_q)

    def _analyze_dynamic(self, exec_id: str, endpoint: str = None) -> dict:
        tmpl = self._questions.get("dynamic", {}).get("question_template", "")
        ep = endpoint or "POST /"
        question = tmpl.replace("{service_name}", self.service_name).replace("{endpoint}", ep)
        return self._ask(exec_id, question)

    def _validate(self, dtype: str, data: dict) -> list:
        if not data:
            return ["empty response"]
        rules = self._questions.get(dtype, {}).get("validation", {})
        errors = []
        for field in rules.get("required_fields", []):
            if not data.get(field):
                errors.append(f"missing required field: {field}")
        if dtype == "component":
            for c in data.get("components", []):
                for f in rules.get("components_required", []):
                    if not c.get(f):
                        errors.append(f"component missing: {f}")
        elif dtype == "dynamic":
            for c in data.get("call_flow", []):
                for f in rules.get("call_flow_required", []):
                    if not c.get(f):
                        errors.append(f"call_flow entry missing: {f}")
        return errors
