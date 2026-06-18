"""
SimulationRun base class and prompt templates for LLM-based executors.
Extracted from verifier.py for modularity.
"""
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone

import cluster_manager

from verifier_utils import (
    AWS_REGION, NAMESPACE, _cfg,
    _AGENT_SPACE_ID, _EVENTS_TABLE, _RUNS_TABLE,
    RESULTS_DIR,
    _run_cmd, _cmd_env, _ensure_results_dir,
    _pre_flight_check, _agent_space_session,
    _find_task_by_incident_id, _send_webhook,
)


def _resolve_scenario_variables(command: str, scenario: dict, context: str = None) -> str:
    """시나리오 variables + 글로벌 변수를 command에 치환."""
    if not command:
        return command
    variables = scenario.get("variables", {})
    for key, val in variables.items():
        if isinstance(val, dict):
            continue
        command = command.replace(f"${{{key}}}", str(val))
    namespace = scenario.get("namespace") or NAMESPACE
    command = command.replace("${NAMESPACE}", namespace)
    command = command.replace("${AWS_REGION}", AWS_REGION)
    project_name = _cfg("project.name", os.environ.get("PROJECT_NAME", ""))
    command = command.replace("${PROJECT_NAME}", project_name)
    if context:
        acct = cluster_manager.get_account_for_context(context)
        if acct:
            command = command.replace("${AWS_ACCOUNT_ID}", acct)
    return command


def _resolve_discovery_variables(scenario: dict, context: str = None, profile: str = None):
    """Execute discovery commands in variables and replace with resolved values.
    Mutates scenario in place: resolves variables dict, then replaces ${VAR} across entire scenario.
    """
    variables = scenario.get("variables", {})
    if not variables:
        return
    resolved = {}
    for key, val in list(variables.items()):
        if not isinstance(val, dict) or "discovery" not in val:
            continue
        cmd = val["discovery"]
        cmd = _resolve_scenario_variables(cmd, scenario, context)
        if "aws " in cmd and "--profile " not in cmd and profile:
            cmd = cmd.replace("aws ", f"aws --profile {profile} ", 1)
        ok, stdout, stderr = _run_cmd(cmd, timeout=30, context=context)
        if ok and stdout.strip():
            resolved[key] = stdout.strip()
        else:
            resolved[key] = ""
    for key, val in resolved.items():
        variables[key] = val
    if resolved:
        _apply_variables_to_scenario(scenario, resolved)


def _apply_variables_to_scenario(scenario: dict, resolved: dict):
    """Replace ${VAR} references throughout the entire scenario JSON."""
    def _replace_in_obj(obj):
        if isinstance(obj, str):
            for k, v in resolved.items():
                obj = obj.replace(f"${{{k}}}", v)
            return obj
        elif isinstance(obj, dict):
            return {key: _replace_in_obj(val) for key, val in obj.items()}
        elif isinstance(obj, list):
            return [_replace_in_obj(item) for item in obj]
        return obj

    for key in ("trigger", "verification", "restore", "pre_cleanup"):
        if key in scenario:
            scenario[key] = _replace_in_obj(scenario[key])


