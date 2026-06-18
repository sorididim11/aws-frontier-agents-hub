"""
Verifier utility functions: config, command execution, preflight checks, Slack.
Extracted from verifier.py for modularity.
"""
import json
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone

import cluster_manager

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
NAMESPACE = os.environ.get("K8S_NAMESPACE", "dockercoins")

try:
    from config import get as _cfg
    AWS_REGION = _cfg("aws.region", AWS_REGION)
    NAMESPACE = _cfg("kubernetes.namespace", NAMESPACE)
    _AGENT_SPACE_ID = _cfg("agent.space_id", "")
    _WEBHOOK_SECRET = _cfg("agent.webhook_secret_name", "")
    _EVENTS_TABLE = _cfg("dynamodb.events_table", "")
    _RUNS_TABLE = _cfg("dynamodb.runs_table", "")
    _PROJECT_NAME = _cfg("project.name", os.environ.get("PROJECT_NAME", "frontier-agent-hub"))
except ImportError:
    def _cfg(path, default=None):
        return default
    _AGENT_SPACE_ID = os.environ.get("AGENT_SPACE_ID", "")
    _WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET_NAME", "")
    _EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "")
    _RUNS_TABLE = os.environ.get("RUNS_TABLE", "")
    _PROJECT_NAME = os.environ.get("PROJECT_NAME", "frontier-agent-hub")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

# Slack config (loaded lazily from Secrets Manager or env vars)
_slack_config = None
_slack_lock = threading.Lock()
SLACK_SECRET_NAME = os.environ.get(
    "SLACK_SECRET_NAME",
    _cfg("slack.secret_name", "devops-agent-test-slack-bot-token"),
)


def _get_slack_config():
    """Get Slack bot token and channel ID from Secrets Manager or env vars."""
    global _slack_config
    if _slack_config:
        return _slack_config
    with _slack_lock:
        if _slack_config:
            return _slack_config
        token = os.environ.get("SLACK_BOT_TOKEN", "")
        channel = os.environ.get("SLACK_CHANNEL_ID", "")
        if token and channel:
            _slack_config = {"bot_token": token, "channel_id": channel}
            return _slack_config
        try:
            import boto3
            sm = boto3.client("secretsmanager", region_name=AWS_REGION)
            resp = sm.get_secret_value(SecretId=SLACK_SECRET_NAME)
            _slack_config = json.loads(resp["SecretString"])
            return _slack_config
        except Exception as e:
            print(f"Slack config load failed: {e}")
            return None


def init_slack_config():
    """Pre-load Slack config at startup to avoid first-request delay."""
    try:
        cfg = _get_slack_config()
        if cfg:
            print(f"Slack config loaded: channel={cfg.get('channel_id','?')}")
        else:
            print("Slack config not available")
    except Exception as e:
        print(f"Slack config init error: {e}")


def _ensure_results_dir():
    os.makedirs(RESULTS_DIR, exist_ok=True)


_CMD_ENV = None

def _cmd_env():
    global _CMD_ENV
    if _CMD_ENV is None:
        env = {**os.environ, "AWS_PAGER": ""}
        path = env.get("PATH", "")
        for p in ("/opt/homebrew/bin", "/usr/local/bin"):
            if p not in path:
                path = p + ":" + path
        env["PATH"] = path
        env.setdefault("PROJECT_NAME", _PROJECT_NAME)
        env["AWS_REGION"] = AWS_REGION
        env["AWS_DEFAULT_REGION"] = AWS_REGION
        _CMD_ENV = env
    return _CMD_ENV


def _run_cmd(cmd, timeout=30, context=None):
    """Run a shell command and return (success, stdout, stderr).
    If context is given, inject --context directly instead of auto-discovery.
    """
    if context and "kubectl" in cmd:
        cmd = cmd.replace("kubectl ", f"kubectl --context {context} ", 1)
    else:
        cmd = cluster_manager.inject_context(cmd)
    try:
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True, timeout=timeout,
            env=_cmd_env(),
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out"
    except Exception as e:
        return False, "", str(e)


def _extract_target_service(trigger_cmd):
    """Extract target service name from trigger command."""
    m = re.search(r'selectorValue.*?app=([\w-]+)', trigger_cmd)
    if not m:
        m = re.search(r'deployment/([\w-]+)', trigger_cmd)
    if not m:
        m = re.search(r'-l\s+app=([\w-]+)', trigger_cmd)
    return m.group(1) if m else None


