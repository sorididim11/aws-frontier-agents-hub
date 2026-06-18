"""
Python step-based scenario execution framework.

Replaces monolithic bash run.sh with structured Python steps.
Each step is a decorated function returning StepResult.
ScenarioContext provides kubectl, alarm polling, port-forward, etc.

Usage (standalone):
    python scenario_runner.py steps.py --namespace dockercoins --alarm-name X

Usage (imported):
    from scenario_runner import ScenarioRunner, ScenarioContext, step, StepResult
"""
import dataclasses
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Callable, Optional


# ═══════════════════════════════════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class StepResult:
    status: str  # "pass" | "fail" | "skip"
    detail: str
    duration: float = 0.0
    error_category: Optional[str] = None  # timeout | command_error | config_error | infra_missing | transient
    error_reason: Optional[str] = None
    data: Optional[dict] = None


@dataclasses.dataclass
class StepDef:
    number: int
    name: str
    fn: Callable
    step_type: str = "action"        # "action" | "observe"
    max_retries: int = 0
    retry_delay: float = 5.0
    poll_interval: int = 15          # observe step: polling interval (seconds)
    timeout: int = 0                 # observe step: max wait (0 = use compute_timeouts)
    abort_fn: Optional[Callable] = None


class StopScenario(Exception):
    """Raise inside a step to abort the entire scenario."""
    pass


# ═══════════════════════════════════════════════════════════════════════════
# Step registry + decorator
# ═══════════════════════════════════════════════════════════════════════════

_steps_registry: list[StepDef] = []


def step(number: int, name: str, step_type: str = "action", max_retries: int = 0,
         retry_delay: float = 5.0, poll_interval: int = 15, timeout: int = 0):
    """Decorator to register a scenario step."""
    def wrapper(fn):
        _steps_registry.append(StepDef(
            number=number, name=name, fn=fn, step_type=step_type,
            max_retries=max_retries, retry_delay=retry_delay,
            poll_interval=poll_interval, timeout=timeout,
        ))
        return fn
    return wrapper


def abort_condition(step_number: int):
    """Register an abort condition for a specific observe step."""
    def wrapper(fn):
        for s in _steps_registry:
            if s.number == step_number:
                s.abort_fn = fn
                break
        return fn
    return wrapper


def get_registered_steps() -> list[StepDef]:
    return list(_steps_registry)


def clear_registry():
    _steps_registry.clear()


# ═══════════════════════════════════════════════════════════════════════════
# Event emission
# ═══════════════════════════════════════════════════════════════════════════

def _emit(event: str, **kwargs):
    """Emit a JSON event on stdout (EVENT|{json}) + legacy CHECKPOINT format."""
    payload = {"event": event, "timestamp": datetime.now(timezone.utc).isoformat(), **kwargs}
    print(f"EVENT|{json.dumps(payload, ensure_ascii=False)}", flush=True)


def _checkpoint(step_num, name, status, detail):
    """Emit legacy CHECKPOINT line for backward compat with ScriptExecutor parser."""
    print(f"CHECKPOINT|{step_num}|{name}|{status}|{detail}", flush=True)


# ═══════════════════════════════════════════════════════════════════════════
# PortForwardContext
# ═══════════════════════════════════════════════════════════════════════════

class PortForwardContext:
    """Context manager for kubectl port-forward."""

    def __init__(self, ctx: "ScenarioContext", service: str, local_port: int, remote_port: int = 80):
        self._ctx = ctx
        self._service = service
        self._local_port = local_port
        self._remote_port = remote_port
        self._proc: Optional[subprocess.Popen] = None

    def __enter__(self):
        self._start()
        return self

    def __exit__(self, *exc):
        self._stop()

    def _build_cmd(self):
        ns = self._ctx.namespace
        ctx_flag = f"--context {self._ctx.kubectl_context} " if self._ctx.kubectl_context else ""
        return f"kubectl {ctx_flag}-n {ns} port-forward svc/{self._service} {self._local_port}:{self._remote_port}"

    def _start(self):
        self._stop()
        cmd = self._build_cmd()
        self._proc = subprocess.Popen(
            ["bash", "-c", cmd],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=self._ctx._env,
        )
        for _ in range(10):
            time.sleep(1)
            try:
                import urllib.request
                urllib.request.urlopen(f"http://localhost:{self._local_port}/", timeout=2)
                break
            except Exception:
                if self._proc.poll() is not None:
                    self._proc = subprocess.Popen(
                        ["bash", "-c", cmd],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        env=self._ctx._env,
                    )
                continue

    def _stop(self):
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def ensure_alive(self):
        if self._proc and self._proc.poll() is not None:
            self._start()

    @property
    def url(self) -> str:
        return f"http://localhost:{self._local_port}"


