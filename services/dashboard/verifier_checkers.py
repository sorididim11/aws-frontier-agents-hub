"""
Verification checker functions and error classification.
Each _check_* function takes a config dict and returns (ok: bool, detail: str).
Extracted from verifier.py for modularity.
"""
import json
import os
import re
import time

from verifier_utils import (
    AWS_REGION, NAMESPACE, _cfg,
    _run_cmd, _cmd_env, _get_slack_config,
    _agent_space_session, _send_webhook, _find_task_by_incident_id,
    _AGENT_SPACE_ID,
)
import cluster_manager


def _check_pod_logs(config):
    """Check pod logs for a pattern match."""
    pod = config.get("pod", "")
    pattern = config.get("pattern", "")
    tail = config.get("tail", 50)
    ctx = cluster_manager.get_context_for_service(pod) if cluster_manager.is_multi_cluster() else None
    ns = config.get("_namespace", NAMESPACE)
    cmd = f"kubectl logs -n {ns} -l app={pod} --tail={tail} 2>/dev/null"
    ok, stdout, _ = _run_cmd(cmd, context=ctx)
    if not ok or not stdout:
        return False, f"로그 없음 (pod={pod})"
    matches = re.findall(pattern, stdout, re.IGNORECASE)
    if matches:
        sample = matches[0][:100] if matches else ""
        return True, f"패턴 매칭 {len(matches)}건 (pod={pod}, pattern='{pattern}', sample='{sample}')"
    return False, f"패턴 미발견 (pod={pod}, pattern='{pattern}', lines={len(stdout.splitlines())})"


def _check_xray_trace(config):
    """Check X-Ray for traces matching a filter expression (boto3)."""
    import boto3
    filter_expr = config.get("filter", "")
    minutes = config.get("minutes", 10)
    now = int(time.time())
    start = now - (minutes * 60)
    try:
        client = boto3.client("xray", region_name=AWS_REGION)
        resp = client.get_trace_summaries(
            StartTime=start, EndTime=now,
            FilterExpression=filter_expr,
            TimeRangeType="TraceId",
        )
        count = len(resp.get("TraceSummaries", []))
        if count > 0:
            return True, f"Trace {count}건 (filter='{filter_expr}', range={minutes}min)"
        return False, f"Trace 없음 (filter='{filter_expr}', range={minutes}min)"
    except Exception as e:
        return False, f"X-Ray 조회 실패: {e}"


def _check_alarm_fired_since(alarm_name: str, since_ts: str, profile=None) -> bool:
    """Check if alarm transitioned to ALARM state since given timestamp (Tier 1)."""
    import boto3
    from datetime import datetime, timezone
    try:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        client = session.client("cloudwatch", region_name=AWS_REGION)
        if isinstance(since_ts, str):
            start_date = datetime.fromisoformat(since_ts.replace("Z", "+00:00"))
        else:
            start_date = since_ts
        resp = client.describe_alarm_history(
            AlarmName=alarm_name,
            HistoryItemType="StateUpdate",
            StartDate=start_date,
            MaxRecords=20,
        )
        for item in resp.get("AlarmHistoryItems", []):
            summary = item.get("HistorySummary", "")
            if "to ALARM" in summary or "newState.*ALARM" in summary:
                return True
    except Exception:
        pass
    return False


