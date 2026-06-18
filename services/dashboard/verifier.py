"""
Verification Engine for DevOps Agent Test Simulator.
Run lifecycle management + re-exports from submodules for backward compatibility.
"""
import json
import os
import threading
import time
from datetime import datetime, timezone

# ── Re-exports from verifier_utils ──────────────────────────────────────────
from verifier_utils import (  # noqa: F401
    AWS_REGION, NAMESPACE, _cfg,
    _AGENT_SPACE_ID, _WEBHOOK_SECRET, _EVENTS_TABLE, _RUNS_TABLE, _PROJECT_NAME,
    RESULTS_DIR, SLACK_SECRET_NAME,
    _get_slack_config, init_slack_config, _ensure_results_dir,
    _CMD_ENV, _cmd_env, _run_cmd,
    _extract_target_service, _extract_namespace,
    _preflight_tool_available, _preflight_k8s_access, _preflight_aws_access,
    _preflight_target_ready, _pre_flight_check,
    _agent_space_session, _send_webhook, _find_task_by_incident_id,
)

# ── Re-exports from verifier_checkers ───────────────────────────────────────
from verifier_checkers import (  # noqa: F401
    VERIFIERS, ERROR_CATEGORIES,
    _classify_step_error,
    _devops_agent_client,
)

# ── Re-exports from verifier_base ───────────────────────────────────────────
from verifier_base import (  # noqa: F401
    SimulationRun,
    STEP_UPDATE_RE, VERIFY_PROMPT_TEMPLATE, INVESTIGATE_PROMPT_TEMPLATE,
    _step_to_instruction,
)

# ── Re-exports from verifier_executors ──────────────────────────────────────
from verifier_executors import (  # noqa: F401
    AgentExecutor, AgentSSEExecutor,
    ScriptExecutor, ScriptSSEExecutor,
    PythonScriptExecutor,
    _extract_alarm_names, verify_alarms,
    _fix_bash_compat, CHECKPOINT_RE, RESULT_RE,
    _parse_checkpoints, _inject_resume_step,
)

import cluster_manager


# ═══════════════════════════════════════════════════════════════════════════
# Active runs registry
# ═══════════════════════════════════════════════════════════════════════════

_active_runs = {}
_runs_lock = threading.Lock()


def get_active_run(run_id):
    with _runs_lock:
        return _active_runs.get(run_id)


def list_active_runs():
    with _runs_lock:
        return {rid: r.to_dict() for rid, r in _active_runs.items()
                if r.status in ('running', 'verifying', 'executing')}


def _recover_interrupted_runs():
    """앱 시작 시 DynamoDB에서 status=running인 실행을 interrupted로 마킹."""
    try:
        import boto3
        table = _agent_space_session().resource("dynamodb", region_name=AWS_REGION).Table(_RUNS_TABLE)
        resp = table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("status").is_in(
                ["running", "verifying", "executing"]
            ) & boto3.dynamodb.conditions.Attr("record_type").eq("run"),
            ProjectionExpression="run_id, record_type",
        )
        for item in resp.get("Items", []):
            table.update_item(
                Key={"run_id": item["run_id"], "record_type": "run"},
                UpdateExpression="SET #s = :v, #r = :r",
                ExpressionAttributeNames={"#s": "status", "#r": "result"},
                ExpressionAttributeValues={":v": "interrupted", ":r": "interrupted"},
            )
            print(f"[RECOVERY] run {item['run_id']} marked as interrupted")
    except Exception as e:
        print(f"[RECOVERY] failed: {e}")


_recover_interrupted_runs()


def _resolve_executor_type(scenario) -> str:
    """Determine executor type: scenario field > config > default."""
    force = _cfg("executor.force", False)
    if force:
        return _cfg("executor.default", "classic")
    executor = scenario.get("executor", "")
    if executor == "agent":
        print("[executor] 'agent' deprecated → classic (SimulationRun full pipeline)")
        return "classic"
    if executor in ("classic", "script", "multi_agent"):
        return executor
    return _cfg("executor.default", "classic")


def start_run(scenario, namespace=None, agent_space_id=None, script=None, resume_from=0):
    """Dispatch to appropriate executor and start pipeline in background.

    Executor paths:
      - script: ScriptExecutor (user scripts)
      - multi_agent: MultiAgentEngine (Strands Agent verify, legacy)
      - classic (default): SimulationRun (full pipeline: preflight→trigger→verify→investigate→evaluate→restore)
    """
    executor_type = _resolve_executor_type(scenario)

    if script or executor_type == "script":
        run = ScriptExecutor(scenario, script or "", agent_space_id=agent_space_id, namespace=namespace, resume_from=resume_from)
    elif executor_type == "multi_agent":
        from multi_agent_engine import MultiAgentEngine
        run = MultiAgentEngine(scenario, agent_space_id=agent_space_id, namespace=namespace)
    else:
        # classic + agent 모두 SimulationRun (full pipeline with investigate+evaluate)
        run = SimulationRun(scenario, agent_space_id=agent_space_id, namespace=namespace)

    with _runs_lock:
        _active_runs[run.run_id] = run

    t = threading.Thread(target=run._run_pipeline, daemon=True)
    t.start()

    return run


# ═══════════════════════════════════════════════════════════════════════════
# Auto-correction harness
# ═══════════════════════════════════════════════════════════════════════════

