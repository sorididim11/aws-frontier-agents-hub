#!/usr/bin/env python3
"""Architecture visualization quality engine.

Platform-independent, reusable quality assessment for any analysis result.
Takes raw analysis data (from DynamoDB, fixture, or API) and produces
a quality score with actionable diagnostics.

Usage:
  # CLI — run against a fixture
  python arch_quality.py fixtures/arch_topdown_20260429-023032.json

  # API — called from overview_app.py
  from arch_quality import assess_quality
  report = assess_quality(analysis_data)
  # report = {"score": 96, "suites": [...], "warnings": [...]}

  # With expected topology (optional)
  report = assess_quality(analysis_data, expected="fixtures/expected_dockercoins.json")
"""
import json
import os
import re
import sys
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# Data preparation — mirrors overview_app enrichment pipeline
# ═══════════════════════════════════════════════════════════════

def _remove_phantom_edges(nodes, edges):
    """Remove app→alarm shortcut edges that skip CloudWatch."""
    nm = {n["name"]: n for n in nodes}
    return [e for e in edges
            if not (nm.get(e.get("source", ""), {}).get("namespace", "")
                    not in ("managed", "external", "platform")
                    and "Alarm" in nm.get(e.get("target", ""), {}).get("kind", "")
                    and nm.get(e.get("source", ""), {}).get("group", "")
                    != nm.get(e.get("target", ""), {}).get("group", ""))]


def _enrich_known_edges(nodes, edges):
    """Enrich edges with known relationships from app code.

    This is a generic enrichment: if a node with service_type 'app'
    has an edge to a DevOps Agent API node with incomplete description,
    enrich it. Extend this for other known patterns.
    """
    nm = {n["name"]: n for n in nodes}
    agent_nodes = [n["name"] for n in nodes
                   if "agent" in n.get("kind", "").lower()
                   and n.get("namespace") == "external"]

    for agent_name in agent_nodes:
        for e in edges:
            if e.get("target") == agent_name:
                desc = e.get("description", "").lower()
                if "send_message" not in desc and "대화" not in desc and "인터뷰" not in desc:
                    src = nm.get(e.get("source", ""), {})
                    if src.get("group") in ("ScenarioDashboard", "Simulator"):
                        e["description"] = (
                            "Agent API 다중 호출: 아키텍처 인터뷰 대화 (send_message), "
                            "시나리오 리뷰 채팅, 조사 저널 조회, 실행 목록, Space 관리 (SigV4 인증)"
                        )
    return edges


def _is_infra_noise_l1(n):
    """L1 filter: only removes groupless noise."""
    if n.get("group"):
        return False
    name = (n.get("name") or "").lower()
    if name in ("browser", "client"):
        return True
    if n.get("namespace") in ("managed", "external"):
        return True
    if n.get("kind") == "ExternalService":
        return True
    return False


def prepare_data(raw_data):
    """Load and prepare analysis data for quality assessment."""
    data = raw_data.get("result", raw_data) if "result" in raw_data else raw_data
    graph = data.get("graph", {})
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    edges = _remove_phantom_edges(nodes, edges)
    edges = _enrich_known_edges(nodes, edges)

    return nodes, edges


# ═══════════════════════════════════════════════════════════════
# Quality checks — each returns (pass: bool, message: str)
# ═══════════════════════════════════════════════════════════════

def _check_all_nodes_have_group(nodes, edges):
    orphans = [n["name"] for n in nodes if not n.get("group")]
    return (orphans == [], f"Orphan nodes: {orphans}" if orphans else "All nodes have group")


def _check_namespace_values(nodes, edges):
    allowed = {"", "managed", "external", "platform"}
    bad = [(n["name"], n.get("namespace")) for n in nodes
           if n.get("namespace", "") not in allowed]
    return (bad == [], f"Unknown namespace: {bad}" if bad else "All namespace values valid")


def _check_service_type_values(nodes, edges):
    allowed = {"app", "cache", "db", "gateway", "queue", "worker",
               "managed", "platform", "observe", "ops"}
    bad = [(n["name"], n.get("service_type")) for n in nodes
           if n.get("service_type", "app") not in allowed]
    return (bad == [], f"Unknown service_type: {bad}" if bad else "All service_type values valid")


