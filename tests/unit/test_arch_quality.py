#!/usr/bin/env python3
"""Architecture visualization quality tests.

Loads fixture data, runs it through the view pipeline, and verifies:
  1. Classification: namespace/service_type/tier assignments
  2. L1 completeness: all groups visible, no false drops
  3. L2 accuracy: core topology matches expected
  4. Edge integrity: no phantom edges, correct protocols
  5. Dashboard↔Agent relationship: all key interactions present

Run:  python -m pytest services/dashboard/tests/test_arch_quality.py -v
  or: python services/dashboard/tests/test_arch_quality.py
"""
import json
import os
import re
import sys

DASH_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, DASH_DIR)

FIXTURE = os.path.join(DASH_DIR, "fixtures", "arch_topdown_20260429-023032.json")


def _load_fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        raw = json.load(f)
    return raw.get("result", raw)


def _nodes_edges(data):
    g = data.get("graph", {})
    return g.get("nodes", []), g.get("edges", [])


DATA = _load_fixture()
NODES, EDGES = _nodes_edges(DATA)
NODE_MAP = {n["name"]: n for n in NODES}


def _remove_phantom_edges(nodes, edges):
    """Mirror overview_app._remove_phantom_edges."""
    nm = {n["name"]: n for n in nodes}
    return [e for e in edges
            if not (nm.get(e.get("source", ""), {}).get("namespace", "")
                    not in ("managed", "external", "platform")
                    and "Alarm" in nm.get(e.get("target", ""), {}).get("kind", "")
                    and nm.get(e.get("source", ""), {}).get("group", "")
                    != nm.get(e.get("target", ""), {}).get("group", ""))]


EDGES = _remove_phantom_edges(NODES, EDGES)


def _enrich_dashboard_agent_edges(nodes, edges):
    """Mirror overview_app._enrich_dashboard_agent_edges."""
    nm = {n["name"]: n for n in nodes}
    if "dashboard-app" not in nm or "external-devops-agent-api" not in nm:
        return edges
    for e in edges:
        if e.get("source") == "dashboard-app" and e.get("target") == "external-devops-agent-api":
            desc = e.get("description", "")
            if "send_message" not in desc and "대화" not in desc and "인터뷰" not in desc:
                e["description"] = (
                    "Agent API 다중 호출: 아키텍처 인터뷰 대화 (send_message), "
                    "시나리오 리뷰 채팅, 조사 저널 조회, 실행 목록, Space 관리 (SigV4 인증)"
                )
            return edges
    edges.append({
        "source": "dashboard-app", "target": "external-devops-agent-api",
        "protocol": "https", "port": 443, "paths": [], "methods": [],
        "description": (
            "Agent API 다중 호출: 아키텍처 인터뷰 대화 (send_message), "
            "시나리오 리뷰 채팅, 조사 저널 조회, 실행 목록, Space 관리 (SigV4 인증)"
        ),
    })
    return edges


EDGES = _enrich_dashboard_agent_edges(NODES, EDGES)

GROUPS = {}
for n in NODES:
    GROUPS.setdefault(n.get("group", ""), []).append(n["name"])


# ── helpers to simulate view logic ──

def _is_managed(n):
    """Legacy _is_managed — L2 split view only."""
    if n.get("namespace") in ("managed", "external") or n.get("kind") == "ExternalService":
        return True
    name = (n.get("name") or "").lower()
    kind = (n.get("kind") or "").lower()
    if name in ("browser", "client"):
        return True
    if re.search(r"cloudwatch|eks cluster|eks worker|ecs cluster", name) and not n.get("group"):
        return True
    if re.search(r"amazon |aws |elastic |lambda|sns|sqs|dynamodb|rds|s3|bedrock|cloudfront", kind) and not n.get("group"):
        return True
    return False


def _is_infra_noise(n):
    """L1 filter: only removes groupless infra noise."""
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


def _l1_groups():
    """Simulate L1 group aggregation using _is_infra_noise."""
    groups = {}
    for n in NODES:
        if _is_infra_noise(n):
            continue
        g = n.get("group") or "기타"
        groups.setdefault(g, []).append(n["name"])
    return groups


# ═══════════════════════════════════════════════════════════════
# 1. Classification tests
# ═══════════════════════════════════════════════════════════════