def _check_investigation_exists_for_alarm(alarm_name: str, since_ts: str, profile=None, incident_id: str = None) -> bool:
    """Check if a DevOps Agent investigation task exists for this alarm (Tier 2).

    Strategy:
    1. If incident_id available → exact DynamoDB GSI query (fast)
    2. Fallback → list_backlog_tasks with createdAfter filter, match alarm_name in title
    """
    import boto3
    from datetime import datetime, timezone

    # Strategy 1: exact incident_id lookup
    if incident_id:
        try:
            session = _agent_space_session()
            table = session.resource("dynamodb", region_name=AWS_REGION).Table(
                _cfg("dynamodb.events_table", os.environ.get("EVENTS_TABLE", ""))
            )
            resp = table.query(
                IndexName="reference-id-index",
                KeyConditionExpression=boto3.dynamodb.conditions.Key("reference_id").eq(incident_id),
                ScanIndexForward=False,
                Limit=5,
            )
            for item in resp.get("Items", []):
                received = item.get("received_at", "")
                if received >= since_ts:
                    return True
        except Exception:
            pass

    # Strategy 2: list_backlog_tasks — Agent가 독립적으로 조사 시작한 경우도 감지
    try:
        client = _devops_agent_client()
        since_dt = datetime.fromtimestamp(float(since_ts), tz=timezone.utc) if since_ts else None
        if not since_dt:
            return False
        resp = client.list_backlog_tasks(
            agentSpaceId=_AGENT_SPACE_ID,
            limit=10,
            order="DESC",
            filter={"createdAfter": since_dt},
        )
        for task in resp.get("tasks", []):
            title = str(task.get("title", ""))
            if alarm_name and alarm_name in title:
                return True
    except Exception:
        pass
    return False


def _check_cw_alarm(config):
    """Check CloudWatch alarm state — 3-Tier verification.

    Tier 1: describe_alarm_history (run 시작 이후 ALARM 전환 이력)
    Tier 2: investigation-events DynamoDB (Agent 태스크 존재 = 발화 증거)
    Tier 3: describe_alarms 현재 상태 (기존 polling)
    """
    import boto3
    alarm_name = config.get("alarm", "")
    expected = config.get("expected", "ALARM")
    profile = config.get("_scenario_profile")
    run_started_at = config.get("_run_started_at")

    # Tier 1: 이력 기반 확인 (알람이 잠깐 울리고 복구된 경우도 잡음)
    if expected == "ALARM" and run_started_at:
        if _check_alarm_fired_since(alarm_name, run_started_at, profile):
            return True, f"알람={alarm_name} 이력 확인 (Tier 1: alarm history)"

    # Tier 2: DynamoDB investigation events (Agent가 이 알람으로 조사 시작 = 발화 증거)
    if expected == "ALARM" and run_started_at:
        run_obj = config.get("_run_obj")
        incident_id = getattr(run_obj, "_incident_id", None) if run_obj else None
        if _check_investigation_exists_for_alarm(alarm_name, run_started_at, profile, incident_id=incident_id):
            return True, f"알람={alarm_name} Agent 조사 확인 (Tier 2: investigation event)"

    # Tier 3: 현재 상태 polling (기존 로직)
    try:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        client = session.client("cloudwatch", region_name=AWS_REGION)
        resp = client.describe_alarms(AlarmNames=[alarm_name])
        alarms = resp.get("MetricAlarms", [])
        if not alarms:
            return False, f"알람 없음 (name={alarm_name})"
        state = alarms[0]["StateValue"]
        if state == expected:
            return True, f"알람={alarm_name} 상태={state} (Tier 3: current state)"
        return False, f"알람={alarm_name} 현재={state} 기대={expected}"
    except Exception as e:
        return False, f"알람 조회 실패: {e}"


def _check_lambda_logs(config):
    """Check Lambda function logs for a pattern (boto3)."""
    import boto3
    _default_fn = os.environ.get(
        "WEBHOOK_FUNCTION_NAME",
        _cfg("lambda.webhook_function_name", ""),
    )
    function_name = config.get("function", _default_fn)
    pattern = config.get("pattern", "")
    minutes = config.get("minutes", 10)
    start_ms = int((time.time() - minutes * 60) * 1000)
    log_group = f"/aws/lambda/{function_name}"
    try:
        client = boto3.client("logs", region_name=AWS_REGION)
        resp = client.filter_log_events(
            logGroupName=log_group,
            startTime=start_ms,
            filterPattern=pattern,
            limit=5,
        )
        count = len(resp.get("events", []))
        if count > 0:
            sample = resp["events"][0].get("message", "")[:80]
            return True, f"로그 {count}건 (fn={function_name}, pattern='{pattern}', sample='{sample}')"
        return False, f"로그 미발견 (fn={function_name}, pattern='{pattern}', range={minutes}min)"
    except Exception as e:
        return False, f"로그 조회 실패: {e}"