def _check_l1_no_group_drops(nodes, edges):
    data_groups = {n.get("group") for n in nodes if n.get("group")}
    l1_groups = set()
    for n in nodes:
        if not _is_infra_noise_l1(n):
            g = n.get("group") or "기타"
            l1_groups.add(g)
    dropped = data_groups - l1_groups
    return (dropped == set(),
            f"Groups dropped in L1: {dropped}" if dropped else "All groups visible in L1")


def _check_no_bidirectional_edges(nodes, edges):
    pairs = set()
    dupes = []
    for e in edges:
        key = (e.get("source", ""), e.get("target", ""))
        rev = (key[1], key[0])
        if rev in pairs:
            dupes.append(f"{key[0]}↔{key[1]}")
        pairs.add(key)
    return (dupes == [],
            f"Bidirectional edges: {dupes}" if dupes else "No bidirectional edges")


def _check_all_edge_endpoints_exist(nodes, edges):
    nm = {n["name"] for n in nodes}
    missing = [(e.get("source"), e.get("target")) for e in edges
               if e.get("source") not in nm or e.get("target") not in nm]
    return (missing == [],
            f"Dangling edges: {missing}" if missing else "All edge endpoints exist")


def _check_no_phantom_edges(nodes, edges):
    nm = {n["name"]: n for n in nodes}
    phantoms = []
    for e in edges:
        src = nm.get(e.get("source", ""), {})
        tgt = nm.get(e.get("target", ""), {})
        if (src.get("namespace", "") not in ("managed", "external", "platform")
                and "Alarm" in tgt.get("kind", "")
                and src.get("group", "") != tgt.get("group", "")):
            phantoms.append(f"{e.get('source')}→{e.get('target')}")
    return (phantoms == [],
            f"Phantom edges: {phantoms}" if phantoms else "No phantom edges")


def _check_cross_group_connectivity(nodes, edges):
    """Every group should have at least one cross-group edge."""
    groups = {n.get("group") for n in nodes if n.get("group")}
    node_group = {n["name"]: n.get("group", "") for n in nodes}
    connected_groups = set()
    for e in edges:
        sg = node_group.get(e.get("source", ""))
        tg = node_group.get(e.get("target", ""))
        if sg and tg and sg != tg:
            connected_groups.add(sg)
            connected_groups.add(tg)
    isolated = groups - connected_groups
    return (isolated == set(),
            f"Isolated groups (no cross-group edges): {isolated}" if isolated
            else "All groups have cross-group connectivity")


# ── Expected topology checks (optional, needs expected.json) ──

def _check_expected_nodes(nodes, edges, expected):
    """Check expected nodes exist."""
    if not expected:
        return (True, "No expected topology provided")
    exp_groups = expected.get("expected_groups", {})
    missing = {}
    actual_names = {n["name"] for n in nodes}
    for group, members in exp_groups.items():
        for m in members:
            matches = [a for a in actual_names if m.lower() in a.lower()]
            if not matches:
                missing.setdefault(group, []).append(m)
    if missing:
        return (False, f"Expected nodes missing: {missing}")
    return (True, "All expected nodes found")


def _check_pipeline_chain(nodes, edges):
    """Verify alarm pipeline chain if AlarmPipeline group exists."""
    groups = {n.get("group") for n in nodes}
    if "AlarmPipeline" not in groups:
        return (True, "No AlarmPipeline group — skipped")

    checks = {
        "alarm→sns": lambda e: ("alarm" in e.get("source", "").lower()
                                and "sns" in e.get("target", "").lower()),
        "sns→lambda": lambda e: ("sns" in e.get("source", "").lower()
                                 and "lambda" in e.get("target", "").lower()),
        "lambda→agent": lambda e: ("lambda" in e.get("source", "").lower()
                                   and "agent" in e.get("target", "").lower()),
        "eventbridge→lambda": lambda e: ("eventbridge" in e.get("source", "").lower()
                                         and "lambda" in e.get("target", "").lower()),
    }
    missing = []
    for name, predicate in checks.items():
        if not any(predicate(e) for e in edges):
            missing.append(name)
    return (missing == [],
            f"Pipeline chain missing: {missing}" if missing
            else "AlarmPipeline chain complete")


