"""Topology discovery via DevOps Agent Chat API.

Creates a chat session, asks structured questions about the target namespace,
evaluates responses, and refines with follow-up questions until the topology
meets quality criteria.

Flow: 목표 정의 → 질문 → 응답 평가 → 후속 질문 → 정제 반복 → ServiceGraph
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import boto3

from simulator.config import SimulatorConfig
from simulator.engine.topology import ServiceGraph, ServiceNode, ServiceEdge


# ── Standard Questions (단계별로 분할) ──

Q_SERVICES = """이 환경에서 관리되는 모든 서비스/앱을 알려주세요.
플랫폼(EKS, ECS, EC2, Lambda 등)에 관계없이 실행 중인 서비스를 모두 포함해주세요.
각 서비스에 대해: 이름, 포트, 이미지/패키지, 실행 플랫폼(compute_type),
서비스 유형(service_type: app, cache, db, gateway, queue)을 포함해주세요.
JSON으로만 응답해주세요:
{{"services": [{{"name": "...", "ports": [80], "image": "...",
"service_type": "app", "compute_type": "eks_pod|ecs_task|ec2_instance|lambda_function"}}]}}"""

Q_EDGES = """이 환경의 서비스 간 통신 패턴을 알려주세요.
서비스 맵, 트레이스 데이터, 또는 로드밸런서 설정 등을 기반으로
서비스 간 호출 관계를 모두 보여주세요. 각 통신에 대해: 소스 서비스, 대상 서비스,
프로토콜(http/tcp/redis/grpc), 대상 포트, HTTP 경로와 메서드(가능한 경우),
평균 지연시간과 에러율을 포함해주세요.
JSON으로만 응답해주세요:
{{"edges": [{{"source": "...", "target": "...", "protocol": "http", "port": 80,
"paths": ["/path"], "methods": ["GET"], "avg_latency_ms": 100, "error_rate": 0.0}}]}}"""

Q_EXTERNAL = """이 환경의 서비스가 의존하는 외부 리소스를 알려주세요.
RDS, ElastiCache, S3, DynamoDB, SQS, SNS, 외부 API, 다른 계정 서비스 등을 포함해주세요.
ECR 이미지 풀이나 옵저버빌리티 에이전트(CloudWatch, OTEL)는 제외해주세요.
JSON으로만 응답해주세요:
{{"external_deps": [{{"source": "service_name", "target": "resource_name",
"protocol": "tcp", "port": 5432, "type": "db|cache|queue|storage|api",
"detail": "hostname, ARN, 또는 endpoint URL"}}]}}"""

Q_ENRICHMENT = """각 서비스에 대해 다음 정보를 알려주세요:
- 리소스 설정 (CPU/메모리 limit/request)
- 헬스체크/프로브 설정
- 인스턴스/replica/task 수
- 참조하는 설정 (Secrets Manager, Parameter Store, ConfigMap, 환경변수)
- 스케일링 설정 (Auto Scaling, HPA 등)
JSON으로만 응답해주세요:
{{"enrichment": {{"service_name": {{"replicas": 1, "cpu_limit": "200m",
"memory_limit": "128Mi", "health_check": "/health or null",
"scaling": "auto|manual|none", "config_refs": [],
"secret_refs": []}}}}}}"""


@dataclass
class ChatBlock:
    """A single content block from the agent's response."""
    index: int
    block_type: str  # text, tool_summary, final_response, chat_title
    text: str
    block_id: str = ""


@dataclass
class ChatResponse:
    """Full structured response from a single question."""
    question: str
    blocks: list = field(default_factory=list)  # List[ChatBlock]
    parsed_json: dict = field(default_factory=dict)
    raw_text: str = ""

    @property
    def final_text(self) -> str:
        for b in reversed(self.blocks):
            if b.block_type == "final_response" and b.text:
                return b.text
        for b in reversed(self.blocks):
            if b.block_type == "text" and b.text:
                return b.text
        return ""

    @property
    def tool_calls(self) -> list:
        return [b.text for b in self.blocks if b.block_type == "tool_summary"]

    @property
    def reasoning(self) -> list:
        return [b.text for b in self.blocks if b.block_type == "text"]


