"""Scenario generator: binds topology graph + templates → concrete scenario JSONs."""

import json
import os
from typing import Optional

from jinja2 import Environment, FileSystemLoader

from simulator.config import SimulatorConfig
from simulator.engine.enricher import EnrichedGraph, EnrichedNode
from simulator.engine.topology import ServiceEdge


class TemplateManifest:
    def __init__(self, templates_dir: str):
        self.templates_dir = templates_dir
        manifest_path = os.path.join(templates_dir, "manifest.json")
        with open(manifest_path) as f:
            data = json.load(f)
        self.templates = data.get("templates", [])
        self.jinja_env = Environment(
            loader=FileSystemLoader(templates_dir),
            keep_trailing_newline=True,
        )

    def get_edge_templates(self) -> list:
        return [t for t in self.templates if t.get("scope") == "edge"]

    def get_node_templates(self) -> list:
        return [t for t in self.templates if t.get("scope") == "node"]

    def get_cluster_templates(self) -> list:
        return [t for t in self.templates if t.get("scope") == "cluster"]

    def render(self, template_file: str, variables: dict) -> str:
        tmpl = self.jinja_env.get_template(template_file)
        return tmpl.render(**variables)


def _labels_to_yaml(labels: dict) -> str:
    return "\n".join(f"{k}: \"{v}\"" for k, v in labels.items())


def _is_applicable(template: dict, node: Optional[EnrichedNode] = None, edge: Optional[ServiceEdge] = None) -> bool:
    rules = template.get("applicability", {})
    if rules.get("requires_edge") and edge is None:
        return False
    if rules.get("requires_http_edge") and (edge is None or not edge.is_http):
        return False
    if rules.get("requires_memory_limit") and node and not node.has_memory_limit:
        return False
    if rules.get("requires_volumes") and node and not node.has_volumes:
        return False
    if rules.get("requires_liveness_probe") and node and not node.has_liveness_probe:
        return False
    if rules.get("requires_configmaps") and node and not node.has_configmaps:
        return False
    return True


