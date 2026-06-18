"""
AccountResolver — Space별 account 역할 동적 조회.

Space마다 account 역할(monitor/source)이 다를 수 있음.
전역 "primary"라는 개념 없음 — Space ID를 받아서 그때그때 resolve.

사용:
  from account_resolver import resolver
  resolver.init(registry)

  # Space의 accounts 조회
  accounts = resolver.get_space_accounts(space_id)
  monitor = resolver.get_monitor_account(space_id)

  # account → context (profile 기반)
  ctx = resolver.context_for_account(account_id)
"""
import ast
import json
import os
import threading
from dataclasses import dataclass
from typing import Optional

from account_context import AccountContext
from account_registry import AccountRegistry


@dataclass
class SpaceAccount:
    """Space 내 하나의 account 역할."""
    account_id: str
    role: str  # "monitor" | "source"
    role_arn: str = ""
    profile: str = ""


class AccountResolver:
    """Per-space account resolution. No global primary."""

    def __init__(self):
        self._registry: Optional[AccountRegistry] = None
        self._region = "us-east-1"
        self._space_cache: dict[str, list[SpaceAccount]] = {}
        self._cache_lock = threading.Lock()

    def init(self, registry: AccountRegistry):
        self._registry = registry
        self._region = registry.region

    @property
    def initialized(self) -> bool:
        return self._registry is not None

    def get_space_accounts(self, space_id: str) -> list[SpaceAccount]:
        """Get accounts and their roles for a Space. Cached after first call."""
        if not space_id:
            return []

        with self._cache_lock:
            if space_id in self._space_cache:
                return self._space_cache[space_id]

        accounts = self._discover_space_accounts(space_id)

        with self._cache_lock:
            self._space_cache[space_id] = accounts
        return accounts

    def get_monitor_account(self, space_id: str) -> Optional[SpaceAccount]:
        """Get the monitor (API-callable) account for a Space."""
        for acct in self.get_space_accounts(space_id):
            if acct.role == "monitor":
                return acct
        return None

    def get_source_accounts(self, space_id: str) -> list[SpaceAccount]:
        """Get source accounts for a Space."""
        return [a for a in self.get_space_accounts(space_id) if a.role == "source"]

    def get_monitor_profile(self, space_id: str) -> Optional[str]:
        """Shortcut: get profile for the monitor account."""
        mon = self.get_monitor_account(space_id)
        return mon.profile if mon else None

    def context_for_account(self, account_id: str, namespace: str = "") -> AccountContext:
        """Build AccountContext for an account_id using registry profile."""
        ns = namespace or os.environ.get("K8S_NAMESPACE", "dockercoins")
        profile = self._registry.get_profile(account_id) if self._registry else ""
        return AccountContext(
            account_id=account_id,
            profile=profile or "",
            region=self._region,
            namespace=ns,
        )

    def context_for_space_monitor(self, space_id: str, namespace: str = "") -> AccountContext:
        """Build AccountContext for a Space's monitor account."""
        mon = self.get_monitor_account(space_id)
        if mon:
            return self.context_for_account(mon.account_id, namespace)
        return AccountContext.empty()

    def invalidate_cache(self, space_id: str = None):
        """Clear cached space accounts."""
        with self._cache_lock:
            if space_id:
                self._space_cache.pop(space_id, None)
            else:
                self._space_cache.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _discover_space_accounts(self, space_id: str) -> list[SpaceAccount]:
        """DDB space_metadata.aws_config에서 account 역할 조회."""
        from app_config import _boto_session, RUNS_TABLE
        if not RUNS_TABLE:
            return []

        try:
            session = _boto_session()
            tbl = session.resource("dynamodb").Table(RUNS_TABLE)
            resp = tbl.get_item(
                Key={"run_id": f"space-meta-{space_id}", "record_type": "space_metadata"})
            item = resp.get("Item")
            if not item:
                return []
        except Exception:
            return []

        accounts = []
        aws_config = item.get("aws_config") or {}

        # monitor: aws_config.aws
        aws_mon = aws_config.get("aws")
        if aws_mon:
            acct_id = aws_mon.get("account_id", "")
            profile = item.get("profile") or ""
            if not profile and self._registry:
                profile = self._registry.get_profile(acct_id) or ""
            accounts.append(SpaceAccount(
                account_id=acct_id,
                role="monitor",
                role_arn=aws_mon.get("role_arn", ""),
                profile=profile,
            ))

        # source: aws_config.sourceAws
        aws_src = aws_config.get("sourceAws")
        if aws_src:
            acct_id = aws_src.get("account_id", "")
            profile = self._registry.get_profile(acct_id) if self._registry else ""
            accounts.append(SpaceAccount(
                account_id=acct_id,
                role="source",
                role_arn=aws_src.get("role_arn", ""),
                profile=profile or "",
            ))

        return accounts


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
resolver = AccountResolver()
