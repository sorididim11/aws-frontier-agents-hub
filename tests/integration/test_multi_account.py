#!/usr/bin/env python3
"""Multi-account system unit + integration tests.

Tests AccountRegistry, TopologyProvider, CredentialResolver, and the
scenario execution pipeline using sample data (no live AWS/kubectl calls).

Run:  python -m pytest services/dashboard/tests/test_multi_account.py -v
  or: python services/dashboard/tests/test_multi_account.py
"""
import json
import os
import re
import sys
import threading
from unittest.mock import MagicMock, patch

DASH_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, DASH_DIR)


# ══════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════

SAMPLE_CONFIG = {
    "aws": {"region": "us-east-1", "account_id": "111111111111", "profile": "member1-acc"},
    "kubernetes": {"namespace": "dockercoins", "cluster_name": "devops-agent-test-cluster"},
    "clusters": {
        "primary": {
            "context": "arn:aws:eks:us-east-1:111111111111:cluster/devops-agent-test-cluster",
            "region": "us-east-1",
            "account_id": "111111111111",
            "profile": "member1-acc",
        },
        "member2": {
            "context": "devops-agent-test-m2-cluster",
            "region": "us-east-1",
            "account_id": "222222222222",
            "profile": "member2-acc",
        },
    },
}

SAMPLE_KUBECTL_PRIMARY = "hasher=2 worker=2 webui=1 redis=1 rng=1"
SAMPLE_KUBECTL_MEMBER2 = "rng=1"

SAMPLE_FIS_TRIGGER_OUTPUT = json.dumps({
    "experiment": {
        "id": "EXPtest123abc",
        "arn": "arn:aws:fis:us-east-1:222222222222:experiment/EXPtest123abc",
        "experimentTemplateId": "EXT72sBEdaQgHDMax",
        "state": {"status": "initiating"},
    }
})

SAMPLE_SCENARIO = {
    "id": "sc-test-rng-blackhole",
    "name": "Test RNG DB Blackhole",
    "target_service": "rng",
    "trigger": {"type": "fis", "command": "aws fis start-experiment --experiment-template-id ${FIS_TEMPLATE_ID}"},
    "restore": {"command": "aws fis stop-experiment --id ${FIS_EXPERIMENT_ID}"},
    "variables": {"FIS_TEMPLATE_ID": "EXT72sBEdaQgHDMax", "TARGET_SERVICE": "rng"},
    "verification": {"steps": [
        {"name": "FIS check", "type": "fis_status", "expected": "running", "timeout": 60, "poll_interval": 10},
    ]},
    "pre_cleanup": {"command": "echo clean"},
    "observation_window": 120,
}


# ══════════════════════════════════════════════════════════════════
# Unit Tests — AccountRegistry
# ══════════════════════════════════════════════════════════════════

class TestAccountRegistry:
    def setup_method(self):
        from account_registry import AccountRegistry
        self.reg = AccountRegistry()

    def test_init_from_config_yaml(self):
        with patch.object(self.reg, "_load_from_agent_space", return_value=[]):
            self.reg.init(space_id="", config=SAMPLE_CONFIG, config_dir="/nonexistent")

        accounts = self.reg.list_all()
        assert len(accounts) == 2

        primary = self.reg.get_primary()
        assert primary is not None
        assert primary.account_id == "111111111111"
        assert primary.profile == "member1-acc"
        assert primary.is_primary

        member2 = self.reg.get("222222222222")
        assert member2 is not None
        assert member2.profile == "member2-acc"
        assert not member2.is_primary

    def test_context_lookup(self):
        with patch.object(self.reg, "_load_from_agent_space", return_value=[]):
            self.reg.init(space_id="", config=SAMPLE_CONFIG, config_dir="/nonexistent")

        acct_id = self.reg.get_account_for_context("devops-agent-test-m2-cluster")
        assert acct_id == "222222222222"

        acct_id = self.reg.get_account_for_context(
            "arn:aws:eks:us-east-1:111111111111:cluster/devops-agent-test-cluster"
        )
        assert acct_id == "111111111111"

    def test_profile_lookup(self):
        with patch.object(self.reg, "_load_from_agent_space", return_value=[]):
            self.reg.init(space_id="", config=SAMPLE_CONFIG, config_dir="/nonexistent")

        acct = self.reg.get_by_profile("member2-acc")
        assert acct is not None
        assert acct.account_id == "222222222222"

    def test_merge_priority_yaml_over_env(self):
        from account_registry import RegisteredAccount
        env_acct = RegisteredAccount(
            account_id="222222222222", profile="old-profile", source="env_file"
        )
        with patch.object(self.reg, "_load_from_agent_space", return_value=[]):
            with patch.object(self.reg, "_load_from_env_files", return_value=[env_acct]):
                self.reg.init(space_id="", config=SAMPLE_CONFIG, config_dir="/nonexistent")

        acct = self.reg.get("222222222222")
        assert acct.profile == "member2-acc"  # yaml wins over env