class SimulationRun:
    """Represents a single scenario execution with verification tracking."""

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

        print(f"[run] {self.scenario_id} → account={self._exec_ctx.account_id} "
              f"profile={self._scenario_profile} context={self._scenario_context and self._scenario_context[:50]}")

        self.completed_at = None
        self.status = "running"
        self.result = None
        self.trigger_output = ""
        self.investigation_summary = None
        self.preflight = None
        self.steps = []
        self._init_steps()

    def _init_steps(self):
        self.steps.append({
            "name": "사전 점검 (Pre-flight)",
            "type": "pipeline_preflight",
            "config": {},
            "status": "pending",
            "detail": "",
            "elapsed": None,
            "checked_at": None,
            "events": [],
        })
        if self.scenario.get("pre_cleanup"):
            self.steps.append({
                "name": "환경 초기화 (Pre-cleanup)",
                "type": "pipeline_cleanup",
                "config": {},
                "status": "pending",
                "detail": "",
                "elapsed": None,
                "checked_at": None,
                "events": [],
            })
        self.steps.append({
            "name": "장애 주입 (Trigger)",
            "type": "pipeline_trigger",
            "config": {},
            "status": "pending",
            "detail": "",
            "elapsed": None,
            "checked_at": None,
            "events": [],
        })

        self._verify_start_idx = len(self.steps)

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

        if self.scenario.get("restore"):
            self.steps.append({
                "name": "복원 (Restore)",
                "type": "pipeline_restore",
                "config": {},
                "status": "pending",
                "detail": "",
                "elapsed": None,
                "checked_at": None,
                "events": [],
            })

    @staticmethod
    def _log_event(step, msg):
        """Append a timestamped event to step's event log."""
        import time as _t
        step["events"].append({"t": round(_t.time(), 1), "msg": msg})

    def to_dict(self):
        if not self._investigation_task_id and self.status not in ("running", "verifying"):
            if self._incident_id:
                try:
                    tid, _st = _find_task_by_incident_id(
                        self._incident_id, space_id=self.agent_space_id)
                    if tid:
                        self._investigation_task_id = tid
                except Exception:
                    pass

        if self._investigation_task_id and self.status not in ("running", "verifying"):
            _done = {"COMPLETED", "completed", "done", "LINKED", "linked"}
            for s in self.steps:
                if "조사 종료" in s.get("name", "") and s["status"] in ("checking", "warn"):
                    try:
                        _, st = _find_task_by_incident_id(
                            self._incident_id, space_id=self.agent_space_id)
                        if st in _done:
                            s["status"] = "pass"
                            s["detail"] = f"task: {self._investigation_task_id[:20]}"
                    except Exception:
                        pass
                    break

        d = {
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario.get("name", ""),
            "agent_space_id": self.agent_space_id or "",
            "started_at": self.started_at,
            "started_ts": self._started_ts,
            "completed_at": self.completed_at,
            "status": self.status,
            "result": self.result,
            "trigger_output": self.trigger_output,
            "incident_id": self._incident_id or "",
            "investigation_task_id": self._investigation_task_id,
            "architecture": self.scenario.get("architecture"),
            "normal_flow": self.scenario.get("normal_flow", []),
            "fault_flow": self.scenario.get("fault_flow", []),
            "flow": self.scenario.get("flow", []),
            "steps": [
                {
                    "index": i,
                    "name": s["name"],
                    "type": s["type"],
                    "pod": s["config"].get("pod", ""),
                    "status": s["status"],
                    "detail": s["detail"],
                    "elapsed": s["elapsed"],
                    "events": s.get("events", []),
                    **({"error_category": s["error_category"], "error_reason": s["error_reason"]}
                       if s.get("error_category") else {}),
                }
                for i, s in enumerate(self.steps)
            ],
        }
        if self.investigation_summary:
            d["investigation_summary"] = self.investigation_summary
        if self.preflight is not None:
            d["preflight"] = self.preflight
        return d

    @classmethod
    def from_saved(cls, saved_data, scenario):
        """Restore a SimulationRun from DynamoDB saved data.

        Reconstructs the run object with step states preserved,
        allowing resume from the first fail/pending step.
        """
        run = object.__new__(cls)
        run.run_id = saved_data.get("run_id", str(uuid.uuid4())[:8])
        run.scenario = scenario
        run.scenario_id = scenario["id"]
        run.agent_space_id = saved_data.get("agent_space_id", _AGENT_SPACE_ID)
        run.namespace = scenario.get("namespace") or NAMESPACE
        run.started_at = saved_data.get("started_at", "")
        run._started_ts = float(saved_data.get("started_ts", time.time()))
        run._slack_thread_ts = None
        run._investigation_task_id = saved_data.get("investigation_task_id")
        run._incident_id = saved_data.get("incident_id", "")
        run._baseline_event_ts = None
        run._scenario_context = run._detect_target_context()
        run._scenario_profile = run._detect_target_profile()
        run._managed_alarms = []
        run.completed_at = saved_data.get("completed_at")
        run.status = saved_data.get("status", "interrupted")
        run.result = saved_data.get("result")
        run.trigger_output = saved_data.get("trigger_output", "")
        run.investigation_summary = saved_data.get("investigation_summary")
        run.preflight = saved_data.get("preflight")

        # Restore steps with their states
        run.steps = []
        saved_steps = saved_data.get("steps", [])
        run._init_steps()

        # Map saved step states onto initialized steps
        for i, step in enumerate(run.steps):
            if i < len(saved_steps):
                ss = saved_steps[i]
                step["status"] = ss.get("status", "pending")
                step["detail"] = ss.get("detail", "")
                step["elapsed"] = ss.get("elapsed")
                step["events"] = ss.get("events", [])
                if ss.get("error_category"):
                    step["error_category"] = ss["error_category"]
                    step["error_reason"] = ss.get("error_reason", "")

        run._verify_start_idx = next(
            (i for i, s in enumerate(run.steps) if not s["type"].startswith("pipeline_")),
            len(run.steps)
        )
        return run

    def get_resume_index(self):
        """Find the first step that should be resumed (first fail/pending after last pass)."""
        for i, step in enumerate(self.steps):
            if step["type"].startswith("pipeline_"):
                continue
            if step["status"] in ("fail", "pending", "checking"):
                return i
        return len(self.steps)

    def save(self):
        """Save run result to DynamoDB."""
        try:
            from decimal import Decimal
            table = _agent_space_session().resource("dynamodb", region_name=AWS_REGION).Table(_RUNS_TABLE)
            d = self.to_dict()
            item = json.loads(json.dumps(d), parse_float=Decimal)
            item["run_id"] = self.run_id
            item["record_type"] = "run"
            item["scenario_id"] = self.scenario_id
            if self.agent_space_id:
                item["agent_space_id"] = self.agent_space_id
            table.put_item(Item=item)
            print(f"Saved run {self.run_id} to DynamoDB")
        except Exception as e:
            print(f"DynamoDB save failed, falling back to file: {e}")
            _ensure_results_dir()
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            filename = f"{ts}_{self.scenario_id}_{self.run_id}.json"
            filepath = os.path.join(RESULTS_DIR, filename)
            with open(filepath, "w") as f:
                json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        return self.run_id

    def _get_pipeline_step(self, step_type):
        """Find a pipeline step by type."""
        for s in self.steps:
            if s["type"] == step_type:
                return s
        return None

    def _run_pipeline(self):
        """Execute the full scenario pipeline: preflight -> trigger -> verify -> investigate -> restore."""
        from verifier import _verification_loop
        _ev = SimulationRun._log_event
        scenario = self.scenario

        self.save()

        try:
            tbl = _agent_space_session().resource("dynamodb", region_name=AWS_REGION).Table(_EVENTS_TABLE)
            scan = tbl.scan()
            evts = sorted(scan.get("Items", []), key=lambda x: x.get("received_at", ""), reverse=True)
            if evts:
                self._baseline_event_ts = evts[0].get("received_at", "")
        except Exception as e:
            print(f"Baseline event ts failed: {e}")

        # ── Step: Pre-flight ──
        pf_step = self._get_pipeline_step("pipeline_preflight")
        pf_step["status"] = "checking"
        _ev(pf_step, "사전 점검 시작")
        pf_start = time.time()

        pf_ok, pf_results = _pre_flight_check(self, scenario)
        self.preflight = pf_results
        for pf in pf_results:
            icon = "PASS" if pf["ok"] else "FAIL"
            _ev(pf_step, f"[{icon}] {pf['check']}: {pf['detail']}")

        otel_pods = scenario.get("otel_pods", [])
        if otel_pods and not cluster_manager.is_multi_cluster():
            _ev(pf_step, "OTel instrumentation 확인 중...")
            for pod_app in otel_pods:
                ok, stdout, _ = _run_cmd(
                    f"kubectl get pod -n {self.namespace} -l app={pod_app}"
                    f" -o jsonpath='{{.items[0].spec.initContainers[*].name}}' 2>/dev/null",
                    timeout=10, context=self._scenario_context
                )
                if ok and "opentelemetry" not in stdout:
                    _ev(pf_step, f"OTel 미설치: {pod_app} — rollout restart")
                    _run_cmd(f"kubectl rollout restart deployment/{pod_app} -n {self.namespace}", timeout=15, context=self._scenario_context)
                    _run_cmd(f"kubectl rollout status deployment/{pod_app} -n {self.namespace} --timeout=60s", timeout=70, context=self._scenario_context)
                    _ev(pf_step, f"OTel 재배포 완료: {pod_app}")
                else:
                    _ev(pf_step, f"OTel 확인: {pod_app}")

        pf_step["elapsed"] = round(time.time() - pf_start, 1)
        if not pf_ok:
            failed = [r for r in pf_results if not r["ok"]]
            pf_step["status"] = "fail"
            pf_step["detail"] = "; ".join(r["detail"] for r in failed)
            pf_step["error_category"] = "infra_missing"
            pf_step["error_reason"] = pf_step["detail"]
            _ev(pf_step, f"FAIL: {pf_step['detail']}")
            self.trigger_output = "Pre-flight failed: " + pf_step["detail"]
            self.status = "preflight_failed"
            self.result = "preflight_failed"
            self.completed_at = datetime.now(timezone.utc).isoformat()
            for step in self.steps:
                if step["status"] == "pending":
                    step["status"] = "skipped"
                    step["detail"] = "Pre-flight 실패로 건너뜀"
            self.save()
            return
        pf_step["status"] = "pass"
        pf_step["detail"] = f"모든 점검 통과 ({len(pf_results)}개)"
        _ev(pf_step, "PASS")

        # ── Discovery Variables Resolution ──
        disc_vars = {k: v for k, v in scenario.get("variables", {}).items() if isinstance(v, dict) and "discovery" in v}
        if disc_vars:
            _ev(pf_step, f"Discovery 변수 resolve 시작: {list(disc_vars.keys())}")
            _resolve_discovery_variables(scenario, self._scenario_context, self._scenario_profile)
            resolved = {k: v for k, v in scenario.get("variables", {}).items() if isinstance(v, str)}
            failed = [k for k, v in resolved.items() if not v]
            if failed:
                _ev(pf_step, f"⚠ Discovery 실패 (빈 값): {failed}")
            else:
                _ev(pf_step, f"Discovery 완료: {resolved}")

        # ── Alarm Provisioning (alarm_spec → 동적 생성/재사용) ──
        self._managed_alarms = []
        try:
            from alarm_provisioner import provision_alarm_steps
            v_steps = scenario.get("verification", {}).get("steps", [])
            account_id = _cfg("aws.account_id", "")
            project_name = _cfg("project.name", os.environ.get("PROJECT_NAME", ""))
            self._managed_alarms = provision_alarm_steps(
                v_steps, account_id, AWS_REGION, self._scenario_profile, project_name
            )
            if self._managed_alarms:
                _ev(pf_step, f"동적 알람 {len(self._managed_alarms)}개 생성/재사용: {self._managed_alarms}")
        except Exception as e:
            _ev(pf_step, f"alarm_provisioner 실패 (계속 진행): {e}")

        # ── Step: Pre-cleanup ──
        pre_cleanup = scenario.get("pre_cleanup", {})
        if pre_cleanup:
            cl_step = self._get_pipeline_step("pipeline_cleanup")
            cl_step["status"] = "checking"
            cl_start = time.time()
            _ev(cl_step, "환경 초기화 시작")

            if isinstance(pre_cleanup, list):
                cleanup_cmd = " && ".join(item.get("command", "") for item in pre_cleanup if isinstance(item, dict) and item.get("command"))
                alarm_names = []
                wait_ok_timeout = 120
            else:
                cleanup_cmd = pre_cleanup.get("command", "")
                alarm_names = pre_cleanup.get("reset_alarms", [])
                wait_ok_timeout = pre_cleanup.get("wait_ok_timeout", 120)

            if cleanup_cmd:
                cleanup_cmd = _resolve_scenario_variables(cleanup_cmd, scenario, self._scenario_context)
                for _sub_cmd in cleanup_cmd.split(" && "):
                    _sub_cmd = _sub_cmd.strip()
                    if not _sub_cmd:
                        continue
                    if "aws " in _sub_cmd and "--profile " not in _sub_cmd and self._scenario_profile:
                        _sub_cmd = _sub_cmd.replace("aws ", f"aws --profile {self._scenario_profile} ", 1)
                    _ev(cl_step, f"실행: {_sub_cmd[:80]}")
                    _cok, _cout, _cerr = _run_cmd(_sub_cmd, timeout=120, context=self._scenario_context)
                    if _cok:
                        _ev(cl_step, f"성공: {_cout[:60]}")
                    else:
                        _ev(cl_step, f"실패 (계속 진행): {_cerr[:80]}")

            try:
                import boto3
                _session = boto3.Session(profile_name=self._scenario_profile) if self._scenario_profile else boto3.Session()
                cw = _session.client("cloudwatch", region_name=AWS_REGION)
                for alarm in alarm_names:
                    cw.set_alarm_state(AlarmName=alarm, StateValue="OK", StateReason="Pre-test reset by simulator")
                    _ev(cl_step, f"알람 리셋: {alarm} → OK")
            except Exception as e:
                _ev(cl_step, f"알람 리셋 실패: {e}")

            if alarm_names:
                _ev(cl_step, f"알람 OK 대기 (최대 {wait_ok_timeout}s)...")
                deadline = time.time() + wait_ok_timeout
                all_ok = False
                try:
                    import boto3
                    _session = boto3.Session(profile_name=self._scenario_profile) if self._scenario_profile else boto3.Session()
                    cw = _session.client("cloudwatch", region_name=AWS_REGION)
                    while time.time() < deadline:
                        resp = cw.describe_alarms(AlarmNames=alarm_names)
                        states = [a["StateValue"] for a in resp.get("MetricAlarms", [])]
                        if all(s == "OK" for s in states):
                            all_ok = True
                            _ev(cl_step, "모든 알람 OK 확인")
                            break
                        time.sleep(15)
                    if not all_ok:
                        _ev(cl_step, f"알람 OK 대기 시간 초과 ({wait_ok_timeout}s)")
                except Exception as e:
                    _ev(cl_step, f"알람 대기 실패: {e}")

            cl_step["elapsed"] = round(time.time() - cl_start, 1)
            cl_step["status"] = "pass"
            cl_step["detail"] = "환경 초기화 완료"
            _ev(cl_step, "PASS")

        # ── Step: Trigger ──
        tr_step = self._get_pipeline_step("pipeline_trigger")
        tr_step["status"] = "checking"
        tr_start = time.time()
        _ev(tr_step, "장애 주입 준비")

        trigger = scenario.get("trigger", {})
        command = trigger.get("command", "")
        if not command and isinstance(trigger.get("commands"), list):
            command = " && ".join(trigger["commands"])
            _ev(tr_step, "commands 배열 → 단일 명령 변환")

        if command and "aws:eks:pod-network" in command:
            _ev(tr_step, "FIS 대상 pod 초기화 중...")
            _sel_match = re.search(r'selectorValue.*?app=(\w+)', command)
            _target_app = _sel_match.group(1) if _sel_match else None
            if _target_app:
                _run_cmd(f"kubectl delete pods -n {self.namespace} -l app={_target_app}", timeout=30, context=self._scenario_context)
                _run_cmd(f"kubectl rollout status deployment/{_target_app} -n {self.namespace} --timeout=90s", timeout=100, context=self._scenario_context)
                _ev(tr_step, f"FIS pod 초기화 완료: {_target_app}")
                time.sleep(10)

        if command:
            command = _resolve_scenario_variables(command, scenario, self._scenario_context)
            if "${AWS_ACCOUNT_ID}" in command and self._scenario_profile:
                for _aid, _prof in cluster_manager._account_profile_map.items():
                    if _prof == self._scenario_profile:
                        command = command.replace("${AWS_ACCOUNT_ID}", _aid)
                        break
            _remaining = re.findall(r'\$\{([A-Z_][A-Z0-9_]*)\}', command)
            if _remaining:
                _ev(tr_step, f"미치환 변수: {_remaining}")

        if command:
            if "aws " in command and "--profile " not in command and self._scenario_profile:
                command = command.replace("aws ", f"aws --profile {self._scenario_profile} ", 1)
            _ev(tr_step, f"실행: {command[:120]}")
            ok, stdout, stderr = _run_cmd(command, timeout=120, context=self._scenario_context)
            output = stdout or stderr
            self.trigger_output = output
            if ok:
                _ev(tr_step, f"성공: {output[:100]}")
                tr_step["status"] = "pass"
                tr_step["detail"] = output[:200]
            else:
                _ev(tr_step, f"실패: {output[:100]}")
                tr_step["status"] = "fail"
                tr_step["detail"] = f"trigger 실패: {output[:200]}"
                tr_step["error_category"] = "command_error"
                tr_step["error_reason"] = output[:200]
        else:
            self.trigger_output = "트리거 명령 없음"
            tr_step["status"] = "pass"
            tr_step["detail"] = "트리거 명령 없음 (스킵)"
            _ev(tr_step, "트리거 명령 미정의 — 스킵")

        tr_step["elapsed"] = round(time.time() - tr_start, 1)
        _ev(tr_step, f"완료 ({tr_step['elapsed']}s)")

        if tr_step["status"] == "fail":
            for step in self.steps:
                if step["status"] == "pending":
                    step["status"] = "skipped"
                    step["detail"] = "Trigger 실패로 건너뜀"
            self.status = "completed"
            self.result = "fail"
            self.completed_at = datetime.now(timezone.utc).isoformat()
            self.save()
            return

        # ── Verification loop ──
        scaled_deploy = self._boost_traffic()
        try:
            _verification_loop(self)
        finally:
            self._restore_traffic(scaled_deploy)

        # ── Investigation (Agent가 장애 상태를 볼 수 있도록 restore 전에 트리거) ──
        self._stage_investigate()

        # ── Step: Restore ──
        restore_step = self._get_pipeline_step("pipeline_restore")
        if restore_step:
            restore_cmd = scenario.get("restore", {}).get("command", "")
            if restore_cmd:
                restore_cmd = _resolve_scenario_variables(restore_cmd, scenario, self._scenario_context)
                if "${FIS_EXPERIMENT_ID}" in restore_cmd and self.trigger_output:
                    m = re.search(r'"id"\s*:\s*"(EXP[A-Za-z0-9]+)"', self.trigger_output)
                    if m:
                        restore_cmd = restore_cmd.replace("${FIS_EXPERIMENT_ID}", m.group(1))
            restore_step["status"] = "checking"
            rs_start = time.time()
            _ev(restore_step, "복원 시작")
            if restore_cmd:
                if "aws " in restore_cmd and "--profile " not in restore_cmd and self._scenario_profile:
                    restore_cmd = restore_cmd.replace("aws ", f"aws --profile {self._scenario_profile} ", 1)
                _ev(restore_step, f"실행: {restore_cmd[:120]}")
                rok, rout, rerr = _run_cmd(restore_cmd, timeout=60, context=self._scenario_context)
                output = rout or rerr
                if rok:
                    restore_step["status"] = "pass"
                    restore_step["detail"] = f"복원 완료: {output[:100]}"
                    _ev(restore_step, f"성공: {output[:80]}")
                else:
                    restore_step["status"] = "fail"
                    restore_step["detail"] = f"복원 실패: {output[:100]}"
                    _ev(restore_step, f"실패: {output[:80]}")
            else:
                restore_step["status"] = "pass"
                restore_step["detail"] = "복원 명령 없음"
                _ev(restore_step, "복원 명령 미정의 — 스킵")
            restore_step["elapsed"] = round(time.time() - rs_start, 1)
            _ev(restore_step, "PASS")

        # ── Managed Alarm Cleanup ──
        if getattr(self, "_managed_alarms", None):
            try:
                from alarm_provisioner import cleanup_managed_alarms
                deleted = cleanup_managed_alarms(self._scenario_profile, AWS_REGION)
                if deleted:
                    _ev(restore_step or pf_step, f"관리 알람 정리: {deleted}")
            except Exception as e:
                print(f"[alarm-cleanup] failed: {e}")

        # ── 완료 처리 ──
        if self.status not in ("completed", "cancelled", "preflight_failed"):
            self.status = "completed"
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.save()

        # ── Auto-Evaluate (background — investigation 완료 대기 후 평가) ──
        if getattr(self, "_investigation_task_id", None) or getattr(self, "_incident_id", None):
            threading.Thread(
                target=self._stage_evaluate, daemon=True,
                name=f"eval-{self.run_id[:8]}"
            ).start()

        def _cleanup_run():
            time.sleep(300)
            from verifier import _runs_lock, _active_runs
            with _runs_lock:
                _active_runs.pop(self.run_id, None)
        threading.Thread(target=_cleanup_run, daemon=True).start()

    # ── Investigation & Evaluation Stages ──────────────────────────────

    def _send_investigation_webhook(self, alarm_name, alarm_desc):
        """Send webhook using self.agent_space_id for credential lookup."""
        import hashlib as _hashlib, hmac as _hmac, base64 as _b64, urllib.request as _urllib
        import boto3
        from app_config import _profile_for_space
        try:
            profile = _profile_for_space(self.agent_space_id)
            secret_id = f"webhook-{self.agent_space_id}"
            session = boto3.Session(profile_name=profile, region_name=AWS_REGION)
            sm = session.client("secretsmanager", region_name=AWS_REGION)
            creds = json.loads(sm.get_secret_value(SecretId=secret_id)["SecretString"])
            ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
            iid = f"{alarm_name}-{ts.replace(':','-')}"
            payload = {
                "eventType": "incident", "incidentId": iid, "action": "created",
                "priority": "HIGH", "title": f"[CW Alarm] {alarm_name}: {alarm_desc}",
                "description": f"CloudWatch Alarm '{alarm_name}' triggered. {alarm_desc}.",
                "timestamp": ts, "service": "unknown",
                "data": {"metadata": {"region": AWS_REGION, "alarmName": alarm_name}}
            }
            body = json.dumps(payload)
            sig = _b64.b64encode(
                _hmac.new(creds['webhookSecret'].encode(), f"{ts}:{body}".encode(), _hashlib.sha256).digest()
            ).decode()
            req = _urllib.Request(
                creds['webhookUrl'], data=body.encode(),
                headers={'Content-Type': 'application/json', 'x-amzn-event-timestamp': ts, 'x-amzn-event-signature': sig},
                method='POST',
            )
            with _urllib.urlopen(req, timeout=15) as r:
                print(f"[investigate] webhook {r.status}: {alarm_name} incident_id={iid}")
            return iid
        except Exception as e:
            print(f"[investigate] webhook send failed: {e}")
            return None

    def _stage_investigate(self):
        """Investigation webhook 발송 + task polling. Restore 전에 호출됨."""
        if self._investigation_task_id:
            return
        if not self.agent_space_id:
            return

        if not self._incident_id:
            try:
                alarm_name = f"scenario-{self.scenario_id}"
                alarm_desc = self.scenario.get("purpose", self.scenario.get("name", ""))
                iid = self._send_investigation_webhook(alarm_name, alarm_desc)
                if iid:
                    self._incident_id = iid
                    print(f"[investigate] webhook sent: incident_id={iid}")
            except Exception as e:
                print(f"[investigate] webhook failed: {e}")
                return

        if not self._incident_id:
            return

        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                task_id, status = _find_task_by_incident_id(
                    self._incident_id, space_id=self.agent_space_id)
                if task_id:
                    self._investigation_task_id = task_id
                    print(f"[investigate] task found: {task_id} ({status})")
                    return
            except Exception:
                pass
            time.sleep(15)
        print(f"[investigate] task not found within 2min (will poll in eval stage)")

    def _stage_evaluate(self):
        """Background: investigation 완료 대기 → 자동 평가 → DDB 저장."""
        try:
            task_id = self._investigation_task_id
            if not task_id and self._incident_id:
                deadline = time.time() + 480
                while time.time() < deadline:
                    try:
                        tid, status = _find_task_by_incident_id(
                            self._incident_id, space_id=self.agent_space_id)
                        if tid:
                            task_id = tid
                            self._investigation_task_id = tid
                            if status in ("COMPLETED", "completed", "done", "LINKED"):
                                break
                        time.sleep(20)
                    except Exception:
                        time.sleep(20)

            if not task_id:
                print(f"[evaluate] no task_id for run {self.run_id} — skip")
                return

            deadline = time.time() + 300
            while time.time() < deadline:
                try:
                    _, status = _find_task_by_incident_id(
                        self._incident_id, space_id=self.agent_space_id)
                    if status in ("COMPLETED", "completed", "done", "LINKED"):
                        break
                except Exception:
                    pass
                time.sleep(20)

            from evaluator import auto_evaluate_run
            result = auto_evaluate_run(
                run_id=self.run_id,
                task_id=task_id,
                scenario=self.scenario,
                agent_space_id=self.agent_space_id,
            )
            if result:
                score = result.get("overall_score", "?")
                print(f"[evaluate] run={self.run_id} score={score}/10")
        except Exception as e:
            print(f"[evaluate] failed for run {self.run_id}: {e}")

    def _boost_traffic(self):
        """Trigger 후 트래픽 부스트: worker scale-up + 대상 서비스에 직접 트래픽 생성."""
        target = self.scenario.get("target_service", "")
        if not target:
            return None
        info = {"namespace": self.namespace, "context": self._scenario_context, "traffic_pod": None}
        ctx_args = f"--context {self._scenario_context}" if self._scenario_context else ""
        _MAX_BOOST_REPLICAS = 10
        try:
            ok, out, _ = _run_cmd(
                f"kubectl {ctx_args} -n {self.namespace} get deploy worker -o jsonpath='{{.spec.replicas}}'",
                timeout=10)
            if ok:
                original = int(out.strip().strip("'"))
                boost = min(max(original * 3, 5), _MAX_BOOST_REPLICAS)
                _run_cmd(
                    f"kubectl {ctx_args} -n {self.namespace} scale deploy/worker --replicas={boost}",
                    timeout=15)
                info["deploy"] = "worker"
                info["original"] = original
                print(f"[traffic-boost] worker {original} -> {boost} replicas (max={_MAX_BOOST_REPLICAS})")
        except Exception as e:
            print(f"[traffic-boost] scale skip: {e}")

        try:
            traffic_pod = f"traffic-gen-{self.run_id}"
            svc_url = f"http://{target}.{self.namespace}.svc.cluster.local/"
            _run_cmd(
                f"kubectl {ctx_args} -n {self.namespace} delete pod {traffic_pod} --ignore-not-found=true",
                timeout=10)
            _run_cmd(
                f"kubectl {ctx_args} -n {self.namespace} run {traffic_pod} "
                f"--image=curlimages/curl:8.5.0 --restart=Never -- "
                f"sh -c 'while true; do curl -s -X POST -d test -m 10 {svc_url} >/dev/null 2>&1; sleep 1; done'",
                timeout=15)
            info["traffic_pod"] = traffic_pod
            print(f"[traffic-boost] started traffic pod {traffic_pod} -> {svc_url}")
        except Exception as e:
            print(f"[traffic-boost] traffic pod skip: {e}")

        return info if (info.get("deploy") or info.get("traffic_pod")) else None

    def _restore_traffic(self, info):
        """Verification 완료 후 worker replicas 원복 + traffic pod 삭제."""
        if not info:
            return
        ctx_args = f"--context {info['context']}" if info.get("context") else ""
        if info.get("deploy") and info.get("original") is not None:
            try:
                _run_cmd(
                    f"kubectl {ctx_args} -n {info['namespace']} scale deploy/{info['deploy']} --replicas={info['original']}",
                    timeout=15)
                print(f"[traffic-restore] {info['deploy']} -> {info['original']} replicas")
            except Exception as e:
                print(f"[traffic-restore] scale error: {e}")
        if info.get("traffic_pod"):
            try:
                _run_cmd(
                    f"kubectl {ctx_args} -n {info['namespace']} delete pod {info['traffic_pod']} --ignore-not-found=true",
                    timeout=15)
                print(f"[traffic-restore] deleted {info['traffic_pod']}")
            except Exception as e:
                print(f"[traffic-restore] pod delete error: {e}")


