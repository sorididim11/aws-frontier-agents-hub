"""
ExecutionContext — 시나리오 실행 시 credential routing 일체형 객체.

시나리오의 target_service에서 자동으로 account/context/profile을 resolve하고,
명령어에 --profile, --context를 주입하는 단일 진입점 제공.

내부적으로 AccountContext (immutable 값 객체)를 생성하여 agent에 전달 가능.

사용:
  from execution_context import ExecutionContext
  ctx = ExecutionContext.for_scenario(scenario)
  cmd = ctx.inject_profile(cmd)
  cmd = ctx.inject_context(cmd)
  fis = ctx.get_fis_client()
  account_ctx = ctx.account_context  # → AccountContext for agent isolation
"""
import os
import re
from dataclasses import dataclass
from typing import Optional

import cluster_manager
from account_context import AccountContext
from account_registry import registry as _registry
from topology_provider import topology as _topology
from credential_resolver import credentials as _credentials


AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


@dataclass
class ExecutionContext:
    """Per-scenario resolved routing: account + context + profile."""

    target_service: str
    account_id: str
    profile: str
    kubectl_context: str
    region: str
    namespace: str

    @property
    def account_context(self) -> AccountContext:
        """Immutable AccountContext for per-agent tool isolation."""
        return AccountContext(
            account_id=self.account_id,
            profile=self.profile,
            kubectl_context=self.kubectl_context,
            region=self.region,
            namespace=self.namespace,
        )

    @classmethod
    def for_scenario(cls, scenario: dict, namespace: str = None) -> "ExecutionContext":
        """Factory: resolve all routing info from scenario definition."""
        target = scenario.get("target_service", "").strip()
        ns = namespace or scenario.get("namespace") or os.environ.get("K8S_NAMESPACE", "dockercoins")

        # 1. Resolve via TopologyProvider (dynamic)
        context = None
        account_id = None
        profile = None

        if target:
            loc = _topology.resolve(target)
            if loc:
                context = loc.context
                account_id = loc.account_id
                acct = _registry.get(account_id)
                profile = acct.profile if acct else None

        # 2. Fallback: parse trigger command for service hints
        if not context:
            trigger_cmd = scenario.get("trigger", {}).get("command", "")
            context, account_id, profile = cls._detect_from_command(trigger_cmd)

        # 3. Fallback: use scenario context from cluster_manager
        if not context and target:
            context = cluster_manager.get_context_for_service(target)
            if context:
                account_id = cluster_manager.get_account_for_context(context)
                profile = cluster_manager.get_profile_for_context(context)

        # 4. Final fallback: first available profile from registry
        if not profile:
            all_accts = _registry.list_all()
            if all_accts:
                fallback = all_accts[0]
                profile = fallback.profile
                account_id = account_id or fallback.account_id

        return cls(
            target_service=target or "",
            account_id=account_id or "",
            profile=profile or "",
            kubectl_context=context or "",
            region=AWS_REGION,
            namespace=ns,
        )

    def inject_profile(self, command: str) -> str:
        """Add --profile to aws commands if not already present."""
        if not command or not self.profile:
            return command
        if "aws " in command and "--profile " not in command:
            command = command.replace("aws ", f"aws --profile {self.profile} ", 1)
        return command

    def inject_context(self, command: str) -> str:
        """Add --context to kubectl commands if not already present."""
        if not command or not self.kubectl_context:
            return command
        if "kubectl " in command and "--context " not in command:
            command = command.replace("kubectl ", f"kubectl --context {self.kubectl_context} ", 1)
        return command

    def inject_all(self, command: str) -> str:
        """Inject both --profile and --context."""
        command = self.inject_profile(command)
        command = self.inject_context(command)
        return command

    def get_session(self):
        """Get boto3 session for the target account."""
        return _credentials.get_session(self.account_id, self.region)

    def get_fis_client(self):
        """Get FIS client for the target account."""
        return _credentials.get_fis_client(self.account_id, self.region)

    def get_cw_client(self):
        """Get CloudWatch client for the target account."""
        return _credentials.get_cw_client(self.account_id, self.region)

    # ------------------------------------------------------------------
    # Internal detection helpers
    # ------------------------------------------------------------------

    @classmethod
    def _detect_from_command(cls, cmd: str) -> tuple:
        """Extract account/context/profile hints from a command string."""
        if not cmd:
            return None, None, None

        # Try ARN extraction
        m = re.search(r'arn:aws:[^:]*:[^:]*:(\d{12}):', cmd)
        if m:
            acct_id = m.group(1)
            profile = _registry.get_profile(acct_id)
            acct = _registry.get(acct_id)
            ctx = acct.contexts[0] if acct and acct.contexts else None
            return ctx, acct_id, profile

        # Try service name extraction (deployment/xxx or -l app=xxx)
        dep_match = re.search(r'deployment/(\w+)', cmd)
        if not dep_match:
            dep_match = re.search(r'-l\s+app=(\w+)', cmd)
        if not dep_match:
            dep_match = re.search(r'selectorValue.*?app=(\w+)', cmd)
        if dep_match:
            svc = dep_match.group(1)
            loc = _topology.resolve(svc)
            if loc:
                acct = _registry.get(loc.account_id)
                return loc.context, loc.account_id, acct.profile if acct else None

        return None, None, None