# ═══════════════════════════════════════════════════════════════════════════
# ScenarioContext
# ═══════════════════════════════════════════════════════════════════════════

class ScenarioContext:
    """Shared context providing utility methods for scenario steps."""

    def __init__(self, namespace: str = "dockercoins",
                 aws_profile: str = "",
                 aws_region: str = "us-east-1",
                 alarm_name: str = "",
                 cluster_name: str = "",
                 kubectl_context: Optional[str] = None,
                 dry_run: bool = False):
        self.namespace = namespace
        self.aws_profile = aws_profile
        self.aws_region = aws_region
        self.alarm_name = alarm_name
        self.cluster_name = cluster_name
        self.kubectl_context = kubectl_context
        self.dry_run = dry_run
        self._shared: dict = {}
        self._port_forwards: list[PortForwardContext] = []
        self._background_procs: list[subprocess.Popen] = []
        self._env = self._build_env()

    def _build_env(self):
        env = {**os.environ, "AWS_PAGER": ""}
        path = env.get("PATH", "")
        for p in ("/opt/homebrew/bin", "/usr/local/bin"):
            if p not in path:
                path = p + ":" + path
        env["PATH"] = path
        if self.aws_profile:
            env["AWS_PROFILE"] = self.aws_profile
        if self.aws_region:
            env["AWS_REGION"] = self.aws_region
        return env

    # ── kubectl ──────────────────────────────────────────────────────────

    def kubectl(self, args: str, timeout: int = 30) -> tuple[bool, str, str]:
        """Run kubectl command. Returns (ok, stdout, stderr)."""
        if self.dry_run:
            if args.startswith("run "):
                args += " --dry-run=client -o yaml"
            elif args.startswith("delete "):
                self.log(f"[dry-run] skip: kubectl {args}")
                return True, "", ""
        ctx_flag = f"--context {self.kubectl_context} " if self.kubectl_context else ""
        cmd = f"kubectl {ctx_flag}-n {self.namespace} {args}"
        return self._run(cmd, timeout)

    # ── alarm ────────────────────────────────────────────────────────────

    def alarm_info(self, name: str = "") -> dict:
        """Fetch CloudWatch alarm info. In dry-run, also verifies metric datapoints exist."""
        alarm = name or self.alarm_name
        cmd = (
            f"aws cloudwatch describe-alarms --alarm-names '{alarm}'"
            f" --query 'MetricAlarms[0].{{Threshold:Threshold,Period:Period,"
            f"EvalPeriods:EvaluationPeriods,State:StateValue}}'"
            f" --output json"
        )
        ok, stdout, _ = self._run(cmd, timeout=15)
        if ok and stdout and stdout != "null":
            info = json.loads(stdout)
            if self.dry_run:
                self._verify_alarm_metrics(alarm)
            return info
        return {}

    def _verify_alarm_metrics(self, alarm_name: str):
        """Dry-run probe: verify the alarm's metric has recent datapoints."""
        cmd = (
            f"aws cloudwatch describe-alarms --alarm-names '{alarm_name}'"
            f" --query 'MetricAlarms[0].{{Namespace:Namespace,MetricName:MetricName,"
            f"Dimensions:Dimensions,Statistic:Statistic}}'"
            f" --output json"
        )
        ok, stdout, _ = self._run(cmd, timeout=15)
        if not ok or not stdout or stdout == "null":
            return
        alarm_detail = json.loads(stdout)
        ns = alarm_detail.get("Namespace", "")
        metric = alarm_detail.get("MetricName", "")
        dims = alarm_detail.get("Dimensions", [])
        dim_args = " ".join(
            f"'Name={d['Name']},Value={d['Value']}'" for d in dims
        )
        cmd2 = (
            f"aws cloudwatch get-metric-statistics"
            f" --namespace '{ns}' --metric-name '{metric}'"
            f" --dimensions {dim_args}"
            f" --start-time $(date -u -v-5M '+%Y-%m-%dT%H:%M:%S' 2>/dev/null || date -u -d '5 minutes ago' '+%Y-%m-%dT%H:%M:%S')"
            f" --end-time $(date -u '+%Y-%m-%dT%H:%M:%S')"
            f" --period 60 --statistics Average"
            f" --query 'length(Datapoints)' --output text"
        )
        ok2, stdout2, _ = self._run(cmd2, timeout=15)
        count = int(stdout2.strip()) if ok2 and stdout2.strip().isdigit() else 0
        if count == 0:
            raise StopScenario(
                f"[dry-run] FAIL: 알람 '{alarm_name}' 메트릭에 최근 5분간 datapoint 없음. "
                f"원인: OTEL 미계측 또는 트래픽 없음. "
                f"fix: kubectl rollout restart deployment/{self._shared.get('target_service', 'TARGET')} -n {self.namespace}"
            )
        self.log(f"[dry-run] OK: 알람 '{alarm_name}' 메트릭 datapoint={count} (최근 5분)")

    def compute_timeouts(self, info: dict) -> dict:
        """Compute polling timeouts from alarm config."""
        period = int(info.get("Period", 60))
        eval_periods = int(info.get("EvalPeriods", 1))
        return {
            "max_wait": max(period * eval_periods * 5, 300),
            "ok_wait": max(period * eval_periods * 3, 180),
            "poll_interval": max(min(period, 15), 10),
            "reinject_interval": period,
        }

    def wait_alarm_state(self, target: str, timeout: int, poll_interval: int,
                         on_poll=None) -> tuple[bool, float, str]:
        """Poll alarm until target state or timeout. Returns (ok, elapsed, final_state)."""
        alarm = self.alarm_name
        if self.dry_run:
            info = self.alarm_info(alarm)
            state = info.get("State", "UNKNOWN")
            self.log(f"[dry-run] 알람 현재 상태: {state} (대기 skip, target={target})")
            return True, 0, state
        start = time.time()
        state = "UNKNOWN"
        while time.time() - start < timeout:
            elapsed = time.time() - start
            cmd = (
                f"aws cloudwatch describe-alarms --alarm-names '{alarm}'"
                f" --query 'MetricAlarms[0].StateValue' --output text"
            )
            ok, stdout, _ = self._run(cmd, timeout=15)
            state = stdout.strip() if ok and stdout else "UNKNOWN"

            _emit("step_poll", step=0, elapsed=int(elapsed), total=timeout,
                  message=f"알람 상태: {state}")

            if state == target:
                return True, time.time() - start, state

            if on_poll:
                on_poll(elapsed, state)

            time.sleep(poll_interval)

        return False, time.time() - start, state

    # ── port-forward ─────────────────────────────────────────────────────

    def port_forward(self, service: str, local_port: int, remote_port: int = 80) -> PortForwardContext:
        pf = PortForwardContext(self, service, local_port, remote_port)
        self._port_forwards.append(pf)
        return pf

    def get_or_create_pf(self, service: str, local_port: int, remote_port: int = 80) -> PortForwardContext:
        """Get existing persistent port-forward or create one. Lives until cleanup()."""
        key = f"_pf_{service}_{local_port}"
        pf = self._shared.get(key)
        if pf and pf._proc and pf._proc.poll() is None:
            return pf
        pf = PortForwardContext(self, service, local_port, remote_port)
        pf._start()
        self._port_forwards.append(pf)
        self._shared[key] = pf
        return pf

    # ── HTTP (curl-like) ─────────────────────────────────────────────────

    def curl(self, url: str, method: str = "GET", data: Optional[str] = None,
             timeout: int = 10) -> tuple[int, str]:
        """Simple HTTP request. Returns (status_code, body). 0 on failure.

        Safety: clamps inject-latency seconds to max 3.0 to prevent worker timeouts.
        In dry-run: write endpoints (inject/clear) become connectivity probes.
        """
        import urllib.request
        import re as _re

        if self.dry_run and _re.search(r'inject-latency|clear-latency', url):
            base_url = _re.sub(r'/inject-latency.*|/clear-latency.*', '/', url)
            try:
                req = urllib.request.Request(base_url, method="GET")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.log(f"[dry-run] endpoint reachable: {base_url} → {resp.status}")
                    return resp.status, f"[dry-run] probe OK"
            except Exception as e:
                raise StopScenario(
                    f"[dry-run] FAIL: endpoint 도달 불가 {base_url} — {e}. "
                    f"fix: kubectl port-forward 확인 또는 서비스 배포 상태 확인"
                )

        m = _re.search(r'inject-latency\?seconds=([0-9.]+)', url)
        if m:
            val = float(m.group(1))
            if val > 3.0:
                url = url.replace(f"seconds={m.group(1)}", "seconds=2")
        try:
            req = urllib.request.Request(url, method=method)
            if data:
                req.data = data.encode()
                req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode() if e.fp else ""
        except Exception:
            return 0, ""

    # ── latency injection harness ──────────────────────────────────────────

    def inject_latency(self, service: str = "hasher", seconds: float = 2.0,
                       port: int = 18080) -> tuple[bool, str]:
        """Inject application-level latency (clamped to safe range).

        Clamps seconds to [0.5, 3.0] to prevent worker timeouts that would
        cause ApplicationSignals to stop recording datapoints.
        """
        seconds = max(0.5, min(seconds, 3.0))
        with self.port_forward(service, port) as pf:
            code, body = self.curl(f"{pf.url}/inject-latency?seconds={seconds}")
            if code and code < 400:
                self.curl(f"{pf.url}/", method="POST", data="warmup", timeout=10)
                return True, body
            return False, body

    def clear_latency(self, service: str = "hasher", port: int = 18080) -> tuple[bool, str]:
        """Clear injected latency on service."""
        with self.port_forward(service, port) as pf:
            code, body = self.curl(f"{pf.url}/clear-latency")
            return (code is not None and code < 400), body

    def send_auxiliary_traffic(self, pf: "PortForwardContext", count: int = 1):
        """Send auxiliary requests to accumulate ApplicationSignals datapoints."""
        if self.dry_run:
            return
        for _ in range(count):
            pf.ensure_alive()
            self.curl(f"{pf.url}/", method="POST", data="aux-traffic", timeout=10)

    # ── FIS (Fault Injection Simulator) ────────────────────────────────

    def fis_start(self, template_id: str, timeout: int = 60) -> tuple[bool, str]:
        """Start an FIS experiment. Returns (ok, experiment_id)."""
        if self.dry_run:
            self.log(f"[dry-run] skip fis_start({template_id})")
            return True, "dry-run-experiment-id"
        cmd = (
            f"aws fis start-experiment --experiment-template-id {template_id}"
            f" --region {self.aws_region} --output json"
        )
        ok, stdout, stderr = self._run(cmd, timeout=timeout)
        if not ok:
            return False, stderr
        try:
            import json as _json
            data = _json.loads(stdout)
            exp_id = data.get("experiment", {}).get("id", "")
            return True, exp_id
        except Exception:
            return ok, stdout

    def fis_stop(self, experiment_id: str) -> bool:
        """Stop a running FIS experiment."""
        if self.dry_run:
            self.log(f"[dry-run] skip fis_stop({experiment_id})")
            return True
        cmd = (
            f"aws fis stop-experiment --id {experiment_id}"
            f" --region {self.aws_region}"
        )
        ok, _, _ = self._run(cmd, timeout=30)
        return ok

    def fis_status(self, experiment_id: str) -> str:
        """Get FIS experiment status (running, completed, stopped, failed)."""
        if self.dry_run:
            return "completed"
        cmd = (
            f"aws fis get-experiment --id {experiment_id}"
            f" --region {self.aws_region} --output json"
        )
        ok, stdout, _ = self._run(cmd, timeout=30)
        if not ok:
            return "unknown"
        try:
            import json as _json
            data = _json.loads(stdout)
            return data.get("experiment", {}).get("state", {}).get("status", "unknown")
        except Exception:
            return "unknown"

    # ── pod management ───────────────────────────────────────────────────

    def run_pod(self, name: str, image: str, command: str) -> bool:
        ok, _, _ = self.kubectl(
            f"run {name} --image={image} --restart=Never -- {command}"
        )
        return ok

    def delete_pod(self, name: str) -> bool:
        ok, _, _ = self.kubectl(f"delete pod {name} --ignore-not-found=true")
        return ok

    def wait_pod_running(self, name: str, timeout: int = 60) -> bool:
        if self.dry_run:
            self.log(f"[dry-run] skip wait_pod_running({name})")
            return True
        start = time.time()
        while time.time() - start < timeout:
            ok, stdout, _ = self.kubectl(
                f"get pod {name} -o jsonpath='{{.status.phase}}'", timeout=10
            )
            if ok and "Running" in stdout:
                return True
            time.sleep(5)
        return False

    # ── cleanup ──────────────────────────────────────────────────────────

    def log(self, message: str):
        _emit("step_log", step=0, message=message)

    _log = log

    def cleanup(self):
        for pf in self._port_forwards:
            pf._stop()
        self._port_forwards.clear()
        for proc in self._background_procs:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._background_procs.clear()

    # ── internal ─────────────────────────────────────────────────────────

    def _run(self, cmd: str, timeout: int = 30) -> tuple[bool, str, str]:
        try:
            result = subprocess.run(
                ["bash", "-c", cmd],
                capture_output=True, text=True, timeout=timeout,
                env=self._env,
            )
            return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return False, "", "Command timed out"
        except Exception as e:
            return False, "", str(e)