# ══════════════════════════════════════════════════════════════════
# Unit Tests — TopologyProvider
# ══════════════════════════════════════════════════════════════════

class TestTopologyProvider:
    def setup_method(self):
        from account_registry import AccountRegistry
        from topology_provider import TopologyProvider
        self.reg = AccountRegistry()
        with patch.object(self.reg, "_load_from_agent_space", return_value=[]):
            self.reg.init(space_id="", config=SAMPLE_CONFIG, config_dir="/nonexistent")
        self.topo = TopologyProvider()

    def _mock_kubectl(self, context, account_id, label):
        """Return sample kubectl output per context."""
        from topology_provider import ServiceLocation
        if "111111111111" in context or "primary" in label:
            return [
                ServiceLocation("hasher", account_id, context, "dockercoins", label, 2),
                ServiceLocation("worker", account_id, context, "dockercoins", label, 2),
                ServiceLocation("webui", account_id, context, "dockercoins", label, 1),
                ServiceLocation("redis", account_id, context, "dockercoins", label, 1),
            ]
        elif "m2" in context:
            return [
                ServiceLocation("rng", account_id, context, "dockercoins", label, 1),
            ]
        return []

    def test_discover_primary_first(self):
        with patch.object(self.topo, "_discover_kubectl", side_effect=self._mock_kubectl):
            self.topo.init(self.reg)

        loc = self.topo.resolve("hasher")
        assert loc is not None
        assert loc.account_id == "111111111111"

        loc = self.topo.resolve("rng")
        assert loc is not None
        assert loc.account_id == "222222222222"
        assert loc.context == "devops-agent-test-m2-cluster"

    def test_service_not_found(self):
        with patch.object(self.topo, "_discover_kubectl", side_effect=self._mock_kubectl):
            self.topo.init(self.reg)

        loc = self.topo.resolve("nonexistent")
        assert loc is None

    def test_get_services_in_account(self):
        with patch.object(self.topo, "_discover_kubectl", side_effect=self._mock_kubectl):
            self.topo.init(self.reg)

        svcs = self.topo.get_services_in_account("111111111111")
        assert "hasher" in svcs
        assert "worker" in svcs
        assert "rng" not in svcs

        svcs = self.topo.get_services_in_account("222222222222")
        assert svcs == ["rng"]

    def test_resolve_profile(self):
        with patch.object(self.topo, "_discover_kubectl", side_effect=self._mock_kubectl):
            self.topo.init(self.reg)

        profile = self.topo.resolve_profile("rng")
        assert profile == "member2-acc"

        profile = self.topo.resolve_profile("hasher")
        assert profile == "member1-acc"


# ══════════════════════════════════════════════════════════════════
# Unit Tests — CredentialResolver
# ══════════════════════════════════════════════════════════════════

