"""Topology discovery via Kubeshark API + K8s API fallback."""

import json
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

from simulator.config import SimulatorConfig


@dataclass
class ServiceNode:
    name: str
    namespace: str
    kind: str = "Deployment"
    labels: dict = field(default_factory=dict)
    ports: list = field(default_factory=list)
    service_type: str = "app"  # app, cache, db, gateway, queue
    compute_type: str = ""     # eks_pod, ecs_task, ec2_instance, lambda_function, unknown
    group: str = ""            # application group name
    description: str = ""

    def label_selector(self) -> dict:
        return self.labels if self.labels else {"app": self.name}


@dataclass
class ServiceEdge:
    source: str
    target: str
    protocol: str = "tcp"   # tcp, http, grpc, redis
    port: int = 0
    paths: list = field(default_factory=list)   # L7 HTTP paths observed
    methods: list = field(default_factory=list)  # L7 HTTP methods observed
    description: str = ""

    @property
    def is_http(self) -> bool:
        return self.protocol in ("http", "grpc")


@dataclass
class ServiceGraph:
    nodes: list = field(default_factory=list)  # List[ServiceNode]
    edges: list = field(default_factory=list)  # List[ServiceEdge]
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
                {
                    "name": n.name, "namespace": n.namespace, "kind": n.kind,
                    "labels": n.labels, "ports": n.ports, "service_type": n.service_type,
                    "compute_type": n.compute_type, "group": n.group,
                    "description": n.description,
                }
                for n in self.nodes
            ],
            "edges": [
                {
                    "source": e.source, "target": e.target, "protocol": e.protocol,
                    "port": e.port, "paths": e.paths, "methods": e.methods,
                    "description": e.description,
                }
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
                compute_type=n.get("compute_type", ""),
                group=n.get("group", ""), description=n.get("description", ""),
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


SERVICE_TYPE_HEURISTICS = {
    6379: "cache",
    5432: "db",
    3306: "db",
    27017: "db",
    9200: "search",
    9092: "queue",
    4222: "queue",
    5672: "queue",
}

IMAGE_TYPE_HEURISTICS = {
    "redis": "cache",
    "memcached": "cache",
    "postgres": "db",
    "mysql": "db",
    "mongo": "db",
    "elasticsearch": "search",
    "opensearch": "search",
    "kafka": "queue",
    "rabbitmq": "queue",
    "nats": "queue",
    "nginx": "gateway",
    "envoy": "gateway",
    "traefik": "gateway",
}


def _classify_service_type(name: str, ports: list, image: str = "") -> str:
    for port in ports:
        if port in SERVICE_TYPE_HEURISTICS:
            return SERVICE_TYPE_HEURISTICS[port]
    image_lower = image.lower()
    for pattern, stype in IMAGE_TYPE_HEURISTICS.items():
        if pattern in image_lower:
            return stype
    return "app"


def _run_kubectl(cmd: str, timeout: int = 30) -> Optional[str]:
    try:
        result = subprocess.run(
            ["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, Exception):
        pass
    return None


class TopologyDiscoverer:
    def __init__(self, cfg: SimulatorConfig, namespace: str):
        self.cfg = cfg
        self.namespace = namespace
        self._cache: Optional[ServiceGraph] = None
        self._cache_ttl = 300

    def discover(self, force: bool = False) -> ServiceGraph:
        if self._cache and not force:
            if time.time() - self._cache.discovered_at < self._cache_ttl:
                return self._cache

        graph = ServiceGraph(namespace=self.namespace, discovered_at=time.time())

        # Step 1: Discover nodes from K8s API (always available)
        self._discover_nodes_k8s(graph)

        # Step 2: Try Kubeshark for edge discovery (L4 + L7)
        kubeshark_ok = self._discover_edges_kubeshark(graph)

        # Step 3: Fallback — infer edges from env vars if Kubeshark unavailable
        if not kubeshark_ok:
            self._discover_edges_env_vars(graph)

        self._cache = graph
        return graph

    def _discover_nodes_k8s(self, graph: ServiceGraph):
        output = _run_kubectl(
            f"kubectl get deployments -n {self.namespace} "
            f"-o json"
        )
        if not output:
            return

        data = json.loads(output)
        for item in data.get("items", []):
            meta = item.get("metadata", {})
            spec = item.get("spec", {}).get("template", {}).get("spec", {})
            name = meta.get("name", "")
            labels = meta.get("labels", {})

            containers = spec.get("containers", [])
            ports = []
            image = ""
            for c in containers:
                if not image:
                    image = c.get("image", "")
                for p in c.get("ports", []):
                    ports.append(p.get("containerPort", 0))

            svc_type = _classify_service_type(name, ports, image)

            svc_output = _run_kubectl(
                f"kubectl get service {name} -n {self.namespace} "
                f"-o jsonpath='{{.spec.ports[*].port}}' 2>/dev/null"
            )
            if svc_output:
                for p in svc_output.split():
                    try:
                        ports.append(int(p))
                    except ValueError:
                        pass

            ports = sorted(set(ports))

            graph.nodes.append(ServiceNode(
                name=name,
                namespace=self.namespace,
                labels=labels,
                ports=ports,
                service_type=svc_type,
            ))

    def _discover_edges_kubeshark(self, graph: ServiceGraph) -> bool:
        try:
            resp = requests.get(
                f"{self.cfg.kubeshark.url}/api/mcp/flows",
                params={"ns": self.namespace, "format": "compact", "aggregate": "service"},
                timeout=10,
            )
            if resp.status_code != 200:
                return False

            flows = resp.json().get("flows", [])
            edge_map = {}

            for flow in flows:
                src_svc = flow.get("client", {}).get("svc", "")
                dst_svc = flow.get("server", {}).get("svc", "")
                dst_port = flow.get("server", {}).get("port", 0)
                proto = flow.get("proto", "tcp")

                if not src_svc or not dst_svc:
                    continue
                if src_svc == dst_svc:
                    continue

                key = (src_svc, dst_svc)
                if key not in edge_map:
                    edge_map[key] = ServiceEdge(
                        source=src_svc, target=dst_svc, protocol=proto, port=dst_port,
                    )

            # Enrich with L7 data
            self._enrich_edges_l7(edge_map)

            graph.edges = list(edge_map.values())
            return True

        except (requests.RequestException, Exception):
            return False

    def _enrich_edges_l7(self, edge_map: dict):
        try:
            resp = requests.get(
                f"{self.cfg.kubeshark.url}/api/mcp/calls",
                params={
                    "kfl": f'src.ns == "{self.namespace}"',
                    "format": "compact",
                    "limit": 500,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                return

            for call in resp.json().get("calls", []):
                src_svc = call.get("src", {}).get("svc", "")
                dst_svc = call.get("dst", {}).get("svc", "")
                method = call.get("method", "")
                path = call.get("path", "")

                key = (src_svc, dst_svc)
                if key in edge_map:
                    edge = edge_map[key]
                    edge.protocol = call.get("proto", edge.protocol)
                    if path and path not in edge.paths:
                        edge.paths.append(path)
                    if method and method not in edge.methods:
                        edge.methods.append(method)

        except (requests.RequestException, Exception):
            pass

    def _discover_edges_env_vars(self, graph: ServiceGraph):
        node_names = {n.name for n in graph.nodes}

        for node in graph.nodes:
            output = _run_kubectl(
                f"kubectl get deployment {node.name} -n {self.namespace} "
                f"-o jsonpath='{{.spec.template.spec.containers[*].env}}'"
            )
            if not output:
                continue

            try:
                env_lists = json.loads(f"[{output}]") if not output.startswith("[") else json.loads(output)
            except (json.JSONDecodeError, Exception):
                continue

            envs = env_lists if isinstance(env_lists, list) else []
            if envs and isinstance(envs[0], list):
                envs = [e for sublist in envs for e in sublist]

            for env in envs:
                if not isinstance(env, dict):
                    continue
                val = env.get("value", "")
                if not val:
                    continue
                for target_name in node_names:
                    if target_name == node.name:
                        continue
                    if target_name in val or target_name.replace("-", "_") in val:
                        if not any(e.source == node.name and e.target == target_name for e in graph.edges):
                            target_node = graph.get_node(target_name)
                            port = target_node.ports[0] if target_node and target_node.ports else 0
                            graph.edges.append(ServiceEdge(
                                source=node.name, target=target_name,
                                protocol="tcp", port=port,
                            ))
