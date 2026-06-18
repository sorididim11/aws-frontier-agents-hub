#!/usr/bin/env python3
"""
Universal Architecture Diagram Verifier — knowledge-driven, project-agnostic.

Supports multi-page L3: if knowledge.json has `l3_pages`, each entry maps to
a separate diagram page (index 2, 3, 4, …). Without `l3_pages`, falls back
to single-page L3 at index 2 for backward compatibility.

Usage:
    python3 verify_drawio.py \
        --drawio docs/architecture/my_architecture.drawio \
        --knowledge docs/architecture/knowledge.json \
        --icons <skill_dir>/icon_registry.json \
        --library <skill_dir>/component_library.json \
        --output docs/architecture/verification_report.json

Exit codes: 0=PASS, 1=FAIL, 2=ERROR
"""
import argparse
import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


# ─── XML Helpers ─────────────────────────────────────────────────────────────

def get_diagrams(tree: ET.ElementTree) -> list:
    return tree.findall(".//diagram")


def get_cells(diagram: ET.Element) -> list:
    cells = []
    for model in diagram.iter("mxGraphModel"):
        for root in model.iter("root"):
            cells.extend(root.findall("mxCell"))
    return cells


def cell_style(cell: ET.Element) -> str:
    return cell.get("style", "")


def cell_value(cell: ET.Element) -> str:
    return cell.get("value", "")


def is_edge(cell: ET.Element) -> bool:
    return cell.get("edge") == "1"


def is_vertex(cell: ET.Element) -> bool:
    return cell.get("vertex") == "1"


def cell_text(cell: ET.Element) -> str:
    """Normalize cell value for text matching."""
    import re
    text = cell_value(cell).lower()
    # Replace HTML line breaks with space
    text = re.sub(r'<br\s*/?>', ' ', text)
    text = text.replace("&#xa;", " ").replace("\n", " ")
    # Collapse multiple spaces
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def find_cells_by_name(cells: list, name: str) -> list:
    """Find vertex cells whose value contains the given name (case-insensitive)."""
    name_lower = name.lower()
    return [c for c in cells if is_vertex(c) and name_lower in cell_text(c)]


def cell_geometry(cell: ET.Element) -> dict:
    """Extract x, y, width, height from the mxGeometry child of a cell.

    Returns a dict with keys x, y, width, height or an empty dict if
    the cell has no mxGeometry child or the attributes are missing.
    """
    geom = cell.find("mxGeometry")
    if geom is None:
        return {}
    try:
        return {
            "x": float(geom.get("x", 0)),
            "y": float(geom.get("y", 0)),
            "width": float(geom.get("width", 0)),
            "height": float(geom.get("height", 0)),
        }
    except (TypeError, ValueError):
        return {}


# ─── Check Result ────────────────────────────────────────────────────────────

class CheckResult:
    def __init__(self, category: str, check: str, target: str,
                 passed: bool, message: str = "", suggestion: str = "",
                 severity: str = "ERROR"):
        self.category = category
        self.check = check
        self.target = target
        self.passed = passed
        self.message = message
        self.suggestion = suggestion
        self.severity = severity

    def to_dict(self) -> dict:
        d = {
            "category": self.category,
            "check": self.check,
            "target": self.target,
            "status": "PASS" if self.passed else "FAIL",
        }
        if not self.passed:
            d["message"] = self.message
            d["suggestion"] = self.suggestion
            d["severity"] = self.severity
        return d


# ─── Verifier ────────────────────────────────────────────────────────────────