def _check_pod_status(config):
    """Check pod status (Running, CrashLoopBackOff, OOMKilled, etc.)."""
    pod_label = config.get("pod", "")
    expected = config.get("expected", "")
    ns = config.get("_namespace", NAMESPACE)
    ctx = cluster_manager.get_context_for_service(pod_label) if cluster_manager.is_multi_cluster() else None
    cmd = (
        f"kubectl get pods -n {ns} -l app={pod_label}"
        f" -o jsonpath='{{.items[0].status.phase}}' 2>/dev/null"
    )
    ok, stdout, _ = _run_cmd(cmd, context=ctx)
    if ok and stdout:
        if expected.lower() in stdout.lower():
            return True, f"상태: {stdout}"
    cmd2 = (
        f"kubectl get pods -n {ns} -l app={pod_label}"
        f" -o jsonpath='{{.items[0].status.containerStatuses[0].state}}' 2>/dev/null"
    )
    _, stdout2, _ = _run_cmd(cmd2, context=ctx)
    if stdout2 and expected.lower() in stdout2.lower():
        return True, f"상태: {stdout2}"
    cmd3 = (
        f"kubectl get pods -n {ns} -l app={pod_label}"
        f" -o jsonpath='{{.items[0].status.containerStatuses[0].lastState.terminated.reason}}' 2>/dev/null"
    )
    _, stdout3, _ = _run_cmd(cmd3, context=ctx)
    if stdout3 and expected.lower() in stdout3.lower():
        restarts_cmd = (
            f"kubectl get pods -n {ns} -l app={pod_label}"
            f" -o jsonpath='{{.items[0].status.containerStatuses[0].restartCount}}' 2>/dev/null"
        )
        _, restarts, _ = _run_cmd(restarts_cmd, context=ctx)
        return True, f"pod={pod_label} lastState={stdout3} restarts={restarts}"
    return False, f"pod={pod_label} 현재={stdout or 'unknown'} 기대={expected}"


def _check_slack_message(config):
    """Check Slack channel for messages matching a pattern after run start time."""
    slack = _get_slack_config()
    if not slack:
        return False, "Slack 설정 없음"
    token = slack["bot_token"]
    channel = config.get("channel", slack.get("channel_id", ""))
    pattern = config.get("pattern", "")
    run_started_at = config.get("_run_started_at")
    if run_started_at:
        oldest = str(run_started_at)
    else:
        minutes = config.get("minutes", 10)
        oldest = str(time.time() - minutes * 60)
    try:
        import urllib.request
        url = (
            f"https://slack.com/api/conversations.history"
            f"?channel={channel}&oldest={oldest}&limit=50"
        )
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if not data.get("ok"):
            return False, f"Slack API 에러: {data.get('error', 'unknown')}"
        messages = data.get("messages", [])
        for msg in messages:
            text = msg.get("text", "")
            if re.search(pattern, text, re.IGNORECASE):
                run_obj = config.get("_run_obj")
                if run_obj and "Investigation started" in text:
                    run_obj._slack_thread_ts = msg.get("ts")
                return True, f"Slack 메시지 매칭: {text[:100]}..."
        for msg in messages:
            if msg.get("reply_count", 0) > 0:
                try:
                    replies_url = (
                        f"https://slack.com/api/conversations.replies"
                        f"?channel={channel}&ts={msg['ts']}&limit=50"
                    )
                    req2 = urllib.request.Request(replies_url, headers={"Authorization": f"Bearer {token}"})
                    with urllib.request.urlopen(req2, timeout=10) as resp2:
                        replies_data = json.loads(resp2.read().decode())
                    if replies_data.get("ok"):
                        for reply in replies_data.get("messages", []):
                            if reply.get("ts") == msg["ts"]:
                                continue
                            text = reply.get("text", "")
                            if re.search(pattern, text, re.IGNORECASE):
                                run_obj = config.get("_run_obj")
                                if run_obj:
                                    run_obj._slack_thread_ts = msg.get("ts")
                                return True, f"Slack 메시지 매칭: {text[:100]}..."
                except Exception:
                    pass
        return False, f"패턴 미발견 ({len(messages)}건 검색)"
    except Exception as e:
        return False, f"Slack 조회 실패: {e}"


