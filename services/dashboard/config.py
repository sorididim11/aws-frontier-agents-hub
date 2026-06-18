"""Load simulator configuration from config.yaml or environment variables.

Environment variables take precedence over config.yaml values.
Mapping:
    AGENT_SPACE_ID        → agent.space_id
    WEBHOOK_SECRET_NAME   → agent.webhook_secret_name
    EKS_CLUSTER_NAME      → kubernetes.cluster_name
    EVENTS_TABLE          → dynamodb.events_table
    RUNS_TABLE            → dynamodb.runs_table
    SLACK_SECRET_NAME     → slack.secret_name
    ALARM_PREFIX          → alarm.prefix
    WEBHOOK_FUNCTION_NAME → lambda.webhook_function_name
    PROJECT_NAME          → project.name
"""
import os
import yaml

_config = None

# Mapping: env var name → config.yaml dot-path
_ENV_TO_PATH = {
    "AGENT_SPACE_ID": "agent.space_id",
    "WEBHOOK_SECRET_NAME": "agent.webhook_secret_name",
    "EKS_CLUSTER_NAME": "kubernetes.cluster_name",
    "EVENTS_TABLE": "dynamodb.events_table",
    "RUNS_TABLE": "dynamodb.runs_table",
    "SLACK_SECRET_NAME": "slack.secret_name",
    "ALARM_PREFIX": "alarm.prefix",
    "WEBHOOK_FUNCTION_NAME": "lambda.webhook_function_name",
    "PROJECT_NAME": "project.name",
}

# Reverse mapping: config path → env var name
_PATH_TO_ENV = {v: k for k, v in _ENV_TO_PATH.items()}


def load_config():
    global _config
    if _config:
        return _config
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    if os.path.exists(config_path):
        with open(config_path) as f:
            _config = yaml.safe_load(f)
    else:
        _config = {}
    return _config


def get(path, default=None):
    """Get config value by dot-separated path. e.g. get('agent.space_id')

    Resolution order:
      1. Environment variable (via _PATH_TO_ENV mapping)
      2. config.yaml value
      3. default
    """
    # 1. Check env var override
    env_key = _PATH_TO_ENV.get(path)
    if env_key:
        env_val = os.environ.get(env_key)
        if env_val is not None:
            return env_val

    # 2. Check config.yaml
    cfg = load_config()
    keys = path.split(".")
    val = cfg
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
    return val if val is not None else default


# Convenience
AGENT_SPACE_ID = lambda: get("agent.space_id", os.environ.get("AGENT_SPACE_ID", ""))
AWS_REGION = lambda: get("aws.region", os.environ.get("AWS_REGION", "us-east-1"))
NAMESPACE = lambda: get("kubernetes.namespace", "dockercoins")
WEBHOOK_SECRET = lambda: get("agent.webhook_secret_name", "")
EVENTS_TABLE = lambda: get("dynamodb.events_table", "")
RUNS_TABLE = lambda: get("dynamodb.runs_table", "")
DEFAULT_MODEL = lambda: get("bedrock.default_model", "us.anthropic.claude-opus-4-6-v1")