class Verifier:
    def __init__(self, drawio_path: str, knowledge: dict,
                 icons: dict, library: dict):
        self.tree = ET.parse(drawio_path)
        self.knowledge = knowledge
        self.icons = icons
        self.library = library
        self.diagrams = get_diagrams(self.tree)
        self.results: list = []

        # Pre-compute lookups
        self.services = knowledge.get("services", [])
        self.namespaces = knowledge.get("namespaces", [])
        self.boundaries = knowledge.get("boundaries", [])
        self.data_flows = knowledge.get("data_flows", [])
        self.flows = knowledge.get("flows", [])
        self.groups = knowledge.get("groups", [])
        self.meta = knowledge.get("meta", {})
        self.l3_pages = knowledge.get("l3_pages", [])

        # Derive hop-level flows from flows[] (source of truth)
        self._flows_hops = []
        for flow in self.flows:
            for hop in flow.get("hops", []):
                self._flows_hops.append(hop)

        # Derived counts
        self.l3_count = max(1, len(self.l3_pages))
        self.expected_page_count = 2 + self.l3_count

        # Provider style markers
        self.provider_markers = {}
        for provider, pdata in icons.get("providers", {}).items():
            self.provider_markers[provider] = pdata.get("style_marker", "")
        self.k8s_marker = icons.get("kubernetes", {}).get("style_marker", "mxgraph.kubernetes")

        # Build service lookup by id
        self.svc_by_id = {s["id"]: s for s in self.services}

    # ── Level → Page Index Helpers ──────────────────────────────────────

    def level_to_indices(self, level: str) -> list:
        """Return list of page indices for a level. L3 returns multiple if l3_pages defined."""
        if level == "L1":
            return [0]
        if level == "L2":
            return [1]
        if level == "L3":
            if not self.l3_pages:
                return [2]  # backward compat: single L3
            return list(range(2, 2 + len(self.l3_pages)))
        return []

    def page_level(self, page_idx: int) -> str:
        """Given a page index, return the level name."""
        if page_idx == 0:
            return "L1"
        if page_idx == 1:
            return "L2"
        if page_idx >= 2:
            return "L3"
        return ""

    def l3_page_for_service(self, svc_id: str) -> list:
        """Return L3 page indices where this service appears (focal or collapsed)."""
        if not self.l3_pages:
            return [2]  # backward compat
        indices = []
        for i, page in enumerate(self.l3_pages):
            all_ids = set(page.get("focal_service_ids", []) + page.get("collapsed_service_ids", []))
            if svc_id in all_ids:
                indices.append(2 + i)
        return indices

    def run_all(self) -> list:
        self.check_structure()
        self.check_naming_conventions()
        self.check_l1_compliance()
        self.check_l2_compliance()
        self.check_l3_compliance()
        self.check_icon_consistency()
        self.check_icon_completeness()
        self.check_forbidden_combinations()
        self.check_completeness()
        self.check_l3_page_coverage()
        self.check_edge_label_quality()
        self.check_stereotypes()
        self.check_legend_presence()
        self.check_l3_component_breakdown()
        self.check_phantom_edges()
        self.check_missing_edges()
        self.check_flows_edge_labels()
        self.check_layout_quality()
        self.check_edge_routing_quality()
        self.check_observed_visualization()
        self.check_staleness()
        return self.results

    # ── Naming Conventions ───────────────────────────────────────────────

    def check_naming_conventions(self):
        cat = "naming_conventions"
        import re
        kebab = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')

        svc_ids = [s["id"] for s in self.services]
        flow_ids = [f["id"] for f in self.flows]
        bad = [i for i in svc_ids + flow_ids if not kebab.match(i)]
        self.results.append(CheckResult(
            cat, "ids_kebab_case", "knowledge.json",
            len(bad) == 0,
            f"{len(bad)} non-kebab-case ID(s): {', '.join(bad[:5])}",
            "Use lowercase kebab-case for all service and flow IDs"
        ))

        seen = set()
        dupes = set()
        for i in svc_ids + flow_ids:
            if i in seen:
                dupes.add(i)
            seen.add(i)
        self.results.append(CheckResult(
            cat, "ids_unique", "knowledge.json",
            len(dupes) == 0,
            f"Duplicate ID(s): {', '.join(dupes)}",
            "Ensure all service and flow IDs are unique"
        ))

    # ── Structure ────────────────────────────────────────────────────────

    def check_structure(self):
        cat = "structure"

        self.results.append(CheckResult(
            cat, "diagram_count", "mxfile",
            len(self.diagrams) >= self.expected_page_count,
            f"Expected >= {self.expected_page_count} diagram pages, found {len(self.diagrams)}",
            f"Add missing diagram pages (L1, L2, {self.l3_count} L3 pages)"
        ))

        # Check L1 and L2 page names
        for i, label in enumerate(["L1", "L2"]):
            if i < len(self.diagrams):
                name = self.diagrams[i].get("name", "")
                self.results.append(CheckResult(
                    cat, "page_name_contains_level", f"Page {i+1}: {name}",
                    label in name,
                    f"Page {i+1} name '{name}' does not contain '{label}'",
                    f"Rename page to include '{label}'"
                ))

        # Check L3 page names
        if self.l3_pages:
            for i, l3_page in enumerate(self.l3_pages):
                page_idx = 2 + i
                if page_idx < len(self.diagrams):
                    name = self.diagrams[page_idx].get("name", "")
                    self.results.append(CheckResult(
                        cat, "page_name_contains_level",
                        f"Page {page_idx+1}: {name}",
                        "L3" in name,
                        f"Page {page_idx+1} name '{name}' does not contain 'L3'",
                        f"Rename page to include 'L3' (expected: '{l3_page['name']}')"
                    ))
        else:
            # Single L3 backward compat
            if 2 < len(self.diagrams):
                name = self.diagrams[2].get("name", "")
                self.results.append(CheckResult(
                    cat, "page_name_contains_level", f"Page 3: {name}",
                    "L3" in name,
                    f"Page 3 name '{name}' does not contain 'L3'",
                    "Rename page to include 'L3'"
                ))

        # Check mxGraphModel and root for all expected pages
        for i, diag in enumerate(self.diagrams[:self.expected_page_count]):
            model = diag.find("mxGraphModel")
            has_model = model is not None
            self.results.append(CheckResult(
                cat, "has_graph_model", f"Page {i+1}",
                has_model,
                f"Page {i+1} missing <mxGraphModel>"
            ))
            if has_model:
                self.results.append(CheckResult(
                    cat, "has_root", f"Page {i+1}",
                    model.find("root") is not None,
                    f"Page {i+1} missing <root>"
                ))

    # ── L1 Compliance ────────────────────────────────────────────────────

    def check_l1_compliance(self):
        cat = "l1_compliance"
        if len(self.diagrams) < 1:
            return

        cells = get_cells(self.diagrams[0])
        vertices = [c for c in cells if is_vertex(c)]
        edges = [c for c in cells if is_edge(c)]

        # No K8s icons on L1
        k8s_in_l1 = [c for c in vertices if self.k8s_marker in cell_style(c)]
        self.results.append(CheckResult(
            cat, "no_k8s_icons_in_l1", "L1 page",
            len(k8s_in_l1) == 0,
            f"L1 should not contain K8s icons, found {len(k8s_in_l1)}",
            "L1 is service-level only — remove K8s deploy/svc icons",
            severity="WARNING"
        ))

        # No namespace groupings on L1
        ns_names = [ns["name"].lower() for ns in self.namespaces]
        ns_groups = [c for c in vertices
                     if "namespace" in cell_text(c)
                     or ("container=1" in cell_style(c)
                         and any(n in cell_text(c) for n in ns_names))]
        self.results.append(CheckResult(
            cat, "no_namespace_groups_in_l1", "L1 page",
            len(ns_groups) == 0,
            f"L1 should not contain namespace groupings, found {len(ns_groups)}",
            "L1 is service-level — remove namespace containers",
            severity="WARNING"
        ))

        # Should have flow edges
        l1_flows = [f for f in self.data_flows if "L1" in f.get("levels", [])]
        min_edges = max(3, len(l1_flows) - 2)  # some tolerance
        self.results.append(CheckResult(
            cat, "has_flow_edges", "L1 page",
            len(edges) >= min_edges,
            f"L1 should have flow arrows, found {len(edges)} edges (expected >= {min_edges} based on knowledge)",
            "Add numbered flow arrows"
        ))

    # ── L2 Compliance ────────────────────────────────────────────────────

    def check_l2_compliance(self):
        cat = "l2_compliance"
        if len(self.diagrams) < 2:
            return

        cells = get_cells(self.diagrams[1])
        vertices = [c for c in cells if is_vertex(c)]

        # Namespace containers
        l2_namespaces = [ns for ns in self.namespaces if "L2" in ns.get("levels", [])]
        if l2_namespaces:
            ns_containers = []
            ns_labels = []
            for c in vertices:
                val = cell_text(c)
                style = cell_style(c)
                if "container=1" in style or "swimlane" in style or "childLayout" in style:
                    for ns in l2_namespaces:
                        if ns["name"].lower() in val:
                            ns_containers.append(c)
                if "text;" in style or style.startswith("text;"):
                    if "ns:" in val or "namespace:" in val:
                        ns_labels.append(c)
            ns_total = max(len(ns_containers), len(ns_labels))
            self.results.append(CheckResult(
                cat, "has_namespace_containers", "L2 page",
                ns_total >= len(l2_namespaces),
                f"L2 should have {len(l2_namespaces)} namespace containers, found {ns_total}",
                "Add namespace container boxes"
            ))

        # K8s deploy icons
        l2_k8s = [s for s in self.services
                  if "L2" in s.get("levels", []) and s.get("category") == "k8s_workload"]
        if l2_k8s:
            k8s_deploys = [c for c in vertices if "prIcon=deploy" in cell_style(c)]
            self.results.append(CheckResult(
                cat, "has_k8s_deploy_icons", "L2 page",
                len(k8s_deploys) >= len(l2_k8s) * 0.5,
                f"L2 should have K8s deploy icons for {len(l2_k8s)} workloads, found {len(k8s_deploys)}",
                "Add K8s deploy icons"
            ))

        # K8s svc icons
        l2_svc = [s for s in self.services
                  if "L2" in s.get("levels", []) and s.get("category") == "k8s_service"]
        if l2_svc:
            k8s_svcs = [c for c in vertices if "prIcon=svc" in cell_style(c)]
            self.results.append(CheckResult(
                cat, "has_k8s_svc_icons", "L2 page",
                len(k8s_svcs) >= len(l2_svc) * 0.5,
                f"L2 should have K8s svc icons for {len(l2_svc)} services, found {len(k8s_svcs)}",
                "Add K8s svc icons"
            ))

        # Cloud provider icons
        l2_cloud = [s for s in self.services
                    if "L2" in s.get("levels", []) and s.get("category") == "cloud_managed"]
        if l2_cloud:
            provider = self.meta.get("cloud_provider", "aws")
            marker = self.provider_markers.get(provider, "mxgraph.aws4")
            provider_icons = [c for c in vertices if marker in cell_style(c)]
            self.results.append(CheckResult(
                cat, "has_provider_icons", "L2 page",
                len(provider_icons) >= len(l2_cloud) * 0.5,
                f"L2 should have provider icons for {len(l2_cloud)} cloud services, found {len(provider_icons)}",
                f"Add {provider.upper()} icons for managed services"
            ))

    # ── L3 Compliance ────────────────────────────────────────────────────

    def check_l3_compliance(self):
        cat = "l3_compliance"

        if self.l3_pages:
            self._check_l3_multi_page(cat)
        else:
            self._check_l3_single_page(cat)

    def _check_l3_single_page(self, cat: str):
        """Backward-compatible single L3 page check at index 2."""
        if len(self.diagrams) < 3:
            return

        cells = get_cells(self.diagrams[2])
        vertices = [c for c in cells if is_vertex(c)]

        l3_services = [s for s in self.services if "L3" in s.get("levels", [])]
        self._check_l3_detail_cards(cat, vertices, l3_services, "L3 page")
        self._check_l3_collapsed_icons(cat, vertices, l3_services, "L3 page")
        self._check_l3_k8s_icons(cat, vertices, l3_services, "L3 page")

    def _check_l3_multi_page(self, cat: str):
        """Check each L3 page independently based on l3_pages config."""
        for i, l3_page in enumerate(self.l3_pages):
            page_idx = 2 + i
            if page_idx >= len(self.diagrams):
                continue

            page_name = l3_page.get("name", f"L3 page {i+1}")
            cells = get_cells(self.diagrams[page_idx])
            vertices = [c for c in cells if is_vertex(c)]

            focal_ids = set(l3_page.get("focal_service_ids", []))
            collapsed_ids = set(l3_page.get("collapsed_service_ids", []))

            focal_services = [s for s in self.services if s["id"] in focal_ids]
            collapsed_services = [s for s in self.services if s["id"] in collapsed_ids]
            all_page_services = focal_services + collapsed_services

            # Focal services should have detail cards
            detail_card_svcs = [s for s in focal_services
                                if (s.get("l3_detail") or {}).get("type") == "detail_card"]
            if detail_card_svcs:
                detail_cards = [c for c in vertices
                                if sum(1 for p in self.DETAIL_CARD_PATTERNS if p in cell_text(c)) >= 2]
                self.results.append(CheckResult(
                    cat, "has_resource_detail_cards", page_name,
                    len(detail_cards) >= len(detail_card_svcs) * 0.5,
                    f"{page_name} should have detail cards for {len(detail_card_svcs)} focal services, found {len(detail_cards)}",
                    "Add detail cards with resource fields"
                ))

            # Collapsed services should have provider icons
            collapsed_cloud = [s for s in collapsed_services
                               if s.get("category") == "cloud_managed"]
            if collapsed_cloud:
                provider = self.meta.get("cloud_provider", "aws")
                marker = self.provider_markers.get(provider, "mxgraph.aws4")
                provider_icons = [c for c in vertices if marker in cell_style(c)]
                self.results.append(CheckResult(
                    cat, "has_collapsed_service_icons", page_name,
                    len(provider_icons) >= len(collapsed_cloud) * 0.5,
                    f"{page_name} should have collapsed icons for {len(collapsed_cloud)} services, found {len(provider_icons)}",
                    "Add collapsed service-level provider icons on the side"
                ))

            # K8s icons on focal area
            self._check_l3_k8s_icons(cat, vertices, focal_services, page_name)

    def _check_l3_detail_cards(self, cat: str, vertices: list, services: list, target: str):
        """Check detail cards exist for detail_card type services."""
        l3_detail = [s for s in services
                     if "L3" in s.get("levels", [])
                     and (s.get("l3_detail") or {}).get("type") == "detail_card"]
        if l3_detail:
            detail_cards = [c for c in vertices
                            if sum(1 for p in self.DETAIL_CARD_PATTERNS if p in cell_text(c)) >= 2]
            self.results.append(CheckResult(
                cat, "has_resource_detail_cards", target,
                len(detail_cards) >= len(l3_detail) * 0.5,
                f"{target} should have detail cards for {len(l3_detail)} services, found {len(detail_cards)}",
                "Add deployment cards with image, CPU, memory, probes"
            ))

    def _check_l3_collapsed_icons(self, cat: str, vertices: list, services: list, target: str):
        """Check collapsed icons for collapsed_icon type services."""
        l3_collapsed = [s for s in services
                        if "L3" in s.get("levels", [])
                        and (s.get("l3_detail") or {}).get("type") == "collapsed_icon"]
        if l3_collapsed:
            provider = self.meta.get("cloud_provider", "aws")
            marker = self.provider_markers.get(provider, "mxgraph.aws4")
            provider_icons = [c for c in vertices if marker in cell_style(c)]
            self.results.append(CheckResult(
                cat, "has_collapsed_service_icons", target,
                len(provider_icons) >= len(l3_collapsed) * 0.5,
                f"{target} should have collapsed icons for {len(l3_collapsed)} services, found {len(provider_icons)}",
                "Add collapsed service-level provider icons on the side"
            ))

    def _check_l3_k8s_icons(self, cat: str, vertices: list, services: list, target: str):
        """Check K8s icons exist for k8s_workload and k8s_service category services."""
        k8s_workloads = [s for s in services
                         if s.get("category") == "k8s_workload"
                         and "L3" in s.get("levels", [])]
        if k8s_workloads:
            k8s_icons = [c for c in vertices if self.k8s_marker in cell_style(c)]
            self.results.append(CheckResult(
                cat, "l3_k8s_workloads_have_icons", target,
                len(k8s_icons) >= len(k8s_workloads) * 0.5,
                f"{target} has {len(k8s_workloads)} K8s workloads but only {len(k8s_icons)} K8s icons. "
                f"Each K8s workload must have a K8s icon alongside its detail card.",
                "Add K8s deploy/svc icons paired with each detail card"
            ))

        k8s_services = [s for s in services
                        if s.get("category") == "k8s_service"
                        and "L3" in s.get("levels", [])]
        if k8s_services:
            svc_icons = [c for c in vertices if "prIcon=svc" in cell_style(c)]
            self.results.append(CheckResult(
                cat, "l3_k8s_services_have_icons", target,
                len(svc_icons) >= len(k8s_services) * 0.5,
                f"{target} has {len(k8s_services)} K8s services but only {len(svc_icons)} svc icons.",
                "Add K8s svc icons alongside service cards"
            ))

    # ── Icon Consistency ─────────────────────────────────────────────────

    def check_icon_consistency(self):
        cat = "icon_consistency"
        provider = self.meta.get("cloud_provider", "aws")
        provider_marker = self.provider_markers.get(provider, "mxgraph.aws4")

        # Check pages 1 (L2) through all L3 pages
        for page_idx in range(1, min(len(self.diagrams), self.expected_page_count)):
            level = self.page_level(page_idx)
            cells = get_cells(self.diagrams[page_idx])

            for svc in self.services:
                if level not in svc.get("levels", []):
                    continue

                # For L3 multi-page: only check service on pages where it appears
                if level == "L3" and self.l3_pages:
                    svc_pages = self.l3_page_for_service(svc["id"])
                    if page_idx not in svc_pages:
                        continue

                svc_category = svc.get("category", "")
                svc_name = svc.get("name", "")
                matching = find_cells_by_name(cells, svc_name)

                if not matching:
                    continue  # handled by completeness check

                # For L3 detail_card type, the card itself is a rectangle — that's OK
                if level == "L3" and (svc.get("l3_detail") or {}).get("type") == "detail_card":
                    continue  # handled by l3_compliance

                # For L3 component_breakdown type, icon is a child cell inside container — skip
                if level == "L3" and (svc.get("l3_detail") or {}).get("type") == "component_breakdown":
                    continue  # checked by check_l3_component_breakdown

                # Check cloud-managed services use provider icons
                if svc_category == "cloud_managed":
                    has_provider = any(provider_marker in cell_style(c) for c in matching)
                    if not has_provider:
                        icon_cells = [c for c in matching
                                      if is_vertex(c)
                                      and "text;" not in cell_style(c)
                                      and "container=1" not in cell_style(c)]
                        if icon_cells:
                            self.results.append(CheckResult(
                                cat, "cloud_service_uses_provider_icon",
                                f"{svc_name} ({level})",
                                False,
                                f"Cloud service '{svc_name}' on {level} does not use provider icon",
                                f"Use style containing '{provider_marker}'"
                            ))

    # ── Icon Completeness ──────────────────────────────────────────────

    def check_icon_completeness(self):
        cat = "icon_completeness"
        valid_templates = set(self.library.get("templates", {}).keys())
        bad = []
        for svc in self.services:
            tmpl = svc.get("icon_template", "")
            if tmpl and tmpl not in valid_templates:
                bad.append(f"{svc['id']}={tmpl}")
        self.results.append(CheckResult(
            cat, "valid_icon_templates", "knowledge.json",
            len(bad) == 0,
            f"{len(bad)} service(s) with invalid icon_template: {', '.join(bad[:5])}",
            "Use a template from component_library.json templates section"
        ))

    # ── Forbidden Combinations ───────────────────────────────────────────

    def check_forbidden_combinations(self):
        cat = "forbidden_combo"

        # Build set of L3 detail_card service names for exception handling
        l3_detail_card_names = set()
        for svc in self.services:
            if "L3" in svc.get("levels", []):
                detail = svc.get("l3_detail") or {}
                if detail.get("type") == "detail_card":
                    l3_detail_card_names.add(svc["name"].lower())

        for rule in self.icons.get("forbidden_rules", []):
            bad_fragment = rule.get("bad_style_fragment", "")
            if not bad_fragment:
                continue

            has_exception = "detail_card" in rule.get("exception", "")

            for page_idx, diag in enumerate(self.diagrams[:self.expected_page_count]):
                level = self.page_level(page_idx)

                # The "no plain rectangles" rule only applies to L2/L3
                if "L2" not in rule.get("condition", "") and "L3" not in rule.get("condition", ""):
                    pass  # apply to all levels
                elif level == "L1":
                    continue  # skip L1 for L2/L3-specific rules

                for cell in get_cells(diag):
                    if not is_vertex(cell):
                        continue
                    if bad_fragment in cell_style(cell):
                        val = cell_text(cell)

                        # On L3, detail cards are intentionally rectangles — skip them
                        if level == "L3" and has_exception:
                            if any(n in val for n in l3_detail_card_names):
                                continue

                        # Check if this cell belongs to a K8s workload
                        for svc in self.services:
                            if svc.get("category") in ("k8s_workload", "k8s_service") and svc["name"].lower() in val:
                                style = cell_style(cell)
                                if "container=1" in style or style.startswith("text;"):
                                    continue
                                self.results.append(CheckResult(
                                    cat, "forbidden_icon_combo",
                                    f"{val[:40]} ({level})",
                                    False,
                                    rule.get("description", "Forbidden icon combination"),
                                    f"Replace with {rule.get('correct_template', 'correct icon')}"
                                ))

    # ── Completeness ─────────────────────────────────────────────────────

    def _name_found_in_text(self, name: str, all_text: str) -> bool:
        """Flexible name matching: try the full name, then key parts."""
        name_lower = name.lower()

        # Direct match
        if name_lower in all_text:
            return True

        # Strip common prefixes/suffixes and try again
        for prefix in ("lambda ", "aws ", "amazon "):
            if name_lower.startswith(prefix):
                stripped = name_lower[len(prefix):]
                if stripped in all_text or stripped.replace(" ", "-") in all_text:
                    return True

        # Try hyphenated version: "Event Handler" → "event-handler"
        hyphenated = name_lower.replace(" ", "-")
        if hyphenated in all_text:
            return True

        # Try underscore version: "Secrets Manager" → "secrets_manager"
        underscored = name_lower.replace(" ", "_")
        if underscored in all_text:
            return True

        # For services with "-svc" suffix, check for "svc/name" pattern in L3 cards
        if name_lower.endswith("-svc"):
            base = name_lower[:-4]  # "hasher-svc" → "hasher"
            if f"svc/{base}" in all_text:
                return True

        return False

    def check_completeness(self):
        cat = "completeness"

        for svc in self.services:
            for level in svc.get("levels", []):
                if level == "L3":
                    # For L3 with multi-page: check only the pages where this service should appear
                    page_indices = self.l3_page_for_service(svc["id"])
                    for idx in page_indices:
                        if idx >= len(self.diagrams):
                            continue
                        cells = get_cells(self.diagrams[idx])
                        all_text = " ".join(cell_text(c) for c in cells)
                        found = self._name_found_in_text(svc["name"], all_text)
                        # Determine page label for reporting
                        if self.l3_pages and 0 <= idx - 2 < len(self.l3_pages):
                            page_label = self.l3_pages[idx - 2]["name"]
                        else:
                            page_label = "L3"
                        self.results.append(CheckResult(
                            cat, "service_present",
                            f"{svc['name']} on {page_label}",
                            found,
                            f"'{svc['name']}' listed for {page_label} but not found in diagram",
                            f"Add '{svc['name']}' to {page_label} page",
                            severity="ERROR" if svc.get("category") in ("k8s_workload", "cloud_managed") else "WARNING"
                        ))
                else:
                    # L1 and L2 — simple index lookup
                    idx = {"L1": 0, "L2": 1}.get(level, -1)
                    if idx < 0 or idx >= len(self.diagrams):
                        continue
                    cells = get_cells(self.diagrams[idx])
                    all_text = " ".join(cell_text(c) for c in cells)
                    found = self._name_found_in_text(svc["name"], all_text)
                    self.results.append(CheckResult(
                        cat, "service_present",
                        f"{svc['name']} on {level}",
                        found,
                        f"'{svc['name']}' listed in knowledge for {level} but not found in diagram",
                        f"Add '{svc['name']}' to {level} page",
                        severity="ERROR" if svc.get("category") in ("k8s_workload", "cloud_managed") else "WARNING"
                    ))

    # ── L3 Page Coverage ─────────────────────────────────────────────────

    def check_l3_page_coverage(self):
        """Warn if any L3 service is not assigned to any l3_pages entry."""
        if not self.l3_pages:
            return  # only applies when l3_pages is defined

        cat = "l3_coverage"
        all_assigned = set()
        for page in self.l3_pages:
            all_assigned.update(page.get("focal_service_ids", []))
            all_assigned.update(page.get("collapsed_service_ids", []))

        for svc in self.services:
            if "L3" in svc.get("levels", []):
                self.results.append(CheckResult(
                    cat, "service_in_l3_page",
                    f"{svc['name']} (id: {svc['id']})",
                    svc["id"] in all_assigned,
                    f"Service '{svc['name']}' has L3 in levels but is not in any l3_pages entry",
                    f"Add '{svc['id']}' to focal_service_ids or collapsed_service_ids in an l3_pages entry",
                    severity="WARNING"
                ))

    # ── Edge Label Quality ────────────────────────────────────────────

    DETAIL_CARD_PATTERNS = [
        # K8s workload fields
        "image:", "cpu:", "mem:", "port:", "replicas:", "probe",
        # Lambda/serverless fields
        "runtime:", "timeout:", "handler:", "triggers:",
        # Cloud service fields
        "model:", "table:", "invocation:", "billing:", "partition_key:",
        "webhook:", "tools:", "region:", "bus:", "pattern:", "targets:",
        "topic:", "rule:", "type:", "memory:",
        # Networking fields (VPC, PrivateLink, NLB)
        "acceptance:", "nlb:", "allowed_principals:", "sg_ingress:",
        "sg:", "private_dns:", "subnets:", "cidr:",
        # IAM / Security fields
        "role:", "role_name:", "trust:", "managed_policy:", "policy:",
        "eks_access:", "cluster:", "permissions:", "principal:",
    ]

    # Labels that are too generic — just a protocol or single vague word
    VAGUE_LABELS = {
        "trigger", "sql", "webhook", "store", "query", "invoke",
        "call", "send", "get", "post", "put", "delete", "update",
        "event", "data", "request", "response", "message",
    }

    def check_edge_label_quality(self):
        """Check that edge labels describe business actions, not just protocols."""
        cat = "edge_label_quality"

        # Check L2 (index 1) — most important for label quality
        if len(self.diagrams) < 2:
            return

        cells = get_cells(self.diagrams[1])
        edges = [c for c in cells if is_edge(c)]

        vague_count = 0
        good_count = 0
        for edge in edges:
            label = cell_text(edge).strip()
            if not label:
                continue
            # A label is vague if it's a single word matching our vague list
            words = label.replace("-", " ").replace("_", " ").split()
            if len(words) <= 1 and words[0].lower() in self.VAGUE_LABELS:
                vague_count += 1
            elif len(words) <= 2 and all(w.lower() in self.VAGUE_LABELS for w in words):
                vague_count += 1
            else:
                good_count += 1

        total_labeled = vague_count + good_count
        if total_labeled > 0:
            vague_ratio = vague_count / total_labeled
            self.results.append(CheckResult(
                cat, "action_oriented_labels", "L2 page",
                vague_ratio < 0.3,  # allow up to 30% vague (some short labels are OK)
                f"L2 has {vague_count}/{total_labeled} vague edge labels "
                f"(single generic words like 'Trigger', 'SQL'). "
                f"Labels should describe actions: 'forwards alarm notification (SNS)'",
                "Rewrite vague labels to action+object+(protocol) pattern",
                severity="WARNING"
            ))

    # ── Stereotype Checks ───────────────────────────────────────────

    def check_stereotypes(self):
        """Check that L2 services have UML stereotype annotations."""
        cat = "stereotypes"

        # Only check if knowledge.json has stereotype fields
        services_with_stereotype = [
            s for s in self.services if s.get("stereotype")
        ]
        if not services_with_stereotype:
            return  # no stereotypes defined → skip

        if len(self.diagrams) < 2:
            return

        cells = get_cells(self.diagrams[1])
        all_text = " ".join(cell_text(c) for c in cells if is_vertex(c))

        # Check for stereotype notation (<<...>>) in L2
        # In draw.io XML, << is encoded as &lt;&lt; or &amp;lt;&amp;lt; in html=1 cells
        stereotype_count = (all_text.count("<<")
                            + all_text.count("&lt;&lt;")
                            + all_text.count("&amp;lt;&amp;lt;"))
        expected = len(services_with_stereotype)

        self.results.append(CheckResult(
            cat, "l2_stereotype_annotations", "L2 page",
            stereotype_count >= expected * 0.5,
            f"L2 should have stereotype annotations for {expected} services, "
            f"found {stereotype_count} '<<...>>' markers",
            "Add <<Stereotype>> labels below service icons (fontSize=9, italic)",
            severity="WARNING"
        ))

    # ── Legend Presence ────────────────────────────────────────────────

    def check_legend_presence(self):
        cat = "legend_presence"
        for page_idx in (0, 1):
            if page_idx >= len(self.diagrams):
                continue
            level = self.page_level(page_idx)
            cells = get_cells(self.diagrams[page_idx])
            found = False
            has_colors = False
            for c in cells:
                cid = (c.get("id") or "").lower()
                val = cell_text(c).lower()
                if "legend" in cid or "legend" in val:
                    found = True
                    raw = c.get("value", "")
                    if "font color" in raw.lower() or "color=" in raw.lower():
                        has_colors = True
                    break
            self.results.append(CheckResult(
                cat, "legend_exists", level,
                found and has_colors,
                f"{level} page {'missing legend cell' if not found else 'legend has no color descriptions'}",
                "Add an edge-color legend at the bottom of the page",
                severity="WARNING"
            ))

    # ── L3 Component Breakdown ──────────────────────────────────────

    def check_l3_component_breakdown(self):
        """Check that component_breakdown services show internal structure."""
        cat = "component_breakdown"

        if not self.l3_pages:
            return

        for i, l3_page in enumerate(self.l3_pages):
            page_idx = 2 + i
            if page_idx >= len(self.diagrams):
                continue

            page_name = l3_page.get("name", f"L3 page {i+1}")
            focal_ids = set(l3_page.get("focal_service_ids", []))

            # Find services with component_breakdown type that are focal on this page
            breakdown_services = [
                s for s in self.services
                if s["id"] in focal_ids
                and (s.get("l3_detail") or {}).get("type") == "component_breakdown"
            ]

            if not breakdown_services:
                continue

            cells = get_cells(self.diagrams[page_idx])
            vertices = [c for c in cells if is_vertex(c)]
            all_text = " ".join(cell_text(c) for c in vertices)

            for svc in breakdown_services:
                components = (svc.get("l3_detail") or {}).get("components", [])
                if not components:
                    continue

                # Check that at least some component names appear in the page
                found = sum(
                    1 for comp in components
                    if comp["name"].lower() in all_text
                )

                self.results.append(CheckResult(
                    cat, "components_rendered",
                    f"{svc['name']} on {page_name}",
                    found >= len(components) * 0.5,
                    f"'{svc['name']}' has {len(components)} internal components "
                    f"but only {found} found in diagram",
                    f"Render internal components as swimlane sections "
                    f"(Input/Processing/Output) inside a container",
                    severity="WARNING"
                ))

    # ── Phantom Edge Detection ──────────────────────────────────────────

    def check_phantom_edges(self):
        """Check that edges in drawio correspond to data_flows in knowledge.json.

        A 'phantom edge' is an edge drawn in the diagram that has no corresponding
        data_flow entry at that level. This catches the #1 cause of bad diagrams:
        agents inventing edges that don't exist in the data.
        """
        cat = "phantom_edges"

        for page_idx in range(min(len(self.diagrams), self.expected_page_count)):
            level = self.page_level(page_idx)
            cells = get_cells(self.diagrams[page_idx])
            edges = [c for c in cells if is_edge(c)]

            # Build set of expected flows for this level
            # Prefer flows[].hops[] (source of truth) over legacy data_flows
            if self.flows:
                level_flows = [
                    hop for hop in self._flows_hops
                    if level in hop.get("levels", [])
                ]
            else:
                level_flows = [
                    f for f in self.data_flows
                    if level in f.get("levels", [])
                ]

            # Build set of service names at this level for matching
            level_svc_names = {}
            for svc in self.services:
                if level in svc.get("levels", []):
                    level_svc_names[svc["name"].lower()] = svc["id"]
                    # Also index by id for direct matching
                    level_svc_names[svc["id"].lower()] = svc["id"]

            # For L3 multi-page, also include collapsed services
            if level == "L3" and self.l3_pages and page_idx - 2 < len(self.l3_pages):
                l3_page = self.l3_pages[page_idx - 2]
                for sid in l3_page.get("collapsed_service_ids", []):
                    svc = self.svc_by_id.get(sid)
                    if svc:
                        level_svc_names[svc["name"].lower()] = svc["id"]
                        level_svc_names[svc["id"].lower()] = svc["id"]

            # Build flow signature set: (from_id, to_id)
            expected_flow_sigs = set()
            for f in level_flows:
                expected_flow_sigs.add((f["from"], f["to"]))

            # Count edges that have labels (meaningful flow edges, not layout helpers)
            labeled_edges = [e for e in edges if cell_text(e).strip()]

            # For each labeled edge, try to match it against expected flows
            # We check the edge's source/target cells to identify service names
            unmatched_labels = []
            for edge in labeled_edges:
                source_id = edge.get("source", "")
                target_id = edge.get("target", "")
                label = cell_text(edge).strip()

                # Skip legend/title text edges and very short decorative edges
                if not label or len(label) < 3:
                    continue
                # Skip if label looks like a legend entry (contains multiple colors/dashes)
                if "──" in label or "—" in label or "font color" in label.lower():
                    continue

                # Try to resolve source/target cell IDs to service names
                source_svc = self._resolve_cell_to_service(cells, source_id, level_svc_names)
                target_svc = self._resolve_cell_to_service(cells, target_id, level_svc_names)

                if source_svc and target_svc:
                    sig = (source_svc, target_svc)
                    if sig not in expected_flow_sigs:
                        unmatched_labels.append(
                            f"'{label[:50]}' ({source_svc} → {target_svc})"
                        )

            # Report: allow some tolerance (legends, decorative edges)
            page_label = level
            if level == "L3" and self.l3_pages and page_idx - 2 < len(self.l3_pages):
                page_label = self.l3_pages[page_idx - 2].get("name", level)

            if labeled_edges:
                self.results.append(CheckResult(
                    cat, "no_phantom_edges", page_label,
                    len(unmatched_labels) == 0,
                    f"{page_label} has {len(unmatched_labels)} phantom edge(s) not in knowledge.json: "
                    + "; ".join(unmatched_labels[:3]),
                    "Remove edges that don't correspond to data_flows entries, "
                    "or add the missing flows to knowledge.json",
                    severity="WARNING"
                ))

    def _resolve_cell_to_service(self, cells: list, cell_id: str,
                                  svc_names: dict) -> str:
        """Try to resolve a cell ID to a service ID via its value or parent chain."""
        if not cell_id:
            return ""

        # Find the cell
        cell = None
        for c in cells:
            if c.get("id") == cell_id:
                cell = c
                break
        if cell is None:
            return ""

        # Check cell value against service names (longest match first to avoid
        # substring false positives, e.g. "hasher" matching "hasher-svc")
        val = cell_text(cell).strip()
        val_lower = val.lower()
        for name, svc_id in sorted(svc_names.items(), key=lambda x: len(x[0]), reverse=True):
            if name in val_lower:
                return svc_id

        # Word-set fallback: all words of service name appear in cell value
        val_words = set(val_lower.split())
        for name, svc_id in sorted(svc_names.items(), key=lambda x: len(x[0]), reverse=True):
            name_words = set(name.split())
            if len(name_words) >= 2 and name_words.issubset(val_words):
                return svc_id

        # Check parent cell (for cells inside containers)
        parent_id = cell.get("parent", "")
        if parent_id and parent_id not in ("0", "1"):
            for c in cells:
                if c.get("id") == parent_id:
                    pval = cell_text(c).strip().lower()
                    for name, svc_id in sorted(svc_names.items(), key=lambda x: len(x[0]), reverse=True):
                        if name in pval:
                            return svc_id
                    break

        return ""

    # ── Missing Edges ─────────────────────────────────────────────────

    def check_missing_edges(self):
        """Reverse of phantom_edges: find flows in knowledge.json with no diagram edge."""
        cat = "missing_edges"

        for page_idx in range(min(len(self.diagrams), self.expected_page_count)):
            level = self.page_level(page_idx)
            cells = get_cells(self.diagrams[page_idx])
            edges = [c for c in cells if is_edge(c)]
            labeled_edges = [e for e in edges if cell_text(e).strip()]

            level_svc_names = {}
            for svc in self.services:
                if level in svc.get("levels", []):
                    level_svc_names[svc["name"].lower()] = svc["id"]
                    level_svc_names[svc["id"].lower()] = svc["id"]
            if level == "L3" and self.l3_pages and page_idx - 2 < len(self.l3_pages):
                l3_page = self.l3_pages[page_idx - 2]
                for sid in l3_page.get("collapsed_service_ids", []):
                    svc = self.svc_by_id.get(sid)
                    if svc:
                        level_svc_names[svc["name"].lower()] = svc["id"]
                        level_svc_names[svc["id"].lower()] = svc["id"]

            if self.flows:
                level_flows = [h for h in self._flows_hops if level in h.get("levels", [])]
            else:
                level_flows = [f for f in self.data_flows if level in f.get("levels", [])]

            expected_flow_sigs = set()
            if level == "L3" and self.l3_pages and page_idx - 2 < len(self.l3_pages):
                l3_page = self.l3_pages[page_idx - 2]
                page_svc_ids = set(l3_page.get("focal_service_ids", []) + l3_page.get("collapsed_service_ids", []))
                for f in level_flows:
                    if f["from"] in page_svc_ids and f["to"] in page_svc_ids:
                        expected_flow_sigs.add((f["from"], f["to"]))
            else:
                for f in level_flows:
                    expected_flow_sigs.add((f["from"], f["to"]))

            diagram_sigs = set()
            for edge in labeled_edges:
                label = cell_text(edge).strip()
                if not label or len(label) < 3:
                    continue
                if "──" in label or "—" in label or "font color" in label.lower():
                    continue
                src = self._resolve_cell_to_service(cells, edge.get("source", ""), level_svc_names)
                tgt = self._resolve_cell_to_service(cells, edge.get("target", ""), level_svc_names)
                if src and tgt:
                    diagram_sigs.add((src, tgt))

            missing = expected_flow_sigs - diagram_sigs
            page_label = level
            if level == "L3" and self.l3_pages and page_idx - 2 < len(self.l3_pages):
                page_label = self.l3_pages[page_idx - 2].get("name", level)

            sev = "WARNING" if level == "L3" else "ERROR"
            self.results.append(CheckResult(
                cat, "all_expected_edges_present", page_label,
                len(missing) == 0,
                f"{page_label} missing {len(missing)} edge(s) from knowledge.json: "
                + "; ".join(f"{m[0]}→{m[1]}" for m in list(missing)[:3]),
                "Add diagram edges for all flows defined at this level",
                severity=sev
            ))

    # ── Flows Edge Label Validation ────────────────────────────────────

    def check_flows_edge_labels(self):
        """Validate edge labels match flows[].hops[] label format.

        L1: label only (e.g. "runs scenario")
        L2/L3: label + detail (e.g. "runs scenario (HTTP)")
        """
        if not self.flows:
            return

        cat = "flows_edge_labels"

        for page_idx in range(min(len(self.diagrams), self.expected_page_count)):
            level = self.page_level(page_idx)
            cells = get_cells(self.diagrams[page_idx])
            edges = [c for c in cells if is_edge(c)]

            level_hops = [
                hop for hop in self._flows_hops
                if level in hop.get("levels", [])
            ]
            if not level_hops:
                continue

            level_svc_names = {}
            for svc in self.services:
                if level in svc.get("levels", []):
                    level_svc_names[svc["name"].lower()] = svc["id"]
                    level_svc_names[svc["id"].lower()] = svc["id"]
            if level == "L3" and self.l3_pages and page_idx - 2 < len(self.l3_pages):
                l3_page = self.l3_pages[page_idx - 2]
                for sid in l3_page.get("collapsed_service_ids", []):
                    svc = self.svc_by_id.get(sid)
                    if svc:
                        level_svc_names[svc["name"].lower()] = svc["id"]
                        level_svc_names[svc["id"].lower()] = svc["id"]

            # Build hop lookup: (from_id, to_id) → hop
            hop_lookup = {}
            for hop in level_hops:
                key = (hop["from"], hop["to"])
                hop_lookup[key] = hop

            page_label = level
            if level == "L3" and self.l3_pages and 0 <= page_idx - 2 < len(self.l3_pages):
                page_label = self.l3_pages[page_idx - 2].get("name", level)

            mismatched = []
            for edge in edges:
                label = cell_text(edge).strip()
                if not label or len(label) < 3:
                    continue
                if "──" in label or "—" in label or "font color" in label.lower():
                    continue

                source_id = edge.get("source", "")
                target_id = edge.get("target", "")
                source_svc = self._resolve_cell_to_service(cells, source_id, level_svc_names)
                target_svc = self._resolve_cell_to_service(cells, target_id, level_svc_names)

                if not source_svc or not target_svc:
                    continue

                hop = hop_lookup.get((source_svc, target_svc))
                if not hop:
                    continue

                hop_label = hop.get("label", "")
                hop_detail = hop.get("detail", "")

                if level == "L1":
                    expected = hop_label
                else:
                    expected = f"{hop_label} {hop_detail}".strip() if hop_detail else hop_label

                if expected and expected.lower() not in label.lower():
                    if hop_label.lower() not in label.lower():
                        mismatched.append(
                            f"'{label[:40]}' should contain '{expected[:40]}'"
                        )

            if mismatched:
                self.results.append(CheckResult(
                    cat, "edge_labels_match_flows", page_label,
                    False,
                    f"{page_label} has {len(mismatched)} edge(s) with labels not matching flows[].hops[]: "
                    + "; ".join(mismatched[:3]),
                    "Update edge labels to use hop.label (L1) or hop.label + hop.detail (L2/L3)",
                    severity="WARNING"
                ))
            else:
                self.results.append(CheckResult(
                    cat, "edge_labels_match_flows", page_label,
                    True, ""
                ))

    # ── Layout Quality ──────────────────────────────────────────────────

    def check_layout_quality(self):
        """Check visual layout quality: spacing, overlaps, flow direction,
        canvas utilization, edge detours, and margins."""
        cat = "layout_quality"

        # Spacing thresholds per level
        spacing_thresholds = {"L1": 60, "L2": 40, "L3": 30}

        for page_idx in range(min(len(self.diagrams), self.expected_page_count)):
            level = self.page_level(page_idx)
            cells = get_cells(self.diagrams[page_idx])
            vertices = [c for c in cells if is_vertex(c)]
            edges = [c for c in cells if is_edge(c)]

            # Build set of vertex cells with geometry, filtering out
            # text-only cells, edge labels, and tiny elements
            layout_cells = []
            cell_ids = set()
            for c in vertices:
                g = cell_geometry(c)
                if not g or g["width"] <= 10:
                    continue
                style = cell_style(c)
                if "text;" in style or style.startswith("text;"):
                    continue
                # Skip cells with width < 20 (likely labels/decorations)
                if g["width"] < 20:
                    continue
                layout_cells.append(c)
                cell_ids.add(c.get("id", ""))

            # Build parent-child set for nesting detection
            parent_child_pairs = set()
            for c in layout_cells:
                pid = c.get("parent", "")
                if pid in cell_ids:
                    parent_child_pairs.add((pid, c.get("id", "")))
                    parent_child_pairs.add((c.get("id", ""), pid))

            # Build set of K8s icon IDs (deploy/svc icons intentionally paired close together)
            k8s_icon_ids = set()
            for c in layout_cells:
                style = cell_style(c)
                if 'mxgraph.kubernetes' in style:
                    k8s_icon_ids.add(c.get('id', ''))

            # Build set of container IDs (cells that act as parents to other layout cells)
            container_ids = set()
            for c in layout_cells:
                cid = c.get('id', '')
                if any(lc.get('parent', '') == cid for lc in layout_cells):
                    container_ids.add(cid)

            # Build sibling skip set: pairs of cells that share the same parent
            # where at least one is a small K8s icon (intentionally close pairs)
            def _visually_contains(outer, inner):
                """True if outer's bounding box fully contains inner's."""
                go = cell_geometry(outer)
                gi_ = cell_geometry(inner)
                if not go or not gi_:
                    return False
                return (go['x'] <= gi_['x'] and go['y'] <= gi_['y'] and
                        go['x'] + go['width'] >= gi_['x'] + gi_['width'] and
                        go['y'] + go['height'] >= gi_['y'] + gi_['height'])

            # Set of cells that are inside a container
            contained_ids = set()
            for c in layout_cells:
                if c.get('parent', '') in container_ids:
                    contained_ids.add(c.get('id', ''))

            # Set of icon IDs (K8s or AWS service icons, width ≤ 48)
            icon_ids = set()
            for c in layout_cells:
                style = cell_style(c)
                g = cell_geometry(c)
                if g and g['width'] <= 48:
                    if 'mxgraph.kubernetes' in style or 'mxgraph.aws4' in style:
                        icon_ids.add(c.get('id', ''))

            def _should_skip_pair(i_idx, j_idx):
                id_i_ = layout_cells[i_idx].get('id', '')
                id_j_ = layout_cells[j_idx].get('id', '')
                if (id_i_, id_j_) in parent_child_pairs:
                    return True
                pi = layout_cells[i_idx].get('parent', '')
                pj = layout_cells[j_idx].get('parent', '')
                # Skip same-parent siblings (deploy+svc, icon+card, component breakdowns)
                if pi == pj and pi in cell_ids:
                    return True
                # Skip icon+card pairs that share any parent (including root)
                if pi == pj and (id_i_ in icon_ids or id_j_ in icon_ids):
                    return True
                # Skip visual containment (boundary boxes containing inner cells)
                if (_visually_contains(layout_cells[i_idx], layout_cells[j_idx]) or
                        _visually_contains(layout_cells[j_idx], layout_cells[i_idx])):
                    return True
                # Skip pairs involving container cells
                if id_i_ in container_ids or id_j_ in container_ids:
                    return True
                # Skip pairs in different containers
                if pi != pj and pi in container_ids and pj in container_ids:
                    return True
                # Skip cross-depth: root-level cell vs containerized cell
                i_contained = id_i_ in contained_ids
                j_contained = id_j_ in contained_ids
                if i_contained != j_contained:
                    return True
                return False

            # Page label for reporting
            page_label = level
            if level == "L3" and self.l3_pages and 0 <= page_idx - 2 < len(self.l3_pages):
                page_label = self.l3_pages[page_idx - 2].get("name", level)

            # ── Check 1: Minimum spacing ──────────────────────────────
            threshold = spacing_thresholds.get(level, 40)
            spacing_violations = 0
            for i in range(len(layout_cells)):
                gi = cell_geometry(layout_cells[i])
                id_i = layout_cells[i].get("id", "")
                for j in range(i + 1, len(layout_cells)):
                    id_j = layout_cells[j].get("id", "")
                    if _should_skip_pair(i, j):
                        continue
                    gj = cell_geometry(layout_cells[j])
                    # Compute gap between bounding boxes
                    gap_x = max(0, max(gi["x"], gj["x"]) - min(gi["x"] + gi["width"], gj["x"] + gj["width"]))
                    gap_y = max(0, max(gi["y"], gj["y"]) - min(gi["y"] + gi["height"], gj["y"] + gj["height"]))
                    gap = max(gap_x, gap_y) if gap_x > 0 or gap_y > 0 else 0
                    if gap < threshold:
                        spacing_violations += 1

            if layout_cells:
                self.results.append(CheckResult(
                    cat, "minimum_spacing", page_label,
                    spacing_violations == 0,
                    f"{page_label} has {spacing_violations} cell pair(s) closer than {threshold}px",
                    f"Increase spacing between non-nested cells to at least {threshold}px",
                    severity="WARNING"
                ))

            # ── Check 2: Overlap detection ────────────────────────────
            overlap_count = 0
            for i in range(len(layout_cells)):
                gi = cell_geometry(layout_cells[i])
                id_i = layout_cells[i].get("id", "")
                for j in range(i + 1, len(layout_cells)):
                    id_j = layout_cells[j].get("id", "")
                    if _should_skip_pair(i, j):
                        continue
                    gj = cell_geometry(layout_cells[j])
                    # Rectangle intersection test
                    ax1, ay1 = gi["x"], gi["y"]
                    ax2, ay2 = gi["x"] + gi["width"], gi["y"] + gi["height"]
                    bx1, by1 = gj["x"], gj["y"]
                    bx2, by2 = gj["x"] + gj["width"], gj["y"] + gj["height"]
                    if not (ax2 < bx1 or bx2 < ax1 or ay2 < by1 or by2 < ay1):
                        overlap_count += 1

            if layout_cells:
                self.results.append(CheckResult(
                    cat, "no_overlaps", page_label,
                    overlap_count == 0,
                    f"{page_label} has {overlap_count} overlapping non-nested cell pair(s)",
                    "Rearrange cells to eliminate overlaps",
                    severity="WARNING"
                ))

            # ── Check 3: Flow direction (L1 and L2 only) ─────────────
            if level in ("L1", "L2"):
                forward = 0
                backward = 0
                cell_map = {c.get("id", ""): c for c in cells}
                for edge in edges:
                    src_id = edge.get("source", "")
                    tgt_id = edge.get("target", "")
                    src_cell = cell_map.get(src_id)
                    tgt_cell = cell_map.get(tgt_id)
                    if src_cell is None or tgt_cell is None:
                        continue
                    sg = cell_geometry(src_cell)
                    tg = cell_geometry(tgt_cell)
                    if not sg or not tg:
                        continue
                    if sg["x"] < tg["x"]:
                        forward += 1
                    else:
                        backward += 1

                total_directed = forward + backward
                if total_directed > 0:
                    fwd_ratio = forward / total_directed
                    self.results.append(CheckResult(
                        cat, "flow_direction_ltr", page_label,
                        fwd_ratio >= 0.6,
                        f"{page_label} has {forward}/{total_directed} forward (left-to-right) edges "
                        f"({fwd_ratio:.0%}), expected >= 60%",
                        "Rearrange nodes so the primary flow goes left-to-right",
                        severity="WARNING"
                    ))

            # ── Check 4: Canvas utilization ───────────────────────────
            if layout_cells:
                geoms = [cell_geometry(c) for c in layout_cells]
                min_x = min(g["x"] for g in geoms)
                max_x = max(g["x"] + g["width"] for g in geoms)
                min_y = min(g["y"] for g in geoms)
                max_y = max(g["y"] + g["height"] for g in geoms)
                content_area = (max_x - min_x) * (max_y - min_y)

                # Read canvas size from mxGraphModel
                model = self.diagrams[page_idx].find("mxGraphModel")
                dx = float(model.get("dx", 1400)) if model is not None else 1400
                dy = float(model.get("dy", 800)) if model is not None else 800
                canvas_area = dx * dy

                if canvas_area > 0:
                    utilization = content_area / canvas_area
                    self.results.append(CheckResult(
                        cat, "canvas_utilization", page_label,
                        0.15 <= utilization <= 0.95,
                        f"{page_label} canvas utilization is {utilization:.2f} "
                        f"({'too sparse' if utilization < 0.15 else 'too packed'})",
                        "Resize canvas or redistribute cells for 15-95% utilization",
                        severity="WARNING"
                    ))

            # ── Check 5: Edge route detour ────────────────────────────
            cell_map = {c.get("id", ""): c for c in cells}
            detour_count = 0
            for edge in edges:
                geom_el = edge.find("mxGeometry")
                if geom_el is None:
                    continue
                # Find Array element with mxPoint waypoints
                array_el = geom_el.find("Array")
                if array_el is None:
                    continue
                points = array_el.findall("mxPoint")
                if not points:
                    continue

                # Resolve source and target centers
                src_id = edge.get("source", "")
                tgt_id = edge.get("target", "")
                src_cell = cell_map.get(src_id)
                tgt_cell = cell_map.get(tgt_id)
                if src_cell is None or tgt_cell is None:
                    continue
                sg = cell_geometry(src_cell)
                tg = cell_geometry(tgt_cell)
                if not sg or not tg:
                    continue

                src_cx = sg["x"] + sg["width"] / 2
                src_cy = sg["y"] + sg["height"] / 2
                tgt_cx = tg["x"] + tg["width"] / 2
                tgt_cy = tg["y"] + tg["height"] / 2

                # Build waypoint list: source center -> waypoints -> target center
                wp = [(src_cx, src_cy)]
                for pt in points:
                    try:
                        px = float(pt.get("x", 0))
                        py = float(pt.get("y", 0))
                        wp.append((px, py))
                    except (TypeError, ValueError):
                        pass
                wp.append((tgt_cx, tgt_cy))

                # Total Manhattan distance through waypoints
                total_dist = sum(
                    abs(wp[k + 1][0] - wp[k][0]) + abs(wp[k + 1][1] - wp[k][1])
                    for k in range(len(wp) - 1)
                )
                # Straight-line Manhattan distance
                straight_dist = abs(tgt_cx - src_cx) + abs(tgt_cy - src_cy)

                if straight_dist > 0 and total_dist > straight_dist * 3.5:
                    detour_count += 1

            if edges:
                self.results.append(CheckResult(
                    cat, "edge_route_detour", page_label,
                    detour_count == 0,
                    f"{page_label} has {detour_count} edge(s) with excessive detours (>3.5x straight distance)",
                    "Simplify edge routes — remove unnecessary waypoints or rearrange nodes",
                    severity="WARNING"
                ))

            # ── Check 6: Margin check ─────────────────────────────────
            if layout_cells:
                geoms = [cell_geometry(c) for c in layout_cells]
                min_vertex_y = min(g["y"] for g in geoms)

                # Canvas height from mxGraphModel pageHeight or default
                model = self.diagrams[page_idx].find("mxGraphModel")
                page_height = 1600
                if model is not None:
                    try:
                        page_height = float(model.get("pageHeight", 1600))
                    except (TypeError, ValueError):
                        pass

                max_vertex_bottom = max(g["y"] + g["height"] for g in geoms)

                top_ok = min_vertex_y >= 10
                bottom_ok = max_vertex_bottom <= page_height

                self.results.append(CheckResult(
                    cat, "margin_top", page_label,
                    top_ok,
                    f"{page_label} has cells starting at y={min_vertex_y:.0f}, need y >= 10 for title room",
                    "Move top-most cells down to leave at least 10px margin for title",
                    severity="WARNING"
                ))
                self.results.append(CheckResult(
                    cat, "margin_bottom", page_label,
                    bottom_ok,
                    f"{page_label} has cells extending to y={max_vertex_bottom:.0f}, "
                    f"beyond page height {page_height:.0f}",
                    "Move or resize cells to fit within the page height",
                    severity="WARNING"
                ))

    # ── Edge Routing Quality (L2) ──────────────────────────────────────

    def check_edge_routing_quality(self):
        """L2-specific: validate edges respect zone corridor and max-y rules."""
        cat = "edge_routing_quality"
        if len(self.diagrams) < 2:
            return

        er = self.library.get("layout_formulas", {}).get("L2", {}).get("edge_routing", {})
        max_edge_y = er.get("max_edge_y", 1200)
        corridor_x = er.get("cross_zone_corridor_x", 1390)

        cells = get_cells(self.diagrams[1])
        edges = [c for c in cells if is_edge(c)]
        vertices = {c.get("id"): c for c in cells if not is_edge(c)}

        bottom_violations = 0
        cross_zone_count = 0
        for edge in edges:
            src_cell = vertices.get(edge.get("source", ""))
            tgt_cell = vertices.get(edge.get("target", ""))
            if not src_cell or not tgt_cell:
                continue
            sg = cell_geometry(src_cell)
            tg = cell_geometry(tgt_cell)
            if not sg or not tg:
                continue
            src_cy = sg["y"] + sg["height"] / 2
            tgt_cy = tg["y"] + tg["height"] / 2
            src_cx = sg["x"] + sg["width"] / 2
            tgt_cx = tg["x"] + tg["width"] / 2

            if src_cy > max_edge_y or tgt_cy > max_edge_y:
                bottom_violations += 1

            if (src_cx < corridor_x and tgt_cx > corridor_x) or \
               (src_cx > corridor_x and tgt_cx < corridor_x):
                cross_zone_count += 1

        self.results.append(CheckResult(
            cat, "no_bottom_highway", "L2",
            bottom_violations == 0,
            f"L2 has {bottom_violations} edge(s) with endpoint below y={max_edge_y}",
            "Move edge endpoints above the max_edge_y threshold",
            severity="WARNING"
        ))
        self.results.append(CheckResult(
            cat, "cross_zone_corridor", "L2",
            cross_zone_count > 0,
            "L2 has no cross-zone edges (K8s ↔ AWS) — expected some via corridor",
            f"Ensure K8s→AWS edges cross the corridor at x≈{corridor_x}",
            severity="WARNING"
        ))

    # ── Observed Data Visualization ─────────────────────────────────────

    def check_observed_visualization(self):
        """Check that observed runtime data is reflected in diagrams (WARNING severity)."""
        cat = "observed_viz"

        # Only check if knowledge.json has observed data
        has_observed_services = any(
            "observed" in s for s in self.services
        )
        has_observed_flows = any(
            "observed" in f for f in self.data_flows
        )
        has_alarms = bool(self.knowledge.get("alarms"))
        has_shadow_flows = bool(self.knowledge.get("observed_flows"))

        if not (has_observed_services or has_observed_flows or has_alarms or has_shadow_flows):
            return  # no observed data → nothing to check

        # Check L2 page (index 1) for observed annotations
        if len(self.diagrams) >= 2:
            l2_cells = get_cells(self.diagrams[1])
            l2_edges = [c for c in l2_cells if is_edge(c)]
            l2_vertices = [c for c in l2_cells if is_vertex(c)]

            # Check: edges with observed data should have varied strokeWidth
            if has_observed_flows:
                varied_edges = [e for e in l2_edges
                                if "strokeWidth" in cell_style(e)
                                and "strokeWidth=1;" not in cell_style(e)]
                self.results.append(CheckResult(
                    cat, "edge_thickness_varies", "L2 page",
                    len(varied_edges) > 0,
                    "L2 edges should have varied strokeWidth based on observed call_count",
                    "Set strokeWidth=1-4 on edges based on log10(call_count)",
                    severity="WARNING"
                ))

            # Check: unhealthy/degraded services should have colored borders
            if has_observed_services:
                unhealthy = [s for s in self.services
                             if s.get("observed", {}).get("health") in ("unhealthy", "degraded")]
                if unhealthy:
                    colored_borders = [v for v in l2_vertices
                                       if ("strokeColor=#D32F2F" in cell_style(v)
                                           or "strokeColor=#FF9800" in cell_style(v))]
                    self.results.append(CheckResult(
                        cat, "unhealthy_nodes_highlighted", "L2 page",
                        len(colored_borders) > 0,
                        f"{len(unhealthy)} unhealthy/degraded services should have colored borders",
                        "Set strokeColor=#D32F2F (unhealthy) or #FF9800 (degraded) on affected nodes",
                        severity="WARNING"
                    ))

            # Check: ALARM state alarms should show alarm badges
            if has_alarms:
                alarm_active = [a for a in self.knowledge.get("alarms", [])
                                if a.get("state") == "ALARM"]
                if alarm_active:
                    alarm_indicators = [v for v in l2_vertices
                                        if "alarm" in cell_text(v).lower()
                                        and ("strokeColor=#D32F2F" in cell_style(v)
                                             or "fillColor=#D32F2F" in cell_style(v)
                                             or "fontColor=#D32F2F" in cell_style(v))]
                    self.results.append(CheckResult(
                        cat, "alarm_badges_shown", "L2 page",
                        len(alarm_indicators) > 0,
                        f"{len(alarm_active)} active alarms should have visual indicators",
                        "Add red alarm badge/icon overlay on affected service nodes",
                        severity="WARNING"
                    ))

            # Check: shadow flows should appear as dashed lines
            if has_shadow_flows:
                dashed_edges = [e for e in l2_edges if "dashed=1" in cell_style(e)]
                shadow_count = len(self.knowledge.get("observed_flows", []))
                self.results.append(CheckResult(
                    cat, "shadow_flows_shown", "L2 page",
                    len(dashed_edges) >= shadow_count,
                    f"{shadow_count} shadow flows should appear as dashed lines on L2",
                    "Add dashed edges for each observed_flows entry with 'discovered' label",
                    severity="WARNING"
                ))

        # Check L3 pages for metric annotations in detail cards
        if has_observed_services and self.l3_pages:
            for i, l3_page in enumerate(self.l3_pages):
                page_idx = 2 + i
                if page_idx >= len(self.diagrams):
                    continue

                cells = get_cells(self.diagrams[page_idx])
                vertices = [c for c in cells if is_vertex(c)]

                # Check if any detail cards mention latency/throughput/error metrics
                focal_observed = [
                    s for s in self.services
                    if s["id"] in set(l3_page.get("focal_service_ids", []))
                    and "observed" in s
                ]
                if focal_observed:
                    metric_keywords = ["latency", "throughput", "req/s", "error", "ms"]
                    cards_with_metrics = [
                        v for v in vertices
                        if sum(1 for kw in metric_keywords if kw in cell_text(v)) >= 2
                    ]
                    self.results.append(CheckResult(
                        cat, "l3_metric_annotations",
                        l3_page.get("name", f"L3 page {i+1}"),
                        len(cards_with_metrics) > 0,
                        f"{len(focal_observed)} focal services have observed data but detail cards lack metric annotations",
                        "Add latency/throughput/error rows to L3 detail cards for services with observed data",
                        severity="WARNING"
                    ))

    # ── Staleness ────────────────────────────────────────────────────────

    def check_staleness(self):
        cat = "staleness"
        source_mode = self.meta.get("source_mode", "")
        stored_hash = self.meta.get("source_files_hash")

        if source_mode != "scan" or not stored_hash:
            return  # staleness only applies to scan mode

        current_hash = compute_source_hash()
        if not current_hash:
            return  # could not compute hash (no infra files found)

        self.results.append(CheckResult(
            cat, "knowledge_fresh", "knowledge vs source files",
            current_hash == stored_hash,
            f"Knowledge hash mismatch (stored: {stored_hash[:12]}..., current: {current_hash[:12]}...)",
            "Run /architecture knowledge collect to update",
            severity="WARNING"
        ))


