"""
Executor classes for the DevOps Agent Test Simulator.
Extracted from verifier.py: AgentExecutor, AgentSSEExecutor,
ScriptExecutor, ScriptSSEExecutor, and supporting functions.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

import cluster_manager

from verifier_utils import (
    AWS_REGION, NAMESPACE, _cfg,
    _AGENT_SPACE_ID, _WEBHOOK_SECRET, _EVENTS_TABLE, _RUNS_TABLE, _PROJECT_NAME,
    RESULTS_DIR,
    _run_cmd, _cmd_env, _ensure_results_dir,
    _pre_flight_check, _agent_space_session, _send_webhook, _find_task_by_incident_id,
)
from verifier_checkers import (
    VERIFIERS, _classify_step_error, _devops_agent_client,
)
from verifier_base import (
    SimulationRun,
    STEP_UPDATE_RE, VERIFY_PROMPT_TEMPLATE, INVESTIGATE_PROMPT_TEMPLATE,
    _step_to_instruction,
)


class AgentExecutor:
    """AgentCore-based scenario executor. Drop-in replacement for SimulationRun."""

    def __init__(self, scenario, agent_space_id=None, namespace=None):
        self.run_id = str(uuid.uuid4())[:8]
        self.scenario = scenario
        self.scenario_id = scenario["id"]
        self.agent_space_id = agent_space_id or _AGENT_SPACE_ID
        self.namespace = namespace or scenario.get("namespace") or NAMESPACE
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._started_ts = time.time()
        self._slack_thread_ts = None
        self._investigation_task_id = None
        self._incident_id = None
        self._baseline_event_ts = None

        from execution_context import ExecutionContext
        self._exec_ctx = ExecutionContext.for_scenario(scenario, namespace=self.namespace)
        self._scenario_context = self._exec_ctx.kubectl_context or None
        self._scenario_profile = self._exec_ctx.profile or None

        self.completed_at = None
        self.status = "running"
        self.result = None
        self.trigger_output = ""
        self.investigation_summary = None
        self.preflight = None
        self.steps = []
        self._init_steps()
        self._chat_client = None
        self._execution_id = None

    to_dict = SimulationRun.to_dict
    save = SimulationRun.save

    def _init_steps(self):
        verification = self.scenario.get("verification", {})
        step_defs = verification.get("steps") or verification.get("checks") or []
        for step_def in step_defs:
            name = step_def.get("name") or step_def.get("description") or step_def.get("type", "unknown")
            self.steps.append({
                "name": name,
                "type": step_def.get("type", "manual"),
                "config": step_def,
                "status": "pending",
                "detail": "",
                "elapsed": None,
                "checked_at": None,
                "events": [],
            })
        self._verify_start_idx = 0

    # -- Agent session --

    def _ensure_agent_session(self):
        from arch_analysis import AgentChatClient
        session = _agent_space_session()
        self._chat_client = AgentChatClient(self.agent_space_id, session)
        self._execution_id = self._chat_client.create_session()
        print(f"[AgentExecutor] session created: {self._execution_id}")

    # -- Step update parsing --

    def _parse_step_updates(self, text):
        updates = []
        for match in STEP_UPDATE_RE.finditer(text):
            try:
                data = json.loads(match.group(1))
                if "index" in data and "status" in data:
                    updates.append(data)
            except json.JSONDecodeError:
                continue
        return updates

    def _apply_step_update(self, update):
        idx = update.get("index")
        if idx is None or idx < 0 or idx >= len(self.steps):
            return
        step = self.steps[idx]
        new_status = update.get("status", "")
        if new_status in ("checking", "pass", "fail", "skipped"):
            step["status"] = new_status
        if "detail" in update:
            step["detail"] = str(update["detail"])[:500]
        if "elapsed" in update:
            try:
                step["elapsed"] = round(float(update["elapsed"]), 1)
            except (ValueError, TypeError):
                pass

    # -- Step description builder --

    def _build_step_descriptions(self):
        descs = []
        for i, step in enumerate(self.steps):
            cfg = step["config"]
            descs.append({
                "index": i,
                "name": step["name"],
                "type": step["type"],
                "timeout_seconds": cfg.get("timeout", 60),
                "instruction": _step_to_instruction(step["type"], cfg, self.namespace),
            })
        return json.dumps(descs, indent=2, ensure_ascii=False)

    def _build_verification_summary(self):
        lines = []
        for s in self.steps:
            lines.append(f"- [{s['status']}] {s['name']}: {s['detail']}")
        return "\n".join(lines)

    # -- Phase execution --

    def _run_trigger_phase(self):
        """Phase 1: Local trigger execution (reuses SimulationRun logic)."""
        scenario = self.scenario

        # Pre-cleanup
        pre_cleanup = scenario.get("pre_cleanup", {})
        if pre_cleanup:
            self.trigger_output = "🔄 환경 초기화 중..."
            cleanup_cmd = pre_cleanup.get("command", "")
            alarm_names = pre_cleanup.get("reset_alarms", [])
            wait_ok_timeout = pre_cleanup.get("wait_ok_timeout", 120)

            if cleanup_cmd:
                for _sub_cmd in cleanup_cmd.split(" && "):
                    _sub_cmd = _sub_cmd.strip()
                    if not _sub_cmd:
                        continue
                    _cok, _cout, _cerr = _run_cmd(_sub_cmd, timeout=120)
                    if not _cok:
                        print(f"Pre-cleanup failed (continuing): {_sub_cmd[:80]}")

            try:
                import boto3
                _session = boto3.Session(profile_name=self._scenario_profile) if self._scenario_profile else boto3.Session()
                cw = _session.client("cloudwatch", region_name=AWS_REGION)
                for alarm in alarm_names:
                    cw.set_alarm_state(AlarmName=alarm, StateValue="OK", StateReason="Pre-test reset by simulator")
            except Exception as e:
                print(f"Alarm reset failed: {e}")

            if alarm_names:
                self.trigger_output = "⏳ 알람 OK 대기 중..."
                deadline = time.time() + wait_ok_timeout
                try:
                    import boto3
                    _session = boto3.Session(profile_name=self._scenario_profile) if self._scenario_profile else boto3.Session()
                    cw = _session.client("cloudwatch", region_name=AWS_REGION)
                    while time.time() < deadline:
                        resp = cw.describe_alarms(AlarmNames=alarm_names)
                        states = [a["StateValue"] for a in resp.get("MetricAlarms", [])]
                        if all(s == "OK" for s in states):
                            self.trigger_output = "✅ 환경 초기화 완료"
                            break
                        time.sleep(15)
                except Exception as e:
                    print(f"Alarm wait failed: {e}")

        # Trigger command
        trigger = scenario.get("trigger", {})
        command = trigger.get("command", "")
        if not command and isinstance(trigger.get("commands"), list):
            command = " && ".join(trigger["commands"])

        # Variable substitution
        if command and "${AWS_ACCOUNT_ID}" in command:
            acct_id = None
            if self._scenario_context:
                _m = re.search(r':(\d{12}):', self._scenario_context)
                if _m:
                    acct_id = _m.group(1)
            if not acct_id and self._scenario_profile:
                for _aid, _prof in cluster_manager._account_profile_map.items():
                    if _prof == self._scenario_profile:
                        acct_id = _aid
                        break
            if acct_id:
                command = command.replace("${AWS_ACCOUNT_ID}", acct_id)

        if command:
            ok, stdout, stderr = _run_cmd(command, timeout=120)
            self.trigger_output = stdout or stderr
            return ok
        else:
            self.trigger_output = "트리거 명령 없음"
            return True

    def _run_verify_phase(self):
        """Phase 2: Agent-driven verification with [STEP_UPDATE] markers."""
        if not self.steps:
            self.result = "pass"
            return

        prompt = VERIFY_PROMPT_TEMPLATE.format(
            scenario_name=self.scenario.get("name", ""),
            purpose=self.scenario.get("purpose", ""),
            trigger_output=self.trigger_output[:1000],
            namespace=self.namespace,
            region=AWS_REGION,
            step_descriptions=self._build_step_descriptions(),
        )

        try:
            response = self._chat_client.ask(self._execution_id, prompt)
            text = response.final_text or response.raw_text or ""

            # Parse intermediate [STEP_UPDATE] markers
            updates = self._parse_step_updates(text)
            for update in updates:
                self._apply_step_update(update)

            # Parse final JSON result (overrides intermediate markers)
            final = response.parsed_json
            if final and final.get("phase") == "verify":
                for step_data in final.get("steps", []):
                    self._apply_step_update(step_data)

        except Exception as e:
            print(f"[AgentExecutor] Verify phase error: {e}")
            for step in self.steps:
                if step["status"] in ("pending", "checking"):
                    step["status"] = "fail"
                    step["detail"] = f"Agent 검증 오류: {e}"

        # Fallback: any step still pending → run classic verifier
        pending_steps = [i for i, s in enumerate(self.steps) if s["status"] in ("pending", "checking")]
        if pending_steps:
            print(f"[AgentExecutor] {len(pending_steps)} steps still pending, fallback to classic")
            self._fallback_classic(pending_steps)

        # Determine result
        passed = sum(1 for s in self.steps if s["status"] == "pass")
        total = len(self.steps)
        manual_skipped = sum(1 for s in self.steps if s["type"] == "manual" and s["status"] == "skipped")
        if passed == total:
            self.result = "pass"
        elif passed + manual_skipped == total:
            self.result = "partial"
        else:
            self.result = "fail"

    def _fallback_classic(self, step_indices):
        """Run specified steps using classic VERIFIERS dict."""
        start_time = time.time()
        for idx in step_indices:
            step = self.steps[idx]
            if self.status == "cancelled":
                step["status"] = "skipped"
                continue
            config = step["config"]
            config["_namespace"] = self.namespace
            if self._scenario_context:
                config["_scenario_context"] = self._scenario_context
            if self._scenario_profile:
                config["_scenario_profile"] = self._scenario_profile
            verifier = VERIFIERS.get(step["type"])
            if not verifier:
                step["status"] = "fail"
                step["detail"] = f"알 수 없는 검증 타입: {step['type']}"
                continue
            step["status"] = "checking"
            timeout = config.get("timeout", 60)
            poll_interval = config.get("poll_interval", 10)
            deadline = time.time() + timeout
            passed = False
            while time.time() < deadline and self.status != "cancelled":
                if step["type"] in ("slack_message", "investigation_event", "fis_experiment", "agent_investigation"):
                    config["_run_started_at"] = self._started_ts
                    config["_run_obj"] = self
                ok, detail = verifier(config)
                step["detail"] = detail
                if ok:
                    step["status"] = "pass"
                    step["elapsed"] = round(time.time() - start_time, 1)
                    passed = True
                    break
                time.sleep(poll_interval)
            if not passed and step["status"] == "checking":
                step["status"] = "fail"
                timed_out = True
                step["detail"] = f"시간 초과 ({timeout}s) - {step['detail']}"
                step["elapsed"] = round(time.time() - start_time, 1)
                cat, reason = _classify_step_error(step["type"], step["detail"], timed_out=timed_out)
                step["error_category"] = cat
                step["error_reason"] = reason

    def _run_investigate_phase(self):
        """Phase 3: Trigger investigation webhook + optional Agent investigation."""
        if not self._incident_id:
            try:
                alarm_name = f"scenario-{self.scenario_id}"
                alarm_desc = self.scenario.get("purpose", self.scenario.get("name", ""))
                iid = _send_webhook(alarm_name, alarm_desc)
                if iid:
                    self._incident_id = iid
                    print(f"[AgentExecutor] investigation triggered: incident={iid}")
            except Exception as e:
                print(f"[AgentExecutor] webhook failed: {e}")

        # Poll for task mapping
        if self._incident_id and not self._investigation_task_id:
            deadline = time.time() + 60
            while time.time() < deadline:
                try:
                    task_id, _st = _find_task_by_incident_id(self._incident_id)
                    if task_id:
                        self._investigation_task_id = task_id
                        print(f"[AgentExecutor] investigation task: {task_id}")
                        break
                except Exception:
                    pass
                time.sleep(5)

        # Agent-driven investigation
        if self._chat_client and self._execution_id:
            try:
                prompt = INVESTIGATE_PROMPT_TEMPLATE.format(
                    scenario_name=self.scenario.get("name", ""),
                    purpose=self.scenario.get("purpose", ""),
                    expected_root_cause=self.scenario.get("expected_root_cause", ""),
                    verification_summary=self._build_verification_summary(),
                )
                resp = self._chat_client.ask(self._execution_id, prompt)
                if resp.parsed_json:
                    self.investigation_summary = resp.parsed_json
            except Exception as e:
                print(f"[AgentExecutor] investigate error: {e}")

    # -- Main pipeline --

    def _run_pipeline(self):
        """Background thread entry: preflight → trigger → verify → investigate → restore."""
        try:
            # Baseline event timestamp
            try:
                tbl = _agent_space_session().resource("dynamodb", region_name=AWS_REGION).Table(_EVENTS_TABLE)
                scan = tbl.scan()
                evts = sorted(scan.get("Items", []), key=lambda x: x.get("received_at", ""), reverse=True)
                if evts:
                    self._baseline_event_ts = evts[0].get("received_at", "")
            except Exception as e:
                print(f"Baseline event ts failed: {e}")

            # Pre-flight
            self.trigger_output = "🔍 Pre-flight check..."
            pf_ok, pf_results = _pre_flight_check(self, self.scenario)
            self.preflight = pf_results
            if not pf_ok:
                failed = [r for r in pf_results if not r["ok"]]
                self.trigger_output = "Pre-flight failed: " + "; ".join(r["detail"] for r in failed)
                self.status = "preflight_failed"
                self.result = "preflight_failed"
                self.completed_at = datetime.now(timezone.utc).isoformat()
                for step in self.steps:
                    step["status"] = "skipped"
                    step["detail"] = "Pre-flight 실패로 건너뜀"
                self.save()
                return

            # Agent session
            self._ensure_agent_session()

            # Phase 1: Trigger (local)
            self.status = "running"
            trigger_ok = self._run_trigger_phase()
            if not trigger_ok:
                for step in self.steps:
                    step["status"] = "skipped"
                    step["detail"] = "Trigger 실패로 건너뜀"
                self.status = "completed"
                self.result = "fail"
                self.completed_at = datetime.now(timezone.utc).isoformat()
                self.save()
                return

            # Phase 2: Verify (Agent)
            self.status = "verifying"
            self._run_verify_phase()

            # Phase 3: Investigate
            self._run_investigate_phase()

            # Finalize
            if self.status != "cancelled":
                self.status = "completed"
            self.completed_at = datetime.now(timezone.utc).isoformat()
            self.save()

            # Restore
            restore_cmd = self.scenario.get("restore", {}).get("command", "")
            if restore_cmd:
                try:
                    _run_cmd(restore_cmd, timeout=60)
                    print(f"[AgentExecutor] restore executed for {self.scenario_id}")
                except Exception as e:
                    print(f"[AgentExecutor] restore failed: {e}")

        except Exception as e:
            print(f"[AgentExecutor] Pipeline error: {e}")
            self.status = "completed"
            self.result = "fail"
            self.trigger_output = f"Pipeline error: {e}"
            self.completed_at = datetime.now(timezone.utc).isoformat()
            self.save()
        finally:
            def cleanup():
                time.sleep(300)
                from verifier import _active_runs, _runs_lock
                with _runs_lock:
                    _active_runs.pop(self.run_id, None)
            threading.Thread(target=cleanup, daemon=True).start()


class AgentSSEExecutor(AgentExecutor):
    """AgentExecutor with SSE event emission between phases."""

    def __init__(self, scenario, agent_space_id=None, namespace=None,
                 event_callback=None):
        super().__init__(scenario, agent_space_id, namespace)
        self._event_cb = event_callback or (lambda e: None)
        self._review_session = False

    def set_review_session(self, chat_client, execution_id):
        self._chat_client = chat_client
        self._execution_id = execution_id
        self._review_session = True

    def _emit(self, event_type, **kwargs):
        self._event_cb({"type": event_type, "timestamp": time.time(), **kwargs})

    def _run_pipeline(self):
        try:
            self._emit("phase_start", phase="preflight", label="사전 점검")
            pf_ok, pf_results = _pre_flight_check(self, self.scenario)
            self.preflight = pf_results
            self._emit("preflight_result", ok=pf_ok, results=pf_results)

            if not pf_ok:
                failed = [r for r in pf_results if not r["ok"]]
                self.trigger_output = "Pre-flight failed: " + "; ".join(r["detail"] for r in failed)
                self.status = "preflight_failed"
                self.result = "preflight_failed"
                self.completed_at = datetime.now(timezone.utc).isoformat()
                for step in self.steps:
                    step["status"] = "skipped"
                    step["detail"] = "Pre-flight 실패로 건너뜀"
                self.save()
                self._emit("complete", status="preflight_failed")
                return

            if not self._review_session:
                self._emit("phase_start", phase="session", label="Agent 세션 생성")
                self._ensure_agent_session()
            self._emit("session_ready", execution_id=self._execution_id)

            self._emit("phase_start", phase="trigger", label="장애 주입")
            self.status = "running"
            trigger_ok = self._run_trigger_phase()
            self._emit("trigger_result", ok=trigger_ok, output=self.trigger_output[:500])

            if not trigger_ok:
                for step in self.steps:
                    step["status"] = "skipped"
                    step["detail"] = "Trigger 실패로 건너뜀"
                self.status = "completed"
                self.result = "fail"
                self.completed_at = datetime.now(timezone.utc).isoformat()
                self.save()
                self._emit("complete", status="completed", result="fail")
                return

            self._emit("phase_start", phase="verify", label="검증")
            self.status = "verifying"
            self._run_verify_phase_sse()

            self._emit("phase_start", phase="investigate", label="근본 원인 조사")
            self._run_investigate_phase()
            self._emit("investigate_result", summary=self.investigation_summary)

            if self.status != "cancelled":
                self.status = "completed"
            self.completed_at = datetime.now(timezone.utc).isoformat()
            self.save()
            self._emit("complete", status="completed", result=self.result, run=self.to_dict())

            restore_cmd = self.scenario.get("restore", {}).get("command", "")
            if restore_cmd:
                try:
                    _run_cmd(restore_cmd, timeout=60)
                except Exception as e:
                    print(f"[AgentSSEExecutor] restore failed: {e}")

        except Exception as e:
            print(f"[AgentSSEExecutor] Pipeline error: {e}")
            self.status = "completed"
            self.result = "fail"
            self.trigger_output = f"Pipeline error: {e}"
            self.completed_at = datetime.now(timezone.utc).isoformat()
            self.save()
            self._emit("error", error=str(e))
        finally:
            def cleanup():
                time.sleep(300)
                from verifier import _active_runs, _runs_lock
                with _runs_lock:
                    _active_runs.pop(self.run_id, None)
            threading.Thread(target=cleanup, daemon=True).start()

    def _run_verify_phase_sse(self):
        if not self.steps:
            self.result = "pass"
            return

        prompt = VERIFY_PROMPT_TEMPLATE.format(
            scenario_name=self.scenario.get("name", ""),
            purpose=self.scenario.get("purpose", ""),
            trigger_output=self.trigger_output[:1000],
            namespace=self.namespace,
            region=AWS_REGION,
            step_descriptions=self._build_step_descriptions(),
        )

        try:
            response = self._chat_client.ask(self._execution_id, prompt)
            text = response.final_text or response.raw_text or ""

            updates = self._parse_step_updates(text)
            for update in updates:
                self._apply_step_update(update)
                self._emit("step_update", step=update)

            final = response.parsed_json
            if final and final.get("phase") == "verify":
                for step_data in final.get("steps", []):
                    self._apply_step_update(step_data)
                    self._emit("step_update", step=step_data)

            if response.tool_calls:
                tool_texts = [b.text[:120] for b in response.tool_calls[:10]]
                self._emit("agent_tools", tools=tool_texts)

        except Exception as e:
            print(f"[AgentSSEExecutor] Verify error: {e}")
            self._emit("verify_error", error=str(e))
            for step in self.steps:
                if step["status"] in ("pending", "checking"):
                    step["status"] = "fail"
                    step["detail"] = f"Agent 검증 오류: {e}"

        pending = [i for i, s in enumerate(self.steps) if s["status"] in ("pending", "checking")]
        if pending:
            self._emit("fallback_start", count=len(pending))
            self._fallback_classic(pending)

        passed = sum(1 for s in self.steps if s["status"] == "pass")
        total = len(self.steps)
        manual_skipped = sum(1 for s in self.steps if s["type"] == "manual" and s["status"] == "skipped")
        if passed == total:
            self.result = "pass"
        elif passed + manual_skipped == total:
            self.result = "partial"
        else:
            self.result = "fail"


# ---------------------------------------------------------------------------
# Script-based execution: ScriptExecutor + alarm-centric verification
# ---------------------------------------------------------------------------

def _extract_alarm_names(scenario):
    """시나리오에서 검증 대상 alarm 이름 추출."""
    alarms = []
    v = scenario.get("verification", {})

    for a in v.get("alarms", []):
        alarms.append({
            "name": a["name"],
            "expected": a.get("expected", "ALARM"),
            "timeout": a.get("timeout", 300),
            "poll_interval": a.get("poll_interval", 15),
        })

    for step in v.get("steps", []):
        if step.get("type") in ("cw_alarm", "alarm_state"):
            name = step.get("alarm") or step.get("alarm_name", "")
            if name and not any(a["name"] == name for a in alarms):
                alarms.append({
                    "name": name,
                    "expected": step.get("expected", "ALARM"),
                    "timeout": step.get("timeout", 300),
                    "poll_interval": step.get("poll_interval", 15),
                })

    for name in scenario.get("pre_cleanup", {}).get("reset_alarms", []):
        if not any(a["name"] == name for a in alarms):
            alarms.append({"name": name, "expected": "ALARM", "timeout": 300, "poll_interval": 15})

    return alarms


def verify_alarms(alarm_names_cfg, profile=None):
    """CloudWatch alarm 상태를 polling하여 검증.

    alarm_names_cfg: list of {"name", "expected", "timeout", "poll_interval"}
    Returns: list of {"alarm", "status", "current_state", "elapsed"}
    """
    import boto3
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    cw = session.client("cloudwatch", region_name=AWS_REGION)
    results = []

    for acfg in alarm_names_cfg:
        name = acfg["name"]
        expected = acfg.get("expected", "ALARM")
        timeout = acfg.get("timeout", 300)
        poll_interval = acfg.get("poll_interval", 15)
        start = time.time()
        status = "fail"
        current_state = "UNKNOWN"

        deadline = start + timeout
        while time.time() < deadline:
            try:
                resp = cw.describe_alarms(AlarmNames=[name])
                metric_alarms = resp.get("MetricAlarms", [])
                if metric_alarms:
                    current_state = metric_alarms[0]["StateValue"]
                    if current_state == expected:
                        status = "pass"
                        break
            except Exception as e:
                current_state = f"ERROR: {e}"
            time.sleep(poll_interval)

        timed_out = status == "fail" and time.time() >= deadline
        results.append({
            "alarm": name,
            "status": status,
            "current_state": current_state,
            "elapsed": round(time.time() - start, 1),
            "timed_out": timed_out,
        })
    return results


def _fix_bash_compat(script):
    """macOS bash 3 호환성 처리."""
    script = script.replace('declare -A ', '# declare -A ')
    script = script.replace('set -euo pipefail', 'set -e')
    return script


CHECKPOINT_RE = re.compile(r'^CHECKPOINT\|(\d+)\|(.+?)\|(\w+)\|(.*)$', re.MULTILINE)
RESULT_RE = re.compile(r'^RESULT\|(\d+)/(\d+)$', re.MULTILINE)


def _parse_checkpoints(stdout):
    """Parse CHECKPOINT|N|name|status|detail lines from script stdout."""
    checkpoints = []
    for m in CHECKPOINT_RE.finditer(stdout):
        checkpoints.append({
            "step": int(m.group(1)),
            "name": m.group(2),
            "status": m.group(3),
            "detail": m.group(4),
        })
    result_m = RESULT_RE.search(stdout)
    summary = None
    if result_m:
        summary = {"passed": int(result_m.group(1)), "total": int(result_m.group(2))}
    return checkpoints, summary


def _inject_resume_step(script, resume_from):
    """Inject STEP=<resume_from> at beginning to skip completed steps."""
    return f'RESUME_FROM={resume_from}\n' + script


class ScriptExecutor:
    """스크립트 기반 시나리오 실행기. Agent 생성 run.sh를 subprocess로 실행."""

    def __init__(self, scenario, script, agent_space_id=None, namespace=None, resume_from=0):
        self.run_id = str(uuid.uuid4())[:8]
        self.scenario = scenario
        self.scenario_id = scenario["id"]
        self.script_type = "bash"
        self.agent_space_id = agent_space_id or _AGENT_SPACE_ID
        self.namespace = namespace or scenario.get("namespace") or NAMESPACE
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._started_ts = time.time()
        self._slack_thread_ts = None
        self._investigation_task_id = None
        self._incident_id = None
        self._baseline_event_ts = None
        from execution_context import ExecutionContext
        self._exec_ctx = ExecutionContext.for_scenario(scenario, namespace=self.namespace)
        self._scenario_context = self._exec_ctx.kubectl_context or None
        self._scenario_profile = self._exec_ctx.profile or _cfg("aws.profile", None)
        self.completed_at = None
        self.status = "running"
        self.result = None
        self.trigger_output = ""
        self.investigation_summary = None
        self.preflight = None
        self.steps = []
        self.resume_from = resume_from
        script = _fix_bash_compat(script)
        if resume_from > 0:
            script = _inject_resume_step(script, resume_from)
        self.script = script
        self.script_output = {"stdout": "", "stderr": "", "exit_code": None}
        self.checkpoints = []
        self.alarm_results = []
        self._script_lines = []
        self._current_phase = "init"
        self._alarm_cfgs = _extract_alarm_names(scenario)
        self._init_steps()

    def _init_steps(self):
        """Alarm-centric: alarm에서 step 자동 생성 + 비-alarm step 보존."""
        for acfg in self._alarm_cfgs:
            self.steps.append({
                "name": f"alarm: {acfg['name']}",
                "type": "alarm_state",
                "config": {"alarm": acfg["name"], "expected": acfg["expected"],
                           "timeout": acfg["timeout"], "poll_interval": acfg["poll_interval"]},
                "status": "pending",
                "detail": "",
                "elapsed": None,
                "checked_at": None,
            })
        v = self.scenario.get("verification", {})
        for step_def in v.get("steps", []):
            if step_def.get("type") not in ("cw_alarm", "alarm_state", "metric_check"):
                self.steps.append({
                    "name": step_def["name"],
                    "type": step_def["type"],
                    "config": step_def,
                    "status": "pending",
                    "detail": "",
                    "elapsed": None,
                    "checked_at": None,
                })

    to_dict = SimulationRun.to_dict
    save = SimulationRun.save

    def _inject_context(self, script_text):
        """스크립트 내 kubectl 호출에 --context를 주입."""
        if not self._scenario_context:
            return script_text
        return script_text.replace("kubectl ", f"kubectl --context {self._scenario_context} ")

    def _build_script_env(self):
        env = os.environ.copy()
        env["AWS_PROFILE"] = self._scenario_profile or _cfg("aws.profile", "")
        env["AWS_REGION"] = AWS_REGION
        env["NAMESPACE"] = self.namespace
        env["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:{env.get('PATH', '')}"
        env["PYTHONUNBUFFERED"] = "1"
        if self._scenario_context:
            env["KUBECTL_CONTEXT"] = self._scenario_context
        return env

    def _run_pipeline(self):
        """pre_cleanup → script exec → alarm verify → non-alarm verify → restore."""
        scenario = self.scenario

        # Pre-cleanup
        self._current_phase = "pre_cleanup"
        pre_cleanup = scenario.get("pre_cleanup", {})
        if pre_cleanup:
            self.trigger_output = "환경 초기화 중..."
            cleanup_cmd = pre_cleanup.get("command", "")
            alarm_names = pre_cleanup.get("reset_alarms", [])

            if cleanup_cmd:
                for sub_cmd in cleanup_cmd.split(" && "):
                    sub_cmd = sub_cmd.strip()
                    if sub_cmd:
                        _run_cmd(sub_cmd, timeout=120)

            if alarm_names:
                try:
                    import boto3
                    _session = boto3.Session(profile_name=self._scenario_profile) if self._scenario_profile else boto3.Session()
                    cw = _session.client("cloudwatch", region_name=AWS_REGION)
                    for alarm in alarm_names:
                        cw.set_alarm_state(AlarmName=alarm, StateValue="OK", StateReason="Pre-test reset by simulator")
                except Exception as e:
                    print(f"[ScriptExecutor] Alarm reset failed: {e}")

                wait_ok = pre_cleanup.get("wait_ok_timeout", 120)
                deadline = time.time() + wait_ok
                try:
                    import boto3
                    _session = boto3.Session(profile_name=self._scenario_profile) if self._scenario_profile else boto3.Session()
                    cw = _session.client("cloudwatch", region_name=AWS_REGION)
                    while time.time() < deadline:
                        resp = cw.describe_alarms(AlarmNames=alarm_names)
                        states = [a["StateValue"] for a in resp.get("MetricAlarms", [])]
                        if all(s == "OK" for s in states):
                            break
                        time.sleep(15)
                except Exception as e:
                    print(f"[ScriptExecutor] Alarm wait failed: {e}")

        # Script execution — Popen으로 실시간 stdout 읽기
        self._current_phase = "executing"
        self.trigger_output = "스크립트 실행 중..."
        self.status = "executing"
        exec_timeout = scenario.get("execution_timeout", 360)
        tf_path = None

        try:
            import tempfile
            script_text = self._inject_context(self.script)
            with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as tf:
                tf.write(script_text)
                tf_path = tf.name
            os.chmod(tf_path, 0o755)

            env = self._build_script_env()

            proc = subprocess.Popen(
                ["bash", tf_path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env,
            )

            # stderr를 별도 thread로 수집 (deadlock 방지)
            stderr_lines = []
            def _read_stderr():
                for line in proc.stderr:
                    stderr_lines.append(line.rstrip())
            stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
            stderr_thread.start()

            # timeout 타이머
            timed_out = [False]
            def _kill_on_timeout():
                timed_out[0] = True
                try:
                    proc.kill()
                except Exception:
                    pass
            timer = threading.Timer(exec_timeout, _kill_on_timeout)
            timer.start()

            # stdout 줄 단위 실시간 읽기
            full_stdout = []
            for line in proc.stdout:
                line = line.rstrip()
                full_stdout.append(line)
                # 최대 200줄 유지
                self._script_lines.append(line)
                if len(self._script_lines) > 200:
                    self._script_lines.pop(0)
                # CHECKPOINT 즉시 파싱
                m = CHECKPOINT_RE.match(line)
                if m:
                    cp = {"step": int(m.group(1)), "name": m.group(2),
                          "status": m.group(3), "detail": m.group(4)}
                    self.checkpoints.append(cp)
                    self.trigger_output = f"CP{cp['step']}: {cp['name']} → {cp['status']}"
                    try:
                        self.save()
                    except Exception:
                        pass
                elif line and not line.startswith("RESULT|"):
                    self.trigger_output = line[:120]

            proc.wait()
            timer.cancel()
            stderr_thread.join(timeout=5)

            stdout_text = "\n".join(full_stdout)
            stderr_text = "\n".join(stderr_lines)

            if timed_out[0]:
                self.script_output = {
                    "stdout": stdout_text[-3000:],
                    "stderr": f"timeout ({exec_timeout}s)\n{stderr_text[-1000:]}",
                    "exit_code": -1,
                }
                self.trigger_output = f"스크립트 타임아웃 ({exec_timeout}s)"
            else:
                self.script_output = {
                    "stdout": stdout_text[-5000:],
                    "stderr": stderr_text[-2000:],
                    "exit_code": proc.returncode,
                }
                self.trigger_output = f"스크립트 완료 (exit={proc.returncode})"
                print(f"[ScriptExecutor] exit={proc.returncode}, stdout={len(stdout_text)}B, stderr={len(stderr_text)}B")

            if self.checkpoints:
                print(f"[ScriptExecutor] checkpoints: {len(self.checkpoints)}")
        except Exception as e:
            self.script_output = {"stdout": "", "stderr": str(e), "exit_code": -1}
            self.trigger_output = f"스크립트 실행 오류: {e}"
        finally:
            if tf_path:
                try:
                    os.unlink(tf_path)
                except Exception:
                    pass

        # Alarm-centric verification
        self._current_phase = "alarm_verify"
        if self._alarm_cfgs:
            # 스크립트 체크포인트에서 ALARM 전환이 PASS면 후속 polling 불필요
            alarm_cp_passed = any(
                cp.get("status") == "PASS" and "ALARM" in (cp.get("name", "") + cp.get("detail", ""))
                for cp in self.checkpoints
            )

            if alarm_cp_passed:
                self.alarm_results = []
                for step in self.steps:
                    if step["type"] == "alarm_state" and step["status"] == "pending":
                        step["status"] = "pass"
                        step["detail"] = "스크립트 체크포인트에서 검증 완료"
                        step["checked_at"] = datetime.now(timezone.utc).isoformat()
            else:
                self.status = "verifying"
                self.trigger_output = "알람 검증 중..."
                self.alarm_results = verify_alarms(self._alarm_cfgs, profile=self._scenario_profile)
                for ar in self.alarm_results:
                    for step in self.steps:
                        if step["type"] == "alarm_state" and step["config"].get("alarm") == ar["alarm"]:
                            step["status"] = ar["status"]
                            step["detail"] = f"state={ar['current_state']}"
                            step["elapsed"] = ar["elapsed"]
                            step["checked_at"] = datetime.now(timezone.utc).isoformat()
                            if ar["status"] == "fail":
                                cat, reason = _classify_step_error(step["type"], step["detail"], timed_out=(ar.get("timed_out", False)))
                                step["error_category"] = cat
                                step["error_reason"] = reason

        # Non-alarm steps fallback (kubectl_check, investigation_event, etc.)
        self._current_phase = "step_verify"
        non_alarm = [s for s in self.steps if s["type"] not in ("alarm_state", "cw_alarm") and s["status"] == "pending"]
        for step in non_alarm:
            verifier_fn = VERIFIERS.get(step["type"])
            if verifier_fn:
                cfg = dict(step["config"])
                cfg["_namespace"] = self.namespace
                cfg["_scenario_profile"] = self._scenario_profile
                cfg["_run_started_at"] = self._started_ts
                cfg["_run_obj"] = self
                start_t = time.time()
                timeout = cfg.get("timeout", 60)
                poll_interval = cfg.get("poll_interval", 10)
                deadline = time.time() + timeout
                passed = False
                while time.time() < deadline and self.status != "cancelled":
                    ok, detail = verifier_fn(cfg)
                    step["detail"] = detail
                    if ok:
                        step["status"] = "pass"
                        step["elapsed"] = round(time.time() - start_t, 1)
                        passed = True
                        break
                    time.sleep(poll_interval)
                if not passed and step["status"] != "pass":
                    step["status"] = "fail"
                    step["detail"] = f"시간 초과 ({timeout}s) - {step.get('detail', '')}"
                    step["elapsed"] = round(time.time() - start_t, 1)
                    cat, reason = _classify_step_error(step["type"], step["detail"], timed_out=True)
                    step["error_category"] = cat
                    step["error_reason"] = reason
                step["checked_at"] = datetime.now(timezone.utc).isoformat()

        # Result — checkpoints are primary signal for script-based execution
        cp_passed = sum(1 for c in self.checkpoints if c["status"] == "PASS")
        cp_total = len(self.checkpoints)
        script_ok = self.script_output.get("exit_code") == 0

        if cp_total > 0 and script_ok:
            self.result = "pass" if cp_passed == cp_total else "fail"
        elif cp_total > 0:
            self.result = "fail"
        else:
            step_passed = sum(1 for s in self.steps if s["status"] == "pass")
            step_total = len(self.steps)
            if step_total == 0:
                self.result = "pass" if script_ok else "fail"
            elif step_passed == step_total:
                self.result = "pass"
            else:
                self.result = "fail"

        print(f"[ScriptExecutor] result={self.result} (cp={cp_passed}/{cp_total}, exit={self.script_output.get('exit_code')})")

        # Restore
        self._current_phase = "restore"
        restore = scenario.get("restore", scenario.get("recovery", {}))
        restore_cmd = restore.get("command", "")
        if restore_cmd:
            self.trigger_output = "복원 중..."
            try:
                _run_cmd(restore_cmd, timeout=120)
                print(f"[ScriptExecutor] restore done")
            except Exception as e:
                print(f"[ScriptExecutor] restore failed: {e}")

        # DevOps Agent 조사 트리거
        self._current_phase = "agent_trigger"
        if not self._investigation_task_id and not self._incident_id:
            try:
                alarm_name = f"scenario-{self.scenario_id}"
                alarm_desc = scenario.get("purpose", scenario.get("name", ""))
                iid = _send_webhook(alarm_name, alarm_desc)
                if iid:
                    self._incident_id = iid
                    self.trigger_output = f"Agent 조사 트리거: incident={iid[:12]}..."
                    print(f"[ScriptExecutor] investigation triggered: incident={iid}")
            except Exception as e:
                print(f"[ScriptExecutor] webhook failed: {e}")

        if not self._investigation_task_id and self._incident_id:
            try:
                task_id, _st = _find_task_by_incident_id(self._incident_id)
                if task_id:
                    self._investigation_task_id = task_id
                    print(f"[ScriptExecutor] investigation task: {task_id}")
            except Exception as e:
                print(f"[ScriptExecutor] task lookup failed: {e}")

        self._current_phase = "done"
        self.status = "completed"
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.save()

        def cleanup():
            time.sleep(300)
            from verifier import _active_runs, _runs_lock
            with _runs_lock:
                _active_runs.pop(self.run_id, None)
        threading.Thread(target=cleanup, daemon=True).start()

    def to_dict(self):
        d = SimulationRun.to_dict(self)
        d["script_output"] = self.script_output
        d["alarm_results"] = self.alarm_results
        d["checkpoints"] = self.checkpoints
        d["script_log"] = self._script_lines[-50:]
        d["current_phase"] = self._current_phase
        d["resume_from"] = self.resume_from
        d["script_type"] = getattr(self, "script_type", "bash")
        last_pass = 0
        for cp in self.checkpoints:
            if cp["status"] == "PASS":
                last_pass = max(last_pass, cp["step"])
        d["last_passed_step"] = last_pass
        return d


class ScriptSSEExecutor(ScriptExecutor):
    """ScriptExecutor + SSE event emission."""

    def __init__(self, scenario, script, agent_space_id=None, namespace=None, event_callback=None):
        super().__init__(scenario, script, agent_space_id, namespace)
        self._event_cb = event_callback or (lambda e: None)

    def _emit(self, event_type, **kwargs):
        self._event_cb({"type": event_type, "timestamp": time.time(), **kwargs})

    def _run_pipeline(self):
        scenario = self.scenario
        self._emit("phase_start", phase="pre_cleanup")

        # Pre-cleanup (same as ScriptExecutor)
        pre_cleanup = scenario.get("pre_cleanup", {})
        if pre_cleanup:
            cleanup_cmd = pre_cleanup.get("command", "")
            alarm_names = pre_cleanup.get("reset_alarms", [])

            if cleanup_cmd:
                for sub_cmd in cleanup_cmd.split(" && "):
                    sub_cmd = sub_cmd.strip()
                    if sub_cmd:
                        _run_cmd(sub_cmd, timeout=120)

            if alarm_names:
                try:
                    import boto3
                    _session = boto3.Session(profile_name=self._scenario_profile) if self._scenario_profile else boto3.Session()
                    cw = _session.client("cloudwatch", region_name=AWS_REGION)
                    for alarm in alarm_names:
                        cw.set_alarm_state(AlarmName=alarm, StateValue="OK", StateReason="Pre-test reset by simulator")
                except Exception as e:
                    print(f"[ScriptSSEExecutor] Alarm reset failed: {e}")

                wait_ok = pre_cleanup.get("wait_ok_timeout", 120)
                deadline = time.time() + wait_ok
                try:
                    import boto3
                    _session = boto3.Session(profile_name=self._scenario_profile) if self._scenario_profile else boto3.Session()
                    cw = _session.client("cloudwatch", region_name=AWS_REGION)
                    while time.time() < deadline:
                        resp = cw.describe_alarms(AlarmNames=alarm_names)
                        states = [a["StateValue"] for a in resp.get("MetricAlarms", [])]
                        if all(s == "OK" for s in states):
                            break
                        time.sleep(15)
                except Exception as e:
                    print(f"[ScriptSSEExecutor] Alarm wait failed: {e}")

        self._emit("phase_start", phase="script_execution")
        self.status = "executing"
        exec_timeout = scenario.get("execution_timeout", 360)

        try:
            import tempfile
            script_text = self._inject_context(self.script)
            with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as tf:
                tf.write(script_text)
                tf_path = tf.name
            os.chmod(tf_path, 0o755)

            env = self._build_script_env()

            proc = subprocess.run(
                ["bash", tf_path],
                capture_output=True, text=True, timeout=exec_timeout, env=env,
            )
            self.script_output = {
                "stdout": proc.stdout[-5000:] if proc.stdout else "",
                "stderr": proc.stderr[-2000:] if proc.stderr else "",
                "exit_code": proc.returncode,
            }
            self._emit("script_output", exit_code=proc.returncode,
                        stdout_tail=proc.stdout[-500:] if proc.stdout else "",
                        stderr_tail=proc.stderr[-500:] if proc.stderr else "")
            self.trigger_output = f"스크립트 완료 (exit={proc.returncode})"
        except subprocess.TimeoutExpired:
            self.script_output = {"stdout": "", "stderr": f"timeout ({exec_timeout}s)", "exit_code": -1}
            self._emit("script_output", exit_code=-1, error=f"timeout ({exec_timeout}s)")
            self.trigger_output = f"스크립트 타임아웃 ({exec_timeout}s)"
        except Exception as e:
            self.script_output = {"stdout": "", "stderr": str(e), "exit_code": -1}
            self._emit("script_output", exit_code=-1, error=str(e))
            self.trigger_output = f"스크립트 실행 오류: {e}"
        finally:
            try:
                os.unlink(tf_path)
            except Exception:
                pass

        # Alarm verification
        if self._alarm_cfgs:
            self._emit("phase_start", phase="alarm_verification")
            self.status = "verifying"
            self.alarm_results = verify_alarms(self._alarm_cfgs, profile=self._scenario_profile)

            for ar in self.alarm_results:
                self._emit("alarm_check", alarm=ar["alarm"], status=ar["status"],
                           state=ar["current_state"], elapsed=ar["elapsed"])
                for step in self.steps:
                    if step["type"] == "alarm_state" and step["config"].get("alarm") == ar["alarm"]:
                        step["status"] = ar["status"]
                        step["detail"] = f"state={ar['current_state']}"
                        step["elapsed"] = ar["elapsed"]
                        step["checked_at"] = datetime.now(timezone.utc).isoformat()
                        if ar["status"] == "fail":
                            cat, reason = _classify_step_error(step["type"], step["detail"], timed_out=ar.get("timed_out", False))
                            step["error_category"] = cat
                            step["error_reason"] = reason

        # Non-alarm steps
        non_alarm = [s for s in self.steps if s["type"] not in ("alarm_state", "cw_alarm") and s["status"] == "pending"]
        if non_alarm:
            self._emit("phase_start", phase="classic_verification")
            for step in non_alarm:
                verifier_fn = VERIFIERS.get(step["type"])
                if verifier_fn:
                    cfg = dict(step["config"])
                    cfg["_namespace"] = self.namespace
                    cfg["_scenario_profile"] = self._scenario_profile
                    start_t = time.time()
                    ok, detail = verifier_fn(cfg)
                    step["status"] = "pass" if ok else "fail"
                    step["detail"] = detail
                    step["elapsed"] = round(time.time() - start_t, 1)
                    step["checked_at"] = datetime.now(timezone.utc).isoformat()
                    if not ok:
                        cat, reason = _classify_step_error(step["type"], detail)
                        step["error_category"] = cat
                        step["error_reason"] = reason
                    self._emit("step_update", step={"name": step["name"], "status": step["status"], "detail": detail})

        # Result
        passed = sum(1 for s in self.steps if s["status"] == "pass")
        total = len(self.steps)
        if total == 0:
            self.result = "pass" if self.script_output.get("exit_code") == 0 else "fail"
        elif passed == total:
            self.result = "pass"
        else:
            self.result = "fail"

        # Restore
        self._emit("phase_start", phase="restore")
        restore = scenario.get("restore", scenario.get("recovery", {}))
        restore_cmd = restore.get("command", "")
        if restore_cmd:
            try:
                _run_cmd(restore_cmd, timeout=120)
            except Exception as e:
                print(f"[ScriptSSEExecutor] restore failed: {e}")

        self.status = "completed"
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.save()

        self._emit("complete", run_id=self.run_id, result=self.result,
                    passed=passed, total=total, alarm_results=self.alarm_results)

        def cleanup():
            time.sleep(300)
            from verifier import _active_runs, _runs_lock
            with _runs_lock:
                _active_runs.pop(self.run_id, None)
        threading.Thread(target=cleanup, daemon=True).start()


# ═══════════════════════════════════════════════════════════════════════════
# PythonScriptExecutor — runs steps.py via scenario_runner.py
# ═══════════════════════════════════════════════════════════════════════════

JSON_EVENT_RE = re.compile(r'^EVENT\|(.+)$')


class PythonScriptExecutor(ScriptExecutor):
    """Python steps.py 기반 실행기. scenario_runner.py를 subprocess로 실행."""

    def __init__(self, scenario, steps_script, agent_space_id=None, namespace=None, resume_from=0):
        self.steps_script = steps_script
        self.script_type = "python"
        self.json_events = []
        super().__init__(scenario, "", agent_space_id=agent_space_id,
                         namespace=namespace, resume_from=resume_from)
        self.script = ""

    def _run_pipeline(self):
        import tempfile

        self._current_phase = "trigger"
        self.status = "running"

        steps_file = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False,
                                              dir=os.path.dirname(__file__)) as f:
                f.write(self.steps_script)
                steps_file = f.name

            runner_path = os.path.join(os.path.dirname(__file__), "scenario_runner.py")
            cmd = [
                sys.executable, runner_path, steps_file,
                "--namespace", self.namespace,
                "--aws-region", AWS_REGION,
            ]
            if self._scenario_profile:
                cmd.extend(["--aws-profile", self._scenario_profile])
            if self._scenario_context:
                cmd.extend(["--kubectl-context", self._scenario_context])

            alarm_names = [a["name"] for a in self._alarm_cfgs]
            if alarm_names:
                cmd.extend(["--alarm-name", alarm_names[0]])
            if self.resume_from > 0:
                cmd.extend(["--resume-from", str(self.resume_from)])

            env = self._build_script_env()
            env["PYTHONUNBUFFERED"] = "1"

            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env,
            )

            for line in proc.stdout:
                line = line.rstrip("\n")
                self._script_lines.append(line)

                event_m = JSON_EVENT_RE.match(line)
                if event_m:
                    try:
                        event = json.loads(event_m.group(1))
                        self._process_event(event)
                    except json.JSONDecodeError:
                        pass
                    continue

                # Skip CHECKPOINT lines — already handled via EVENT| above
                if CHECKPOINT_RE.match(line):
                    continue

                result_m = RESULT_RE.match(line)
                if result_m:
                    self.trigger_output = line
                    continue

                self.trigger_output = line

            proc.wait(timeout=600)
            stderr = proc.stderr.read()
            self.script_output = {
                "stdout": "\n".join(self._script_lines[-200:]),
                "stderr": stderr[-2000:] if stderr else "",
                "exit_code": proc.returncode,
            }

        except Exception as e:
            self.script_output["stderr"] = str(e)
            self.script_output["exit_code"] = -1
        finally:
            if steps_file:
                try:
                    os.unlink(steps_file)
                except OSError:
                    pass

        passed = sum(1 for c in self.checkpoints if c["status"] == "PASS")
        total = max(len(self.checkpoints), 1)

        if self.script_output.get("exit_code") == 0:
            self.result = "pass"
        elif passed > 0:
            self.result = "partial"
        else:
            self.result = "fail"

        self.status = "completed"
        self.completed_at = datetime.now(timezone.utc).isoformat()

        # Map checkpoints (from scenario_runner events) → self.steps
        self._sync_checkpoints_to_steps()

        if self._alarm_cfgs:
            self.alarm_results = verify_alarms(self._alarm_cfgs, profile=self._scenario_profile)
            for i, ar in enumerate(self.alarm_results):
                if i < len(self.steps):
                    self.steps[i]["status"] = "pass" if ar.get("ok") else "fail"
                    self.steps[i]["detail"] = ar.get("detail", "")

        self.save()

        def _cleanup():
            time.sleep(300)
            from verifier import _active_runs, _runs_lock
            with _runs_lock:
                _active_runs.pop(self.run_id, None)
        threading.Thread(target=_cleanup, daemon=True).start()

    def _sync_checkpoints_to_steps(self):
        """Map checkpoint results from scenario_runner back to self.steps."""
        # Non-alarm steps start after alarm steps
        alarm_count = len(self._alarm_cfgs)
        non_alarm_steps = self.steps[alarm_count:]

        for cp in self.checkpoints:
            cp_name = cp.get("name", "")
            cp_status = "pass" if cp["status"] == "PASS" else ("fail" if cp["status"] == "FAIL" else "skipped")
            cp_detail = cp.get("detail", "")

            # Match by step number (1-indexed from scenario_runner)
            step_idx = cp.get("step", 0) - 1
            if 0 <= step_idx < len(non_alarm_steps):
                target = non_alarm_steps[step_idx]
                target["status"] = cp_status
                target["detail"] = cp_detail
                continue

            # Fallback: match by name similarity
            for step in non_alarm_steps:
                if step["status"] == "pending" and cp_name and cp_name in step["name"]:
                    step["status"] = cp_status
                    step["detail"] = cp_detail
                    break

    def _process_event(self, event):
        """Process JSON event from scenario_runner.py."""
        self.json_events.append(event)
        evt = event.get("event")

        if evt == "step_pass":
            self.checkpoints.append({
                "step": event.get("step", 0),
                "name": event.get("name", ""),
                "status": "PASS",
                "detail": event.get("detail", ""),
            })
            self._periodic_save()
        elif evt == "step_fail":
            self.checkpoints.append({
                "step": event.get("step", 0),
                "name": event.get("name", ""),
                "status": "FAIL",
                "detail": event.get("detail", ""),
            })
            self._periodic_save()
        elif evt == "step_poll":
            elapsed = event.get("elapsed", 0)
            total = event.get("total", 0)
            msg = event.get("message", "")
            self.trigger_output = f"[{elapsed}s/{total}s] {msg}"
        elif evt == "step_start":
            self._current_phase = f"step_{event.get('step', '?')}"
            self.trigger_output = f"Step {event.get('step')}: {event.get('name', '')}"
        elif evt == "step_skip":
            self.checkpoints.append({
                "step": event.get("step", 0),
                "name": event.get("name", ""),
                "status": "SKIP",
                "detail": event.get("detail", ""),
            })
        elif evt == "step_retry":
            attempt = event.get("attempt", 0)
            max_a = event.get("max_attempts", 0)
            self.trigger_output = f"재시도 {attempt}/{max_a}: {event.get('name', '')}"

    def _periodic_save(self):
        """Save intermediate state to DynamoDB for resume capability."""
        try:
            self.save()
        except Exception:
            pass

    def to_dict(self):
        d = super().to_dict()
        d["script_type"] = "python"
        d["json_events"] = self.json_events[-100:]
        return d