def _check_investigation_event(config):
    """Check investigation status by sending webhook and polling get_backlog_task."""
    expected_status = config.get("expected_status", "COMPLETED")
    run_obj = config.get("_run_obj")

    if run_obj and run_obj._investigation_task_id:
        if expected_status == "IN_PROGRESS":
            return True, f"조사 태스크 생성 확인: task_id={run_obj._investigation_task_id}"
        try:
            space_id = getattr(run_obj, "agent_space_id", "") or _AGENT_SPACE_ID
            client = _devops_agent_client(space_id=space_id)
            task = client.get_backlog_task(agentSpaceId=space_id, taskId=run_obj._investigation_task_id)
            status = task.get('task', {}).get('status', '')
            if status == 'COMPLETED':
                return True, f"조사 완료: task_id={run_obj._investigation_task_id}"
            if expected_status == "IN_PROGRESS" and status:
                return True, f"조사 진행 중 확인: task_id={run_obj._investigation_task_id} status={status}"
            return False, f"조사 진행 중: task_id={run_obj._investigation_task_id} status={status}"
        except Exception as e:
            return False, f"task 조회 실패: {e}"

    if run_obj and not run_obj._incident_id:
        alarm_name = config.get("alarm_name", "") or config.get("alarm", "")
        alarm_desc = ""
        if not alarm_name and run_obj.scenario:
            for step in run_obj.scenario.get("verification", {}).get("steps", []):
                if step.get("type") in ("cw_alarm", "alarm_state"):
                    alarm_name = step.get("alarm_name") or step.get("alarm") or step.get("config", {}).get("alarm", "")
                    if not alarm_name and step.get("alarm_spec"):
                        alarm_name = step["alarm_spec"].get("metric_name", "")
                    if alarm_name:
                        break
            alarm_desc = run_obj.scenario.get("purpose", "")
        if not alarm_desc:
            alarm_desc = config.get("description", "") or (run_obj.scenario or {}).get("purpose", "")
        if not alarm_name and run_obj.scenario:
            alarm_name = f"scenario-{run_obj.scenario.get('id', 'unknown')}"
        if alarm_name:
            if not alarm_desc:
                alarm_desc = (run_obj.scenario or {}).get("name", alarm_name)
            iid = _send_webhook(alarm_name, alarm_desc, space_id=getattr(run_obj, "agent_space_id", ""))
            if iid:
                run_obj._incident_id = iid
            else:
                return False, "webhook 전송 실패"
        else:
            return False, "시나리오에 알람 정보 없음 (alarm_state step 또는 investigation config에 alarm_name 필요)"

    if run_obj and run_obj._incident_id:
        task_id, status = _find_task_by_incident_id(run_obj._incident_id, space_id=getattr(run_obj, "agent_space_id", ""))
        if task_id:
            run_obj._investigation_task_id = task_id
            return True, f"incident_id={run_obj._incident_id} task_id={task_id} status={status}"

        # Fallback: list_backlog_tasks로 alarm_name in title 매칭
        alarm_name = config.get("alarm_name", "") or config.get("alarm", "")
        if not alarm_name:
            # incident_id에서 alarm_name 추출 (format: {alarm_name}-{timestamp})
            parts = run_obj._incident_id.rsplit("-2026", 1)
            if len(parts) == 2:
                alarm_name = parts[0]
        run_started_at = config.get("_run_started_at")
        if alarm_name and run_started_at:
            if _check_investigation_exists_for_alarm(alarm_name, str(run_started_at), incident_id=run_obj._incident_id):
                return True, f"Agent 조사 확인 (list_backlog_tasks): alarm={alarm_name}"

        return False, f"task 매칭 대기: incident_id={run_obj._incident_id}"

    return False, "incident_id 없음"