def _extract_namespace(trigger_cmd, default=None):
    """Extract namespace from -n/--namespace flag in command."""
    m = re.search(r'(?:-n|--namespace)\s+(\S+)', trigger_cmd)
    if m:
        val = m.group(1)
        if val.startswith("${"):
            return default
        return val
    return default


def _preflight_tool_available(tool_name):
    """Check if a CLI tool is available on PATH."""
    import shutil
    env_path = _cmd_env().get("PATH", "")
    path = shutil.which(tool_name, path=env_path)
    if path:
        return True, f"{tool_name}: {path}"
    return False, f"{tool_name} not found"


def _preflight_k8s_access(context=None, namespace="dockercoins"):
    """Check kubectl can reach the cluster."""
    ctx_flag = f"--context {context}" if context else ""
    cmd = f"kubectl {ctx_flag} get namespace {namespace} --no-headers 2>/dev/null"
    ok, stdout, stderr = _run_cmd(cmd, timeout=10)
    if ok:
        return True, f"K8s OK (context={context or 'default'}, ns={namespace})"
    return False, f"K8s 접근 실패: {stderr[:100] or 'no response'} (context={context or 'default'})"


def _preflight_aws_access(profile=None):
    """Check AWS credentials are valid."""
    import boto3
    try:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        sts = session.client("sts", region_name=AWS_REGION)
        identity = sts.get_caller_identity()
        account = identity.get("Account", "")
        return True, f"AWS OK (account={account}, profile={profile or 'default'})"
    except Exception as e:
        return False, f"AWS 인증 실패: {str(e)[:100]} (profile={profile or 'default'})"


def _preflight_target_ready(service, namespace, context=None):
    """Check target deployment has at least one Running pod."""
    cmd = (
        f"kubectl get pods -n {namespace} -l app={service}"
        f" -o jsonpath='{{.items[*].status.phase}}' 2>/dev/null"
    )
    ok, stdout, stderr = _run_cmd(cmd, timeout=10, context=context)
    if not ok or not stdout:
        return False, f"'{service}': pod 없음 (ns={namespace})"
    phases = stdout.split()
    running = sum(1 for p in phases if p == "Running")
    if running > 0:
        return True, f"'{service}': {running}/{len(phases)} Running"
    return False, f"'{service}': 0 Running (상태: {', '.join(phases)})"


def _preflight_network_policy_enforcement(context=None):
    """Check if NetworkPolicy enforcement is enabled in VPC CNI."""
    cmd = (
        "kubectl get configmap -n kube-system amazon-vpc-cni"
        " -o jsonpath='{.data.enable-network-policy-controller}' 2>/dev/null"
    )
    ok, stdout, stderr = _run_cmd(cmd, timeout=10, context=context)
    if not ok:
        return False, "VPC CNI ConfigMap 조회 실패 — NetworkPolicy 적용 불가"
    val = stdout.strip().strip("'\"")
    if val == "true":
        return True, "NetworkPolicy enforcement 활성 (VPC CNI)"
    return False, f"NetworkPolicy enforcement 비활성 (enable-network-policy-controller={val}). NetworkPolicy 트리거 사용 불가"


def _pre_flight_check(run, scenario):
    """Run pre-flight validation before trigger.
    Returns (all_ok, results) where results is list of {"check", "ok", "detail"}.
    """
    results = []
    trigger = scenario.get("trigger", {})
    trigger_cmd = trigger.get("command", "")
    verification_steps = scenario.get("verification", {}).get("steps", [])

    needs_kubectl = "kubectl" in trigger_cmd
    needs_aws = "aws " in trigger_cmd
    k8s_types = {"pod_logs", "pod_status", "kubectl_check"}
    aws_types = {"cw_alarm", "alarm_state", "metric_check", "log_pattern",
                 "lambda_logs", "xray_trace", "xray_latency", "fis_experiment",
                 "investigation_event", "agent_investigation"}
    for step in verification_steps:
        st = step.get("type", "")
        if st in k8s_types:
            needs_kubectl = True
        if st in aws_types:
            needs_aws = True

    if needs_kubectl:
        ok, detail = _preflight_tool_available("kubectl")
        results.append({"check": "kubectl", "ok": ok, "detail": detail})
        if not ok:
            return False, results

    if needs_aws:
        ok, detail = _preflight_aws_access(profile=run._scenario_profile)
        results.append({"check": "AWS 인증", "ok": ok, "detail": detail})

    trigger_ns = _extract_namespace(trigger_cmd, default=run.namespace)

    if needs_kubectl:
        ok, detail = _preflight_k8s_access(
            context=run._scenario_context, namespace=trigger_ns)
        results.append({"check": "K8s 접근", "ok": ok, "detail": detail})

    if needs_kubectl:
        target = scenario.get("target_service") or _extract_target_service(trigger_cmd)
        if target:
            ok, detail = _preflight_target_ready(
                target, trigger_ns, run._scenario_context)
            results.append({"check": "대상 서비스", "ok": ok, "detail": detail})

    if "NetworkPolicy" in trigger_cmd or "networkpolicy" in trigger_cmd.lower():
        ok, detail = _preflight_network_policy_enforcement(run._scenario_context)
        results.append({"check": "NetworkPolicy 적용 가능", "ok": ok, "detail": detail})

    all_ok = all(r["ok"] for r in results)
    return all_ok, results