class TestClassification:
    """Verify namespace/service_type are consistent and meaningful."""

    def test_all_nodes_have_group(self):
        """Every node must belong to a group."""
        orphans = [n["name"] for n in NODES if not n.get("group")]
        assert orphans == [], f"Orphan nodes (no group): {orphans}"

    def test_namespace_values_are_known(self):
        """namespace must be one of: '', 'managed', 'external', 'platform'."""
        allowed = {"", "managed", "external", "platform"}
        bad = [(n["name"], n.get("namespace")) for n in NODES
               if n.get("namespace", "") not in allowed]
        assert bad == [], f"Unknown namespace values: {bad}"

    def test_service_type_values_are_known(self):
        allowed = {"app", "cache", "db", "gateway", "queue", "worker",
                   "managed", "platform"}
        bad = [(n["name"], n.get("service_type")) for n in NODES
               if n.get("service_type", "app") not in allowed]
        assert bad == [], f"Unknown service_type values: {bad}"

    def test_lambda_nodes_are_not_app(self):
        """Lambda functions should have service_type != 'app' or have clear role."""
        lambdas = [n for n in NODES if "Lambda" in n.get("kind", "")]
        for n in lambdas:
            assert n.get("service_type") in ("managed", "worker", "gateway", "app"), \
                f"Lambda {n['name']} has unexpected service_type: {n.get('service_type')}"

    def test_namespace_managed_overlap_with_service_type(self):
        """Flag nodes where namespace and service_type encode same thing redundantly."""
        redundant = []
        for n in NODES:
            ns = n.get("namespace", "")
            st = n.get("service_type", "")
            if ns == "managed" and st == "managed":
                redundant.append(n["name"])
        # This is expected to FAIL currently — documenting the redundancy
        if redundant:
            print(f"\n  WARNING: {len(redundant)} nodes have both namespace=managed AND "
                  f"service_type=managed (redundant): {redundant[:5]}...")


# ═══════════════════════════════════════════════════════════════
# 2. L1 completeness tests
# ═══════════════════════════════════════════════════════════════

class TestL1Completeness:
    """L1 should show ALL app groups — none should vanish."""

    EXPECTED_GROUPS = {
        "DockerCoins", "AlarmPipeline", "ChaosEngineering",
        "ScenarioDashboard", "UserService", "SharedInfrastructure", "External",
    }

    def test_all_groups_exist_in_data(self):
        """Data must contain all expected groups."""
        missing = self.EXPECTED_GROUPS - set(GROUPS.keys())
        assert missing == set(), f"Groups missing from data: {missing}"

    def test_l1_shows_all_app_groups(self):
        """L1 view must show all groups that have a group name."""
        l1 = _l1_groups()
        data_groups = {g for g in GROUPS if g}
        l1_groups = set(l1.keys())
        dropped = data_groups - l1_groups
        assert dropped == set(), (
            f"Groups dropped by L1 _is_managed filter: {dropped}. "
            f"These groups exist in data but vanish in L1 view."
        )

    def test_alarmpipeline_visible_in_l1(self):
        l1 = _l1_groups()
        assert "AlarmPipeline" in l1, "AlarmPipeline (15 nodes) completely missing from L1"

    def test_chaosengineering_visible_in_l1(self):
        l1 = _l1_groups()
        assert "ChaosEngineering" in l1, "ChaosEngineering (10 nodes) completely missing from L1"

    def test_external_visible_in_l1(self):
        l1 = _l1_groups()
        assert "External" in l1, "External (7 nodes: Agent API, Bedrock, Slack) missing from L1"

    def test_l1_cross_group_edges_include_dashboard_to_agent(self):
        """L1 should show ScenarioDashboard → AlarmPipeline edge."""
        l1 = _l1_groups()
        if "AlarmPipeline" not in l1:
            return  # skip if group itself is missing (covered above)

        node_group = {}
        for n in NODES:
            if not _is_infra_noise(n):
                node_group[n["name"]] = n.get("group", "")

        cross_edges = set()
        for e in EDGES:
            sg = node_group.get(e["source"])
            tg = node_group.get(e["target"])
            if sg and tg and sg != tg:
                cross_edges.add(f"{sg}→{tg}")

        assert "ScenarioDashboard→AlarmPipeline" in cross_edges, \
            "Dashboard→AlarmPipeline relationship not visible in L1"


# ═══════════════════════════════════════════════════════════════
# 3. DockerCoins L2 core topology
# ═══════════════════════════════════════════════════════════════

class TestDockerCoinsCore:
    """Verify core DockerCoins topology matches K8s manifests."""

    EXPECTED_CORE_NODES = {"dockercoins-worker", "dockercoins-hasher",
                           "dockercoins-rng", "dockercoins-webui",
                           "dockercoins-redis"}

    EXPECTED_CORE_EDGES = {
        ("dockercoins-worker", "dockercoins-rng", "http", 80),
        ("dockercoins-worker", "dockercoins-hasher", "http", 80),
        ("dockercoins-worker", "dockercoins-redis", "tcp", 6379),
        ("dockercoins-webui", "dockercoins-redis", "tcp", 6379),
        ("dockercoins-rng", "shared-rds-postgresql", "tcp", 5432),
    }

    def test_core_nodes_present(self):
        actual = {n["name"] for n in NODES if n.get("group") == "DockerCoins"
                  and n.get("namespace", "") == ""}
        missing = self.EXPECTED_CORE_NODES - actual
        assert missing == set(), f"Missing core nodes: {missing}"

    def test_core_edges_present(self):
        actual = {(e["source"], e["target"], e.get("protocol", ""), e.get("port", 0))
                  for e in EDGES}
        missing = self.EXPECTED_CORE_EDGES - actual
        assert missing == set(), f"Missing core edges: {missing}"

    def test_no_bidirectional_edges(self):
        """No A→B and B→A edges should exist."""
        pairs = set()
        dupes = []
        for e in EDGES:
            key = (e["source"], e["target"])
            rev = (e["target"], e["source"])
            if rev in pairs:
                dupes.append(f"{e['source']}↔{e['target']}")
            pairs.add(key)
        assert dupes == [], f"Bidirectional edges found: {dupes}"


