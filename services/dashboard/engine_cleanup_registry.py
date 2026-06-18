"""
Engine Cleanup Registry: guaranteed resource teardown.

Tracks every resource created during scenario execution and ensures cleanup
regardless of outcome (success, failure, crash, timeout).

Usage:
    registry = CleanupRegistry(namespace="dockercoins", context=None)
    registry.register("kubectl", "deployment/test-imagepull-fail",
                      cleanup_cmd="kubectl delete deployment test-imagepull-fail -n dockercoins --ignore-not-found")
    ...
    registry.drain()  # runs all cleanup commands, removes entries

Design:
- Register-on-create: caller registers IMMEDIATELY after creating a resource
- drain() is idempotent — safe to call multiple times
- atexit hook catches Python crashes (belt + suspenders)
- Thread-safe: multiple steps can register concurrently
"""
import atexit
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum

from verifier_utils import _run_cmd

log = logging.getLogger(__name__)

# Global registry of active registries (for atexit hook)
_active_registries: list = []
_registries_lock = threading.Lock()


class ResourceType(str, Enum):
    KUBECTL = "kubectl"
    AWS_CLI = "aws_cli"
    FIS_TEMPLATE = "fis_template"
    FIS_EXPERIMENT = "fis_experiment"


@dataclass
class ResourceEntry:
    resource_type: str
    resource_id: str
    cleanup_cmd: str
    namespace: str = "dockercoins"
    registered_at: float = field(default_factory=time.time)
    cleaned: bool = False
    error: str | None = None