def _auto_correct_step(run, step, config, _ev, scope="command"):
    """Agent에게 실패 정보를 전달하고 교정된 config를 받아 적용.

    scope:
      "command" — kubectl/CLI 명령만 교정
      "config"  — 전체 step config JSON 교정 (api_call의 jmespath, parameters 등)

    Returns True if config was corrected, False otherwise.
    """
    try:
        from ai_provider import get_provider
    except Exception:
        _ev(step, "ai_provider 미사용 — 교정 스킵")
        return False

    cmd = config.get("command", "")
    expected = config.get("expected", "")
    detail = step.get("detail", "")

    if scope == "config":
        import json as _json
        safe_config = {k: v for k, v in config.items() if not k.startswith("_")}
        prompt = (
            f"시나리오 검증 스텝 실패:\n"
            f"- step: {step['name']} (type={step['type']})\n"
            f"- 에러: {detail}\n"
            f"- 현재 config:\n```json\n{_json.dumps(safe_config, ensure_ascii=False, indent=2)}\n```\n\n"
            f"수정된 config를 JSON으로 반환하세요. 설명 없이 JSON만 출력하세요."
        )
    else:
        prompt = (
            f"시나리오 검증 스텝 실패:\n"
            f"- step: {step['name']} (type={step['type']})\n"
            f"- 에러: {detail}\n"
        )
        if cmd:
            prompt += f"- 명령: `{cmd}`\n"
        if expected:
            prompt += f"- 기대값: {expected}\n"
        prompt += "\n수정된 명령을 한 줄로 반환하세요. 설명 없이 명령만 출력하세요."

    try:
        space_id = run.agent_space_id or ""
        resp = get_provider().send_raw(space_id=space_id, session_id="", prompt=prompt)
        reply = resp.get("reply", "").strip()
        if not reply:
            _ev(step, "Agent 응답 없음")
            return False

        if scope == "config":
            import json as _json
            json_match = reply
            if "```" in reply:
                import re
                m = re.search(r'```(?:json)?\s*(.*?)```', reply, re.DOTALL)
                if m:
                    json_match = m.group(1).strip()
            try:
                new_config = _json.loads(json_match)
                changed = False
                for k, v in new_config.items():
                    if k not in ("_namespace", "_scenario_context", "_scenario_profile") and config.get(k) != v:
                        config[k] = v
                        changed = True
                if changed:
                    _ev(step, f"config 교정 적용: {list(new_config.keys())[:5]}")
                    return True
                _ev(step, "Agent config 교정 결과 동일")
                return False
            except Exception as je:
                _ev(step, f"config JSON 파싱 실패: {je}")
                return False
        else:
            corrected_cmd = reply.split("\n")[0].strip().strip("`").strip()
            if corrected_cmd and corrected_cmd != cmd:
                _ev(step, f"교정: {cmd[:60]} → {corrected_cmd[:60]}")
                config["command"] = corrected_cmd
                return True
            _ev(step, "Agent 교정 결과 동일 — 변경 없음")
            return False
    except Exception as e:
        _ev(step, f"교정 실패: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Verification loop
# ═══════════════════════════════════════════════════════════════════════════

def _verification_loop(run):
    """Background loop that checks each verification step with strategy-based error handling."""
    from verifier_base import SimulationRun
    from error_response_strategy import get_response_action, ErrorAction, compute_backoff_delay
    _ev = SimulationRun._log_event
    start_time = time.time()

    verify_start = getattr(run, "_verify_start_idx", 0)
    verify_steps = [(i, step) for i, step in enumerate(run.steps) if i >= verify_start and not step["type"].startswith("pipeline_")]

    for i, step in verify_steps:
        if run.status == "cancelled":
            step["status"] = "skipped"
            _ev(step, "cancelled — 건너뜀")
            continue

        step_type = step["type"]
        config = step["config"]
        config["_namespace"] = run.namespace
        config["_run_obj"] = run
        if run._scenario_context:
            config["_scenario_context"] = run._scenario_context
        if run._scenario_profile:
            config["_scenario_profile"] = run._scenario_profile
        from verifier_base import _resolve_scenario_variables
        for _k, _v in list(config.items()):
            if isinstance(_v, str) and "${" in _v:
                config[_k] = _resolve_scenario_variables(
                    _v, run.scenario, run._scenario_context)
        timeout = config.get("timeout", 60)
        poll_interval = config.get("poll_interval", 10)
        error_handling = config.get("error_handling")

        if step_type == "manual":
            step["status"] = "checking"
            step["detail"] = "수동 확인 대기 중..."
            _ev(step, "수동 확인 대기 시작")
            deadline = time.time() + timeout
            while time.time() < deadline and run.status != "cancelled":
                if step["status"] in ("pass", "fail"):
                    break
                time.sleep(2)
            if step["status"] == "checking":
                step["status"] = "skipped"
                step["detail"] = "시간 초과 - 건너뜀"
                _ev(step, "수동 확인 시간 초과")
            step["elapsed"] = round(time.time() - start_time, 1)
            continue

        verifier = VERIFIERS.get(step_type)
        if not verifier:
            step["status"] = "fail"
            step["detail"] = f"알 수 없는 검증 타입: {step_type}"
            step["elapsed"] = round(time.time() - start_time, 1)
            step["error_category"] = "config_error"
            step["error_reason"] = f"미등록 검증 타입: {step_type}"
            _ev(step, f"ERROR: 미등록 타입 {step_type}")
            continue

        step["status"] = "checking"
        _ev(step, f"검증 시작 (timeout={timeout}s, poll={poll_interval}s)")
        deadline = time.time() + timeout
        passed = False
        poll_count = 0
        immediate_fail = False

        while time.time() < deadline and run.status != "cancelled":
            config["_run_started_at"] = run._started_ts
            config["_run_obj"] = run
            ok, detail = verifier(config)
            step["detail"] = detail
            poll_count += 1
            if poll_count <= 3 or poll_count % 5 == 0 or ok:
                _ev(step, f"poll#{poll_count}: {detail[:100]}")
            if ok:
                step["status"] = "pass"
                step["elapsed"] = round(time.time() - start_time, 1)
                passed = True
                _ev(step, "PASS")
                break
            if poll_count == 1 and step_type not in ("investigation_event", "agent_investigation", "fis_experiment"):
                cat_check, _ = _classify_step_error(step_type, detail)
                first_action = get_response_action(step_type, cat_check, error_handling)
                if first_action == ErrorAction.BLOCKED:
                    immediate_fail = True
                    _ev(step, f"[HARNESS:BLOCKED] 즉시 실패: {cat_check}")
                    break
                elif first_action == ErrorAction.AGENT_CORRECT and cat_check == "command_error":
                    immediate_fail = True
                    _ev(step, f"[HARNESS:AGENT_CORRECT] 즉시 교정 필요: {cat_check}")
                    break
            time.sleep(poll_interval)

        if passed and step_type in ("alarm_state", "cw_alarm") and config.get("expected") == "ALARM":
            next_expects_ok = (i + 1 < len(run.steps)
                               and run.steps[i+1].get("type") in ("alarm_state", "cw_alarm")
                               and run.steps[i+1].get("config", {}).get("expected") == "OK")
            if next_expects_ok:
                restore_cmd = run.scenario.get("restore", {}).get("command", "")
                if restore_cmd:
                    _ev(step, "mid-restore 실행 중...")
                    rok, rout, rerr = _run_cmd(restore_cmd, timeout=60,
                                               context=run._scenario_context)
                    _ev(step, f"mid-restore: ok={rok} out={rout[:80]}")

        # ── 실패 대응: 전략 매트릭스 디스패치 ──
        if not passed and step["status"] == "checking":
            cat, reason = _classify_step_error(step_type, step["detail"], timed_out=(not immediate_fail))
            action = get_response_action(step_type, cat, error_handling)
            correction_scope = (error_handling or {}).get("correction_scope", "command")
            max_retries = (error_handling or {}).get("max_retries", 3)

            passed = _handle_step_failure(
                run, step, config, verifier, _ev,
                action=action, cat=cat, reason=reason,
                immediate_fail=immediate_fail,
                timeout=timeout, poll_interval=poll_interval,
                correction_scope=correction_scope, max_retries=max_retries,
                start_time=start_time,
            )

            if not passed:
                step["status"] = "fail"
                step["elapsed"] = round(time.time() - start_time, 1)
                step["error_category"] = cat
                step["error_reason"] = reason
                if not immediate_fail:
                    step["detail"] = f"시간 초과 ({timeout}s) - {step['detail']}"
                _ev(step, f"FAIL [{cat}|{action.value}]: {step['detail'][:120]}")

                # Cross-step inference: alarm 실패 시 뒤에 investigation이 있으면 break하지 않음
                has_pending_investigation = any(
                    s["type"] in ("investigation_event", "agent_investigation")
                    and s["status"] == "pending"
                    for _, s in verify_steps if _ > i
                )
                if step_type in ("alarm_state", "cw_alarm") and has_pending_investigation and config.get("expected") == "ALARM":
                    _ev(step, "TENTATIVE FAIL: investigation 결과에 따라 소급 PASS 가능")
                    continue

                # kubectl_check (trigger 확인용) 실패는 뒤 step을 막지 않음
                # trigger pipeline이 이미 성공했으면 effect/reaction은 계속 진행
                trigger_step = next((s for s in run.steps if s["type"] == "pipeline_trigger"), None)
                if step_type == "kubectl_check" and trigger_step and trigger_step.get("status") == "pass":
                    _ev(step, "trigger 성공 상태이므로 후속 검증 계속 진행")
                    continue

                for remaining in run.steps[i+1:]:
                    if remaining["type"].startswith("pipeline_"):
                        continue
                    remaining["status"] = "skipped"
                    remaining["detail"] = f"이전 단계 실패로 건너뜀 ({step['name']})"
                break

        # Cross-step inference: investigation PASS → 이전 alarm 실패 소급 PASS
        if passed and step_type in ("investigation_event", "agent_investigation"):
            for prev_i, prev_step in verify_steps:
                if prev_i >= i:
                    break
                if (prev_step["type"] in ("alarm_state", "cw_alarm")
                        and prev_step.get("status") == "fail"
                        and prev_step.get("config", {}).get("expected") == "ALARM"):
                    prev_step["status"] = "pass"
                    prev_step["detail"] = "Agent 조사 시작 → 알람 발화 간접 확인 (cross-step inference)"
                    _ev(prev_step, "PASS (cross-step inference: investigation confirmed alarm)")

    verify_passed = sum(1 for _, s in verify_steps if s["status"] == "pass")
    verify_total = len(verify_steps)
    manual_skipped = sum(1 for _, s in verify_steps if s["type"] == "manual" and s["status"] == "skipped")

    if run.status != "cancelled":
        if verify_total == 0 or verify_passed == verify_total:
            run.result = "pass"
        elif verify_passed + manual_skipped == verify_total:
            run.result = "partial"
        else:
            run.result = "fail"

    if not run._investigation_task_id and not run._incident_id:
        try:
            alarm_name = f"scenario-{run.scenario_id}"
            alarm_desc = run.scenario.get("purpose", run.scenario.get("name", ""))
            iid = _send_webhook(alarm_name, alarm_desc, space_id=run.agent_space_id)
            if iid:
                run._incident_id = iid
                print(f"Auto-triggered investigation: incident_id={iid}")
        except Exception as e:
            print(f"Auto-trigger webhook failed: {e}")

    if not run._investigation_task_id and run._incident_id:
        try:
            task_id, _st = _find_task_by_incident_id(run._incident_id, space_id=run.agent_space_id)
            if task_id:
                run._investigation_task_id = task_id
                print(f"Auto-detected investigation task: {task_id}")
        except Exception as e:
            print(f"Auto-detect investigation task failed: {e}")


def _handle_step_failure(run, step, config, verifier, _ev, *,
                         action, cat, reason, immediate_fail,
                         timeout, poll_interval, correction_scope, max_retries,
                         start_time):
    """Execute the error response action. Returns True if step was recovered."""
    from error_response_strategy import ErrorAction, compute_backoff_delay

    if action == ErrorAction.BLOCKED:
        _ev(step, f"[HARNESS:BLOCKED] 재시도 불가 — {reason}")
        return False

    elif action == ErrorAction.AGENT_CORRECT:
        _ev(step, f"[HARNESS:AGENT_CORRECT] scope={correction_scope}")
        step["detail"] = "Agent 교정 요청 중..."
        corrected = _auto_correct_step(run, step, config, _ev, scope=correction_scope)
        if corrected:
            _ev(step, "교정 완료 — 재검증")
            ok, detail = verifier(config)
            step["detail"] = detail
            _ev(step, f"교정 후 검증: {detail[:100]}")
            if ok:
                step["status"] = "pass"
                step["elapsed"] = round(time.time() - start_time, 1)
                _ev(step, "PASS (교정 성공)")
                return True
        return False

    elif action == ErrorAction.TRIGGER_REINJECT:
        trigger_cmd = run.scenario.get("trigger", {}).get("command", "")
        if not trigger_cmd:
            _ev(step, "[HARNESS:TRIGGER_REINJECT] trigger 명령 없음 — POLL_CONTINUE로 전환")
            action = ErrorAction.POLL_CONTINUE
        else:
            _ev(step, f"[HARNESS:TRIGGER_REINJECT] trigger 재주입")
            step["detail"] = "자동 재시도 중 (trigger 재주입)..."
            _run_cmd(trigger_cmd, timeout=60, context=run._scenario_context)
            _ev(step, "trigger 재주입 완료, 10s 대기 후 재검증")
            time.sleep(10)
            retry_deadline = time.time() + timeout
            retry_poll = 0
            while time.time() < retry_deadline and run.status != "cancelled":
                ok, detail = verifier(config)
                step["detail"] = detail
                retry_poll += 1
                if retry_poll <= 2 or retry_poll % 5 == 0 or ok:
                    _ev(step, f"reinject poll#{retry_poll}: {detail[:100]}")
                if ok:
                    step["status"] = "pass"
                    step["elapsed"] = round(time.time() - start_time, 1)
                    _ev(step, "PASS (trigger 재주입 성공)")
                    return True
                time.sleep(poll_interval)
            return False

    elif action == ErrorAction.RETRY_BACKOFF:
        _ev(step, f"[HARNESS:RETRY_BACKOFF] 지수 백오프 (최대 {max_retries}회)")
        for attempt in range(max_retries):
            delay = compute_backoff_delay(attempt)
            _ev(step, f"backoff #{attempt+1}: {delay}s 대기")
            time.sleep(delay)
            if run.status == "cancelled":
                return False
            ok, detail = verifier(config)
            step["detail"] = detail
            _ev(step, f"backoff #{attempt+1} 결과: {detail[:100]}")
            if ok:
                step["status"] = "pass"
                step["elapsed"] = round(time.time() - start_time, 1)
                _ev(step, f"PASS (backoff #{attempt+1} 성공)")
                return True
        _ev(step, f"backoff {max_retries}회 실패 — AGENT_CORRECT로 격상")
        corrected = _auto_correct_step(run, step, config, _ev, scope=correction_scope)
        if corrected:
            ok, detail = verifier(config)
            step["detail"] = detail
            if ok:
                step["status"] = "pass"
                step["elapsed"] = round(time.time() - start_time, 1)
                _ev(step, "PASS (backoff 실패 후 교정 성공)")
                return True
        return False

    # ErrorAction.POLL_CONTINUE (or fallthrough from TRIGGER_REINJECT without trigger_cmd)
    _ev(step, f"[HARNESS:POLL_CONTINUE] 추가 대기 ({timeout}s)")
    step["detail"] = "추가 대기 중..."
    retry_deadline = time.time() + timeout
    retry_poll = 0
    while time.time() < retry_deadline and run.status != "cancelled":
        ok, detail = verifier(config)
        step["detail"] = detail
        retry_poll += 1
        if retry_poll <= 2 or retry_poll % 5 == 0 or ok:
            _ev(step, f"continue poll#{retry_poll}: {detail[:100]}")
        if ok:
            step["status"] = "pass"
            step["elapsed"] = round(time.time() - start_time, 1)
            _ev(step, "PASS (추가 대기 성공)")
            return True
        time.sleep(poll_interval)
    # POLL_CONTINUE 실패 후 재분류 → AGENT_CORRECT 시도
    recat, _ = _classify_step_error(step["type"], step["detail"])
    if recat == "command_error":
        _ev(step, "추가 대기 실패 — 재분류 command_error → Agent 교정")
        corrected = _auto_correct_step(run, step, config, _ev, scope=correction_scope)
        if corrected:
            ok, detail = verifier(config)
            step["detail"] = detail
            if ok:
                step["status"] = "pass"
                step["elapsed"] = round(time.time() - start_time, 1)
                _ev(step, "PASS (추가 대기 후 교정 성공)")
                return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
# Run control
# ═══════════════════════════════════════════════════════════════════════════

def confirm_manual_step(run_id, step_index, passed=True):
    """Confirm a manual verification step."""
    run = get_active_run(run_id)
    if not run or step_index >= len(run.steps):
        return False
    step = run.steps[step_index]
    if step["type"] != "manual":
        return False
    step["status"] = "pass" if passed else "fail"
    step["detail"] = "수동 확인 완료" if passed else "수동 확인 실패"
    return True


def cancel_run(run_id):
    """Cancel an active run."""
    run = get_active_run(run_id)
    if run:
        run.status = "cancelled"
        return True
    return False


def resume_run(saved_data, scenario):
    """Resume a run from DynamoDB saved state. Starts from first fail/pending step."""
    run = SimulationRun.from_saved(saved_data, scenario)
    resume_idx = run.get_resume_index()

    # Reset fail/pending steps for re-execution
    for i in range(resume_idx, len(run.steps)):
        if run.steps[i]["type"].startswith("pipeline_"):
            if run.steps[i]["type"] == "pipeline_restore":
                run.steps[i]["status"] = "pending"
                run.steps[i]["detail"] = ""
            continue
        run.steps[i]["status"] = "pending"
        run.steps[i]["detail"] = ""
        run.steps[i]["elapsed"] = None
        run.steps[i].pop("error_category", None)
        run.steps[i].pop("error_reason", None)

    run.status = "running"
    run.result = None
    run.completed_at = None

    with _runs_lock:
        _active_runs[run.run_id] = run

    def _resume():
        _verification_loop_from(run, resume_idx)
        # Restore step
        restore_step = run._get_pipeline_step("pipeline_restore")
        if restore_step and restore_step["status"] == "pending":
            restore_cmd = run.scenario.get("restore", {}).get("command", "")
            restore_step["status"] = "checking"
            SimulationRun._log_event(restore_step, "복원 시작")
            if restore_cmd:
                rok, rout, rerr = _run_cmd(restore_cmd, timeout=60, context=run._scenario_context)
                output = rout or rerr
                restore_step["status"] = "pass" if rok else "fail"
                restore_step["detail"] = f"{'복원 완료' if rok else '복원 실패'}: {output[:100]}"
            else:
                restore_step["status"] = "pass"
                restore_step["detail"] = "복원 명령 없음"

        # Cleanup managed alarms
        if getattr(run, "_managed_alarms", None):
            try:
                from alarm_provisioner import cleanup_managed_alarms
                cleanup_managed_alarms(run._scenario_profile, AWS_REGION)
            except Exception:
                pass

        if run.status not in ("completed", "cancelled"):
            run.status = "completed"
        run.completed_at = datetime.now(timezone.utc).isoformat()
        run.save()

    t = threading.Thread(target=_resume, daemon=True)
    t.start()
    return run


def retry_from_step(run_id, step_index):
    """Retry verification from a failed step onward (background thread).
    If run is no longer in memory, look up scenario_id from DynamoDB
    and return a new_run action for the frontend to re-dispatch.
    PythonScriptExecutor always returns new_run (subprocess-based execution).
    """
    run = get_active_run(run_id)
    if not run:
        try:
            table = _agent_space_session().resource(
                "dynamodb", region_name=AWS_REGION
            ).Table(_RUNS_TABLE)
            resp = table.get_item(Key={"run_id": run_id, "record_type": "run"})
            item = resp.get("Item")
            if item and item.get("scenario_id"):
                return {
                    "action": "new_run",
                    "scenario_id": item["scenario_id"],
                    "resume_from": step_index,
                }
        except Exception as e:
            print(f"retry_from_step DDB lookup failed: {e}")
        return None

    if isinstance(run, PythonScriptExecutor):
        scenario_id = run.scenario.get("id", "")
        return {
            "action": "new_run",
            "scenario_id": scenario_id,
            "resume_from": step_index,
        }

    if step_index < 0 or step_index >= len(run.steps):
        return None

    for i in range(step_index, len(run.steps)):
        run.steps[i]["status"] = "pending"
        run.steps[i]["detail"] = ""
        run.steps[i]["elapsed"] = None

    run.status = "verifying"
    run.result = None
    run.completed_at = None

    if isinstance(run, AgentExecutor):
        def _retry():
            run._run_verify_phase()
            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc).isoformat()
            run.save()
        t = threading.Thread(target=_retry, daemon=True)
    else:
        def _retry():
            _verification_loop_from(run, step_index)
        t = threading.Thread(target=_retry, daemon=True)

    t.start()
    return run