# ═══════════════════════════════════════════════════════════════════════════
# Error classification
# ═══════════════════════════════════════════════════════════════════════════

_INFRA_PATTERNS = re.compile(
    r"not found|no matches|unable to connect|couldn't get|서비스를 찾을 수 없|pod 없음",
    re.IGNORECASE,
)
_TRANSIENT_PATTERNS = re.compile(
    r"timeout|timed out|connection refused|temporarily unavailable|UNKNOWN",
    re.IGNORECASE,
)
_CMD_PATTERNS = re.compile(
    r"command not found|syntax error|permission denied|invalid|error:",
    re.IGNORECASE,
)


def classify_error(detail: str, timed_out: bool = False) -> tuple[str, str]:
    """Classify error into (category, reason)."""
    if timed_out:
        return "timeout", "Step exceeded time limit"
    if _INFRA_PATTERNS.search(detail):
        return "infra_missing", detail[:120]
    if _CMD_PATTERNS.search(detail):
        return "command_error", detail[:120]
    if _TRANSIENT_PATTERNS.search(detail):
        return "transient", detail[:120]
    return "command_error", detail[:120]


# ═══════════════════════════════════════════════════════════════════════════
# ScenarioRunner
# ═══════════════════════════════════════════════════════════════════════════

class ScenarioRunner:
    """Execute steps with action/observe model.

    Action steps run sequentially. Observe steps run in a single polling loop
    that checks all pending conditions each tick. Steps are partitioned into
    blocks: [actions, observes, actions, observes, ...].
    """

    _DRY_RUN_SKIP_KEYWORDS = ("주입", "inject", "대기", "wait", "복원", "restore", "에러 유발")

    def __init__(self, ctx: ScenarioContext, steps: list[StepDef], resume_from: int = 0):
        self.ctx = ctx
        self.steps = sorted(steps, key=lambda s: s.number)
        self.resume_from = resume_from

    def _is_action_step(self, step_def: StepDef) -> bool:
        name_lower = step_def.name.lower()
        return any(kw in name_lower for kw in self._DRY_RUN_SKIP_KEYWORDS)

    def _partition_into_blocks(self):
        """Split steps into alternating action/observe blocks."""
        blocks = []
        current_type = None
        current_steps = []
        for s in self.steps:
            stype = s.step_type if s.step_type == "observe" else "action"
            if stype != current_type:
                if current_steps:
                    blocks.append((current_type, current_steps))
                current_type = stype
                current_steps = [s]
            else:
                current_steps.append(s)
        if current_steps:
            blocks.append((current_type, current_steps))
        return blocks

    def run(self) -> dict:
        """Run all steps using action/observe block model."""
        total = len(self.steps)
        passed = 0
        failed = False

        _emit("run_start", total_steps=total)

        try:
            blocks = self._partition_into_blocks()

            for block_type, block_steps in blocks:
                if failed:
                    break

                if block_type == "action":
                    for step_def in block_steps:
                        if step_def.number < self.resume_from:
                            _emit("step_skip", step=step_def.number, name=step_def.name,
                                  detail="resumed")
                            _checkpoint(step_def.number, step_def.name, "PASS", "resumed")
                            passed += 1
                            continue

                        if self.ctx.dry_run and self._is_action_step(step_def):
                            _emit("step_skip", step=step_def.number, name=step_def.name,
                                  detail="[dry-run] action step skipped")
                            _checkpoint(step_def.number, step_def.name, "PASS",
                                        "[dry-run] action step skipped")
                            passed += 1
                            continue

                        result = self._execute_step(step_def)

                        if result.status == "pass":
                            passed += 1
                            _emit("step_pass", step=step_def.number, name=step_def.name,
                                  detail=result.detail, duration=round(result.duration, 1))
                            _checkpoint(step_def.number, step_def.name, "PASS", result.detail)
                        elif result.status == "fail":
                            _emit("step_fail", step=step_def.number, name=step_def.name,
                                  detail=result.detail, duration=round(result.duration, 1),
                                  error_category=result.error_category or "unknown")
                            _checkpoint(step_def.number, step_def.name, "FAIL", result.detail)
                            failed = True
                            break

                elif block_type == "observe":
                    if self.ctx.dry_run:
                        for step_def in block_steps:
                            _emit("step_skip", step=step_def.number, name=step_def.name,
                                  detail="[dry-run] observe step skipped")
                            _checkpoint(step_def.number, step_def.name, "PASS",
                                        "[dry-run] observe step skipped")
                            passed += 1
                        continue

                    obs_passed = self._run_observation_loop(block_steps)
                    passed += obs_passed
                    if obs_passed < len(block_steps):
                        failed = True

        except StopScenario as e:
            _emit("step_fail", step=0, name="abort", detail=str(e),
                  duration=0, error_category="command_error")
        finally:
            self.ctx.cleanup()

        result_str = "pass" if passed == total else "fail"
        _emit("run_complete", passed=passed, total=total, result=result_str)
        print(f"RESULT|{passed}/{total}", flush=True)

        return {"passed": passed, "total": total, "result": result_str}

    def _run_observation_loop(self, observe_steps: list[StepDef]) -> int:
        """Single polling loop that checks all observe steps each tick.

        Returns number of steps that passed.
        """
        pending = {}
        for s in observe_steps:
            if s.number < self.resume_from:
                _emit("step_skip", step=s.number, name=s.name, detail="resumed")
                _checkpoint(s.number, s.name, "PASS", "resumed")
                continue
            pending[s.number] = s
            _emit("step_start", step=s.number, name=s.name)

        if not pending:
            return len(observe_steps)

        passed_count = len(observe_steps) - len(pending)
        start_times = {num: time.time() for num in pending}
        default_timeout = 600

        while pending:
            for step_num, step_def in list(pending.items()):
                step_elapsed = time.time() - start_times[step_num]
                timeout = step_def.timeout or default_timeout

                # Timeout (per-step)
                if step_elapsed > timeout:
                    detail = f"{int(step_elapsed)}초 타임아웃"
                    _emit("step_fail", step=step_num, name=step_def.name,
                          detail=detail, duration=round(step_elapsed, 1),
                          error_category="timeout")
                    _checkpoint(step_num, step_def.name, "FAIL", detail)
                    self._fail_remaining(pending, step_num, "블록 내 다른 step 타임아웃으로 중단")
                    return passed_count

                # Abort condition
                if step_def.abort_fn:
                    try:
                        abort_result = step_def.abort_fn(self.ctx)
                        if abort_result and abort_result.status == "fail":
                            _emit("step_fail", step=step_num, name=step_def.name,
                                  detail=abort_result.detail, duration=round(step_elapsed, 1),
                                  error_category=abort_result.error_category or "timeout")
                            _checkpoint(step_num, step_def.name, "FAIL", abort_result.detail)
                            self._fail_remaining(pending, step_num, "abort 조건 충족으로 중단")
                            return passed_count
                    except Exception as e:
                        _emit("step_log", step=step_num, message=f"abort_fn error: {e}")

                # Check observation condition
                try:
                    result = step_def.fn(self.ctx)
                except Exception as e:
                    result = StepResult("fail", str(e))

                if result is None:
                    _emit("step_poll", step=step_num, elapsed=int(step_elapsed),
                          total=timeout, message=f"관찰 중...")
                elif result.status == "pass":
                    result.duration = step_elapsed
                    _emit("step_pass", step=step_num, name=step_def.name,
                          detail=result.detail, duration=round(step_elapsed, 1))
                    _checkpoint(step_num, step_def.name, "PASS", result.detail)
                    del pending[step_num]
                    passed_count += 1
                elif result.status == "fail":
                    _emit("step_fail", step=step_num, name=step_def.name,
                          detail=result.detail, duration=round(step_elapsed, 1),
                          error_category=result.error_category or "unknown")
                    _checkpoint(step_num, step_def.name, "FAIL", result.detail)
                    self._fail_remaining(pending, step_num, "블록 내 다른 step 실패로 중단")
                    return passed_count

            if pending:
                interval = min(s.poll_interval for s in pending.values())
                time.sleep(interval)

        return passed_count

    def _fail_remaining(self, pending: dict, failed_num: int, reason: str):
        """Emit FAIL checkpoints for remaining pending steps after a failure."""
        for num, s in pending.items():
            if num != failed_num:
                _emit("step_fail", step=num, name=s.name,
                      detail=reason, duration=0, error_category="aborted")
                _checkpoint(num, s.name, "FAIL", reason)

    def _execute_step(self, step_def: StepDef) -> StepResult:
        """Execute a single step with retry logic."""
        max_attempts = step_def.max_retries + 1

        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                _emit("step_retry", step=step_def.number, name=step_def.name,
                      attempt=attempt, max_attempts=max_attempts)
                time.sleep(step_def.retry_delay)

            _emit("step_start", step=step_def.number, name=step_def.name)
            start = time.time()

            try:
                result = step_def.fn(self.ctx)
                result.duration = time.time() - start
            except StopScenario:
                raise
            except Exception as e:
                result = StepResult(
                    status="fail",
                    detail=str(e),
                    duration=time.time() - start,
                )

            if result.status == "fail" and not result.error_category:
                cat, reason = classify_error(result.detail)
                result.error_category = cat
                result.error_reason = reason

            if result.status == "pass" or attempt == max_attempts:
                return result

        return result  # should not reach here


