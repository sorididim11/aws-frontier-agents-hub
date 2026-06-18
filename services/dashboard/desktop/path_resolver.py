"""Path resolution for bundled (PyInstaller) and development modes."""
import os
import sys
import shutil

APP_NAME = "DevOpsAgent"


def is_bundled() -> bool:
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def get_bundle_dir() -> str:
    if is_bundled():
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_config_dir() -> str:
    if is_bundled():
        d = os.path.join(
            os.path.expanduser("~"), "Library", "Application Support", APP_NAME
        )
    else:
        d = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(d, exist_ok=True)
    return d


def get_config_path() -> str:
    return os.path.join(get_config_dir(), "config.yaml")


def ensure_config_exists():
    config_path = get_config_path()
    if not os.path.exists(config_path):
        example = os.path.join(get_bundle_dir(), "config.yaml.example")
        if os.path.exists(example):
            shutil.copy2(example, config_path)
    # Bundled mode: copy config into bundle dir so __file__-based lookups work
    if is_bundled() and os.path.exists(config_path):
        bundle_cfg = os.path.join(get_bundle_dir(), "config.yaml")
        shutil.copy2(config_path, bundle_cfg)


def get_sidecar_dir() -> str:
    return os.path.join(get_bundle_dir(), "expert_sidecar")


def get_node_binary() -> str:
    bundled = os.path.join(get_bundle_dir(), "node", "bin", "node")
    if os.path.exists(bundled):
        return bundled
    found = shutil.which("node")
    return found or "node"


def get_simulator_root() -> str:
    if is_bundled():
        return get_bundle_dir()
    return os.path.abspath(os.path.join(get_bundle_dir(), "..", ".."))


def setup_paths():
    bundle_dir = get_bundle_dir()
    sim_root = get_simulator_root()
    for p in [bundle_dir, sim_root]:
        if p not in sys.path:
            sys.path.insert(0, p)
