"""
CredentialResolver — 계정→세션/프로필 해결 + 캐싱.

role_arn이 있으면 STS AssumeRole, 없으면 profile 기반 boto3 Session.
55분 TTL 세션 캐싱.

사용:
  from credential_resolver import credentials
  credentials.init(registry)
  session = credentials.get_session("222222222222")
  fis = credentials.get_fis_client("222222222222")
"""
import os
import threading
import time
from typing import Optional

from account_registry import AccountRegistry


AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
CACHE_TTL = 3300  # 55 minutes (refresh 5 min before 1h expiry)


class CredentialResolver:
    """Resolves AWS credentials for a given account."""

    def __init__(self):
        self._registry: Optional[AccountRegistry] = None
        self._session_cache: dict[str, tuple] = {}  # account_id → (session, expiry_ts)
        self._lock = threading.Lock()

    def init(self, registry: AccountRegistry):
        self._registry = registry

    def get_session(self, account_id: str, region: str = None) -> "boto3.Session":
        """Get boto3 Session for an account. Caches AssumeRole sessions."""
        import boto3
        acct = self._registry.get(account_id) if self._registry else None
        if not acct:
            return boto3.Session(region_name=region or AWS_REGION)

        # Check cache
        with self._lock:
            if account_id in self._session_cache:
                session, expiry = self._session_cache[account_id]
                if time.time() < expiry:
                    return session

        # Create new session
        # Prefer profile (local SSO credential) over role_arn.
        # role_arn from Agent Space is the DevOps Agent service role (trusted by
        # aidevops.amazonaws.com only) — not assumable by the dashboard user.
        # Only use AssumeRole when no profile and role is explicitly assumable.
        if acct.profile:
            try:
                session = boto3.Session(profile_name=acct.profile, region_name=region or acct.region)
            except Exception as e:
                raise RuntimeError(
                    f"CredentialResolver: profile '{acct.profile}' failed for account {account_id}: {e}"
                ) from e
        elif acct.role_arn and self._is_assumable_role(acct.role_arn):
            session = self._assume_role(acct.role_arn, region or acct.region)
        else:
            session = boto3.Session(region_name=region or acct.region)

        # Cache
        with self._lock:
            self._session_cache[account_id] = (session, time.time() + CACHE_TTL)

        return session

    def get_profile(self, account_id: str) -> Optional[str]:
        """Get local AWS CLI profile name for an account."""
        if not self._registry:
            return None
        acct = self._registry.get(account_id)
        return acct.profile if acct else None

    def get_fis_client(self, account_id: str, region: str = None):
        """Get FIS client authenticated to the target account."""
        session = self.get_session(account_id, region)
        return session.client("fis", region_name=region or AWS_REGION)

    def get_cw_client(self, account_id: str, region: str = None):
        """Get CloudWatch client for the target account."""
        session = self.get_session(account_id, region)
        return session.client("cloudwatch", region_name=region or AWS_REGION)

    def list_accounts(self):
        """Return all registered accounts."""
        if not self._registry:
            return []
        return self._registry.list_all()

    def invalidate(self, account_id: str):
        with self._lock:
            self._session_cache.pop(account_id, None)

    def invalidate_all(self):
        with self._lock:
            self._session_cache.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _is_assumable_role(self, role_arn: str) -> bool:
        """Check if the role can be assumed by the dashboard.

        Only roles explicitly registered with assumable=True in config are allowed.
        Agent Space roles (source=agent_space without profile) are service-principal
        roles trusted only by managed services, not by local CLI sessions.
        """
        if not self._registry:
            return False
        acct_id = role_arn.split(":")[4] if len(role_arn.split(":")) > 4 else ""
        acct = self._registry.get(acct_id) if acct_id else None
        if not acct:
            return False
        if acct.profile:
            return False
        if acct.source == "agent_space":
            return False
        return True

    def _assume_role(self, role_arn: str, region: str = None) -> "boto3.Session":
        """STS AssumeRole for cross-account access."""
        import boto3
        primary = self._primary_session()
        sts = primary.client("sts")
        account_id = role_arn.split(":")[4] if len(role_arn.split(":")) > 4 else "unknown"
        resp = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName=f"devops-simulator-{account_id}",
            DurationSeconds=3600,
        )
        creds = resp["Credentials"]
        return boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=region or AWS_REGION,
        )

    def _primary_session(self) -> "boto3.Session":
        """Get default session (used as STS caller for AssumeRole)."""
        import boto3
        # Use app default profile from config
        default_profile = os.environ.get("AWS_PROFILE", "")
        if default_profile:
            try:
                return boto3.Session(profile_name=default_profile, region_name=AWS_REGION)
            except Exception:
                pass
        return boto3.Session(region_name=AWS_REGION)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
credentials = CredentialResolver()
