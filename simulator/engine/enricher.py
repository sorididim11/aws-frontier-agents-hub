"""Enrich topology nodes with K8s resource details for template applicability."""

import json
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from simulator.engine.topology import ServiceGraph, ServiceNode


@dataclass
class ResourceSpec:
    cpu_request: str = ""
    cpu_limit: str = ""
    memory_request: str = ""
    memory_limit: str = ""


@dataclass
class ProbeSpec:
    type: str = ""        # httpGet, tcpSocket, exec
    path: str = ""        # for httpGet
    port: int = 0
    period_seconds: int = 0


@dataclass
class EnrichedNode:
    node: ServiceNode
    resources: ResourceSpec = field(default_factory=ResourceSpec)
    liveness_probe: Optional[ProbeSpec] = None
    readiness_probe: Optional[ProbeSpec] = None
    env_names: list = field(default_factory=list)
    configmap_refs: list = field(default_factory=list)
    secret_refs: list = field(default_factory=list)
    volume_paths: list = field(default_factory=list)
    has_hpa: bool = False
    replicas: int = 1
    image: str = ""

    @property
    def name(self) -> str:
        return self.node.name

    @property
    def has_memory_limit(self) -> bool:
        return bool(self.resources.memory_limit)

    @property
    def has_volumes(self) -> bool:
        return len(self.volume_paths) > 0

    @property
    def has_liveness_probe(self) -> bool:
        return self.liveness_probe is not None

    @property
    def has_configmaps(self) -> bool:
        return len(self.configmap_refs) > 0

    def to_dict(self) -> dict:
        return {
            "name": self.node.name,
            "namespace": self.node.namespace,
            "service_type": self.node.service_type,
            "image": self.image,
            "replicas": self.replicas,
            "resources": {
                "cpu_request": self.resources.cpu_request,
                "cpu_limit": self.resources.cpu_limit,
                "memory_request": self.resources.memory_request,
                "memory_limit": self.resources.memory_limit,
            },
            "liveness_probe": {
                "type": self.liveness_probe.type,
                "path": self.liveness_probe.path,
                "port": self.liveness_probe.port,
            } if self.liveness_probe else None,
            "readiness_probe": {
                "type": self.readiness_probe.type,
                "path": self.readiness_probe.path,
                "port": self.readiness_probe.port,
            } if self.readiness_probe else None,
            "configmap_refs": self.configmap_refs,
            "secret_refs": self.secret_refs,
            "volume_paths": self.volume_paths,
            "has_hpa": self.has_hpa,
            "env_names": self.env_names,
        }


@dataclass
class EnrichedGraph:
    graph: ServiceGraph
    enriched_nodes: dict = field(default_factory=dict)  # name -> EnrichedNode

    def get(self, name: str) -> Optional[EnrichedNode]:
        return self.enriched_nodes.get(name)

    def to_dict(self) -> dict:
        base = self.graph.to_dict()
        base["enriched_nodes"] = {k: v.to_dict() for k, v in self.enriched_nodes.items()}
        return base


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


def _parse_probe(probe_dict: dict) -> Optional[ProbeSpec]:
    if not probe_dict:
        return None

    if "httpGet" in probe_dict:
        http = probe_dict["httpGet"]
        return ProbeSpec(
            type="httpGet",
            path=http.get("path", "/"),
            port=http.get("port", 0),
            period_seconds=probe_dict.get("periodSeconds", 10),
        )
    elif "tcpSocket" in probe_dict:
        return ProbeSpec(
            type="tcpSocket",
            port=probe_dict["tcpSocket"].get("port", 0),
            period_seconds=probe_dict.get("periodSeconds", 10),
        )
    elif "exec" in probe_dict:
        return ProbeSpec(
            type="exec",
            period_seconds=probe_dict.get("periodSeconds", 10),
        )
    return None


class TopologyEnricher:
    def __init__(self, namespace: str):
        self.namespace = namespace

    def enrich(self, graph: ServiceGraph) -> EnrichedGraph:
        result = EnrichedGraph(graph=graph)

        for node in graph.nodes:
            enriched = self._enrich_node(node)
            result.enriched_nodes[node.name] = enriched

        return result

    def _enrich_node(self, node: ServiceNode) -> EnrichedNode:
        enriched = EnrichedNode(node=node)

        output = _run_kubectl(
            f"kubectl get deployment {node.name} -n {self.namespace} -o json"
        )
        if not output:
            return enriched

        try:
            deploy = json.loads(output)
        except json.JSONDecodeError:
            return enriched

        spec = deploy.get("spec", {})
        enriched.replicas = spec.get("replicas", 1)

        pod_spec = spec.get("template", {}).get("spec", {})
        containers = pod_spec.get("containers", [])

        if containers:
            c = containers[0]
            enriched.image = c.get("image", "")

            res = c.get("resources", {})
            limits = res.get("limits", {})
            reqs = res.get("requests", {})
            enriched.resources = ResourceSpec(
                cpu_request=reqs.get("cpu", ""),
                cpu_limit=limits.get("cpu", ""),
                memory_request=reqs.get("memory", ""),
                memory_limit=limits.get("memory", ""),
            )

            enriched.liveness_probe = _parse_probe(c.get("livenessProbe"))
            enriched.readiness_probe = _parse_probe(c.get("readinessProbe"))

            for env in c.get("env", []):
                enriched.env_names.append(env.get("name", ""))
                vf = env.get("valueFrom", {})
                if "configMapKeyRef" in vf:
                    cm_name = vf["configMapKeyRef"].get("name", "")
                    if cm_name and cm_name not in enriched.configmap_refs:
                        enriched.configmap_refs.append(cm_name)
                if "secretKeyRef" in vf:
                    s_name = vf["secretKeyRef"].get("name", "")
                    if s_name and s_name not in enriched.secret_refs:
                        enriched.secret_refs.append(s_name)

            for ef in c.get("envFrom", []):
                if "configMapRef" in ef:
                    cm_name = ef["configMapRef"].get("name", "")
                    if cm_name and cm_name not in enriched.configmap_refs:
                        enriched.configmap_refs.append(cm_name)
                if "secretRef" in ef:
                    s_name = ef["secretRef"].get("name", "")
                    if s_name and s_name not in enriched.secret_refs:
                        enriched.secret_refs.append(s_name)

            for vm in c.get("volumeMounts", []):
                enriched.volume_paths.append(vm.get("mountPath", ""))

        # Check HPA
        hpa_output = _run_kubectl(
            f"kubectl get hpa -n {self.namespace} "
            f"-o jsonpath='{{.items[*].spec.scaleTargetRef.name}}' 2>/dev/null"
        )
        if hpa_output and node.name in hpa_output.split():
            enriched.has_hpa = True

        return enriched
