"""Simulator configuration with env-var override + YAML fallback."""

import os
import yaml
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class KubesharkConfig:
    url: str = "http://kubeshark-front.kubeshark.svc.cluster.local:8899"
    namespace: str = "kubeshark"

@dataclass
class ChaosMeshConfig:
    url: str = "http://chaos-dashboard.chaos-mesh.svc:2333"
    namespace: str = "chaos-mesh"
    runtime: str = "containerd"
    socket_path: str = "/run/containerd/containerd.sock"

@dataclass
class FISConfig:
    region: str = "us-east-1"
    log_group: str = "/aws/fis"

@dataclass
class ChatConfig:
    agent_space_id: str = ""
    region: str = "us-east-1"
    profile: str = ""
    timeout_per_question: int = 120

@dataclass
class SimulatorConfig:
    kubeshark: KubesharkConfig = field(default_factory=KubesharkConfig)
    chaos_mesh: ChaosMeshConfig = field(default_factory=ChaosMeshConfig)
    fis: FISConfig = field(default_factory=FISConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    templates_dir: str = ""
    output_dir: str = ""

    def __post_init__(self):
        base = Path(__file__).parent
        if not self.templates_dir:
            self.templates_dir = str(base / "templates")
        if not self.output_dir:
            self.output_dir = str(base / "scenarios")


def load_config(config_path: str = None) -> SimulatorConfig:
    cfg = SimulatorConfig()

    if config_path and os.path.exists(config_path):
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        if "kubeshark" in data:
            for k, v in data["kubeshark"].items():
                setattr(cfg.kubeshark, k, v)
        if "chaos_mesh" in data:
            for k, v in data["chaos_mesh"].items():
                setattr(cfg.chaos_mesh, k, v)
        if "fis" in data:
            for k, v in data["fis"].items():
                setattr(cfg.fis, k, v)
        if "templates_dir" in data:
            cfg.templates_dir = data["templates_dir"]
        if "output_dir" in data:
            cfg.output_dir = data["output_dir"]

        if "chat" in data:
            for k, v in data["chat"].items():
                setattr(cfg.chat, k, v)

    cfg.kubeshark.url = os.environ.get("KUBESHARK_URL", cfg.kubeshark.url)
    cfg.chaos_mesh.url = os.environ.get("CHAOS_MESH_URL", cfg.chaos_mesh.url)
    cfg.chaos_mesh.namespace = os.environ.get("CHAOS_MESH_NAMESPACE", cfg.chaos_mesh.namespace)
    cfg.fis.region = os.environ.get("AWS_REGION", cfg.fis.region)
    cfg.chat.agent_space_id = os.environ.get("AGENT_SPACE_ID", cfg.chat.agent_space_id)
    cfg.chat.region = os.environ.get("AWS_REGION", cfg.chat.region)
    cfg.chat.profile = os.environ.get("AWS_PROFILE", cfg.chat.profile)

    return cfg
