"""
Multi-cluster EKS management — compatibility layer.

Delegates to AccountRegistry + TopologyProvider for dynamic multi-account
discovery while maintaining the existing public API surface unchanged.

Public API (unchanged):
  - init()
  - is_multi_cluster()
  - get_clusters()
  - get_context_for_service(service)
  - get_profile_for_context(context)
  - get_account_for_context(context)
  - get_account_for_service(service)
  - get_service_map()
  - inject_context(cmd)
"""
import os
import re
import threading
import time

from account_registry import registry as _registry
from topology_provider import topology as _topology
from credential_resolver import credentials as _credentials
from account_resolver import resolver as _resolver



_DEFAULT_NAMESPACE = "dockercoins"  # overridden by config in init()
NAMESPACE = os.environ.get("K8S_NAMESPACE", _DEFAULT_NAMESPACE)
DISCOVERY_INTERVAL = 60

# Legacy globals kept for direct access by verifier_base._account_profile_map
_account_profile_map = {}  # {account_id: aws_profile_name}
_context_account_map = {}  # {context_name: account_id}
_service_map = {}
_service_map_lock = threading.Lock()


def _load_config():
    """Load config.yaml."""
    try:
        import yaml
        p = os.path.join(os.path.dirname(__file__), "config.yaml")
        if os.path.exists(p):
            with open(p) as f:
                return yaml.safe_load(f) or {}
    except ImportError:
        pass
    return {}


def init():
    """Initialize multi-account system from all sources."""
    global _account_profile_map, _context_account_map, _service_map, NAMESPACE

    config = _load_config()
    NAMESPACE = (config.get("kubernetes", {}).get("namespace")
                 or os.environ.get("K8S_NAMESPACE")
                 or _DEFAULT_NAMESPACE)

    space_id = config.get("agent", {}).get("space_id", "")
    if not space_id:
        space_id = os.environ.get("AGENT_SPACE_ID", "")
    config_dir = os.path.join(os.path.dirname(__file__), "..", "..", "config")

    # Initialize account registry (profile mapping) + resolver
    _registry.init(config=config, config_dir=config_dir)
    _resolver.init(registry=_registry)
    _topology.init(registry=_registry, namespace=NAMESPACE)
    _credentials.init(registry=_registry)

    # Sync legacy globals for backward compat
    _sync_legacy_globals()

    print(f"[cluster_manager] initialized via AccountRegistry + TopologyProvider", flush=True)


def _sync_legacy_globals():
    """Populate legacy module-level dicts from new system."""
    global _account_profile_map, _context_account_map, _service_map

    new_profile_map = {}
    new_context_map = {}
    for acct in _registry.list_all():
        if acct.profile:
            new_profile_map[acct.account_id] = acct.profile
        for ctx in acct.contexts:
            if ctx:
                new_context_map[ctx] = acct.account_id

    new_service_map = _topology.get_service_map()

    with _service_map_lock:
        _account_profile_map.update(new_profile_map)
        _context_account_map.update(new_context_map)
        _service_map = new_service_map


# ---------------------------------------------------------------------------
# Public API (backward compatible)
# ---------------------------------------------------------------------------

def is_multi_cluster():
    return len(_registry.list_all()) > 1


def get_clusters():
    result = []
    for acct in _registry.list_all():
        for cluster in acct.clusters:
            result.append({
                "name": cluster.get("context", ""),
                "account_id": acct.account_id,
                "profile": acct.profile,
                "region": acct.region,
                "services": cluster.get("services", []),
            })
    return result


def get_context_for_service(service_name):
    """Get kubectl context for a service."""
    if not is_multi_cluster():
        return None
    loc = _topology.resolve(service_name)
    if loc:
        return loc.context
    # Fallback to legacy map
    with _service_map_lock:
        return _service_map.get(service_name)


def get_service_map():
    return _topology.get_service_map()


def get_service_locations():
    locations = {}
    for name, loc in _topology.get_all_locations().items():
        locations.setdefault(name, []).append(loc.context)
    return locations


def get_contexts_for_service(service_name):
    return get_service_locations().get(service_name, [])


def get_account_for_context(context_name):
    """Get account_id for a kubectl context (handles both ARN and alias)."""
    if not context_name:
        return None
    acct_id = _registry.get_account_for_context(context_name)
    if acct_id:
        return acct_id
    # Fallback: ARN regex
    m = re.search(r':(\d{12}):', context_name)
    if m:
        return m.group(1)
    return None


def get_profile_for_context(context_name):
    """Get AWS profile for a kubectl context."""
    acct_id = get_account_for_context(context_name)
    if acct_id:
        return _registry.get_profile(acct_id)
    return None


def get_account_for_service(service_name):
    """Get account_id for a service name."""
    loc = _topology.resolve(service_name)
    if loc:
        return loc.account_id
    ctx = get_context_for_service(service_name)
    return get_account_for_context(ctx)


def get_profile_for_service(service_name):
    """Get AWS profile for a service name."""
    acct_id = get_account_for_service(service_name)
    if acct_id:
        return _registry.get_profile(acct_id)
    return None


# Legacy internal — still used by verifier_base
def _get_profile_for_account(account_id):
    return _registry.get_profile(account_id) or _account_profile_map.get(account_id)


# ---------------------------------------------------------------------------
# Command injection (legacy compat for inject_context callers)
# ---------------------------------------------------------------------------

def _extract_target(cmd_fragment):
    dep_match = re.search(r'deployment/(\S+)', cmd_fragment)
    if dep_match:
        return dep_match.group(1)
    label_match = re.search(r'-l\s+app=(\S+)', cmd_fragment)
    if label_match:
        return label_match.group(1)
    return None


def _inject_single(single_cmd):
    if "kubectl" not in single_cmd:
        return single_cmd
    target = _extract_target(single_cmd)
    if not target:
        return single_cmd
    ctx = get_context_for_service(target)
    if not ctx:
        return single_cmd
    return single_cmd.replace("kubectl ", f"kubectl --context {ctx} ", 1)


def _detect_profile_from_cmd(full_cmd):
    """Detect AWS profile from any ARN in the full command chain."""
    m = re.search(r'arn:aws:[^:]*:[^:]*:(\d{12}):', full_cmd)
    if m:
        return _registry.get_profile(m.group(1))
    return None


def inject_context(cmd):
    """Inject --context and --profile into a command string."""
    profile = _detect_profile_from_cmd(cmd)
    parts = cmd.split("&&")
    result = []
    for p in parts:
        if is_multi_cluster():
            p = _inject_single(p)
        if profile and "aws " in p and "--profile " not in p:
            p = p.replace("aws ", f"aws --profile {profile} ", 1)
        result.append(p)
    return " && ".join(result)


# ---------------------------------------------------------------------------
# Discovery (delegates to TopologyProvider)
# ---------------------------------------------------------------------------

def discover_services():
    """Trigger service discovery refresh."""
    return _topology.discover()


def set_expected_deployments(services: set):
    pass  # No-op: TopologyProvider handles all deployments