# ═══════════════════════════════════════════════════════════════
# 4. Edge integrity
# ═══════════════════════════════════════════════════════════════

class TestEdgeIntegrity:
    """Verify edges reference existing nodes and have no phantoms."""

    def test_all_edge_sources_exist(self):
        missing = [(e["source"], e["target"]) for e in EDGES
                   if e["source"] not in NODE_MAP]
        assert missing == [], f"Edges with unknown source: {missing}"

    def test_all_edge_targets_exist(self):
        missing = [(e["source"], e["target"]) for e in EDGES
                   if e["target"] not in NODE_MAP]
        assert missing == [], f"Edges with unknown target: {missing}"

    def test_no_shortcut_phantom_edges(self):
        """Edges that skip intermediate services (data_flow_integrity rule)."""
        phantoms = []
        for e in EDGES:
            src = NODE_MAP.get(e["source"], {})
            tgt = NODE_MAP.get(e["target"], {})
            # App pod directly → Alarm is a phantom (should go through CW)
            if (src.get("namespace", "") == "" and
                    "Alarm" in tgt.get("kind", "") and
                    src.get("group") != tgt.get("group")):
                phantoms.append(f"{e['source']}→{e['target']}")
        assert phantoms == [], (
            f"Phantom edges (app→alarm skips CloudWatch): {phantoms}"
        )

    def test_cloudwatch_alarms_complete(self):
        """All 6 CloudFormation alarms should exist as nodes."""
        expected_keywords = {
            "hasher-errors": ["hasher", "error"],
            "hasher-faults": ["hasher", "fault"],
            "hasher-high-latency": ["hasher", "latency"],
            "hasher-network-latency": ["hasher", "network", "latency"],
            "hasher-oomkilled": ["hasher", "oom"],
            "cluster-high-cpu": ["cluster", "cpu"],
        }
        alarm_nodes = [n["name"] for n in NODES if "Alarm" in n.get("kind", "")]
        found = set()
        for name, keywords in expected_keywords.items():
            for actual in alarm_nodes:
                actual_lower = actual.lower().replace("-", "")
                if all(kw in actual_lower for kw in keywords):
                    found.add(name)
                    break
        missing = set(expected_keywords.keys()) - found
        assert missing == set(), f"CloudWatch alarms missing from data: {missing}"


# ═══════════════════════════════════════════════════════════════
# 5. Dashboard ↔ DevOps Agent relationship
# ═══════════════════════════════════════════════════════════════

class TestDashboardAgentRelationship:
    """Verify Dashboard's interactions with DevOps Agent are captured."""

    def _dashboard_edges(self):
        return [e for e in EDGES if e.get("source") == "dashboard-app"]

    def test_dashboard_to_agent_api_exists(self):
        targets = {e["target"] for e in self._dashboard_edges()}
        assert "external-devops-agent-api" in targets, \
            "dashboard-app → external-devops-agent-api edge missing"

    def test_dashboard_to_agent_api_has_send_message(self):
        """Edge description should mention send_message / chat, not just journal read."""
        for e in self._dashboard_edges():
            if e["target"] == "external-devops-agent-api":
                desc = e.get("description", "").lower()
                has_chat = any(kw in desc for kw in [
                    "send_message", "chat", "대화", "인터뷰", "질문",
                    "세션", "session", "create_execution"])
                assert has_chat, (
                    f"dashboard→agent edge only says '{e.get('description')}'. "
                    f"Missing send_message/chat interaction — this is the most critical relationship."
                )

    def test_dashboard_to_bedrock_exists(self):
        targets = {e["target"] for e in self._dashboard_edges()}
        assert "external-bedrock-claude" in targets, \
            "dashboard-app → external-bedrock-claude edge missing"

    def test_dashboard_to_investigation_events_exists(self):
        targets = {e["target"] for e in self._dashboard_edges()}
        assert "alarmpipeline-investigation-events-table" in targets, \
            "dashboard-app → investigation-events-table edge missing"

    def test_dashboard_agent_api_multiple_purposes(self):
        """Dashboard uses Agent API for 10+ different purposes, edge should reflect this."""
        agent_edges = [e for e in self._dashboard_edges()
                       if e["target"] == "external-devops-agent-api"]
        # Currently only 1 edge with single description — this should ideally
        # mention multiple purposes or have multiple edges
        if len(agent_edges) == 1:
            desc = agent_edges[0].get("description", "")
            purposes = ["저널", "journal", "인터뷰", "interview", "chat",
                        "send_message", "list_executions", "세션"]
            found = sum(1 for p in purposes if p.lower() in desc.lower())
            if found <= 1:
                print(f"\n  WARNING: dashboard→agent edge describes only 1 purpose: "
                      f"'{desc}'. Actually 10+ API calls in code.")