class CleanupRegistry:
    """Per-run registry that tracks created resources and guarantees cleanup."""

    def __init__(self, namespace: str = "dockercoins", context: str | None = None,
                 profile: str | None = None):
        self._entries: list[ResourceEntry] = []
        self._lock = threading.Lock()
        self._namespace = namespace
        self._context = context
        self._profile = profile
        self._drained = False

        with _registries_lock:
            _active_registries.append(self)

    def register(self, resource_type: str, resource_id: str,
                 cleanup_cmd: str | None = None):
        """Register a resource for guaranteed cleanup.

        If cleanup_cmd is not provided, it will be inferred from type + id.
        """
        if not cleanup_cmd:
            cleanup_cmd = self._infer_cleanup_cmd(resource_type, resource_id)

        entry = ResourceEntry(
            resource_type=resource_type,
            resource_id=resource_id,
            cleanup_cmd=cleanup_cmd,
            namespace=self._namespace,
        )
        with self._lock:
            self._entries.append(entry)
        log.debug(f"[cleanup] registered: {resource_type}/{resource_id}")

    def register_from_trigger(self, trigger_config: dict, trigger_output: str = ""):
        """Auto-detect resources created by a trigger command and register them."""
        command = trigger_config.get("command", "")
        if not command:
            return

        # Detect kubectl apply creating deployments/resources
        if "kubectl apply" in command:
            self._parse_kubectl_apply(command)

        # Detect kubectl scale (no new resource, but track for restore)
        if "kubectl scale" in command:
            self._parse_kubectl_scale(command)

        # Detect FIS experiment creation
        if "fis create-experiment-template" in command:
            self._parse_fis_creation(command, trigger_output)

        # Detect kubectl set resources (deployment modification)
        if "kubectl set resources" in command:
            self._parse_kubectl_set_resources(command)

        # Detect kubectl delete pod (transient — pod auto-recreates, no cleanup needed)
        # Detect kubectl exec (no resource creation)

    def drain(self) -> list[dict]:
        """Execute all cleanup commands. Idempotent — safe to call multiple times.

        Returns list of {resource_id, success, error} dicts.
        """
        if self._drained:
            return []

        results = []
        with self._lock:
            pending = [e for e in self._entries if not e.cleaned]
            self._drained = True

        # Execute in reverse order (LIFO — last created, first cleaned)
        for entry in reversed(pending):
            result = self._execute_cleanup(entry)
            results.append(result)

        # Deregister from global list
        with _registries_lock:
            try:
                _active_registries.remove(self)
            except ValueError:
                pass

        return results

    def force_cleanup_entry(self, resource_id: str) -> bool:
        """Force cleanup of a specific resource by ID."""
        with self._lock:
            for entry in self._entries:
                if entry.resource_id == resource_id and not entry.cleaned:
                    result = self._execute_cleanup(entry)
                    return result.get("success", False)
        return False

    @property
    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for e in self._entries if not e.cleaned)

    @property
    def entries(self) -> list[dict]:
        with self._lock:
            return [
                {"type": e.resource_type, "id": e.resource_id,
                 "cleaned": e.cleaned, "error": e.error}
                for e in self._entries
            ]

    # ── Internal ─────────────────────────────────────────────────────────────

    def _execute_cleanup(self, entry: ResourceEntry) -> dict:
        """Run a single cleanup command."""
        cmd = entry.cleanup_cmd
        if self._profile and "aws " in cmd and "--profile" not in cmd:
            cmd = cmd.replace("aws ", f"aws --profile {self._profile} ", 1)

        try:
            ok, stdout, stderr = _run_cmd(cmd, timeout=60, context=self._context)
            entry.cleaned = True
            if not ok:
                entry.error = (stderr or stdout or "unknown error")[:200]
                log.warning(f"[cleanup] failed {entry.resource_id}: {entry.error}")
            else:
                log.info(f"[cleanup] OK: {entry.resource_id}")
            return {"resource_id": entry.resource_id, "success": ok,
                    "error": entry.error}
        except Exception as e:
            entry.cleaned = True
            entry.error = str(e)[:200]
            log.error(f"[cleanup] exception {entry.resource_id}: {e}")
            return {"resource_id": entry.resource_id, "success": False,
                    "error": entry.error}

    def _infer_cleanup_cmd(self, resource_type: str, resource_id: str) -> str:
        """Infer cleanup command from resource type and ID."""
        ns = self._namespace
        if resource_type == ResourceType.KUBECTL:
            # resource_id format: "kind/name" e.g. "deployment/test-imagepull-fail"
            return f"kubectl delete {resource_id} -n {ns} --ignore-not-found"
        elif resource_type == ResourceType.FIS_TEMPLATE:
            region = self._get_region()
            return f"aws fis delete-experiment-template --id {resource_id} --region {region} --no-cli-pager 2>/dev/null"
        elif resource_type == ResourceType.FIS_EXPERIMENT:
            region = self._get_region()
            return f"aws fis stop-experiment --id {resource_id} --region {region} --no-cli-pager 2>/dev/null"
        elif resource_type == ResourceType.AWS_CLI:
            return resource_id  # For AWS_CLI, resource_id IS the cleanup command
        return f"echo 'unknown cleanup: {resource_type}/{resource_id}'"

    def _get_region(self) -> str:
        import os
        return os.environ.get("AWS_REGION", "us-east-1")

    # ── Trigger Parsers ──────────────────────────────────────────────────────

    def _parse_kubectl_apply(self, command: str):
        """Extract resource kind/name from kubectl apply heredoc or -f."""
        # Match inline YAML: kind: Deployment + metadata.name
        kind_match = re.search(r'kind:\s*(\w+)', command)
        name_match = re.search(r'name:\s*([\w-]+)', command)
        if kind_match and name_match:
            kind = kind_match.group(1).lower()
            name = name_match.group(1)
            ns = self._namespace
            # Extract namespace from YAML if present
            ns_match = re.search(r'namespace:\s*([\w-]+)', command)
            if ns_match:
                ns = ns_match.group(1)
            cleanup = f"kubectl delete {kind} {name} -n {ns} --ignore-not-found"
            self.register(ResourceType.KUBECTL, f"{kind}/{name}", cleanup)

    def _parse_kubectl_scale(self, command: str):
        """Track scale operations (restore is handled by scenario restore cmd)."""
        # kubectl scale deployment hasher -n dockercoins --replicas=0
        match = re.search(r'kubectl scale\s+(deployment/?\w+|\w+)\s+(\S+)?.*--replicas=(\d+)', command)
        if not match:
            match = re.search(r'kubectl scale\s+deployment\s+([\w-]+).*--replicas=(\d+)', command)
        # Scale operations don't create new resources — scenario restore handles them

    def _parse_fis_creation(self, command: str, trigger_output: str):
        """Extract FIS template and experiment IDs from trigger output."""
        # From trigger output: "FIS experiment: EXPxxxxx template: EXTxxxxx"
        tmpl_match = re.search(r'template[:\s]+(\w+)', trigger_output)
        exp_match = re.search(r'experiment[:\s]+(\w+)', trigger_output)

        if tmpl_match:
            tmpl_id = tmpl_match.group(1)
            self.register(ResourceType.FIS_TEMPLATE, tmpl_id)

        if exp_match:
            exp_id = exp_match.group(1)
            self.register(ResourceType.FIS_EXPERIMENT, exp_id)

        # Also parse tag name for broader cleanup
        tag_match = re.search(r"--tags\s+Name=([\w-]+)", command)
        if tag_match:
            tag_name = tag_match.group(1)
            region = self._get_region()
            cleanup = (
                f"for t in $(aws fis list-experiment-templates --region {region} "
                f"--no-cli-pager --query \"experimentTemplates[?tags.Name=='{tag_name}'].id\" "
                f"--output text 2>/dev/null); do "
                f"aws fis delete-experiment-template --id $t --region {region} "
                f"--no-cli-pager 2>/dev/null; done"
            )
            self.register(ResourceType.AWS_CLI, f"fis-tag:{tag_name}", cleanup)

    def _parse_kubectl_set_resources(self, command: str):
        """Track kubectl set resources (deployment modification)."""
        # kubectl set resources deployment/worker -n dockercoins --limits=memory=1Mi
        match = re.search(r'deployment[/\s]([\w-]+)', command)
        if match:
            deploy_name = match.group(1)
            # Restore to default limits
            cleanup = (
                f"kubectl set resources deployment/{deploy_name} -n {self._namespace} "
                f"--limits=memory=128Mi --requests=memory=64Mi && "
                f"kubectl rollout restart deployment/{deploy_name} -n {self._namespace}"
            )
            self.register(ResourceType.KUBECTL, f"resources/{deploy_name}", cleanup)


# ── Global atexit hook ───────────────────────────────────────────────────────

def _atexit_drain_all():
    """Safety net: drain all active registries on process exit."""
    with _registries_lock:
        remaining = list(_active_registries)
    for registry in remaining:
        try:
            if registry.pending_count > 0:
                log.warning(f"[cleanup] atexit draining {registry.pending_count} resources")
                registry.drain()
        except Exception as e:
            log.error(f"[cleanup] atexit error: {e}")


atexit.register(_atexit_drain_all)