def _verification_loop_from(run, from_index):
    """Run verification loop starting from a specific step index.
    Uses the same strategy-based error handling as _verification_loop().
    """
    from verifier_base import SimulationRun
    from error_response_strategy import get_response_action, ErrorAction
    _ev = SimulationRun._log_event
    start_time = time.time()

    for i in range(from_index, len(run.steps)):
        step = run.steps[i]
        if step["type"].startswith("pipeline_"):
            continue
        if run.status == "cancelled":
            step["status"] = "skipped"
            _ev(step, "cancelled — 건너뜀")
            continue

        step_type = step["type"]
        config = step["config"]
        config["_namespace"] = run.namespace
        config["_run_obj"] = run
        if run._scenario_context:
            config["_scenario_context"] = run._scenario_context
        if run._scenario_profile:
            config["_scenario_profile"] = run._scenario_profile
        from verifier_base import _resolve_scenario_variables
        for _k, _v in list(config.items()):
            if isinstance(_v, str) and "${" in _v:
                config[_k] = _resolve_scenario_variables(
                    _v, run.scenario, run._scenario_context)
        timeout = config.get("timeout", 60)
        poll_interval = config.get("poll_interval", 10)
        error_handling = config.get("error_handling")

        if step_type == "manual":
            step["status"] = "checking"
            step["detail"] = "수동 확인 대기 중..."
            deadline = time.time() + timeout
            while time.time() < deadline and run.status != "cancelled":
                if step["status"] in ("pass", "fail"):
                    break
                time.sleep(2)
            if step["status"] == "checking":
                step["status"] = "skipped"
                step["detail"] = "시간 초과 - 건너뜀"
            step["elapsed"] = round(time.time() - start_time, 1)
            continue

        verifier = VERIFIERS.get(step_type)
        if not verifier:
            step["status"] = "fail"
            step["detail"] = f"알 수 없는 검증 타입: {step_type}"
            step["elapsed"] = round(time.time() - start_time, 1)
            step["error_category"] = "config_error"
            step["error_reason"] = f"미등록 검증 타입: {step_type}"
            continue

        step["status"] = "checking"
        _ev(step, f"검증 시작 (timeout={timeout}s, poll={poll_interval}s)")
        deadline = time.time() + timeout
        passed = False
        poll_count = 0
        immediate_fail = False

        while time.time() < deadline and run.status != "cancelled":
            config["_run_started_at"] = run._started_ts
            config["_run_obj"] = run
            ok, detail = verifier(config)
            step["detail"] = detail
            poll_count += 1
            if poll_count <= 3 or poll_count % 5 == 0 or ok:
                _ev(step, f"poll#{poll_count}: {detail[:100]}")
            if ok:
                step["status"] = "pass"
                step["elapsed"] = round(time.time() - start_time, 1)
                passed = True
                _ev(step, "PASS")
                break
            if poll_count == 1 and step_type not in ("investigation_event", "agent_investigation", "fis_experiment"):
                cat_check, _ = _classify_step_error(step_type, detail)
                first_action = get_response_action(step_type, cat_check, error_handling)
                if first_action == ErrorAction.BLOCKED:
                    immediate_fail = True
                    _ev(step, f"[HARNESS:BLOCKED] 즉시 실패: {cat_check}")
                    break
                elif first_action == ErrorAction.AGENT_CORRECT and cat_check == "command_error":
                    immediate_fail = True
                    _ev(step, f"[HARNESS:AGENT_CORRECT] 즉시 교정 필요: {cat_check}")
                    break
            time.sleep(poll_interval)

        if not passed and step["status"] == "checking":
            cat, reason = _classify_step_error(step_type, step["detail"], timed_out=(not immediate_fail))
            action = get_response_action(step_type, cat, error_handling)
            correction_scope = (error_handling or {}).get("correction_scope", "command")
            max_retries = (error_handling or {}).get("max_retries", 3)

            passed = _handle_step_failure(
                run, step, config, verifier, _ev,
                action=action, cat=cat, reason=reason,
                immediate_fail=immediate_fail,
                timeout=timeout, poll_interval=poll_interval,
                correction_scope=correction_scope, max_retries=max_retries,
                start_time=start_time,
            )

            if not passed:
                step["status"] = "fail"
                step["elapsed"] = round(time.time() - start_time, 1)
                step["error_category"] = cat
                step["error_reason"] = reason
                if not immediate_fail:
                    step["detail"] = f"시간 초과 ({timeout}s) - {step['detail']}"
                _ev(step, f"FAIL [{cat}|{action.value}]: {step['detail'][:120]}")

                # Cross-step inference: alarm 실패 + investigation 대기
                has_pending_inv = any(
                    run.steps[j]["type"] in ("investigation_event", "agent_investigation")
                    and run.steps[j]["status"] == "pending"
                    for j in range(i + 1, len(run.steps))
                    if not run.steps[j]["type"].startswith("pipeline_")
                )
                if step_type in ("alarm_state", "cw_alarm") and has_pending_inv and config.get("expected") == "ALARM":
                    _ev(step, "TENTATIVE FAIL: investigation 결과에 따라 소급 PASS 가능")
                    continue

                for remaining in run.steps[i + 1:]:
                    if remaining["type"].startswith("pipeline_"):
                        continue
                    remaining["status"] = "skipped"
                    remaining["detail"] = f"이전 단계 실패로 건너뜀 ({step['name']})"
                break

        # Cross-step inference: investigation PASS → alarm 소급 PASS
        if passed and step_type in ("investigation_event", "agent_investigation"):
            for j in range(from_index, i):
                prev_step = run.steps[j]
                if (prev_step["type"] in ("alarm_state", "cw_alarm")
                        and prev_step.get("status") == "fail"
                        and prev_step.get("config", {}).get("expected") == "ALARM"):
                    prev_step["status"] = "pass"
                    prev_step["detail"] = "Agent 조사 시작 → 알람 발화 간접 확인 (cross-step inference)"
                    _ev(prev_step, "PASS (cross-step inference: investigation confirmed alarm)")

    v_steps = [s for s in run.steps if not s["type"].startswith("pipeline_")]
    passed_count = sum(1 for s in v_steps if s["status"] == "pass")
    total = len(v_steps)
    manual_skipped = sum(1 for s in v_steps if s["type"] == "manual" and s["status"] == "skipped")

    if run.status != "cancelled":
        if total == 0 or passed_count == total:
            run.result = "pass"
        elif passed_count + manual_skipped == total:
            run.result = "partial"
        else:
            run.result = "fail"
        run.status = "completed"

    run.completed_at = datetime.now(timezone.utc).isoformat()
    run.save()