# ---------------------------------------------------------------------------
# Prompt templates for AgentExecutor
# ---------------------------------------------------------------------------

STEP_UPDATE_RE = re.compile(r'\[STEP_UPDATE\]\s*(\{[^}]*\})')

VERIFY_PROMPT_TEMPLATE = """You are a DevOps automation agent verifying the effects of a fault injection scenario.

## Context
- Scenario: {scenario_name}
- Purpose: {purpose}
- Trigger output: {trigger_output}
- Namespace: {namespace}
- Region: {region}

## Verification Steps
Verify each step below in order. For each step, check the described condition.
If a step fails after reasonable retries, try alternative approaches before giving up.

{step_descriptions}

## Instructions
1. Verify steps in order (index 0, 1, 2, ...)
2. For each step, output a progress marker on its own line:
   - When starting: [STEP_UPDATE]{{"index": N, "status": "checking", "detail": "checking..."}}
   - When done: [STEP_UPDATE]{{"index": N, "status": "pass"|"fail", "detail": "result description", "elapsed": seconds}}
3. If a step fails, you MAY try alternative diagnostic approaches before marking it fail
4. Use your judgment — if kubectl returns unexpected output, try a different query;
   if a metric is not found, check if the metric name or namespace is slightly different
5. After all steps, output a final summary:

```json
{{
  "phase": "verify",
  "steps": [
    {{"index": 0, "status": "pass"|"fail", "detail": "...", "elapsed": N}},
    ...
  ],
  "overall": "pass"|"fail"|"partial"
}}
```

## 응답 언어: 한국어
"""

