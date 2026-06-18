"""Scenario execution and monitoring routes: executor config, active-runs, /api/scenario-run/*."""
import json
import os
import subprocess

from flask import jsonify, request

from app_config import (
    _CFG, _cfg_get, AWS_REGION, RUNS_TABLE,
    _req_space_id, _agent_space_id, _boto_session,
)
from routes_scenario import scenario_bp
from routes_scenario.crud import _load_scenario_by_id, _resolve_namespace


def _get_scenario_script(scenario_id):
    """Return (script_content, script_type). steps.py takes priority over run.sh."""
    base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scenarios", scenario_id)
    py_path = os.path.join(base, "steps.py")
    if os.path.exists(py_path):
        with open(py_path) as f:
            return f.read(), "python"
    sh_path = os.path.join(base, "run.sh")
    if os.path.exists(sh_path):
        with open(sh_path) as f:
            return f.read(), "bash"
    return None, None


# ---------------------------------------------------------------------------
# Executor config API
# ---------------------------------------------------------------------------
@scenario_bp.route("/api/executor/config")
def api_executor_config():
    """현재 executor 설정 반환."""
    return jsonify({
        "ok": True,
        "default": _cfg_get(_CFG, "executor.default", "classic"),
        "force": _cfg_get(_CFG, "executor.force", False),
        "options": ["classic", "multi_agent"],
    })