class TestCredentialResolver:
    def setup_method(self):
        from account_registry import AccountRegistry
        from credential_resolver import CredentialResolver
        self.reg = AccountRegistry()
        with patch.object(self.reg, "_load_from_agent_space", return_value=[]):
            self.reg.init(space_id="", config=SAMPLE_CONFIG, config_dir="/nonexistent")
        self.cred = CredentialResolver()
        self.cred.init(self.reg)

    def test_profile_preferred_over_role(self):
        import boto3 as _b3
        with patch.object(_b3, "Session") as mock_session_cls:
            mock_session_cls.return_value = MagicMock()
            self.cred.invalidate("222222222222")
            session = self.cred.get_session("222222222222")
            mock_session_cls.assert_called_with(profile_name="member2-acc", region_name="us-east-1")

    def test_list_accounts(self):
        accounts = self.cred.list_accounts()
        assert len(accounts) == 2
        ids = {a.account_id for a in accounts}
        assert "111111111111" in ids
        assert "222222222222" in ids

    def test_is_assumable_role_rejects_when_profile_exists(self):
        # 222222222222 has profile=member2-acc → role is NOT assumable (profile wins)
        assert not self.cred._is_assumable_role(
            "arn:aws:iam::222222222222:role/devops-agent-test-aidlc-role-abc123"
        )
        # Same account, different role name — still not assumable because profile exists
        assert not self.cred._is_assumable_role(
            "arn:aws:iam::222222222222:role/cross-account-simulator-role"
        )
        # Unknown account → not assumable (not registered)
        assert not self.cred._is_assumable_role(
            "arn:aws:iam::999999999999:role/some-role"
        )

    def test_session_caching(self):
        import boto3 as _b3
        with patch.object(_b3, "Session") as mock_session_cls:
            mock_session_cls.return_value = MagicMock()
            self.cred.invalidate("111111111111")
            self.cred.get_session("111111111111")
            self.cred.get_session("111111111111")
            assert mock_session_cls.call_count == 1  # cached

    def test_invalidate_clears_cache(self):
        import boto3 as _b3
        with patch.object(_b3, "Session") as mock_session_cls:
            mock_session_cls.return_value = MagicMock()
            self.cred.invalidate("111111111111")
            self.cred.get_session("111111111111")
            self.cred.invalidate("111111111111")
            self.cred.get_session("111111111111")
            assert mock_session_cls.call_count == 2


# ══════════════════════════════════════════════════════════════════
# Unit Tests — ExecutionContext
# ══════════════════════════════════════════════════════════════════

class TestExecutionContext:
    def setup_method(self):
        from account_registry import AccountRegistry, registry
        from topology_provider import TopologyProvider, topology, ServiceLocation
        from credential_resolver import CredentialResolver, credentials

        # Init registry
        with patch.object(registry, "_load_from_agent_space", return_value=[]):
            registry.init(space_id="", config=SAMPLE_CONFIG, config_dir="/nonexistent")

        # Init topology with mock discovery
        def mock_kubectl(ctx, aid, label):
            if "m2" in ctx:
                return [ServiceLocation("rng", aid, ctx, "dockercoins", label, 1)]
            return [
                ServiceLocation("hasher", aid, ctx, "dockercoins", label, 2),
                ServiceLocation("worker", aid, ctx, "dockercoins", label, 2),
            ]

        with patch.object(topology, "_discover_kubectl", side_effect=mock_kubectl):
            topology.init(registry)

        # Init credentials
        credentials.init(registry)

    def test_for_scenario_rng_routes_to_member2(self):
        from execution_context import ExecutionContext
        scenario = {"target_service": "rng", "trigger": {"command": "aws fis start-experiment"}}
        ctx = ExecutionContext.for_scenario(scenario)
        assert ctx.account_id == "222222222222"
        assert ctx.profile == "member2-acc"
        assert ctx.kubectl_context == "devops-agent-test-m2-cluster"

    def test_for_scenario_hasher_routes_to_member1(self):
        from execution_context import ExecutionContext
        scenario = {"target_service": "hasher", "trigger": {"command": "aws fis start-experiment"}}
        ctx = ExecutionContext.for_scenario(scenario)
        assert ctx.account_id == "111111111111"
        assert ctx.profile == "member1-acc"
        assert "111111111111" in ctx.kubectl_context

    def test_inject_profile(self):
        from execution_context import ExecutionContext
        ctx = ExecutionContext(
            target_service="rng", account_id="222222222222",
            profile="member2-acc", kubectl_context="devops-agent-test-m2-cluster",
            region="us-east-1", namespace="dockercoins",
        )
        cmd = "aws fis start-experiment --experiment-template-id EXT123"
        result = ctx.inject_profile(cmd)
        assert "--profile member2-acc" in result

    def test_inject_context(self):
        from execution_context import ExecutionContext
        ctx = ExecutionContext(
            target_service="rng", account_id="222222222222",
            profile="member2-acc", kubectl_context="devops-agent-test-m2-cluster",
            region="us-east-1", namespace="dockercoins",
        )
        cmd = "kubectl get pods -n dockercoins -l app=rng"
        result = ctx.inject_context(cmd)
        assert "--context devops-agent-test-m2-cluster" in result

    def test_inject_all(self):
        from execution_context import ExecutionContext
        ctx = ExecutionContext(
            target_service="rng", account_id="222222222222",
            profile="member2-acc", kubectl_context="devops-agent-test-m2-cluster",
            region="us-east-1", namespace="dockercoins",
        )
        cmd = "aws fis start-experiment && kubectl get pods"
        result = ctx.inject_all(cmd)
        assert "--profile member2-acc" in result
        assert "--context devops-agent-test-m2-cluster" in result

    def test_fallback_to_primary(self):
        from execution_context import ExecutionContext
        scenario = {"target_service": "unknown-service", "trigger": {"command": "echo test"}}
        ctx = ExecutionContext.for_scenario(scenario)
        assert ctx.profile == "member1-acc"
        assert ctx.account_id == "111111111111"