class ScenarioGenerator:
    def __init__(self, enriched_graph: EnrichedGraph, cfg: SimulatorConfig):
        self.graph = enriched_graph.graph
        self.enriched = enriched_graph
        self.cfg = cfg
        self.manifest = TemplateManifest(cfg.templates_dir)

    def generate_all(self, categories: list = None, max_scenarios: int = 0) -> list:
        scenarios = []

        if not categories or "kubernetes" in categories:
            for tmpl in self.manifest.get_cluster_templates():
                if _is_applicable(tmpl):
                    scenarios.append(self._generate_cluster_scenario(tmpl))

        if not categories or "network" in categories:
            for edge in self.graph.edges:
                for tmpl in self.manifest.get_edge_templates():
                    target_node = self.enriched.get(edge.target)
                    if _is_applicable(tmpl, node=target_node, edge=edge):
                        scenarios.append(self._generate_edge_scenario(edge, tmpl))

        if not categories or "application" in categories:
            for node in self.graph.nodes:
                enriched_node = self.enriched.get(node.name)
                for tmpl in self.manifest.get_node_templates():
                    if _is_applicable(tmpl, node=enriched_node):
                        scenarios.append(self._generate_node_scenario(enriched_node, tmpl))

        if max_scenarios > 0:
            scenarios = scenarios[:max_scenarios]
        return scenarios

    # ── Cluster scope ──

    def _generate_cluster_scenario(self, template: dict) -> dict:
        tmpl_id = template["id"]
        scenario_id = tmpl_id.lower()
        chaos_namespace = template.get("chaos_namespace", "kube-system")

        variables = {
            "scenario_id": scenario_id,
            "namespace": self.graph.namespace,
            "chaos_namespace": chaos_namespace,
            **template.get("default_params", {}),
        }
        rendered_yaml = self.manifest.render(template["file"], variables)

        chaos_kind = template.get("chaos_kind", "PodChaos")
        cr_name = scenario_id
        all_services = [n.name for n in self.graph.nodes]

        summary_tmpl = template.get("summary", {})

        return {
            "id": scenario_id,
            "name": template["name"],
            "category": template["category"],
            "layer": template["layer"],
            "namespace": self.graph.namespace,
            "summary": {
                "objective": summary_tmpl.get("objective", ""),
                "description": template["description"],
                "expected_root_cause": f"kube-system의 {template['name']}",
                "detection_challenge": summary_tmpl.get("detection_challenge", ""),
                "success_criteria": summary_tmpl.get("success_criteria", []),
            },
            "normal_flow": self._build_cluster_normal_flow(all_services),
            "fault_flow": self._build_cluster_fault_flow(template, all_services),
            "trigger": {
                "type": "chaos_mesh",
                "kind": chaos_kind,
                "yaml": rendered_yaml,
            },
            "restore": {
                "type": "chaos_mesh_delete",
                "kind": chaos_kind,
                "name": cr_name,
                "namespace": chaos_namespace,
            },
            "verification": self._build_cluster_verification(template, scenario_id, chaos_kind, cr_name, chaos_namespace),
            "topology_context": {
                "fault_target": "kube-dns",
                "affected_services": all_services,
                "scope": "cluster",
            },
        }

    def _build_cluster_normal_flow(self, all_services: list) -> list:
        flow = []
        for edge in self.graph.edges:
            flow.append({
                "step": f"{edge.source} → {edge.target}",
                "from": edge.source,
                "to": edge.target,
                "desc": f"DNS resolve → {edge.protocol}:{edge.port} 호출",
            })
        return flow

    def _build_cluster_fault_flow(self, template: dict, all_services: list) -> list:
        return [
            {"step": "장애 주입", "icon": "red", "target": "kube-dns",
             "desc": f"{template.get('chaos_kind', 'PodChaos')} → {template['name']}"},
            {"step": "DNS 실패", "icon": "yellow", "target": "all",
             "desc": "모든 서비스의 DNS resolve 실패 — Name or service not known"},
            {"step": "통신 불가", "icon": "yellow", "target": "all",
             "desc": f"서비스 간 통신 전면 중단 — 영향: {', '.join(all_services)}"},
            {"step": "복원", "icon": "green", "target": "kube-dns",
             "desc": "Chaos Mesh CR 삭제 → CoreDNS 자동 복구"},
        ]

    def _build_cluster_verification(self, template, scenario_id, chaos_kind, cr_name, chaos_namespace) -> list:
        hints = template.get("verification_hints", {})
        steps = [{
            "name": f"Chaos Mesh {chaos_kind} injection 확인",
            "type": "chaos_status", "kind": chaos_kind,
            "name_": cr_name, "namespace": chaos_namespace,
            "expected": "Injected", "timeout": 60, "poll_interval": 5,
        }]
        coredns_hint = hints.get("coredns", {})
        if coredns_hint:
            steps.append({
                "name": "CoreDNS pod 중단 확인", "type": "pod_status",
                "deployment": coredns_hint.get("deployment", "coredns"),
                "namespace": coredns_hint.get("namespace", "kube-system"),
                "expected": coredns_hint.get("expected", "not_ready"),
                "timeout": 60, "poll_interval": 5,
            })
        pattern = hints.get("all_services", {}).get("pattern", "DNS|name resolution|no such host")
        for node in self.graph.nodes:
            steps.append({
                "name": f"{node.name} DNS 실패 확인", "type": "pod_logs",
                "deployment": node.name, "namespace": self.graph.namespace,
                "pattern": pattern, "timeout": 120, "poll_interval": 10,
            })
        return steps

    # ── Edge scope ──

    def _generate_edge_scenario(self, edge: ServiceEdge, template: dict) -> dict:
        tmpl_id = template["id"]
        scenario_id = f"{tmpl_id}-{edge.source}-{edge.target}".lower()

        target_node = self.graph.get_node(edge.target)
        source_node = self.graph.get_node(edge.source)
        target_labels = target_node.label_selector() if target_node else {"app": edge.target}
        source_labels = source_node.label_selector() if source_node else {"app": edge.source}

        variables = {
            "scenario_id": scenario_id,
            "namespace": self.graph.namespace,
            "label_selector_yaml": _labels_to_yaml(target_labels),
            "target_label_selector_yaml": _labels_to_yaml(source_labels),
            **template.get("default_params", {}),
        }
        rendered_yaml = self.manifest.render(template["file"], variables)

        cr_name = f"{scenario_id}-delay"
        chaos_kind = template.get("chaos_kind", "NetworkChaos")
        callers = self.graph.get_callers(edge.target)
        summary_tmpl = template.get("summary", {})

        return {
            "id": scenario_id,
            "name": f"{template['name']}: {edge.source} → {edge.target}",
            "category": template["category"],
            "layer": template["layer"],
            "namespace": self.graph.namespace,
            "summary": {
                "objective": summary_tmpl.get("objective", ""),
                "description": f"{edge.source}에서 {edge.target}로의 네트워크 지연({template.get('default_params', {}).get('latency', '500ms')})을 주입하여 upstream 서비스의 timeout/에러 발생을 유도",
                "expected_root_cause": f"{edge.target} 서비스로의 네트워크 지연 (NetworkChaos)",
                "detection_challenge": summary_tmpl.get("detection_challenge", ""),
                "success_criteria": summary_tmpl.get("success_criteria", []),
            },
            "normal_flow": self._build_edge_normal_flow(edge),
            "fault_flow": self._build_edge_fault_flow(edge, template, callers),
            "trigger": {
                "type": "chaos_mesh",
                "kind": chaos_kind,
                "yaml": rendered_yaml,
            },
            "restore": {
                "type": "chaos_mesh_delete",
                "kind": chaos_kind,
                "name": cr_name,
                "namespace": self.graph.namespace,
            },
            "verification": self._build_edge_verification(edge, template, scenario_id, chaos_kind, cr_name),
            "topology_context": {
                "fault_target": edge.target,
                "affected_callers": callers,
                "edge": {
                    "source": edge.source, "target": edge.target,
                    "protocol": edge.protocol, "port": edge.port,
                },
            },
        }

    def _build_edge_normal_flow(self, edge: ServiceEdge) -> list:
        flow = [
            {"step": f"{edge.source} → {edge.target}", "from": edge.source,
             "to": edge.target,
             "desc": f"{edge.protocol.upper()} {':'.join(str(p) for p in [edge.port]) if edge.port else ''} 정상 호출"},
        ]
        callees = self.graph.get_callees(edge.source)
        for callee in callees:
            if callee != edge.target:
                e = next((x for x in self.graph.edges if x.source == edge.source and x.target == callee), None)
                if e:
                    flow.append({
                        "step": f"{edge.source} → {callee}", "from": edge.source, "to": callee,
                        "desc": f"{e.protocol.upper()}:{e.port} 정상 호출",
                    })
        return flow

    def _build_edge_fault_flow(self, edge: ServiceEdge, template: dict, callers: list) -> list:
        latency = template.get("default_params", {}).get("latency", "500ms")
        flow = [
            {"step": "장애 주입", "icon": "red", "target": edge.target,
             "desc": f"NetworkChaos delay {latency} → {edge.target}"},
            {"step": "지연 발생", "icon": "yellow", "target": edge.source,
             "desc": f"{edge.source}의 {edge.target} 호출 응답 지연 ({latency})"},
        ]
        for caller in callers:
            if caller != edge.source:
                flow.append({
                    "step": "전파", "icon": "yellow", "target": caller,
                    "desc": f"{caller}도 {edge.target} 호출 시 지연 영향",
                })
        flow.append({
            "step": "복원", "icon": "green", "target": edge.target,
            "desc": "NetworkChaos CR 삭제 → 네트워크 정상 복구",
        })
        return flow

    def _build_edge_verification(self, edge, template, scenario_id, chaos_kind, cr_name) -> list:
        hints = template.get("verification_hints", {})
        steps = [{
            "name": f"Chaos Mesh {chaos_kind} injection 확인",
            "type": "chaos_status", "kind": chaos_kind,
            "name_": cr_name, "namespace": self.graph.namespace,
            "expected": "Injected", "timeout": 60, "poll_interval": 5,
        }]
        callers = self.graph.get_callers(edge.target)
        for caller in callers:
            pattern = hints.get("upstream", {}).get("pattern", "timeout|error|failed")
            steps.append({
                "name": f"{caller} 영향 확인 (upstream)", "type": "pod_logs",
                "deployment": caller, "namespace": self.graph.namespace,
                "pattern": pattern, "timeout": 120, "poll_interval": 10,
            })
        return steps

    # ── Node scope ──

    def _generate_node_scenario(self, node: EnrichedNode, template: dict) -> dict:
        tmpl_id = template["id"]
        scenario_id = f"{tmpl_id}-{node.name}".lower()
        labels = node.node.label_selector()

        variables = {
            "scenario_id": scenario_id, "namespace": self.graph.namespace,
            "deployment_name": node.name,
            "label_selector_yaml": _labels_to_yaml(labels),
            **template.get("default_params", {}),
        }
        rendered_yaml = self.manifest.render(template["file"], variables)

        chaos_kind = template.get("chaos_kind", "PodChaos")
        cr_name = scenario_id
        callers = self.graph.get_callers(node.name)
        summary_tmpl = template.get("summary", {})

        return {
            "id": scenario_id,
            "name": f"{template['name']}: {node.name}",
            "category": template["category"],
            "layer": template["layer"],
            "namespace": self.graph.namespace,
            "summary": {
                "objective": summary_tmpl.get("objective", ""),
                "description": f"{node.name}에 대한 {template['name']}",
                "expected_root_cause": f"{node.name}의 {template['name']}",
                "detection_challenge": summary_tmpl.get("detection_challenge", ""),
                "success_criteria": summary_tmpl.get("success_criteria", []),
            },
            "normal_flow": self._build_node_normal_flow(node),
            "fault_flow": self._build_node_fault_flow(node, template, callers),
            "trigger": {
                "type": "chaos_mesh", "kind": chaos_kind, "yaml": rendered_yaml,
            },
            "restore": {
                "type": "chaos_mesh_delete", "kind": chaos_kind,
                "name": cr_name, "namespace": self.graph.namespace,
            },
            "verification": self._build_node_verification(node, template, scenario_id, chaos_kind, cr_name),
            "topology_context": {
                "fault_target": node.name, "affected_callers": callers,
            },
        }

    def _build_node_normal_flow(self, node: EnrichedNode) -> list:
        flow = []
        for edge in self.graph.edges:
            if edge.source == node.name or edge.target == node.name:
                flow.append({
                    "step": f"{edge.source} → {edge.target}", "from": edge.source, "to": edge.target,
                    "desc": f"{edge.protocol.upper()}:{edge.port} 정상 호출",
                })
        return flow

    def _build_node_fault_flow(self, node: EnrichedNode, template: dict, callers: list) -> list:
        flow = [
            {"step": "장애 주입", "icon": "red", "target": node.name,
             "desc": f"{template.get('chaos_kind', 'PodChaos')} → {node.name} {template['name']}"},
        ]
        for caller in callers:
            flow.append({
                "step": "영향 전파", "icon": "yellow", "target": caller,
                "desc": f"{caller} → {node.name} 호출 실패/지연",
            })
        flow.append({
            "step": "복원", "icon": "green", "target": node.name,
            "desc": "Chaos Mesh CR 삭제 → 자동 복구",
        })
        return flow

    def _build_node_verification(self, node, template, scenario_id, chaos_kind, cr_name) -> list:
        hints = template.get("verification_hints", {})
        steps = [{
            "name": f"Chaos Mesh {chaos_kind} injection 확인",
            "type": "chaos_status", "kind": chaos_kind,
            "name_": cr_name, "namespace": self.graph.namespace,
            "expected": "Injected", "timeout": 60, "poll_interval": 5,
        }]
        direct_hint = hints.get("direct", {})
        if direct_hint:
            steps.append({
                "name": f"{node.name} 직접 영향 확인", "type": direct_hint.get("type", "pod_logs"),
                "deployment": node.name, "namespace": self.graph.namespace,
                "pattern": direct_hint.get("pattern", "error|failed"),
                "timeout": 120, "poll_interval": 10,
            })
        for caller in self.graph.get_callers(node.name):
            pattern = hints.get("upstream", {}).get("pattern", "timeout|error|failed")
            steps.append({
                "name": f"{caller} 영향 확인 (upstream)", "type": "pod_logs",
                "deployment": caller, "namespace": self.graph.namespace,
                "pattern": pattern, "timeout": 120, "poll_interval": 10,
            })
        return steps

    # ── Save ──

    def save(self, scenarios: list, output_dir: str = None):
        out = output_dir or self.cfg.output_dir
        os.makedirs(out, exist_ok=True)
        for scenario in scenarios:
            path = os.path.join(out, f"{scenario['id']}.json")
            with open(path, "w") as f:
                json.dump(scenario, f, indent=2, ensure_ascii=False)
        print(f"[OK] {len(scenarios)} scenarios saved to {out}")