def _check_fis_experiment(config):
    """Check FIS experiment status. Uses experiment_id stored on run object."""
    import boto3
    run_obj = config.get("_run_obj")
    expected = config.get("expected_status", "running")
    experiment_id = getattr(run_obj, "_fis_experiment_id", None) if run_obj else None

    if not experiment_id:
        trigger_out = getattr(run_obj, "trigger_output", "") if run_obj else ""
        m = re.search(r"(EXP[A-Za-z0-9]+)", trigger_out)
        if m:
            experiment_id = m.group(1)
            if run_obj:
                run_obj._fis_experiment_id = experiment_id
        else:
            snippet = trigger_out[:120] if trigger_out else "(빈 출력)"
            return False, f"FIS experiment ID 없음 — trigger 출력: {snippet}"

    try:
        profile = config.get("_scenario_profile")
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        client = session.client("fis", region_name=AWS_REGION)
        resp = client.get_experiment(id=experiment_id)
        exp = resp.get("experiment", {})
        state = exp.get("state", {}).get("status", "unknown")
        reason = exp.get("state", {}).get("reason", "")[:80]

        if expected == "running" and state == "running":
            return True, f"FIS 실험 실행 중: id={experiment_id}"
        if expected == "completed" and state == "completed":
            return True, f"FIS 실험 완료: id={experiment_id}"
        if expected == "running" and state in ("initiating", "pending"):
            return False, f"FIS 실험 시작 중: id={experiment_id} status={state}"
        if state in ("failed", "stopped", "cancelled"):
            return False, f"FIS 실험 비정상: id={experiment_id} status={state} reason={reason}"
        return False, f"FIS 실험: id={experiment_id} 현재={state} 기대={expected}"
    except Exception as e:
        return False, f"FIS 조회 실패: {e}"


def _check_xray_latency(config):
    """Check X-Ray traces for latency on a specific service segment."""
    import boto3
    service = config.get("service", "")
    min_latency_ms = config.get("min_latency_ms", 1000)
    minutes = config.get("minutes", 10)
    now = int(time.time())
    start = now - (minutes * 60)
    try:
        client = boto3.client("xray", region_name=AWS_REGION)
        filter_expr = f'service("{service}") AND responsetime > {min_latency_ms / 1000}'
        resp = client.get_trace_summaries(
            StartTime=start, EndTime=now,
            FilterExpression=filter_expr,
            TimeRangeType="TraceId",
        )
        traces = resp.get("TraceSummaries", [])
        if traces:
            avg_rt = sum(t.get("Duration", 0) for t in traces) / len(traces)
            return True, f"{service} 고지연 trace {len(traces)}건 (평균 {avg_rt:.1f}s, 임계치 {min_latency_ms}ms)"
        return False, f"{service} 고지연 trace 없음 (filter='{filter_expr}')"
    except Exception as e:
        return False, f"X-Ray 조회 실패: {e}"


EPHEMERAL_DIMENSION_NAMES = {"PodName", "InstanceId"}


def _strip_ephemeral_dimensions(dimensions):
    """PodName, InstanceId 등 변동성 dimension 자동 제거."""
    stripped = [d for d in dimensions if d.get("Name") not in EPHEMERAL_DIMENSION_NAMES]
    removed = [d.get("Name") for d in dimensions if d.get("Name") in EPHEMERAL_DIMENSION_NAMES]
    if removed:
        print(f"[metric_check] ephemeral dimensions 제거: {removed}")
    return stripped