# ═══════════════════════════════════════════════════════════════════════════
# History & Environment status
# ═══════════════════════════════════════════════════════════════════════════

def get_history(limit=10, last_key=None, agent_space_id=None):
    """Load past run results from DynamoDB with pagination."""
    results = []
    next_key = None
    try:
        import boto3
        table = _agent_space_session().resource("dynamodb", region_name=AWS_REGION).Table(_RUNS_TABLE)
        filter_expr = boto3.dynamodb.conditions.Attr("record_type").eq("run")
        scan_kwargs = {
            "FilterExpression": filter_expr,
            "ProjectionExpression": "run_id, scenario_id, scenario_name, agent_space_id, started_at, completed_at, #s, #r, incident_id, investigation_task_id, steps",
            "ExpressionAttributeNames": {"#s": "status", "#r": "result"},
        }
        if last_key:
            scan_kwargs["ExclusiveStartKey"] = last_key

        last_evaluated = None
        while len(results) < limit:
            resp = table.scan(**scan_kwargs)
            items = resp.get("Items", [])
            items_json = json.loads(json.dumps(items, default=str))
            results.extend(items_json)
            last_evaluated = resp.get("LastEvaluatedKey")
            if not last_evaluated:
                break
            scan_kwargs["ExclusiveStartKey"] = last_evaluated

        if agent_space_id:
            filtered = []
            for r in results:
                r_space = r.get("agent_space_id", "")
                if r_space and r_space != agent_space_id:
                    continue
                if not r_space and agent_space_id != _AGENT_SPACE_ID:
                    continue
                filtered.append(r)
            results = filtered
        next_key = last_evaluated if len(results) >= limit and last_evaluated else None
        results.sort(key=lambda x: x.get("completed_at") or x.get("started_at") or "", reverse=True)
        results = results[:limit]
    except Exception as e:
        print(f"DynamoDB history failed: {e}")
    return results, next_key