# ═══════════════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Python scenario step runner")
    parser.add_argument("steps_file", help="Path to steps.py file")
    parser.add_argument("--namespace", default="dockercoins")
    parser.add_argument("--aws-profile", default="")
    parser.add_argument("--aws-region", default="us-east-1")
    parser.add_argument("--alarm-name", default="")
    parser.add_argument("--cluster-name", default="")
    parser.add_argument("--kubectl-context", default="")
    parser.add_argument("--resume-from", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true",
                        help="Fail-fast validation: probes endpoints, checks metrics, skips waits/writes")
    args = parser.parse_args()

    clear_registry()

    steps_dir = os.path.dirname(os.path.abspath(args.steps_file))
    runner_dir = os.path.dirname(os.path.abspath(__file__))
    for d in (steps_dir, runner_dir):
        if d not in sys.path:
            sys.path.insert(0, d)

    # When run as __main__, `from scenario_runner import step` inside steps.py
    # would import a separate module instance with its own _steps_registry.
    # Fix: register the current (__main__) module as "scenario_runner".
    this_module = sys.modules[__name__]
    sys.modules["scenario_runner"] = this_module

    spec = importlib.util.spec_from_file_location("steps", args.steps_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    steps = get_registered_steps()
    if not steps:
        print("ERROR: No steps registered in " + args.steps_file, file=sys.stderr)
        sys.exit(1)

    ctx = ScenarioContext(
        namespace=args.namespace,
        aws_profile=args.aws_profile,
        aws_region=args.aws_region,
        alarm_name=args.alarm_name,
        cluster_name=args.cluster_name,
        kubectl_context=args.kubectl_context or None,
        dry_run=args.dry_run,
    )

    runner = ScenarioRunner(ctx, steps, resume_from=args.resume_from)
    summary = runner.run()
    sys.exit(0 if summary["result"] == "pass" else 1)


if __name__ == "__main__":
    main()