def _check_metric(config):
    """CloudWatch 메트릭 임계값 확인 (플랫폼 무관)."""
    import boto3
    cw_namespace = config.get("namespace", "")
    metric_name = config.get("metric_name", "")
    raw_dims = config.get("dimensions", [])
    if isinstance(raw_dims, dict):
        raw_dims = [{"Name": k, "Value": v} for k, v in raw_dims.items()]
    dimensions = _strip_ephemeral_dimensions(raw_dims)
    statistic = config.get("statistic", "Average")
    period = config.get("period", 300)
    threshold = config.get("threshold", 0)
    comparison = config.get("comparison", "gt")
    minutes = config.get("minutes", 10)
    profile = config.get("_scenario_profile")
    run_started_at = config.get("_run_started_at")
    try:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        cw = session.client("cloudwatch", region_name=AWS_REGION)
        start_time = int(run_started_at) if run_started_at else int(time.time()) - minutes * 60
        resp = cw.get_metric_statistics(
            Namespace=cw_namespace, MetricName=metric_name,
            Dimensions=dimensions,
            StartTime=start_time,
            EndTime=int(time.time()),
            Period=period, Statistics=[statistic],
        )
        points = resp.get("Datapoints", [])
        if not points:
            return False, f"메트릭 데이터 없음 ({cw_namespace}/{metric_name})"
        values = [p[statistic] for p in points]
        if comparison == "gt":
            peak = max(values)
            passed = peak > threshold
        elif comparison == "lt":
            peak = min(values)
            passed = peak < threshold
        else:
            peak = values[-1]
            passed = peak == threshold
        if passed:
            return True, f"{metric_name}={peak:.2f} {comparison} {threshold}"
        latest = sorted(points, key=lambda p: p["Timestamp"])[-1]
        return False, f"{metric_name}={latest[statistic]:.2f} (peak={peak:.2f}, 기대: {comparison} {threshold})"
    except Exception as e:
        return False, f"메트릭 조회 실패: {e}"


def _check_log_pattern(config):
    """CloudWatch Logs filter 패턴 검색 (플랫폼 무관)."""
    import boto3
    log_group = config.get("log_group", "")
    filter_pattern = config.get("filter_pattern", "")
    minutes = config.get("minutes", 10)
    start_ms = int((time.time() - minutes * 60) * 1000)
    profile = config.get("_scenario_profile")
    try:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        client = session.client("logs", region_name=AWS_REGION)
        resp = client.filter_log_events(
            logGroupName=log_group, startTime=start_ms,
            filterPattern=filter_pattern, limit=5,
        )
        count = len(resp.get("events", []))
        if count > 0:
            sample = resp["events"][0].get("message", "")[:80]
            return True, f"로그 {count}건 (group={log_group}, pattern='{filter_pattern}', sample='{sample}')"
        return False, f"로그 미발견 (group={log_group}, pattern='{filter_pattern}', range={minutes}min)"
    except Exception as e:
        return False, f"로그 조회 실패: {e}"


def _check_alarm_state(config):
    """CloudWatch 알람 상태 확인 (alarm_state 필드명 통일)."""
    if "alarm_name" in config and "alarm" not in config:
        config["alarm"] = config["alarm_name"]
    return _check_cw_alarm(config)


def _check_kubectl(config):
    """범용 kubectl 명령 실행 + 결과 검증."""
    command = config.get("command", "")
    if not command:
        service = config.get("service", "")
        action = config.get("action", "")
        command = f"{action}" if action else ""
    if not command:
        return False, "kubectl command 없음"
    if not command.startswith("kubectl"):
        command = f"kubectl {command}"
    expected = config.get("expected", "")
    pod = config.get("pod", "")
    ns = config.get("_namespace", NAMESPACE)
    if ns and "-n " not in command and "--namespace" not in command:
        command = command.replace("kubectl ", f"kubectl -n {ns} ", 1)
    run_obj = config.get("_run_obj")
    ctx = getattr(run_obj, "_scenario_context", None) if run_obj else None
    if not ctx:
        ctx = cluster_manager.get_context_for_service(pod) if pod and cluster_manager.is_multi_cluster() else None
    ok, stdout, stderr = _run_cmd(command, timeout=30, context=ctx)
    output = (stdout or stderr)[:200]
    if ok and expected:
        expected_vals = [v.strip() for v in str(expected).split("|")]
        if any(v in output for v in expected_vals):
            return True, f"kubectl: {output}"
        # trigger_active phase: Succeeded도 유효 (pod가 작업 수행 후 정상 종료)
        phase = config.get("phase", "")
        if phase == "trigger_active" and "Running" in expected_vals and "Succeeded" in output:
            return True, f"kubectl: {output} (Succeeded — trigger 실행 완료 인정)"
    elif ok and not expected:
        return True, f"kubectl: {output}"
    elif not ok:
        return False, f"kubectl 실패: {output}"
    return False, f"kubectl: 결과={output} 기대={expected}"