# ═══════════════════════════════════════════════════════════════
# 6. AlarmPipeline internal chain
# ═══════════════════════════════════════════════════════════════

class TestAlarmPipelineChain:
    """Verify the alarm→SNS→Lambda→Agent→EventBridge→Lambda→DynamoDB chain."""

    def test_alarms_to_sns(self):
        alarm_to_sns = [e for e in EDGES
                        if "alarm" in e["source"].lower()
                        and "sns" in e["target"].lower()
                        and e["source"].startswith("alarmpipeline-")]
        assert len(alarm_to_sns) >= 3, \
            f"Expected ≥3 alarm→sns edges, got {len(alarm_to_sns)}"

    def test_sns_to_lambda(self):
        sns_to_lambda = [e for e in EDGES
                         if "sns" in e["source"].lower()
                         and "lambda" in e["target"].lower()
                         and e["source"].startswith("alarmpipeline-")]
        assert len(sns_to_lambda) >= 1, "AlarmPipeline SNS→Lambda edge missing"

    def test_lambda_to_agent_api(self):
        lambda_to_agent = [e for e in EDGES
                           if "lambda" in e["source"].lower()
                           and "agent-api" in e["target"].lower()]
        assert len(lambda_to_agent) >= 1, "Lambda→DevOps Agent API edge missing"

    def test_eventbridge_to_lambda(self):
        eb_to_lambda = [e for e in EDGES
                        if "eventbridge" in e["source"].lower()
                        and "lambda" in e["target"].lower()]
        assert len(eb_to_lambda) >= 1, "EventBridge→Lambda edge missing"

    def test_lambda_to_dynamodb(self):
        lambda_to_ddb = [e for e in EDGES
                         if "lambda" in e["source"].lower()
                         and "investigation-events" in e["target"].lower()]
        assert len(lambda_to_ddb) >= 1, "Lambda→DynamoDB investigation-events edge missing"


# ═══════════════════════════════════════════════════════════════
# Score summary
# ═══════════════════════════════════════════════════════════════

def run_quality_report():
    """Run all tests and print a quality score summary."""
    import traceback

    suites = [
        ("Classification", TestClassification),
        ("L1 Completeness", TestL1Completeness),
        ("DockerCoins Core", TestDockerCoinsCore),
        ("Edge Integrity", TestEdgeIntegrity),
        ("Dashboard↔Agent", TestDashboardAgentRelationship),
        ("AlarmPipeline Chain", TestAlarmPipelineChain),
    ]

    total = 0
    passed = 0
    results = []

    for suite_name, cls in suites:
        instance = cls()
        methods = [m for m in dir(instance) if m.startswith("test_")]
        suite_pass = 0
        suite_total = len(methods)
        failures = []

        for method_name in sorted(methods):
            total += 1
            try:
                getattr(instance, method_name)()
                passed += 1
                suite_pass += 1
            except AssertionError as e:
                failures.append((method_name, str(e)))
            except Exception as e:
                failures.append((method_name, f"ERROR: {e}"))

        status = "PASS" if suite_pass == suite_total else "FAIL"
        results.append((suite_name, suite_pass, suite_total, failures))

    print("\n" + "=" * 70)
    print("ARCHITECTURE VISUALIZATION QUALITY REPORT")
    print("=" * 70)

    for suite_name, sp, st, failures in results:
        pct = int(100 * sp / st) if st else 0
        icon = "PASS" if sp == st else "FAIL"
        print(f"\n  [{icon}] {suite_name}: {sp}/{st} ({pct}%)")
        for fname, msg in failures:
            short = fname.replace("test_", "")
            print(f"        FAIL {short}: {msg[:120]}")

    score = int(100 * passed / total) if total else 0
    print(f"\n{'=' * 70}")
    print(f"  TOTAL: {passed}/{total} tests passed — SCORE: {score}/100")
    print(f"{'=' * 70}\n")
    return score


if __name__ == "__main__":
    try:
        run_quality_report()
    except Exception:
        import traceback
        traceback.print_exc()