# ═══════════════════════════════════════════════════════════════
# Quality engine
# ═══════════════════════════════════════════════════════════════

CHECKS = [
    ("Classification", "all_nodes_have_group", _check_all_nodes_have_group),
    ("Classification", "namespace_values", _check_namespace_values),
    ("Classification", "service_type_values", _check_service_type_values),
    ("L1 Completeness", "no_group_drops", _check_l1_no_group_drops),
    ("Edge Integrity", "no_bidirectional", _check_no_bidirectional_edges),
    ("Edge Integrity", "endpoints_exist", _check_all_edge_endpoints_exist),
    ("Edge Integrity", "no_phantoms", _check_no_phantom_edges),
    ("Connectivity", "cross_group", _check_cross_group_connectivity),
    ("Pipeline", "alarm_chain", _check_pipeline_chain),
]


def assess_quality(raw_data: dict, expected: Optional[dict] = None) -> dict:
    """Run all quality checks and return structured report.

    Args:
        raw_data: Analysis result (from DynamoDB or fixture).
        expected: Optional expected topology (from expected_*.json).

    Returns:
        {
            "score": int (0-100),
            "total": int,
            "passed": int,
            "suites": {suite_name: {"passed": int, "total": int, "checks": [...]}},
            "warnings": [str],
        }
    """
    nodes, edges = prepare_data(raw_data)

    suites = {}
    total = 0
    passed = 0
    warnings = []

    for suite, name, fn in CHECKS:
        total += 1
        ok, msg = fn(nodes, edges)
        if ok:
            passed += 1

        if suite not in suites:
            suites[suite] = {"passed": 0, "total": 0, "checks": []}
        suites[suite]["total"] += 1
        if ok:
            suites[suite]["passed"] += 1
        suites[suite]["checks"].append({"name": name, "passed": ok, "message": msg})

    if expected:
        total += 1
        ok, msg = _check_expected_nodes(nodes, edges, expected)
        if ok:
            passed += 1
        suite = "Expected Topology"
        suites[suite] = {"passed": 1 if ok else 0, "total": 1,
                         "checks": [{"name": "expected_nodes", "passed": ok, "message": msg}]}

    # Collect warnings
    redundant = sum(1 for n in nodes
                    if n.get("namespace") == "managed" and n.get("service_type") == "managed")
    if redundant:
        warnings.append(f"{redundant} nodes have redundant namespace=managed + service_type=managed")
    ambiguous = sum(1 for n in nodes if n.get("service_type") in ("managed", "platform")
                    and n.get("namespace") == n.get("service_type"))
    if ambiguous:
        warnings.append(f"{ambiguous} nodes have service_type matching namespace (ambiguous role)")

    score = int(100 * passed / total) if total else 0

    return {
        "score": score,
        "total": total,
        "passed": passed,
        "suites": suites,
        "warnings": warnings,
    }


def print_report(report: dict):
    """Pretty-print quality report to stdout."""
    print("\n" + "=" * 70)
    print("ARCHITECTURE VISUALIZATION QUALITY REPORT")
    print("=" * 70)

    for suite_name, suite in report["suites"].items():
        sp, st = suite["passed"], suite["total"]
        icon = "PASS" if sp == st else "FAIL"
        print(f"\n  [{icon}] {suite_name}: {sp}/{st}")
        for check in suite["checks"]:
            status = "ok" if check["passed"] else "FAIL"
            print(f"        {status}: {check['name']} — {check['message'][:100]}")

    if report["warnings"]:
        print(f"\n  Warnings:")
        for w in report["warnings"]:
            print(f"    - {w}")

    print(f"\n{'=' * 70}")
    print(f"  TOTAL: {report['passed']}/{report['total']} — SCORE: {report['score']}/100")
    print(f"{'=' * 70}\n")


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python arch_quality.py <fixture.json> [expected.json]")
        sys.exit(1)

    fixture_path = sys.argv[1]
    with open(fixture_path, encoding="utf-8") as f:
        raw = json.load(f)

    expected = None
    if len(sys.argv) >= 3:
        with open(sys.argv[2], encoding="utf-8") as f:
            expected = json.load(f)

    report = assess_quality(raw, expected)
    print_report(report)
    sys.exit(0 if report["score"] >= 80 else 1)