def _check_api_call(config):
    """범용 AWS API 호출 + JMESPath 검증."""
    service = config.get("service", "")
    action = config.get("action", "")
    expected = config.get("expected", "")
    if service == "kubectl":
        return _check_kubectl(config)
    import boto3
    parameters = config.get("parameters", {})
    jmespath_expr = config.get("jmespath", "")
    profile = config.get("_scenario_profile")
    try:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        client = session.client(service, region_name=AWS_REGION)
        method = getattr(client, action)
        resp = method(**parameters)
        if jmespath_expr:
            try:
                import jmespath as jmp
                value = jmp.search(jmespath_expr, resp)
            except ImportError:
                value = resp
                for key in jmespath_expr.split("."):
                    value = value.get(key) if isinstance(value, dict) else None
        else:
            value = resp
        value_str = str(value)[:200]
        if expected and str(expected) in value_str:
            return True, f"API {service}.{action}: {value_str}"
        elif not expected and value is not None:
            return True, f"API {service}.{action}: {value_str}"
        return False, f"API {service}.{action}: 결과={value_str} 기대={expected}"
    except Exception as e:
        return False, f"API 호출 실패 ({service}.{action}): {e}"


def _devops_agent_client(space_id=None):
    """Create devops-agent client with correct profile for Agent Space account."""
    if space_id:
        import boto3
        from app_config import _profile_for_space
        profile = _profile_for_space(space_id)
        session = boto3.Session(profile_name=profile, region_name=AWS_REGION)
        return session.client("devops-agent", region_name=AWS_REGION)
    return _agent_space_session().client("devops-agent", region_name=AWS_REGION)


_INVESTIGATION_DONE_STATUSES = {"COMPLETED", "completed", "done", "LINKED", "linked"}


def _check_agent_investigation(config):
    """Webhook으로 Agent 조사 트리거 후 incident_id 기반 task 추적."""
    run_obj = config.get("_run_obj")
    alarm_name = config.get("alarm_name", "simulator-investigation")
    prompt = config.get("prompt", "")
    if not run_obj:
        return False, "run 객체 없음"

    space_id = getattr(run_obj, "agent_space_id", "") or _AGENT_SPACE_ID

    if not getattr(run_obj, "_incident_id", None):
        iid = _send_webhook(alarm_name, prompt[:200], space_id=space_id)
        if not iid:
            return False, "webhook 전송 실패"
        run_obj._incident_id = iid
        return False, f"webhook 전송 완료 (incident={iid})"

    if getattr(run_obj, "_investigation_task_id", None):
        try:
            from app_config import _profile_for_space
            import boto3
            profile = _profile_for_space(space_id)
            session = boto3.Session(profile_name=profile, region_name=AWS_REGION)
            client = session.client("devops-agent", region_name=AWS_REGION)
            task = client.get_backlog_task(
                agentSpaceId=space_id,
                taskId=run_obj._investigation_task_id,
            )
            status = task.get("task", {}).get("status", "")
            if status in _INVESTIGATION_DONE_STATUSES:
                return True, f"Agent 조사 완료 (task={run_obj._investigation_task_id[:12]}…)"
            return False, f"Agent 조사 진행 중: status={status}"
        except Exception as e:
            return False, f"task 조회 실패: {e}"

    task_id, status = _find_task_by_incident_id(run_obj._incident_id, space_id=space_id)
    if task_id:
        run_obj._investigation_task_id = task_id
        if status in _INVESTIGATION_DONE_STATUSES:
            return True, f"Agent 조사 완료 (task={task_id[:12]}…)"
        return False, f"task 연결: incident→task={task_id[:12]}…, status={status}"
    return False, f"Agent task 생성 대기 (incident={run_obj._incident_id})"