INVESTIGATE_PROMPT_TEMPLATE = """Based on the fault injection scenario results, conduct a root cause investigation.

## Scenario
- Name: {scenario_name}
- Purpose: {purpose}
- Expected root cause: {expected_root_cause}

## Verification Results
{verification_summary}

## Instructions
1. Investigate the root cause of the observed symptoms
2. Check relevant logs, metrics, and traces
3. Provide your findings

```json
{{
  "phase": "investigate",
  "root_cause": "description",
  "evidence": ["evidence 1", "evidence 2"],
  "remediation": "suggested fix",
  "confidence": "high|medium|low"
}}
```

## 응답 언어: 한국어
"""


def _step_to_instruction(step_type, config, namespace):
    """Convert a typed verification step into a natural language instruction."""
    ns = config.get("_namespace", namespace)
    if step_type == "metric_check":
        return (
            f"Check CloudWatch metric {config.get('namespace','')}/{config.get('metric_name','')} "
            f"with dimensions {json.dumps(config.get('dimensions',[]), ensure_ascii=False)}. "
            f"Expect {config.get('statistic','Average')} {config.get('comparison','GreaterThanThreshold')} {config.get('threshold',0)}"
        )
    if step_type == "pod_status":
        return (
            f"Check pod with label app={config.get('pod','')} in namespace {ns}. "
            f"Expected status: {config.get('expected','Running')}"
        )
    if step_type in ("cw_alarm", "alarm_state"):
        return (
            f"Check CloudWatch alarm '{config.get('alarm_name', config.get('alarm',''))}'. "
            f"Expected state: {config.get('expected','ALARM')}"
        )
    if step_type == "kubectl_check":
        return (
            f"Run kubectl command: {config.get('command','')}. "
            f"Expected output contains: {config.get('expected','')}"
        )
    if step_type == "log_pattern":
        return (
            f"Search CloudWatch Logs group '{config.get('log_group','')}' "
            f"for pattern '{config.get('filter_pattern','')}' in last {config.get('minutes',10)} minutes"
        )
    if step_type == "pod_logs":
        return (
            f"Check pod logs for app={config.get('pod','')} in namespace {ns} "
            f"for pattern '{config.get('pattern','')}'"
        )
    if step_type == "fis_experiment":
        return f"Check FIS experiment state for experiment template: {config.get('experiment_template_id','')}"
    if step_type == "slack_message":
        return f"Check Slack channel for message matching pattern: {config.get('pattern','')}"
    if step_type == "xray_trace":
        return f"Search X-Ray traces with filter: {config.get('filter_expression','')}"
    if step_type == "xray_latency":
        return f"Check X-Ray service latency for {config.get('service_name','')} > {config.get('threshold_ms',0)}ms"
    if step_type == "lambda_logs":
        return f"Check Lambda function '{config.get('function_name','')}' logs for: {config.get('pattern','')}"
    if step_type == "api_call":
        return (
            f"Call AWS API: {config.get('service','')}.{config.get('api','')} "
            f"with params {json.dumps(config.get('params',{}), ensure_ascii=False)}. "
            f"Check result with JMESPath '{config.get('jmespath','')}' = {config.get('expected','')}"
        )
    return f"Verify: {json.dumps(config, ensure_ascii=False, default=str)}"