# ─── Hash Computation ────────────────────────────────────────────────────────

INFRA_PATTERNS = [
    "infrastructure", "terraform", "cloudformation",
    "kubernetes", "k8s", "helm", "cdk",
]
INFRA_EXTENSIONS = {".yml", ".yaml", ".json", ".tf", ".tfvars", ".ts"}


def compute_source_hash() -> str:
    """Compute SHA-256 of infrastructure source files (generic, not project-specific)."""
    project_root = find_project_root()
    h = hashlib.sha256()
    found_files = []

    for pattern_dir in INFRA_PATTERNS:
        infra_dir = project_root / pattern_dir
        if infra_dir.is_dir():
            for p in sorted(infra_dir.rglob("*")):
                if p.suffix in INFRA_EXTENSIONS and p.is_file():
                    found_files.append(p)

    # Also check root-level infra files
    for p in sorted(project_root.glob("*")):
        if p.is_file() and p.name in (
            "docker-compose.yml", "docker-compose.yaml",
            "serverless.yml", "cdk.json"
        ):
            found_files.append(p)

    if not found_files:
        return ""

    for f in found_files:
        with open(f, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


def find_project_root() -> Path:
    """Walk up from CWD to find project root (has .git or known infra dirs)."""
    p = Path.cwd()
    while p != p.parent:
        if (p / ".git").exists():
            return p
        for pattern in INFRA_PATTERNS:
            if (p / pattern).is_dir():
                return p
        p = p.parent
    return Path.cwd()


# ─── Report ──────────────────────────────────────────────────────────────────

def build_report(results: list) -> dict:
    checks = [r.to_dict() for r in results]
    failures = [r.to_dict() for r in results if not r.passed]
    passed = sum(1 for r in results if r.passed)

    return {
        "status": "PASS" if not failures else "FAIL",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(failures),
        },
        "checks": checks,
        "failures": failures,
    }


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Verify draw.io architecture diagrams")
    parser.add_argument("--drawio", required=True, help="Path to .drawio file")
    parser.add_argument("--knowledge", required=True, help="Path to knowledge.json")
    parser.add_argument("--icons", default=None, help="Path to icon_registry.json")
    parser.add_argument("--library", default=None, help="Path to component_library.json")
    parser.add_argument("--output", default=None, help="Path to write verification_report.json")
    args = parser.parse_args()

    # Load knowledge
    with open(args.knowledge) as f:
        knowledge = json.load(f)

    # Load icon registry
    icons = {}
    if args.icons and os.path.exists(args.icons):
        with open(args.icons) as f:
            icons = json.load(f)

    # Load component library
    library = {}
    if args.library and os.path.exists(args.library):
        with open(args.library) as f:
            library = json.load(f)

    # Run verification
    verifier = Verifier(args.drawio, knowledge, icons, library)
    results = verifier.run_all()
    report = build_report(results)

    # Output
    report_json = json.dumps(report, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w") as f:
            f.write(report_json)
        print(f"Report written to {args.output}", file=sys.stderr)

    # Summary to stderr
    s = report["summary"]
    status = report["status"]
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  Verification: {status}  ({s['passed']}/{s['total']} passed, {s['failed']} failed)", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    if report["failures"]:
        print("\nFailures:", file=sys.stderr)
        for f_item in report["failures"]:
            sev = f_item.get("severity", "ERROR")
            print(f"  [{sev}] {f_item['category']}/{f_item['check']}: {f_item['target']}", file=sys.stderr)
            print(f"         {f_item['message']}", file=sys.stderr)
            if f_item.get("suggestion"):
                print(f"         -> {f_item['suggestion']}", file=sys.stderr)

    print(report_json)
    sys.exit(0 if status == "PASS" else 1)


if __name__ == "__main__":
    main()
