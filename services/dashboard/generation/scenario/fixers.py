"""Scenario auto-fixers — mechanical corrections that don't need LLM.

Extracted from routes_scenario.py _fix_scenario() for reuse in the harness.
"""

from __future__ import annotations

import re


class ScenarioAutoFixer:
    """기계적 시나리오 교정 — trigger type 추론, timeout 최소값, 구조 정규화."""

    def fix(self, artifact: dict, context: dict | None = None) -> tuple[dict, list[str]]:
        fixes: list[str] = []
        scenario = artifact

        trigger = scenario.get("trigger", {})
        trigger_cmd = trigger.get("command", "") if isinstance(trigger, dict) else ""

        self._fix_target_service(scenario, trigger_cmd, fixes)
        self._fix_trigger_type(scenario, trigger_cmd, fixes)
        self._fix_rm_flag(scenario, fixes)
        self._fix_trigger_mode(scenario, fixes)
        self._fix_alarm_timeout(scenario, fixes)
        self._fix_verification_structure(scenario, fixes)
        self._fix_flow_format(scenario, fixes)
        self._fix_rollback_to_restore(scenario, fixes)

        return scenario, fixes

    def _fix_target_service(self, scenario: dict, trigger_cmd: str, fixes: list):
        if scenario.get("target_service", "").strip():
            return
        if not trigger_cmd:
            return

        patterns = [
            r'svc/(\w+)',
            r'deployment/(\w[\w-]*)',
            r'-l\s+app=(\w+)',
            r'http://(\w+)[.:/]',
        ]
        for pat in patterns:
            m = re.search(pat, trigger_cmd)
            if m:
                scenario["target_service"] = m.group(1)
                fixes.append(f"target_service 자동 추출: {m.group(1)}")
                return

    def _fix_trigger_type(self, scenario: dict, trigger_cmd: str, fixes: list):
        trigger = scenario.get("trigger", {})
        if not isinstance(trigger, dict) or not trigger_cmd:
            return

        declared = trigger.get("type", "")
        if "kubectl " in trigger_cmd and declared != "kubectl":
            trigger["type"] = "kubectl"
            fixes.append(f"trigger.type 교정: {declared} -> kubectl")
        elif "aws fis " in trigger_cmd and declared != "fis":
            trigger["type"] = "fis"
            fixes.append(f"trigger.type 교정: {declared} -> fis")
        elif "aws " in trigger_cmd and "aws fis " not in trigger_cmd and declared != "aws":
            trigger["type"] = "aws"
            fixes.append(f"trigger.type 교정: {declared} -> aws")

    def _fix_rm_flag(self, scenario: dict, fixes: list):
        for field_path in ["trigger.command", "restore.command", "pre_cleanup.command"]:
            parts = field_path.split(".")
            obj = scenario.get(parts[0], {})
            if isinstance(obj, dict) and "--rm" in obj.get(parts[1], ""):
                obj[parts[1]] = obj[parts[1]].replace(" --rm", "")
                fixes.append(f"{field_path}에서 --rm 제거")

    def _fix_trigger_mode(self, scenario: dict, fixes: list):
        if scenario.get("trigger_mode"):
            return
        v_steps = scenario.get("verification", {}).get("steps", [])
        has_investigation = any(
            isinstance(s, dict) and s.get("type") == "investigation_event"
            for s in v_steps)
        has_agent_inv = any(
            isinstance(s, dict) and s.get("type") == "agent_investigation"
            for s in v_steps)

        if has_investigation:
            scenario["trigger_mode"] = "reactive"
            fixes.append("trigger_mode 추론: reactive")
        elif has_agent_inv:
            scenario["trigger_mode"] = "proactive"
            fixes.append("trigger_mode 추론: proactive")
        else:
            scenario["trigger_mode"] = "reactive"
            fixes.append("trigger_mode 기본값: reactive")

    def _fix_alarm_timeout(self, scenario: dict, fixes: list):
        v_steps = scenario.get("verification", {}).get("steps", [])
        for step in v_steps:
            if not isinstance(step, dict):
                continue
            if step.get("type") not in ("alarm_state", "cw_alarm"):
                continue
            expected = step.get("expected", "")
            min_timeout = 300 if expected == "ALARM" else 180
            current = step.get("timeout", 60)
            if isinstance(current, int) and current < min_timeout:
                step["timeout"] = min_timeout
                fixes.append(f"alarm_state timeout 교정: {current}s -> {min_timeout}s")

    def _fix_verification_structure(self, scenario: dict, fixes: list):
        verification = scenario.get("verification", {})
        if not isinstance(verification, dict):
            return

        # checks → steps
        if "checks" in verification and "steps" not in verification:
            verification["steps"] = verification.pop("checks")
            scenario["verification"] = verification
            fixes.append("verification.checks -> steps 변환")

        # commands array → single command
        trigger = scenario.get("trigger", {})
        if isinstance(trigger, dict) and isinstance(trigger.get("commands"), list):
            trigger["command"] = " && ".join(trigger.pop("commands"))
            fixes.append("trigger.commands 배열 -> command 문자열 합침")

        # description → name in steps
        for step in verification.get("steps", []):
            if isinstance(step, dict) and "name" not in step and "description" in step:
                step["name"] = step.pop("description")

    def _fix_flow_format(self, scenario: dict, fixes: list):
        for flow_key in ("normal_flow", "fault_flow"):
            flow = scenario.get(flow_key)
            if isinstance(flow, list) and flow and isinstance(flow[0], str):
                scenario[flow_key] = [
                    {"step": f"{i+1}. {s.split('(')[0].strip()}",
                     "desc": s.split('(')[-1].rstrip(')') if '(' in s else ""}
                    for i, s in enumerate(flow)
                ]
                fixes.append(f"{flow_key} 문자열→객체 변환")

    def _fix_rollback_to_restore(self, scenario: dict, fixes: list):
        if scenario.get("restore"):
            return
        rollback = scenario.get("rollback")
        if isinstance(rollback, dict):
            steps = rollback.get("steps", [])
            if steps:
                cmds = [s.get("command", "") for s in steps
                        if isinstance(s, dict) and s.get("command")]
                scenario["restore"] = {"command": " && ".join(cmds)}
            elif rollback.get("command"):
                scenario["restore"] = {"command": rollback["command"]}
            scenario.pop("rollback", None)
            fixes.append("rollback -> restore 변환")
