"""
AccountRegistry — account_id ↔ profile 매핑 레지스트리.

역할:
  - ~/.aws/config에서 account_id → profile 동적 파싱
  - config.yaml account_profiles (있으면) 오버라이드
  - config/*.env 파일에서 추가 매핑

Space별 account 역할(monitor/source)은 이 모듈의 책임이 아님.
→ Space 조회 시 Agent Space API에서 동적으로 결정.

사용:
  from account_registry import registry
  registry.init(config)
  profile = registry.get_profile("111111111111")  # → "member1-acc"
  all_accounts = registry.list_all()
"""
import configparser
import os
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RegisteredAccount:
    account_id: str
    profile: str = ""
    role_arn: str = ""
    region: str = "us-east-1"
    source: str = "aws_config"  # "aws_config" | "config_yaml" | "env_file"
    contexts: list = field(default_factory=list)
    clusters: list = field(default_factory=list)


class AccountRegistry:
    """Account ID ↔ local profile mapping registry.

    Does NOT determine primary/secondary — that's per-Space, resolved dynamically.
    """

    def __init__(self):
        self._accounts: dict[str, RegisteredAccount] = {}
        self._context_map: dict[str, str] = {}  # context_name → account_id
        self._lock = threading.Lock()
        self._initialized = False
        self._region = "us-east-1"

    def init(self, config: dict = None, config_dir: str = None):
        """Initialize registry from ~/.aws/config + optional config.yaml overrides."""
        config = config or {}
        if not config_dir:
            config_dir = os.path.join(os.path.dirname(__file__), "..", "..", "config")

        self._region = config.get("aws", {}).get("region", "us-east-1")

        with self._lock:
            self._accounts.clear()
            self._context_map.clear()

            # 1. ~/.aws/config (base — all SSO profiles)
            from_aws_config = self._load_from_aws_config()

            # 2. config/*.env files (supplement)
            from_env = self._load_from_env_files(config_dir)

            # 3. config.yaml account_profiles (override, if present)
            from_yaml = self._load_from_config_yaml(config)

            # Merge: aws_config < env < yaml
            for acct in from_aws_config:
                if acct.account_id:
                    self._accounts[acct.account_id] = acct

            for acct in from_env:
                if acct.account_id:
                    if acct.account_id in self._accounts:
                        self._accounts[acct.account_id].profile = acct.profile
                    else:
                        self._accounts[acct.account_id] = acct

            for acct in from_yaml:
                if acct.account_id:
                    if acct.account_id in self._accounts:
                        existing = self._accounts[acct.account_id]
                        existing.profile = acct.profile or existing.profile
                        existing.source = "config_yaml"
                    else:
                        self._accounts[acct.account_id] = acct

            self._initialized = True

        self._log_state()

    def get(self, account_id: str) -> Optional[RegisteredAccount]:
        with self._lock:
            return self._accounts.get(account_id)

    def get_profile(self, account_id: str) -> Optional[str]:
        acct = self.get(account_id)
        return acct.profile if acct else None

    def get_by_profile(self, profile: str) -> Optional[RegisteredAccount]:
        with self._lock:
            for acct in self._accounts.values():
                if acct.profile == profile:
                    return acct
        return None

    def list_all(self) -> list[RegisteredAccount]:
        with self._lock:
            return list(self._accounts.values())

    def list_profiles(self) -> list[str]:
        """Return all known profiles."""
        with self._lock:
            return [a.profile for a in self._accounts.values() if a.profile]

    def get_account_for_context(self, context: str) -> Optional[str]:
        with self._lock:
            return self._context_map.get(context)

    @property
    def region(self) -> str:
        return self._region

    # ------------------------------------------------------------------
    # Source loaders
    # ------------------------------------------------------------------

    def _load_from_aws_config(self) -> list[RegisteredAccount]:
        """Parse ~/.aws/config for profile → sso_account_id mapping."""
        accounts = []
        aws_config_path = os.path.expanduser("~/.aws/config")
        if not os.path.exists(aws_config_path):
            return accounts

        parser = configparser.ConfigParser()
        parser.read(aws_config_path)

        for section in parser.sections():
            profile_name = section.replace("profile ", "")
            account_id = parser.get(section, "sso_account_id", fallback="")
            region = parser.get(section, "region", fallback=self._region)
            if account_id:
                accounts.append(RegisteredAccount(
                    account_id=account_id,
                    profile=profile_name,
                    region=region,
                    source="aws_config",
                ))
        return accounts

    def _load_from_env_files(self, config_dir: str) -> list[RegisteredAccount]:
        """Load account→profile from config/*.env files."""
        accounts = []
        if not os.path.isdir(config_dir):
            return accounts
        for fname in os.listdir(config_dir):
            if not fname.endswith(".env"):
                continue
            acct_id, profile = "", ""
            try:
                with open(os.path.join(config_dir, fname)) as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("AWS_ACCOUNT_ID="):
                            acct_id = line.split("=", 1)[1].strip('"').strip("'")
                        elif line.startswith("AWS_PROFILE="):
                            profile = line.split("=", 1)[1].strip('"').strip("'")
            except IOError:
                continue
            if acct_id and profile:
                accounts.append(RegisteredAccount(
                    account_id=acct_id,
                    profile=profile,
                    source="env_file",
                ))
        return accounts

    def _load_from_config_yaml(self, config: dict) -> list[RegisteredAccount]:
        """Load explicit account_profiles from config.yaml (override)."""
        accounts = []
        profiles_map = config.get("aws", {}).get("account_profiles", {})
        for acct_id, profile in profiles_map.items():
            if acct_id and profile:
                accounts.append(RegisteredAccount(
                    account_id=acct_id,
                    profile=profile,
                    source="config_yaml",
                ))
        return accounts

    def _log_state(self):
        n = len(self._accounts)
        sources = set(a.source for a in self._accounts.values())
        print(f"[AccountRegistry] initialized: {n} accounts from {sources}", flush=True)
        for acct in self._accounts.values():
            print(f"  {acct.account_id} profile={acct.profile} (from {acct.source})", flush=True)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
registry = AccountRegistry()
