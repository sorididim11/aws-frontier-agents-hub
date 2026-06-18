"""
TopologyProvider — 서비스→계정/클러스터 동적 매핑.

발견 방법:
  1. config.yaml static services (highest priority override)
  2. kubectl get deployments per registered context
  3. (future) Agent Space tagged resources

사용:
  from topology_provider import topology
  topology.init(registry)
  loc = topology.resolve("hasher")  # → ServiceLocation
"""
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

from account_registry import AccountRegistry, RegisteredAccount

KUBECTL = shutil.which("kubectl") or "/opt/homebrew/bin/kubectl"
DEFAULT_NAMESPACE = os.environ.get("K8S_NAMESPACE", "dockercoins")
DISCOVERY_INTERVAL = 60


@dataclass
class ServiceLocation:
    service_name: str
    account_id: str
    context: str
    namespace: str
    cluster_label: str = ""
    replicas: int = 0


class TopologyProvider:
    """Maps services to their account/cluster locations via dynamic discovery."""

    def __init__(self):
        self._registry: Optional[AccountRegistry] = None
        self._namespace = DEFAULT_NAMESPACE
        self._locations: dict[str, ServiceLocation] = {}
        self._lock = threading.Lock()
        self._last_discovery = 0.0
        self._static_overrides: dict[str, ServiceLocation] = {}

    def init(self, registry: AccountRegistry, namespace: str = None):
        """Initialize with account registry and run first discovery."""
        self._registry = registry
        self._namespace = namespace or DEFAULT_NAMESPACE
        self._load_static_overrides()
        self.discover()

    def discover(self, force: bool = False) -> dict[str, ServiceLocation]:
        """Run full service discovery across all registered clusters.

        Routing policy (config.yaml clusters.services) takes priority over
        kubectl discovery when the same service exists in multiple clusters.
        """
        if not self._registry:
            return {}

        discovered = {}

        # kubectl-based discovery per account/context
        # Primary first — setdefault keeps first-discovered, so primary wins ties
        accounts = self._registry.list_all()
        for acct in accounts:
            for cluster in acct.clusters:
                ctx = cluster.get("context", "")
                if not ctx:
                    continue
                label = cluster.get("label", "")
                svc_list = self._discover_kubectl(ctx, acct.account_id, label)
                for loc in svc_list:
                    discovered.setdefault(loc.service_name, loc)

        # Routing policy overrides (highest priority — determines canonical owner
        # when the same service is deployed in multiple clusters)
        for name, loc in self._static_overrides.items():
            discovered[name] = loc

        with self._lock:
            self._locations = discovered
            self._last_discovery = time.time()

        return discovered

    def resolve(self, service_name: str) -> Optional[ServiceLocation]:
        """Resolve full location for a service. Triggers background refresh if stale."""
        self._maybe_refresh()
        with self._lock:
            return self._locations.get(service_name)

    def resolve_account(self, service_name: str) -> Optional[str]:
        loc = self.resolve(service_name)
        return loc.account_id if loc else None

    def resolve_context(self, service_name: str) -> Optional[str]:
        loc = self.resolve(service_name)
        return loc.context if loc else None

    def resolve_profile(self, service_name: str) -> Optional[str]:
        loc = self.resolve(service_name)
        if not loc:
            return None
        acct = self._registry.get(loc.account_id)
        return acct.profile if acct else None

    def get_services_in_account(self, account_id: str) -> list[str]:
        with self._lock:
            return [loc.service_name for loc in self._locations.values()
                    if loc.account_id == account_id]

    def get_all_locations(self) -> dict[str, ServiceLocation]:
        with self._lock:
            return dict(self._locations)

    def get_service_map(self) -> dict[str, str]:
        """Legacy compat: {service_name: context}."""
        with self._lock:
            return {name: loc.context for name, loc in self._locations.items()}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_static_overrides(self):
        """Load static service→account mappings from config.yaml clusters.services."""
        if not self._registry:
            return
        for acct in self._registry.list_all():
            for cluster in acct.clusters:
                ctx = cluster.get("context", "")
                label = cluster.get("label", "")
                for svc in cluster.get("services", []):
                    self._static_overrides[svc] = ServiceLocation(
                        service_name=svc,
                        account_id=acct.account_id,
                        context=ctx,
                        namespace=self._namespace,
                        cluster_label=label,
                    )

    def _discover_kubectl(self, context: str, account_id: str, label: str) -> list[ServiceLocation]:
        """Discover running deployments in a cluster via kubectl."""
        try:
            result = subprocess.run(
                [KUBECTL, "get", "deployments", "-n", self._namespace,
                 "-o", "jsonpath={range .items[*]}{.metadata.name}={.spec.replicas} {end}",
                 "--context", context],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return []

            locations = []
            for token in result.stdout.strip().split():
                if not token:
                    continue
                parts = token.split("=", 1)
                name = parts[0]
                replicas = int(parts[1]) if len(parts) > 1 else 0
                if replicas > 0:
                    locations.append(ServiceLocation(
                        service_name=name,
                        account_id=account_id,
                        context=context,
                        namespace=self._namespace,
                        cluster_label=label,
                        replicas=replicas,
                    ))
            return locations
        except Exception as e:
            print(f"[TopologyProvider] kubectl discovery failed for {context}: {e}", flush=True)
            return []

    def _maybe_refresh(self):
        """Trigger background refresh if stale."""
        if time.time() - self._last_discovery > DISCOVERY_INTERVAL:
            threading.Thread(target=self.discover, daemon=True).start()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
topology = TopologyProvider()