# ══════════════════════════════════════════════════════════════════
# Unit Tests — Variable Resolution & FIS ID Extraction
# ══════════════════════════════════════════════════════════════════

class TestVariableResolution:
    def test_resolve_scenario_variables(self):
        from verifier_base import _resolve_scenario_variables
        cmd = "aws fis start-experiment --experiment-template-id ${FIS_TEMPLATE_ID}"
        scenario = {"variables": {"FIS_TEMPLATE_ID": "EXTabc123"}}
        result = _resolve_scenario_variables(cmd, scenario)
        assert "${FIS_TEMPLATE_ID}" not in result
        assert "EXTabc123" in result

    def test_fis_experiment_id_extraction(self):
        m = re.search(r'"id"\s*:\s*"(EXP[A-Za-z0-9]+)"', SAMPLE_FIS_TRIGGER_OUTPUT)
        assert m is not None
        assert m.group(1) == "EXPtest123abc"

    def test_restore_fis_id_substitution(self):
        restore_cmd = "aws fis stop-experiment --id ${FIS_EXPERIMENT_ID}"
        trigger_output = SAMPLE_FIS_TRIGGER_OUTPUT
        m = re.search(r'"id"\s*:\s*"(EXP[A-Za-z0-9]+)"', trigger_output)
        if m:
            restore_cmd = restore_cmd.replace("${FIS_EXPERIMENT_ID}", m.group(1))
        assert restore_cmd == "aws fis stop-experiment --id EXPtest123abc"


# ══════════════════════════════════════════════════════════════════
# Unit Tests — Verifier Type Registry
# ══════════════════════════════════════════════════════════════════

class TestVerifierRegistry:
    def test_fis_status_alias_exists(self):
        from verifier_checkers import VERIFIERS
        assert "fis_status" in VERIFIERS
        assert "fis_experiment" in VERIFIERS
        assert VERIFIERS["fis_status"] is VERIFIERS["fis_experiment"]

    def test_kubectl_check_exists(self):
        from verifier_checkers import VERIFIERS
        assert "kubectl_check" in VERIFIERS

    def test_all_common_types_registered(self):
        from verifier_checkers import VERIFIERS
        expected = {"pod_logs", "cw_alarm", "metric_check", "alarm_state",
                    "kubectl_check", "fis_experiment", "fis_status", "log_pattern"}
        assert expected.issubset(set(VERIFIERS.keys()))


# ══════════════════════════════════════════════════════════════════
# Integration Tests — Full Pipeline Flow (mocked subprocess)
# ══════════════════════════════════════════════════════════════════