class AgentChatClient:
    """Low-level wrapper around DevOps Agent chat API with full block capture."""

    def __init__(self, space_id: str, region: str = "us-east-1", profile: str = None):
        self.space_id = space_id
        session_kwargs = {"region_name": region}
        if profile:
            session_kwargs["profile_name"] = profile
        session = boto3.Session(**session_kwargs)
        self.client = session.client("devops-agent")

    def create_session(self) -> str:
        resp = self.client.create_chat(
            agentSpaceId=self.space_id,
            userId="simulator",
        )
        return resp["executionId"]

    def ask(self, execution_id: str, question: str) -> ChatResponse:
        """Send a message and collect full structured response."""
        resp = self.client.send_message(
            agentSpaceId=self.space_id,
            executionId=execution_id,
            content=question,
            userId="simulator",
        )
        events = resp.get("events", [])
        blocks_data = {}

        for event in events:
            if not isinstance(event, dict):
                continue
            for etype, edata in event.items():
                if etype == "contentBlockStart":
                    idx = edata.get("index", 0)
                    blocks_data[idx] = ChatBlock(
                        index=idx,
                        block_type=edata.get("type", "unknown"),
                        text="",
                        block_id=edata.get("id", ""),
                    )
                elif etype == "contentBlockDelta":
                    idx = edata.get("index", 0)
                    text = edata.get("delta", {}).get("textDelta", {}).get("text", "")
                    if idx in blocks_data:
                        blocks_data[idx].text += text

        blocks = [blocks_data[i] for i in sorted(blocks_data.keys())]
        chat_resp = ChatResponse(question=question, blocks=blocks)
        chat_resp.raw_text = chat_resp.final_text
        chat_resp.parsed_json = _extract_json(chat_resp.final_text) or {}
        return chat_resp


def _extract_json(text: str) -> Optional[dict]:
    """Extract JSON from agent response (may be wrapped in markdown fences)."""
    for pattern in [
        r"```json\s*\n(.*?)\n```",
        r"```\s*\n(.*?)\n```",
    ]:
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