def get_slack_messages(since_ts=None, limit=20, alarm_name=None, thread_ts=None):
    """Fetch Slack messages. thread_ts가 있으면 해당 thread replies를 직접 반환."""
    slack = _get_slack_config()
    if not slack:
        return {"ok": False, "error": "Slack 설정 없음", "messages": []}
    token = slack["bot_token"]
    channel = slack.get("channel_id", "")

    try:
        import urllib.request

        if thread_ts:
            replies_url = (
                f"https://slack.com/api/conversations.replies"
                f"?channel={channel}&ts={thread_ts}&limit=100"
            )
            req = urllib.request.Request(replies_url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            if not data.get("ok"):
                return {"ok": False, "error": data.get("error", "unknown"), "messages": [], "thread_ts": thread_ts}
            messages = []
            for msg in data.get("messages", []):
                if msg.get("ts") == thread_ts:
                    continue
                messages.append({
                    "text": msg.get("text", ""),
                    "ts": msg.get("ts", ""),
                    "user": msg.get("user", ""),
                    "bot_id": msg.get("bot_id", ""),
                    "is_thread_reply": True,
                })
            messages.sort(key=lambda m: float(m["ts"]))
            return {"ok": True, "messages": messages, "thread_ts": thread_ts}

        if alarm_name:
            hist_url = f"https://slack.com/api/conversations.history?channel={channel}&limit=200"
            req = urllib.request.Request(hist_url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            if not data.get("ok"):
                return {"ok": False, "error": data.get("error", "unknown"), "messages": [], "alarm_name": alarm_name}

            parent_ts = None
            fallback_parent_ts = None
            for msg in data.get("messages", []):
                text = msg.get("text", "")
                msg_ts = float(msg.get("ts", 0))
                if "Investigation started" not in text:
                    continue
                if since_ts is not None and msg_ts < float(since_ts):
                    continue
                if alarm_name in text:
                    parent_ts = msg.get("ts")
                    break
                if fallback_parent_ts is None:
                    fallback_parent_ts = msg.get("ts")

            if not parent_ts:
                parent_ts = fallback_parent_ts

            if not parent_ts:
                return {"ok": True, "messages": [], "alarm_name": alarm_name, "parent_ts": None}

            replies_url = (
                f"https://slack.com/api/conversations.replies"
                f"?channel={channel}&ts={parent_ts}&limit=100"
            )
            req2 = urllib.request.Request(replies_url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                replies_data = json.loads(resp2.read().decode())

            if not replies_data.get("ok"):
                return {"ok": False, "error": replies_data.get("error", "unknown"), "messages": [], "alarm_name": alarm_name}

            messages = []
            for msg in replies_data.get("messages", []):
                if msg.get("ts") == parent_ts:
                    continue
                messages.append({
                    "text": msg.get("text", ""),
                    "ts": msg.get("ts", ""),
                    "user": msg.get("user", ""),
                    "bot_id": msg.get("bot_id", ""),
                    "is_thread_reply": True,
                })
            messages.sort(key=lambda m: float(m["ts"]))
            return {"ok": True, "messages": messages, "alarm_name": alarm_name, "parent_ts": parent_ts}

        url = f"https://slack.com/api/conversations.history?channel={channel}&limit={limit}"
        if since_ts:
            url += f"&oldest={since_ts}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if not data.get("ok"):
            return {"ok": False, "error": data.get("error", "unknown"), "messages": []}
        messages = []
        for msg in data.get("messages", []):
            messages.append({
                "text": msg.get("text", ""),
                "ts": msg.get("ts", ""),
                "user": msg.get("user", ""),
                "bot_id": msg.get("bot_id", ""),
                "is_thread_reply": False,
            })
            reply_count = msg.get("reply_count", 0)
            thread_ts = msg.get("ts", "")
            if reply_count and reply_count > 0 and thread_ts:
                try:
                    replies_url = (
                        f"https://slack.com/api/conversations.replies"
                        f"?channel={channel}&ts={thread_ts}&limit=50"
                    )
                    req2 = urllib.request.Request(replies_url, headers={"Authorization": f"Bearer {token}"})
                    with urllib.request.urlopen(req2, timeout=10) as resp2:
                        replies_data = json.loads(resp2.read().decode())
                    if replies_data.get("ok"):
                        for reply in replies_data.get("messages", []):
                            if reply.get("ts") == thread_ts:
                                continue
                            messages.append({
                                "text": reply.get("text", ""),
                                "ts": reply.get("ts", ""),
                                "user": reply.get("user", ""),
                                "bot_id": reply.get("bot_id", ""),
                                "is_thread_reply": True,
                            })
                except Exception:
                    pass
        messages.reverse()
        return {"ok": True, "messages": messages}
    except Exception as e:
        return {"ok": False, "error": str(e), "messages": []}


def _parse_pods_json(stdout, cluster_ctx=None):
    try:
        pods_data = json.loads(stdout)
        pods = []
        for item in pods_data.get("items", []):
            name = item["metadata"]["name"]
            phase = item["status"].get("phase", "Unknown")
            restarts = 0
            cs = item["status"].get("containerStatuses", [])
            if cs:
                restarts = cs[0].get("restartCount", 0)
            app_label = item["metadata"].get("labels", {}).get("app", "")
            pod = {"name": name, "app": app_label, "phase": phase, "restarts": restarts}
            if cluster_ctx:
                pod["cluster"] = cluster_ctx
            pods.append(pod)
        return pods
    except (json.JSONDecodeError, KeyError):
        return []


def _parse_nodes_json(stdout, cluster_ctx=None):
    try:
        nodes_data = json.loads(stdout)
        nodes = []
        for item in nodes_data.get("items", []):
            name = item["metadata"]["name"]
            conditions = item["status"].get("conditions", [])
            ready = "Unknown"
            for c in conditions:
                if c["type"] == "Ready":
                    ready = c["status"]
            node = {"name": name, "ready": ready}
            if cluster_ctx:
                node["cluster"] = cluster_ctx
            nodes.append(node)
        return nodes
    except (json.JSONDecodeError, KeyError):
        return []


def get_environment_status(namespace=None):
    """Get current environment status (pods, alarms, nodes) across all clusters."""
    ns = namespace or NAMESPACE
    status = {}

    if cluster_manager.is_multi_cluster():
        all_pods = []
        all_nodes = []
        for cluster in cluster_manager.get_clusters():
            ctx = cluster["name"]
            ctx_flag = f"--context {ctx}"
            ok, stdout, _ = _run_cmd(
                f"kubectl get pods -n {ns} -o json {ctx_flag}", timeout=15
            )
            if ok and stdout:
                all_pods.extend(_parse_pods_json(stdout, ctx))
            ok, stdout, _ = _run_cmd(
                f"kubectl get nodes -o json {ctx_flag}", timeout=15
            )
            if ok and stdout:
                all_nodes.extend(_parse_nodes_json(stdout, ctx))
        status["pods"] = all_pods
        status["nodes"] = all_nodes
        status["service_map"] = cluster_manager.get_service_map()
        status["clusters"] = [c["name"] for c in cluster_manager.get_clusters()]
    else:
        ok, stdout, _ = _run_cmd(
            f"kubectl get pods -n {ns} -o json", timeout=15
        )
        status["pods"] = _parse_pods_json(stdout) if ok and stdout else []

        ok, stdout, _ = _run_cmd("kubectl get nodes -o json", timeout=15)
        status["nodes"] = _parse_nodes_json(stdout) if ok and stdout else []

    try:
        import boto3
        cw = boto3.client("cloudwatch", region_name=AWS_REGION)
        _alarm_prefix = os.environ.get(
            "ALARM_PREFIX",
            _cfg("alarm.prefix", _PROJECT_NAME),
        )
        resp = cw.describe_alarms(AlarmNamePrefix=_alarm_prefix)
        status["alarms"] = [
            {"Name": a["AlarmName"], "State": a["StateValue"]}
            for a in resp.get("MetricAlarms", [])
        ]
    except Exception:
        status["alarms"] = []

    return status