def _agent_space_session():
    """Create boto3 session for App infrastructure (DDB, Secrets Manager, etc.)."""
    import boto3
    profile = _cfg("aws.profile", "") or os.environ.get("AWS_PROFILE", "")
    if profile:
        try:
            return boto3.Session(profile_name=profile, region_name=AWS_REGION)
        except Exception:
            pass
    return boto3.Session(region_name=AWS_REGION)


def _send_webhook(alarm_name, alarm_desc, space_id=None):
    """Send webhook to DevOps Agent and return incidentId."""
    import hashlib as _hashlib, hmac as _hmac, base64 as _b64, urllib.request as _urllib
    try:
        effective_space = space_id or _AGENT_SPACE_ID
        secret_id = _WEBHOOK_SECRET or (f"webhook-{effective_space}" if effective_space else "")
        if not secret_id:
            print("Webhook send failed: no webhook_secret_name and no agent_space_id configured")
            return None
        if space_id:
            import boto3
            from app_config import _profile_for_space
            profile = _profile_for_space(space_id)
            session = boto3.Session(profile_name=profile, region_name=AWS_REGION)
            sm = session.client("secretsmanager", region_name=AWS_REGION)
        else:
            sm = _agent_space_session().client("secretsmanager", region_name=AWS_REGION)
        creds = json.loads(sm.get_secret_value(SecretId=secret_id)["SecretString"])
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        iid = f"{alarm_name}-{ts.replace(':','-')}"
        payload = {
            "eventType": "incident", "incidentId": iid, "action": "created",
            "priority": "HIGH", "title": f"[CW Alarm] {alarm_name}: {alarm_desc}",
            "description": f"CloudWatch Alarm '{alarm_name}' triggered. {alarm_desc}.",
            "timestamp": ts, "service": "unknown",
            "data": {"metadata": {"region": AWS_REGION, "environment": _PROJECT_NAME, "alarmName": alarm_name}}
        }
        body = json.dumps(payload)
        sig = _b64.b64encode(_hmac.new(creds['webhookSecret'].encode(), f"{ts}:{body}".encode(), _hashlib.sha256).digest()).decode()
        req = _urllib.Request(creds['webhookUrl'], data=body.encode(),
            headers={'Content-Type': 'application/json', 'x-amzn-event-timestamp': ts, 'x-amzn-event-signature': sig}, method='POST')
        with _urllib.urlopen(req, timeout=15) as r:
            print(f"Webhook {r.status} for {alarm_name} incident_id={iid}")
        return iid
    except Exception as e:
        print(f"Webhook send failed: {e}")
        return None


def _find_task_by_incident_id(incident_id, space_id=None):
    """Find task_id by incident_id via Agent Space list_backlog_tasks API."""
    import boto3
    try:
        if not space_id:
            from app_config import _CFG
            space_id = _CFG.get("agent", {}).get("space_id", "") or _AGENT_SPACE_ID
        if not space_id:
            return None, None
        from app_config import _profile_for_space
        profile = _profile_for_space(space_id)
        session = boto3.Session(profile_name=profile, region_name=AWS_REGION)
        client = session.client("devops-agent", region_name=AWS_REGION)
        resp = client.list_backlog_tasks(
            agentSpaceId=space_id,
            filter={"taskType": ["INVESTIGATION"]},
            limit=20, order="DESC",
        )
        for t in resp.get("tasks", []):
            ref = t.get("reference", {})
            if ref.get("referenceId") == incident_id:
                return t.get("taskId", ""), t.get("status", "")
        return None, None
    except Exception as e:
        print(f"find_task failed: {e}")
        return None, None