class ChatTopologyDiscoverer:
    """Discovers service topology by chatting with DevOps Agent.

    Strategy:
    1. Ask structured questions in sequence (services → edges → external)
    2. Evaluate each response against quality criteria
    3. If insufficient, ask follow-up questions to refine
    4. Build ServiceGraph from accumulated data
    """

    def __init__(self, cfg: SimulatorConfig, namespace: str, on_progress=None):
        self.cfg = cfg
        self.namespace = namespace
        self._cache: Optional[ServiceGraph] = None
        self._cache_ttl = 300
        self.conversation: list = []  # List[ChatResponse] — full conversation log
        self.on_progress = on_progress  # callback(phase, question, response_dict)

    def _emit(self, phase: str, question: str, resp: "ChatResponse", result: dict = None):
        """Emit progress event for streaming UI."""
        if self.on_progress:
            self.on_progress(phase, question, {
                "answer": resp.final_text,
                "tool_calls": resp.tool_calls,
                "parsed_json": resp.parsed_json,
                "result": result or {},
            })

    def discover(self, force: bool = False) -> ServiceGraph:
        if self._cache and not force:
            if time.time() - self._cache.discovered_at < self._cache_ttl:
                return self._cache

        chat_cfg = self.cfg.chat
        if not chat_cfg.agent_space_id:
            raise ValueError(
                "chat.agent_space_id not configured. "
                "Set AGENT_SPACE_ID env var or add chat.agent_space_id to config."
            )

        client = AgentChatClient(
            space_id=chat_cfg.agent_space_id,
            region=chat_cfg.region,
            profile=chat_cfg.profile,
        )
        exec_id = client.create_session()
        print(f"[CHAT] Session: {exec_id}")

        graph = ServiceGraph(namespace=self.namespace, discovered_at=time.time())

        # Phase 1: Service discovery
        nodes = self._collect_services(client, exec_id)
        graph.nodes.extend(nodes)

        # Phase 2: Edge discovery (internal communications)
        edges = self._collect_edges(client, exec_id)
        graph.edges.extend(edges)

        # Phase 3: External dependencies
        ext_nodes, ext_edges = self._collect_external(client, exec_id, graph)
        graph.nodes.extend(ext_nodes)
        graph.edges.extend(ext_edges)

        # Phase 4: Evaluate and refine
        self._evaluate_and_refine(client, exec_id, graph)

        self._cache = graph
        return graph

    def _collect_services(self, client, exec_id) -> list:
        q = Q_SERVICES.format(namespace=self.namespace)
        print("[CHAT] Q1: Discovering services...")
        resp = client.ask(exec_id, q)
        self.conversation.append(resp)

        data = resp.parsed_json
        nodes = []
        if data and "services" in data:
            for svc in data["services"]:
                nodes.append(ServiceNode(
                    name=svc["name"],
                    namespace=self.namespace,
                    labels={"app": svc["name"]},
                    ports=svc.get("ports", []),
                    service_type=svc.get("service_type", "app"),
                    compute_type=svc.get("compute_type", ""),
                ))
            print(f"[CHAT]   → {len(nodes)} services: {[n.name for n in nodes]}")
        else:
            print("[CHAT]   → WARNING: Could not parse services")

        self._emit("services", q, resp, {"count": len(nodes), "names": [n.name for n in nodes]})

        # Quality check: at least 1 service found
        if not nodes:
            print("[CHAT]   → Retrying with simpler question...")
            q2 = (f"Just list the deployment names in the '{self.namespace}' namespace. "
                   f"Respond as JSON: {{\"names\": [\"svc1\", \"svc2\"]}}")
            resp2 = client.ask(exec_id, q2)
            self.conversation.append(resp2)
            data2 = resp2.parsed_json
            if data2 and "names" in data2:
                for name in data2["names"]:
                    nodes.append(ServiceNode(
                        name=name, namespace=self.namespace,
                        labels={"app": name},
                    ))
                print(f"[CHAT]   → Retry found {len(nodes)} services")
            self._emit("services_retry", q2, resp2, {"count": len(nodes)})

        return nodes

    def _collect_edges(self, client, exec_id) -> list:
        q = Q_EDGES.format(namespace=self.namespace)
        print("[CHAT] Q2: Discovering communications...")
        resp = client.ask(exec_id, q)
        self.conversation.append(resp)

        data = resp.parsed_json
        edges = []
        if data and "edges" in data:
            for e in data["edges"]:
                edges.append(ServiceEdge(
                    source=e["source"],
                    target=e["target"],
                    protocol=e.get("protocol", "tcp"),
                    port=e.get("port", 0),
                    paths=e.get("paths") or [],
                    methods=e.get("methods") or [],
                ))
            print(f"[CHAT]   → {len(edges)} edges: "
                  f"{[f'{e.source}→{e.target}' for e in edges]}")
        else:
            print("[CHAT]   → WARNING: Could not parse edges")

        self._emit("edges", q, resp, {
            "count": len(edges),
            "edges": [f"{e.source}→{e.target}" for e in edges],
        })
        return edges

    def _collect_external(self, client, exec_id, graph) -> tuple:
        q = Q_EXTERNAL.format(namespace=self.namespace)
        print("[CHAT] Q3: Discovering external dependencies...")
        resp = client.ask(exec_id, q)
        self.conversation.append(resp)

        data = resp.parsed_json
        ext_nodes = []
        ext_edges = []

        if data and "external_deps" in data:
            for dep in data["external_deps"]:
                target = dep["target"]
                if ".rds.amazonaws.com" in target:
                    short = target.split(".")[0]
                elif "arn:aws:" in target:
                    short = target.split(":")[-1].split("/")[-1]
                elif "." in target:
                    short = target.split(".")[0]
                else:
                    short = target

                dep_type = dep.get("type", "external")
                svc_type = {"db": "db", "cache": "cache", "aws_service": "app",
                            "external_api": "gateway"}.get(dep_type, "app")

                if not graph.get_node(short) and short not in [n.name for n in ext_nodes]:
                    ext_nodes.append(ServiceNode(
                        name=short, namespace="external",
                        kind="ExternalService", service_type=svc_type,
                        ports=[dep.get("port", 0)],
                    ))
                ext_edges.append(ServiceEdge(
                    source=dep["source"], target=short,
                    protocol=dep.get("protocol", "tcp"),
                    port=dep.get("port", 0),
                ))
            print(f"[CHAT]   → {len(data['external_deps'])} external deps")
        else:
            print("[CHAT]   → WARNING: Could not parse external deps")

        self._emit("external", q, resp, {
            "ext_nodes": len(ext_nodes),
            "ext_edges": len(ext_edges),
        })
        return ext_nodes, ext_edges

    def _evaluate_and_refine(self, client, exec_id, graph):
        """Evaluate graph quality and ask follow-up questions if needed."""
        issues = []

        # Check: services with no edges (isolated)
        connected = set()
        for e in graph.edges:
            connected.add(e.source)
            connected.add(e.target)
        isolated = [n.name for n in graph.nodes
                    if n.name not in connected and n.namespace != "external"]
        if isolated:
            issues.append(f"Isolated services (no edges): {isolated}")

        # Check: edges referencing unknown services
        known = {n.name for n in graph.nodes}
        for e in graph.edges:
            if e.source not in known:
                issues.append(f"Edge source '{e.source}' not in services list")
            if e.target not in known:
                issues.append(f"Edge target '{e.target}' not in services list")

        if not issues:
            print("[CHAT] Quality check passed")
            return

        print(f"[CHAT] Quality issues found: {len(issues)}")
        for issue in issues:
            print(f"[CHAT]   - {issue}")

        # Add missing nodes from edges
        for e in graph.edges:
            if e.source not in known:
                graph.nodes.append(ServiceNode(
                    name=e.source, namespace=self.namespace,
                    labels={"app": e.source},
                ))
                known.add(e.source)
            if e.target not in known:
                graph.nodes.append(ServiceNode(
                    name=e.target, namespace=self.namespace,
                    labels={"app": e.target},
                ))
                known.add(e.target)

        # Ask about isolated services
        if isolated:
            print(f"[CHAT] Asking about isolated services: {isolated}")
            q = (f"These services appear isolated with no communications: {isolated}. "
                 f"Do they communicate with any other services? "
                 f"Respond in JSON: {{\"edges\": [{{\"source\": \"...\", \"target\": \"...\", "
                 f"\"protocol\": \"...\", \"port\": 0}}]}}")
            resp = client.ask(exec_id, q)
            self.conversation.append(resp)
            data = resp.parsed_json
            if data and "edges" in data:
                for e in data["edges"]:
                    graph.edges.append(ServiceEdge(
                        source=e["source"], target=e["target"],
                        protocol=e.get("protocol", "tcp"),
                        port=e.get("port", 0),
                    ))
                print(f"[CHAT]   → Found {len(data['edges'])} additional edges")

    def get_conversation_log(self) -> list:
        """Return full conversation with tool calls as evidence trail."""
        log = []
        for resp in self.conversation:
            entry = {
                "question": resp.question,
                "answer": resp.final_text,
                "tool_calls": resp.tool_calls,
                "reasoning_steps": resp.reasoning,
                "parsed_json": resp.parsed_json,
            }
            log.append(entry)
        return log

    def save_conversation(self, path: str):
        """Save conversation log to JSON for audit/evidence."""
        import os
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.get_conversation_log(), f, indent=2, ensure_ascii=False)
        print(f"[CHAT] Conversation saved to {path}")
