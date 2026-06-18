"""
AccountContext — immutable per-operation AWS/K8s routing context.

생성 시점에 account_id, profile, kubectl_context, region, namespace를 캡처.
이후 변경 불가. agent/tool에 매개변수로 전달되어 격리 보장.

사용:
  from account_context import AccountContext
  ctx = AccountContext(account_id="111", profile="prod", ...)
  session = ctx.boto_session()
  cmd = ctx.inject_all("kubectl get pods && aws sts get-caller-identity")
"""
import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AccountContext:
    """Immutable credential + routing context for a single operation.

    Thread-safe by design: frozen dataclass prevents mutation after creation.
    Each agent/tool receives its own instance — no shared mutable state.
    """

    account_id: str = ""
    profile: str = ""
    kubectl_context: str = ""
    region: str = "us-east-1"
    namespace: str = "dockercoins"

    def boto_session(self) -> "boto3.Session":
        """Create a boto3 Session scoped to this context."""
        import boto3
        kwargs = {}
        if self.profile:
            kwargs["profile_name"] = self.profile
        if self.region:
            kwargs["region_name"] = self.region
        try:
            return boto3.Session(**kwargs)
        except Exception:
            return boto3.Session(region_name=self.region)

    def get_client(self, service_name: str) -> "boto3.client":
        """Create a boto3 client for the given AWS service."""
        return self.boto_session().client(service_name, region_name=self.region)

    def inject_profile(self, command: str) -> str:
        """Add --profile to aws CLI commands if not already present."""
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
        """Inject both --profile and --context into a compound command."""
        parts = command.split("&&")
        result = []
        for p in parts:
            p = self.inject_profile(p)
            p = self.inject_context(p)
            result.append(p)
        return "&&".join(result)

    def with_override(self, **kwargs) -> "AccountContext":
        """Return a new context with specified fields overridden."""
        from dataclasses import asdict
        current = asdict(self)
        current.update({k: v for k, v in kwargs.items() if v is not None})
        return AccountContext(**current)

    @classmethod
    def empty(cls) -> "AccountContext":
        """Default empty context (uses env defaults)."""
        return cls(
            region=os.environ.get("AWS_REGION", "us-east-1"),
            namespace=os.environ.get("K8S_NAMESPACE", "dockercoins"),
        )
