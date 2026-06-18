"""
Engine Self-Correction: post-failure probe + scenario auto-patch + retry.

After a run fails, collects ALL observable signals in ONE pass:
- pod logs (actual patterns present)
- pod status transitions (actual states + timing)
- alarm existence & state
- trigger before/after diff
- resource creation vs mutation detection

Then patches the scenario in-memory and retries once.
"""
import json
import re
import time

from verifier_utils import _run_cmd, _cfg, AWS_REGION, NAMESPACE


class SelfCorrector:
    """Probes environment after failure and patches scenario for retry."""

    def __init__(self, run_result: dict, scenario: dict, namespace: str = None,
                 context: str = None, profile: str = None):
        self.run_result = run_result
        self.scenario = json.loads(json.dumps(scenario))  # deep copy
        self.namespace = namespace or NAMESPACE
        self.context = context
        self.profile = profile
        self.corrections = []
        self.probes = {}

    def probe_and_correct(self) -> dict:
        """One-shot: gather all signals, apply all corrections, return patched scenario."""
        failed_steps = [s for s in self.run_result.get("steps", [])
                        if s["status"] == "fail"]

        if not failed_steps:
            return self.scenario

        # ── Collect ALL signals in one pass ──
        self._probe_pod_logs(failed_steps)
        self._probe_pod_states(failed_steps)
        self._probe_alarms(failed_steps)
        self._probe_trigger_effect()
        self._probe_timing(failed_steps)

        # ── Apply corrections based on collected signals ──
        self._correct_patterns(failed_steps)
        self._correct_expected_states(failed_steps)
        self._correct_timeouts(failed_steps)
        self._correct_missing_resources(failed_steps)

        return self.scenario

    # ── Probes (read-only observation) ───────────────────────────────────

    def _probe_pod_logs(self, failed_steps):
        """Fetch actual recent logs for pods referenced in failed steps."""
        for step in failed_steps:
            if step.get("type") not in ("pod_logs",):
                continue
            pod = step.get("pod") or step.get("config", {}).get("pod", "")
            if not pod:
                continue

            cmd = f"kubectl logs -n {self.namespace} -l app={pod} --tail=100 --all-containers 2>/dev/null"
            ok, stdout, _ = _run_cmd(cmd, timeout=15, context=self.context)
            if ok and stdout:
                self.probes[f"logs:{pod}"] = stdout

    def _probe_pod_states(self, failed_steps):
        """Get current pod state + events for referenced pods."""
        pods_checked = set()
        for step in failed_steps:
            if step.get("type") not in ("pod_status",):
                continue
            pod = step.get("pod") or step.get("config", {}).get("pod", "")
            if not pod or pod in pods_checked:
                continue
            pods_checked.add(pod)

            # Get pod JSON
            cmd = f"kubectl get pods -n {self.namespace} -l app={pod} -o json 2>/dev/null"
            ok, stdout, _ = _run_cmd(cmd, timeout=10, context=self.context)
            if ok and stdout:
                self.probes[f"pod_json:{pod}"] = stdout

            # Get events
            cmd = f"kubectl get events -n {self.namespace} --field-selector involvedObject.name={pod} --sort-by=.lastTimestamp 2>/dev/null | tail -10"
            ok, stdout, _ = _run_cmd(cmd, timeout=10, context=self.context)
            if ok and stdout:
                self.probes[f"events:{pod}"] = stdout

    def _probe_alarms(self, failed_steps):
        """Check alarm existence and current state."""
        for step in failed_steps:
            if step.get("type") not in ("cw_alarm", "alarm_state"):
                continue
            alarm = step.get("config", {}).get("alarm") or step.get("alarm", "")
            if not alarm:
                continue

            import boto3
            try:
                session = boto3.Session(profile_name=self.profile) if self.profile else boto3.Session()
                cw = session.client("cloudwatch", region_name=AWS_REGION)
                resp = cw.describe_alarms(AlarmNames=[alarm])
                alarms = resp.get("MetricAlarms", []) + resp.get("CompositeAlarms", [])
                if alarms:
                    self.probes[f"alarm:{alarm}"] = {
                        "exists": True,
                        "state": alarms[0].get("StateValue", "?"),
                    }
                else:
                    self.probes[f"alarm:{alarm}"] = {"exists": False}
            except Exception as e:
                self.probes[f"alarm:{alarm}"] = {"exists": False, "error": str(e)[:100]}

    def _probe_trigger_effect(self):
        """Analyze what trigger actually did (from run result)."""
        trigger_output = self.run_result.get("trigger_output", "")
        trigger_cmd = self.scenario.get("trigger", {}).get("command", "")

        self.probes["trigger"] = {
            "creates_resource": "apply -f" in trigger_cmd or "create" in trigger_cmd,
            "scales": "scale" in trigger_cmd,
            "execs": "exec" in trigger_cmd,
            "output": trigger_output[:500],
        }

    def _probe_timing(self, failed_steps):
        """Analyze timing from step events to understand propagation delay."""
        for step in failed_steps:
            events = step.get("events", [])
            if not events:
                continue
            step_name = step.get("name", "")
            elapsed = step.get("elapsed", 0)
            polls = len([e for e in events if "poll#" in e.get("msg", "")])
            extensions = len([e for e in events if "연장" in e.get("msg", "")])
            progress = len([e for e in events if "progress" in e.get("msg", "")])
            self.probes[f"timing:{step_name}"] = {
                "elapsed": elapsed,
                "polls": polls,
                "extensions": extensions,
                "progress_detected": progress > 0,
            }

    # ── Corrections (modify scenario) ────────────────────────────────────

    def _correct_patterns(self, failed_steps):
        """Fix log patterns based on actual observed logs."""
        verification = self.scenario.get("verification", {})
        steps = verification.get("steps") or verification.get("checks") or []

        for step in failed_steps:
            if step.get("type") != "pod_logs":
                continue
            pod = step.get("pod") or step.get("config", {}).get("pod", "")
            logs = self.probes.get(f"logs:{pod}", "")
            if not logs:
                continue

            original_pattern = step.get("config", {}).get("pattern") or step.get("pattern", "")

            # Extract error-related patterns from actual logs
            new_patterns = self._extract_error_patterns(logs)

            if new_patterns:
                # Found patterns — merge with existing
                for s in steps:
                    if s.get("name") == step.get("name") or (s.get("pod") == pod and s.get("type") == "pod_logs"):
                        old_pattern = s.get("pattern", "")
                        combined = "|".join(set(new_patterns + old_pattern.split("|")))
                        s["pattern"] = combined
                        self.corrections.append({
                            "step": step.get("name"),
                            "field": "pattern",
                            "old": old_pattern,
                            "new": combined,
                            "reason": f"실제 로그에서 발견된 패턴: {new_patterns[:3]}",
                        })
                        break
            else:
                # No error patterns in logs — try trigger output as verification
                trigger_output = self.probes.get("trigger", {}).get("output", "")
                if trigger_output:
                    trigger_keywords = self._extract_trigger_keywords(trigger_output)
                    if trigger_keywords:
                        for s in steps:
                            if s.get("name") == step.get("name") or (s.get("pod") == pod and s.get("type") == "pod_logs"):
                                s["type"] = "kubectl_check"
                                s["command"] = f"kubectl logs -n {self.namespace} -l app={pod} --tail=200"
                                s["expected"] = "|".join(trigger_keywords)
                                s.pop("pattern", None)
                                s.pop("pod", None)
                                self.corrections.append({
                                    "step": step.get("name"),
                                    "field": "type",
                                    "old": "pod_logs",
                                    "new": "kubectl_check (trigger output 기반)",
                                    "reason": f"pod 로그에 에러 패턴 없음 — trigger 출력 키워드로 전환: {trigger_keywords}",
                                })
                                break
                    else:
                        # Last resort: mark step as skip_on_final_fail
                        for s in steps:
                            if s.get("name") == step.get("name"):
                                s["error_handling"] = s.get("error_handling", {})
                                s["error_handling"]["skip_on_final_fail"] = True
                                self.corrections.append({
                                    "step": step.get("name"),
                                    "field": "skip_on_final_fail",
                                    "old": False,
                                    "new": True,
                                    "reason": "로그에 에러 패턴 없고, trigger 출력에도 키워드 없음 — warn 처리",
                                })
                                break

    def _correct_expected_states(self, failed_steps):
        """Fix expected pod states based on actual observed states."""
        verification = self.scenario.get("verification", {})
        steps = verification.get("steps") or verification.get("checks") or []

        for step in failed_steps:
            if step.get("type") != "pod_status":
                continue
            pod = step.get("pod") or step.get("config", {}).get("pod", "")
            pod_json_raw = self.probes.get(f"pod_json:{pod}", "")
            if not pod_json_raw:
                # No pods found — check if trigger creates it
                trigger_info = self.probes.get("trigger", {})
                if trigger_info.get("creates_resource") or trigger_info.get("scales"):
                    continue
                continue

            try:
                pods_data = json.loads(pod_json_raw).get("items", [])
            except (json.JSONDecodeError, ValueError):
                continue

            if not pods_data:
                detail = step.get("detail", "")
                if "파드 없음" in detail:
                    for s in steps:
                        if s.get("name") == step.get("name"):
                            old_expected = s.get("expected", "")
                            if "없음" not in old_expected.lower() and "notready" not in old_expected.lower():
                                s["expected"] = f"{old_expected}|NotReady|없음"
                                self.corrections.append({
                                    "step": step.get("name"),
                                    "field": "expected",
                                    "old": old_expected,
                                    "new": s["expected"],
                                    "reason": "scale 0 → 파드 없음 상태 허용",
                                })
                continue

            # Extract actual states from running pods
            actual_states = set()
            for p in pods_data:
                phase = p.get("status", {}).get("phase", "")
                actual_states.add(phase)
                for cs in p.get("status", {}).get("containerStatuses", []):
                    state = cs.get("state", {})
                    for state_type, state_info in state.items():
                        if state_type == "waiting":
                            reason = state_info.get("reason", "")
                            if reason:
                                actual_states.add(reason)
                        elif state_type == "terminated":
                            reason = state_info.get("reason", "")
                            if reason:
                                actual_states.add(reason)

            if actual_states:
                for s in steps:
                    if s.get("name") == step.get("name"):
                        old_expected = s.get("expected", "")
                        # Merge actual states with expected
                        merged = "|".join(set(old_expected.split("|")) | actual_states)
                        if merged != old_expected:
                            s["expected"] = merged
                            self.corrections.append({
                                "step": step.get("name"),
                                "field": "expected",
                                "old": old_expected,
                                "new": merged,
                                "reason": f"실제 관측 상태: {actual_states}",
                            })
                        break

    def _correct_timeouts(self, failed_steps):
        """Adjust timeouts based on observed timing and progress signals."""
        verification = self.scenario.get("verification", {})
        steps = verification.get("steps") or verification.get("checks") or []

        for step in failed_steps:
            step_name = step.get("name", "")
            timing = self.probes.get(f"timing:{step_name}", {})
            if not timing:
                continue

            elapsed = timing.get("elapsed", 0)
            progress = timing.get("progress_detected", False)

            for s in steps:
                if s.get("name") == step_name:
                    old_timeout = s.get("timeout", 60)
                    if progress:
                        # Progress was detected but ran out of time — double it
                        new_timeout = old_timeout * 2
                        reason = "progress 감지됨 — timeout 확장"
                    elif elapsed >= old_timeout * 0.9:
                        # Used almost all time — likely needs more
                        new_timeout = int(old_timeout * 1.5)
                        reason = f"timeout 거의 소진 ({elapsed:.0f}/{old_timeout}s)"
                    else:
                        continue

                    s["timeout"] = new_timeout
                    self.corrections.append({
                        "step": step_name,
                        "field": "timeout",
                        "old": old_timeout,
                        "new": new_timeout,
                        "reason": reason,
                    })
                    break

    def _correct_missing_resources(self, failed_steps):
        """Handle missing alarms or resources."""
        verification = self.scenario.get("verification", {})
        steps = verification.get("steps") or verification.get("checks") or []

        for step in failed_steps:
            if step.get("type") not in ("cw_alarm", "alarm_state"):
                continue
            alarm = step.get("config", {}).get("alarm") or step.get("alarm", "")
            alarm_info = self.probes.get(f"alarm:{alarm}", {})

            if not alarm_info.get("exists"):
                for s in steps:
                    if s.get("name") == step.get("name"):
                        s["skip_validation"] = True
                        s["error_handling"] = s.get("error_handling", {})
                        s["error_handling"]["skip_on_final_fail"] = True
                        self.corrections.append({
                            "step": step.get("name"),
                            "field": "skip_validation",
                            "old": False,
                            "new": True,
                            "reason": f"알람 '{alarm}' 미존재 — skip 처리",
                        })
                        break

    # ── Helpers ──────────────────────────────────────────────────────────

    def _extract_error_patterns(self, logs: str) -> list[str]:
        """Extract likely error patterns from log text."""
        patterns = set()
        error_indicators = [
            r'(Error|Exception|Failed|Traceback|FATAL|WARN|panic|refused|timeout|denied)',
            r'(Connection\w+|Errno|Exit\w*|OOM|Kill|crash|BackOff)',
        ]
        for line in logs.split("\n"):
            for regex in error_indicators:
                matches = re.findall(regex, line, re.IGNORECASE)
                for m in matches:
                    if len(m) > 3:
                        patterns.add(m)

        return list(patterns)[:5]

    def _extract_trigger_keywords(self, trigger_output: str) -> list[str]:
        """Extract meaningful keywords from trigger output for verification."""
        keywords = set()
        # Look for distinctive words in trigger output (not generic)
        words = re.findall(r'[A-Za-z_]{4,}', trigger_output)
        generic = {'http', 'https', 'kubectl', 'done', 'true', 'false', 'null',
                   'name', 'kind', 'metadata', 'spec', 'status', 'with', 'from'}
        for w in words:
            w_lower = w.lower()
            if w_lower not in generic and len(w) > 4:
                keywords.add(w)
        return list(keywords)[:3]

    def get_correction_summary(self) -> str:
        """Human-readable summary of corrections made."""
        if not self.corrections:
            return "보정 없음 (probe 결과 변경 불필요)"

        lines = [f"자동 보정 {len(self.corrections)}건:"]
        for c in self.corrections:
            lines.append(f"  [{c['step']}] {c['field']}: {c['old']} → {c['new']}")
            lines.append(f"    사유: {c['reason']}")
        return "\n".join(lines)
