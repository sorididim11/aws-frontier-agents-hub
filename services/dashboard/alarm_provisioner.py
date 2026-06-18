"""
Alarm Provisioner — dynamic CloudWatch alarm creation/reuse/cleanup.

Scenarios can declare alarm_spec in verification steps. This module:
  1. Searches for an existing alarm matching the spec (reuse)
  2. Creates a new alarm if none found (put_metric_alarm)
  3. Cleans up simulator-managed alarms after run completes

Managed alarms are tagged with simulator:managed=true and prefixed with "sim-".
IaC-managed alarms are never touched.
"""
import hashlib
import logging
import time

import boto3

log = logging.getLogger(__name__)

MANAGED_TAG_KEY = "simulator:managed"
MANAGED_TAG_VALUE = "true"
NAME_PREFIX = "sim-"


def _get_cw_client(profile=None, region=None):
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    return session.client("cloudwatch", region_name=region or "us-east-1")


def _get_sns_topic_arn(account_id: str, region: str, project_name: str = "devops-agent-test") -> str:
    return f"arn:aws:sns:{region}:{account_id}:{project_name}-alarms"


def _generate_alarm_name(alarm_spec: dict) -> str:
    """Generate a deterministic alarm name from spec for deduplication."""
    key_parts = [
        alarm_spec.get("namespace", ""),
        alarm_spec.get("metric_name", ""),
        alarm_spec.get("statistic", ""),
        alarm_spec.get("comparison", ""),
        str(alarm_spec.get("threshold", "")),
        str(alarm_spec.get("period", "")),
    ]
    for dim in alarm_spec.get("dimensions", []):
        key_parts.append(f"{dim.get('Name', '')}={dim.get('Value', '')}")
    hash_suffix = hashlib.md5("||".join(key_parts).encode()).hexdigest()[:8]
    metric_short = alarm_spec.get("metric_name", "metric")[:20].lower().replace(" ", "-")
    return f"{NAME_PREFIX}{metric_short}-{hash_suffix}"


def _find_matching_alarm(alarm_spec: dict, profile=None, region=None):
    """Search for an existing alarm that matches the spec exactly."""
    cw = _get_cw_client(profile, region)
    target_metric = alarm_spec.get("metric_name", "")
    target_ns = alarm_spec.get("namespace", "")
    target_stat = alarm_spec.get("statistic", "")
    target_comp = alarm_spec.get("comparison", "")
    target_threshold = alarm_spec.get("threshold")
    target_period = alarm_spec.get("period")
    target_dims = sorted(
        [(d["Name"], d["Value"]) for d in alarm_spec.get("dimensions", [])],
        key=lambda x: x[0],
    )

    paginator = cw.get_paginator("describe_alarms")
    for page in paginator.paginate(MaxRecords=100):
        for alarm in page.get("MetricAlarms", []):
            if (
                alarm.get("MetricName") == target_metric
                and alarm.get("Namespace") == target_ns
                and alarm.get("Statistic") == target_stat
                and alarm.get("ComparisonOperator") == target_comp
                and alarm.get("Threshold") == target_threshold
                and alarm.get("Period") == target_period
            ):
                alarm_dims = sorted(
                    [(d["Name"], d["Value"]) for d in alarm.get("Dimensions", [])],
                    key=lambda x: x[0],
                )
                if alarm_dims == target_dims:
                    log.info("기존 알람 재사용: %s", alarm["AlarmName"])
                    return alarm
    return None


def ensure_alarm(
    alarm_spec: dict,
    account_id: str,
    region: str,
    profile: str = None,
    project_name: str = "devops-agent-test",
    app_tag_value: str = "",
    space_id: str = "",
) -> str:
    """Ensure an alarm matching alarm_spec exists. Returns alarm name.

    1. Search for existing alarm with same metric/conditions → reuse
    2. If not found → create with put_metric_alarm + managed tag + App/SpaceId tags
    """
    existing = _find_matching_alarm(alarm_spec, profile, region)
    if existing:
        return existing["AlarmName"]

    cw = _get_cw_client(profile, region)
    alarm_name = _generate_alarm_name(alarm_spec)
    sns_topic_arn = _get_sns_topic_arn(account_id, region, project_name)

    tags = [{"Key": MANAGED_TAG_KEY, "Value": MANAGED_TAG_VALUE}]
    if app_tag_value:
        tags.append({"Key": "App", "Value": app_tag_value})
    if space_id:
        tags.append({"Key": "SpaceId", "Value": space_id})

    params = {
        "AlarmName": alarm_name,
        "MetricName": alarm_spec["metric_name"],
        "Namespace": alarm_spec["namespace"],
        "Statistic": alarm_spec["statistic"],
        "ComparisonOperator": alarm_spec["comparison"],
        "Threshold": float(alarm_spec["threshold"]),
        "Period": int(alarm_spec["period"]),
        "EvaluationPeriods": int(alarm_spec.get("evaluation_periods", 1)),
        "AlarmActions": [sns_topic_arn],
        "OKActions": [sns_topic_arn],
        "Tags": tags,
        "TreatMissingData": "missing",
    }
    if alarm_spec.get("dimensions"):
        params["Dimensions"] = alarm_spec["dimensions"]

    cw.put_metric_alarm(**params)
    log.info("동적 알람 생성: %s (metric=%s, threshold=%s, app=%s, space=%s)",
             alarm_name, alarm_spec["metric_name"], alarm_spec["threshold"],
             app_tag_value, space_id)
    return alarm_name


def cleanup_managed_alarms(profile=None, region=None):
    """Delete all simulator-managed alarms (tagged with simulator:managed=true)."""
    cw = _get_cw_client(profile, region)
    to_delete = []

    paginator = cw.get_paginator("describe_alarms")
    for page in paginator.paginate(AlarmNamePrefix=NAME_PREFIX, MaxRecords=100):
        for alarm in page.get("MetricAlarms", []):
            try:
                tags_resp = cw.list_tags_for_resource(ResourceARN=alarm["AlarmArn"])
                tags = {t["Key"]: t["Value"] for t in tags_resp.get("Tags", [])}
                if tags.get(MANAGED_TAG_KEY) == MANAGED_TAG_VALUE:
                    to_delete.append(alarm["AlarmName"])
            except Exception as e:
                log.warning("알람 태그 조회 실패: %s — %s", alarm["AlarmName"], e)

    if to_delete:
        cw.delete_alarms(AlarmNames=to_delete)
        log.info("관리 알람 %d개 삭제: %s", len(to_delete), to_delete)
    return to_delete


def provision_alarm_steps(
    verification_steps: list,
    account_id: str,
    region: str,
    profile: str = None,
    project_name: str = "devops-agent-test",
    app_tag_value: str = "",
    space_id: str = "",
) -> list:
    """Process verification steps: resolve alarm_spec → alarm_name.

    Returns list of managed alarm names (for cleanup after run).
    """
    managed_alarms = []
    for step in verification_steps:
        if not isinstance(step, dict):
            continue
        if step.get("type") not in ("alarm_state", "cw_alarm"):
            continue
        alarm_spec = step.get("alarm_spec")
        if not alarm_spec:
            continue
        alarm_name = ensure_alarm(
            alarm_spec, account_id, region, profile, project_name,
            app_tag_value=app_tag_value, space_id=space_id,
        )
        step["alarm_name"] = alarm_name
        step["_alarm_managed"] = True
        managed_alarms.append(alarm_name)
        log.info("alarm_spec resolved: %s → %s", alarm_spec.get("metric_name"), alarm_name)
    return managed_alarms