@scenario_bp.route("/api/executor/config", methods=["PUT"])
def api_executor_config_update():
    """executor.default 변경 (런타임 — config.yaml 수정)."""
    import yaml
    body = request.json or {}
    new_default = body.get("default", "")
    if new_default not in ("classic", "multi_agent", "agent"):
        return jsonify({"ok": False, "error": f"invalid executor: {new_default}"}), 400

    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        cfg.setdefault("executor", {})["default"] = new_default
        with open(config_path, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
        _CFG["executor"] = cfg["executor"]
        return jsonify({"ok": True, "default": new_default})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Simulation routes (direct verifier integration, no proxy)
# ---------------------------------------------------------------------------
@scenario_bp.route("/api/active-runs")
def api_active_runs():
    """현재 in-memory에 활성화된 실행 목록."""
    from verifier import list_active_runs
    runs = list_active_runs()
    return jsonify({"ok": True, "runs": runs})


@scenario_bp.route("/api/scenario-run/<scenario_id>", methods=["POST"])
def api_scenario_run(scenario_id):
    from verifier import start_run, ScriptExecutor, PythonScriptExecutor
    try:
        space_id = _req_space_id()
        scenario = _load_scenario_by_id(scenario_id, space_id=space_id)
        if not scenario:
            return jsonify({"ok": False, "error": "not found"}), 404

        resume_from = int(request.args.get("resume_from", 0))

        script, script_type = _get_scenario_script(scenario_id)
        namespace = _resolve_namespace(scenario, space_id)

        if script and script_type == "python":
            run = PythonScriptExecutor(scenario, script, agent_space_id=_agent_space_id(),
                                       namespace=namespace, resume_from=resume_from)
            import threading as _thr
            from verifier import _active_runs, _runs_lock
            with _runs_lock:
                _active_runs[run.run_id] = run
            _thr.Thread(target=run._run_pipeline, daemon=True).start()
        elif script:
            run = ScriptExecutor(scenario, script, agent_space_id=_agent_space_id(),
                                 namespace=namespace, resume_from=resume_from)
            import threading as _thr
            from verifier import _active_runs, _runs_lock
            with _runs_lock:
                _active_runs[run.run_id] = run
            _thr.Thread(target=run._run_pipeline, daemon=True).start()
        else:
            run = start_run(scenario, namespace=namespace, agent_space_id=_agent_space_id())

        return jsonify({"ok": True, "run_id": run.run_id, "status": run.status})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@scenario_bp.route("/api/scenario-run/<run_id>/status")
def api_scenario_run_status(run_id):
    from verifier import get_active_run
    run = get_active_run(run_id)
    if run:
        return jsonify(run.to_dict())
    try:
        table = _boto_session().resource("dynamodb").Table(RUNS_TABLE)
        resp = table.get_item(Key={"run_id": run_id, "record_type": "run"})
        item = resp.get("Item")
        if item:
            item = _backfill_investigation(item, table)
            try:
                eval_resp = table.get_item(Key={"run_id": run_id, "record_type": "evaluation"})
                eval_item = eval_resp.get("Item")
                if eval_item:
                    item["evaluation"] = {
                        "overall_score": eval_item.get("overall_score"),
                        "passing_score": eval_item.get("passing_score"),
                        "regression_warning": eval_item.get("regression_warning"),
                    }
            except Exception:
                pass
            return jsonify(json.loads(json.dumps(item, default=str)))
    except Exception:
        pass
    return jsonify({"error": "Run not found"}), 404


def _backfill_investigation(item, table):
    """investigation_task_id backfill + 조사 종료 step 상태 보정."""
    iid = item.get("incident_id", "")
    if not iid:
        return item
    tid = item.get("investigation_task_id")
    space_id = item.get("agent_space_id") or item.get("space_id", "")
    try:
        from verifier_utils import _find_task_by_incident_id
        if not tid:
            task_id, status = _find_task_by_incident_id(iid, space_id=space_id)
            if task_id:
                item["investigation_task_id"] = task_id
                tid = task_id
                table.update_item(
                    Key={"run_id": item["run_id"], "record_type": "run"},
                    UpdateExpression="SET investigation_task_id = :tid",
                    ExpressionAttributeValues={":tid": task_id},
                )
        else:
            status = None
        # 조사 종료 step이 아직 미완료면 API로 상태 확인 후 보정
        steps = item.get("steps", [])
        need_fix = any(
            "조사 종료" in s.get("name", "") and s.get("status") in ("checking", "pending", "warn")
            for s in steps
        )
        if need_fix and tid:
            if status is None:
                _, status = _find_task_by_incident_id(iid, space_id=space_id)
            step_updated = False
            for s in steps:
                if "조사 종료" in s.get("name", "") and s.get("status") in ("checking", "pending", "warn"):
                    s["status"] = "pass" if status in ("COMPLETED", "completed", "done", "LINKED", "linked") else "checking"
                    s["detail"] = f"task: {tid[:20]}" if s["status"] == "pass" else f"진행 중 ({status})"
                    step_updated = True
            if step_updated:
                table.update_item(
                    Key={"run_id": item["run_id"], "record_type": "run"},
                    UpdateExpression="SET steps = :s",
                    ExpressionAttributeValues={":s": steps},
                )
    except Exception:
        pass
    return item


@scenario_bp.route("/api/scenario-runs/<scenario_id>")
def api_scenario_runs(scenario_id):
    """시나리오의 실행 이력 목록 (최신순, 최대 20개)."""
    try:
        from boto3.dynamodb.conditions import Attr
        table = _boto_session().resource("dynamodb").Table(RUNS_TABLE)
        resp = table.scan(
            FilterExpression=Attr("record_type").eq("run") & Attr("scenario_id").eq(scenario_id),
            ProjectionExpression="run_id, scenario_id, #s, #r, investigation_task_id, started_at, completed_at, elapsed",
            ExpressionAttributeNames={"#s": "status", "#r": "result"},
        )
        items = resp.get("Items", [])
        items.sort(key=lambda x: x.get("started_at", ""), reverse=True)
        runs = json.loads(json.dumps(items[:20], default=str))
        return jsonify({"ok": True, "runs": runs})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@scenario_bp.route("/api/scenario-run/<run_id>/resume", methods=["POST"])
def api_scenario_run_resume(run_id):
    """Resume a saved run from the first fail/pending step."""
    from verifier import resume_run, get_active_run, ScriptExecutor, PythonScriptExecutor
    from verifier import _active_runs, _runs_lock
    if get_active_run(run_id):
        return jsonify({"ok": False, "error": "Run already active"}), 409
    try:
        table = _boto_session().resource("dynamodb").Table(RUNS_TABLE)
        resp = table.get_item(Key={"run_id": run_id, "record_type": "run"})
        item = resp.get("Item")
        if not item:
            return jsonify({"ok": False, "error": "Run not found in history"}), 404

        scenario_id = item.get("scenario_id", "")
        space_id = item.get("agent_space_id") or _req_space_id()
        scenario = _load_scenario_by_id(scenario_id, space_id=space_id)
        if not scenario:
            return jsonify({"ok": False, "error": f"Scenario '{scenario_id}' not found"}), 404

        saved_data = json.loads(json.dumps(item, default=str))
        script_type = saved_data.get("script_type", "")
        resume_from = _find_resume_index(saved_data)

        if script_type == "python":
            script, _ = _get_scenario_script(scenario_id)
            if not script:
                return jsonify({"ok": False, "error": "steps.py not found for scenario"}), 404
            namespace = _resolve_namespace(scenario, space_id)
            run = PythonScriptExecutor(scenario, script, agent_space_id=_agent_space_id(),
                                       namespace=namespace, resume_from=resume_from)
            run._incident_id = saved_data.get("incident_id", "") or None
            run._investigation_task_id = saved_data.get("investigation_task_id") or None
            with _runs_lock:
                _active_runs[run.run_id] = run
            import threading as _thr
            _thr.Thread(target=run._run_pipeline, daemon=True).start()
        elif script_type == "bash":
            script, _ = _get_scenario_script(scenario_id)
            if not script:
                return jsonify({"ok": False, "error": "run.sh not found for scenario"}), 404
            namespace = _resolve_namespace(scenario, space_id)
            run = ScriptExecutor(scenario, script, agent_space_id=_agent_space_id(),
                                 namespace=namespace, resume_from=resume_from)
            run._incident_id = saved_data.get("incident_id", "") or None
            run._investigation_task_id = saved_data.get("investigation_task_id") or None
            with _runs_lock:
                _active_runs[run.run_id] = run
            import threading as _thr
            _thr.Thread(target=run._run_pipeline, daemon=True).start()
        else:
            run = resume_run(saved_data, scenario)

        return jsonify({"ok": True, "run_id": run.run_id, "status": run.status,
                        "resume_from": resume_from})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _find_resume_index(saved_data):
    """Find first fail/pending step index from saved run data."""
    steps = saved_data.get("steps", [])
    for i, s in enumerate(steps):
        if s.get("status") in ("fail", "pending", "checking"):
            return i
    return 0


@scenario_bp.route("/api/scenario-run/<run_id>/cancel", methods=["POST"])
def api_scenario_run_cancel(run_id):
    from verifier import cancel_run
    if cancel_run(run_id):
        return jsonify({"success": True})
    return jsonify({"error": "Run not found or already completed"}), 404


@scenario_bp.route("/api/scenario-run/<run_id>/retry/<int:step_index>", methods=["POST"])
def api_scenario_run_retry(run_id, step_index):
    from verifier import retry_from_step
    result = retry_from_step(run_id, step_index)
    if not result:
        return jsonify({"error": "Run not found or invalid step index"}), 404
    if isinstance(result, dict) and result.get("action") == "new_run":
        return jsonify({
            "success": True,
            "action": "new_run",
            "scenario_id": result["scenario_id"],
            "resume_from": result["resume_from"],
        })
    return jsonify({"success": True, "run_id": result.run_id, "status": result.status})


@scenario_bp.route("/api/scenario-run/<run_id>/correct", methods=["POST"])
def api_scenario_run_correct(run_id):
    """실패한 스텝의 error_category에 따라 교정 액션 수행.

    Body: {"space_id": "...", "session_id": "...(optional)"}
    - timeout: 기계적 재시도 (timeout 연장)
    - command_error / config_error: Agent에게 교정 요청
    - transient: 단순 재시도
    - infra_missing: blocked 반환
    """
    from verifier import get_active_run, VERIFIERS, _run_cmd, _classify_step_error
    from routes_scenario.chat import _extract_json_block
    run = get_active_run(run_id)
    if not run:
        return jsonify({"ok": False, "error": "Run not found or completed"}), 404
    if run.status != "completed":
        return jsonify({"ok": False, "error": f"Run still {run.status}"}), 409

    body = request.json or {}
    space_id = body.get("space_id", "").strip() or _req_space_id()
    session_id = body.get("session_id", "")

    failed_steps = [s for s in run.steps if s["status"] == "fail"]
    if not failed_steps:
        return jsonify({"ok": True, "message": "실패한 스텝 없음", "actions": []})

    actions = []
    agent_correction_needed = []

    for step in failed_steps:
        cat = step.get("error_category", "command_error")
        step_info = {"name": step["name"], "type": step["type"],
                     "detail": step["detail"], "category": cat}

        if cat == "infra_missing":
            actions.append({**step_info, "action": "blocked",
                            "message": "인프라 부재 — 수동 조치 필요"})
        elif cat == "transient":
            verifier_fn = VERIFIERS.get(step["type"])
            if verifier_fn:
                cfg = dict(step["config"])
                cfg["_namespace"] = run.namespace
                cfg["_scenario_profile"] = run._scenario_profile
                ok, detail = verifier_fn(cfg)
                step["status"] = "pass" if ok else "fail"
                step["detail"] = detail
                if ok:
                    actions.append({**step_info, "action": "retry_success", "detail": detail})
                else:
                    cat2, reason2 = _classify_step_error(step["type"], detail)
                    step["error_category"] = cat2
                    actions.append({**step_info, "action": "retry_failed",
                                    "escalated_to": cat2, "detail": detail})
                    if cat2 in ("command_error", "config_error"):
                        agent_correction_needed.append(step)
            else:
                actions.append({**step_info, "action": "no_verifier"})
        elif cat == "timeout":
            actions.append({**step_info, "action": "timeout_noted",
                            "message": "timeout — traffic boost + reinject 후 재실행 필요"})
        elif cat in ("command_error", "config_error"):
            agent_correction_needed.append(step)

    # Agent 교정이 필요한 스텝들을 모아서 한번에 요청
    correction_result = None
    if agent_correction_needed:
        lines = ["시나리오 실행 중 다음 검증 스텝이 실패했습니다. 시나리오 JSON의 해당 스텝을 수정해주세요.\n"]
        for s in agent_correction_needed:
            cat = s.get("error_category", "command_error")
            lines.append(f"### 스텝: {s['name']} (type={s['type']}, category={cat})")
            lines.append(f"- 에러: {s['detail']}")
            cmd = s.get("config", {}).get("command", "")
            if cmd:
                lines.append(f"- 명령: `{cmd}`")
            expected = s.get("config", {}).get("expected", "")
            if expected:
                lines.append(f"- 기대값: {expected}")
            lines.append("")

        lines.append("수정된 시나리오 JSON을 ```json 블록으로 반환해주세요.")
        correction_prompt = "\n".join(lines)

        try:
            from ai_provider import get_provider
            resp = get_provider().send_raw(
                space_id=space_id,
                session_id=session_id or "",
                prompt=correction_prompt,
            )
            correction_reply = resp["reply"]
            corrected_scenario = _extract_json_block(correction_reply)

            correction_result = {
                "reply": correction_reply,
                "has_corrected_json": bool(corrected_scenario),
                "session_id": resp.get("session_id", session_id),
            }
            if corrected_scenario:
                correction_result["corrected_scenario"] = corrected_scenario

            for s in agent_correction_needed:
                actions.append({
                    "name": s["name"], "type": s["type"],
                    "detail": s["detail"], "category": s.get("error_category"),
                    "action": "agent_correction_requested",
                })
        except Exception as e:
            correction_result = {"error": str(e)}
            for s in agent_correction_needed:
                actions.append({
                    "name": s["name"], "type": s["type"],
                    "detail": s["detail"], "category": s.get("error_category"),
                    "action": "agent_correction_failed", "error": str(e),
                })

    return jsonify({
        "ok": True,
        "run_id": run_id,
        "actions": actions,
        "correction": correction_result,
    })


@scenario_bp.route("/api/scenario-run/<run_id>/exec-cmd", methods=["POST"])
def api_scenario_run_exec_cmd(run_id):
    """사용자가 직접 명령어를 실행하고 결과를 확인."""
    from verifier import _run_cmd
    body = request.json or {}
    cmd = body.get("command", "").strip()
    if not cmd:
        return jsonify({"ok": False, "error": "명령어 없음"}), 400
    if any(dangerous in cmd for dangerous in ["rm -rf /", "mkfs", "> /dev/"]):
        return jsonify({"ok": False, "error": "위험한 명령어 차단"}), 400
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return jsonify({
            "ok": True,
            "stdout": proc.stdout[-2000:] if proc.stdout else "",
            "stderr": proc.stderr[-1000:] if proc.stderr else "",
            "exit_code": proc.returncode,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "명령어 실행 timeout (30s)"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@scenario_bp.route("/api/scenario-run/<run_id>/restore", methods=["POST"])
def api_scenario_run_restore(run_id):
    from verifier import get_active_run, _run_cmd
    run = get_active_run(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    restore_cmd = run.scenario.get("restore", {}).get("command", "")
    if not restore_cmd:
        return jsonify({"success": True, "message": "복원 명령 없음"})
    import cluster_manager
    restore_cmd = cluster_manager.inject_context(restore_cmd)
    ok, stdout, stderr = _run_cmd(restore_cmd, timeout=60)
    return jsonify({"success": ok, "output": stdout, "error": stderr})