class TestPipelineIntegration:
    """Tests the scenario execution flow with mocked external calls."""

    def setup_method(self):
        from account_registry import AccountRegistry
        from topology_provider import TopologyProvider
        from credential_resolver import CredentialResolver

        self.reg = AccountRegistry()
        with patch.object(self.reg, "_load_from_agent_space", return_value=[]):
            self.reg.init(space_id="", config=SAMPLE_CONFIG, config_dir="/nonexistent")

        self.topo = TopologyProvider()
        self.cred = CredentialResolver()
        self.cred.init(self.reg)

    def test_scenario_routing_member2(self):
        """Verify rng scenario routes to member2 account/profile."""
        from topology_provider import ServiceLocation
        def mock_kubectl(ctx, aid, label):
            if "m2" in ctx:
                return [ServiceLocation("rng", aid, ctx, "dockercoins", label, 1)]
            return [
                ServiceLocation("hasher", aid, ctx, "dockercoins", label, 2),
                ServiceLocation("worker", aid, ctx, "dockercoins", label, 2),
            ]

        with patch.object(self.topo, "_discover_kubectl", side_effect=mock_kubectl):
            self.topo.init(self.reg)

        profile = self.topo.resolve_profile("rng")
        assert profile == "member2-acc"

        context = self.topo.resolve_context("rng")
        assert context == "devops-agent-test-m2-cluster"

        account = self.topo.resolve_account("rng")
        assert account == "222222222222"

    def test_scenario_routing_member1(self):
        """Verify hasher scenario routes to member1 account/profile."""
        from topology_provider import ServiceLocation
        def mock_kubectl(ctx, aid, label):
            if "m2" in ctx:
                return [ServiceLocation("rng", aid, ctx, "dockercoins", label, 1)]
            return [
                ServiceLocation("hasher", aid, ctx, "dockercoins", label, 2),
                ServiceLocation("worker", aid, ctx, "dockercoins", label, 2),
            ]

        with patch.object(self.topo, "_discover_kubectl", side_effect=mock_kubectl):
            self.topo.init(self.reg)

        profile = self.topo.resolve_profile("hasher")
        assert profile == "member1-acc"

        context = self.topo.resolve_context("hasher")
        assert "111111111111" in context

    def test_fis_templates_multi_account_collection(self):
        """Verify FIS template collection spans all accounts."""
        accounts = self.cred.list_accounts()
        # Both accounts should be iterable for FIS template collection
        profiles = {a.profile for a in accounts}
        assert "member1-acc" in profiles
        assert "member2-acc" in profiles

    def test_scenario_variable_pipeline(self):
        """Full variable resolution: scenario vars → trigger → extract ID → restore."""
        from verifier_base import _resolve_scenario_variables
        scenario = SAMPLE_SCENARIO.copy()

        # 1. Trigger command resolution
        trigger_cmd = scenario["trigger"]["command"]
        trigger_cmd = _resolve_scenario_variables(trigger_cmd, scenario)
        assert "EXT72sBEdaQgHDMax" in trigger_cmd
        assert "${FIS_TEMPLATE_ID}" not in trigger_cmd

        # 2. Simulate trigger output (FIS start-experiment response)
        trigger_output = SAMPLE_FIS_TRIGGER_OUTPUT

        # 3. Restore command resolution
        restore_cmd = scenario["restore"]["command"]
        restore_cmd = _resolve_scenario_variables(restore_cmd, scenario)
        # FIS_EXPERIMENT_ID is not in scenario variables, so it remains
        assert "${FIS_EXPERIMENT_ID}" in restore_cmd

        # 4. Dynamic extraction from trigger output
        m = re.search(r'"id"\s*:\s*"(EXP[A-Za-z0-9]+)"', trigger_output)
        if m:
            restore_cmd = restore_cmd.replace("${FIS_EXPERIMENT_ID}", m.group(1))
        assert restore_cmd == "aws fis stop-experiment --id EXPtest123abc"


# ══════════════════════════════════════════════════════════════════
# Integration Tests — Scenario Validation
# ══════════════════════════════════════════════════════════════════

class TestScenarioValidation:
    def test_fis_experiment_id_allowed_in_restore(self):
        """${FIS_EXPERIMENT_ID} should pass validation (runtime-resolved variable)."""
        from routes_scenario import SCENARIO_PLACEHOLDER_VARS_GLOBAL
        assert "FIS_EXPERIMENT_ID" in SCENARIO_PLACEHOLDER_VARS_GLOBAL

    def test_find_undefined_vars_allows_fis_id(self):
        from routes_scenario import _find_undefined_vars
        cmd = "aws fis stop-experiment --id ${FIS_EXPERIMENT_ID}"
        bad = _find_undefined_vars(cmd, scenario_vars=set())
        assert "FIS_EXPERIMENT_ID" not in bad

    def test_find_undefined_vars_catches_unknown(self):
        from routes_scenario import _find_undefined_vars
        cmd = "aws fis stop-experiment --id ${UNKNOWN_VAR}"
        bad = _find_undefined_vars(cmd, scenario_vars=set())
        assert "UNKNOWN_VAR" in bad


# ══════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        import pytest
        sys.exit(pytest.main([__file__, "-v"]))
    except ImportError:
        import unittest
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        for cls in [TestAccountRegistry, TestTopologyProvider, TestCredentialResolver,
                    TestVariableResolution, TestVerifierRegistry, TestPipelineIntegration,
                    TestScenarioValidation]:
            for method in dir(cls):
                if method.startswith("test_"):
                    obj = cls()
                    if hasattr(obj, "setup_method"):
                        obj.setup_method()
                    try:
                        getattr(obj, method)()
                        print(f"  PASS {cls.__name__}.{method}")
                    except AssertionError as e:
                        print(f"  FAIL {cls.__name__}.{method}: {e}")
                    except Exception as e:
                        print(f"  ERROR {cls.__name__}.{method}: {e}")