# Verification type dispatcher
VERIFIERS = {
    "pod_logs": _check_pod_logs,
    "xray_trace": _check_xray_trace,
    "cw_alarm": _check_cw_alarm,
    "lambda_logs": _check_lambda_logs,
    "pod_status": _check_pod_status,
    "slack_message": _check_slack_message,
    "investigation_event": _check_investigation_event,
    "fis_experiment": _check_fis_experiment,
    "fis_status": _check_fis_experiment,
    "xray_latency": _check_xray_latency,
    "manual": lambda c: (False, "수동 확인 대기"),
    "metric_check": _check_metric,
    "log_pattern": _check_log_pattern,
    "alarm_state": _check_alarm_state,
    "api_call": _check_api_call,
    "kubectl_check": _check_kubectl,
    "agent_investigation": _check_agent_investigation,
}


# ── Error category classification ──────────────────────────────────────────

ERROR_CATEGORIES = {
    "timeout": "앱이 기계적 처리 (traffic boost, reinject, timeout 연장 후 재시도)",
    "command_error": "Agent에게 에러+stderr 전달 → 교정된 명령 수신 → 해당 스텝 재실행",
    "config_error": "Agent에게 설정+실제 상태 전달 → 교정된 config 수신 → 재검증",
    "infra_missing": "수정 안내 표시 + 시나리오 blocked 마킹",
    "transient": "자동 재시도 (backoff), 3회 실패 시 command_error로 격상",
}

_INFRA_MISSING_PATTERNS = [
    "알람 없음", "not found", "NotFound", "does not exist",
    "NoSuchEntity", "ResourceNotFoundException",
    "AccessDenied", "Forbidden", "UnauthorizedAccess",
    "InvalidIdentityToken", "ExpiredTokenException",
]

_TRANSIENT_PATTERNS = [
    "throttl", "TooManyRequestsException", "ServiceUnavailable",
    "connection reset", "connection refused", "ECONNREFUSED",
    "i/o timeout", "network is unreachable",
    "Too many pods", "Pending", "ImagePullBackOff",
    "ContainerCreating", "PodInitializing",
    "timeout expired", "deadline exceeded",
]

_COMMAND_ERROR_PATTERNS = [
    "error:", "Error:", "syntax error", "invalid", "unrecognized",
    "Unknown flag", "unknown command", "command not found",
    "No such file", "cannot access", "unable to connect",
]


def _classify_step_error(step_type, detail, timed_out=False):
    """Classify a failed verification step into an error category.
    Returns: (category, reason)
    """
    if not detail:
        detail = ""

    if timed_out:
        return "timeout", "검증 polling deadline 초과"

    # kubectl_check에서 pod NotFound는 생성 대기/이름 불일치이지 인프라 부재 아님
    if step_type in ("kubectl_check", "pod_status") and (
        "NotFound" in detail or ("not found" in detail and "pods" in detail.lower())
    ):
        return "command_error", "리소스 미발견 — pod 이름 확인 또는 생성 대기 필요"

    for pat in _INFRA_MISSING_PATTERNS:
        if pat in detail:
            return "infra_missing", f"인프라 부재 또는 접근 불가: {pat}"

    for pat in _TRANSIENT_PATTERNS:
        if pat.lower() in detail.lower():
            return "transient", f"일시적 에러: {pat}"

    if step_type in ("alarm_state", "cw_alarm") and "기대=" in detail:
        return "config_error", "알람 상태 불일치"
    if "기대=" in detail or "expected=" in detail.lower():
        return "config_error", "검증 결과와 기대값 불일치"
    if "결과=" in detail and step_type in ("kubectl_check", "api_call", "metric_check"):
        return "config_error", "검증 결과와 기대값 불일치"

    for pat in _COMMAND_ERROR_PATTERNS:
        if pat in detail:
            return "command_error", f"명령 실행 실패: {pat}"
    if "실패" in detail or "failed" in detail.lower():
        return "command_error", "명령 실행 실패"

    return "command_error", "분류 불가 — Agent 교정 대상"
