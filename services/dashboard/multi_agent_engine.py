"""
Multi-Agent Simulation Engine: Generator + Verifier.

Generator: 시나리오+코드 생성
Verifier: 생성 후 검증 (실행 가능성) + 실행 후 검증 (효과 확인)

Integration: executor_type="multi_agent" in config.yaml or scenario JSON.
"""
import json
import os
import re
import time
import threading
import uuid
from datetime import datetime, timezone

from strands import Agent
from strands.agent.agent import null_callback_handler

from execution_context import ExecutionContext
from engine_cleanup_registry import CleanupRegistry
from verifier_utils import _run_cmd, _agent_space_session, AWS_REGION, NAMESPACE, _AGENT_SPACE_ID, _RUNS_TABLE


class LiveCallbackHandler:
    """Strands callback that relays streaming events to a UI proxy."""

    def __init__(self, proxy, agent_name="Agent"):
        self._proxy = proxy
        self._agent_name = agent_name
        self._buf = ""

    def __call__(self, **kwargs):
        if not hasattr(self._proxy, "_append_event"):
            return

        # Tool use start — emit tool name immediately
        tool_use = kwargs.get("event", {}).get("contentBlockStart", {}).get("start", {}).get("toolUse")
        if tool_use:
            self._flush()
            self._proxy._append_event(self._agent_name, f"🔧 {tool_use.get('name','tool')}")
            return

        # Streaming text tokens — buffer until newline or complete
        chunk = kwargs.get("data", "")
        if chunk:
            self._buf += str(chunk)
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                line = line.strip()
                if line:
                    self._proxy._append_event(self._agent_name, line[:200])

        # End of response — flush remaining buffer
        if kwargs.get("complete"):
            self._flush()

    def _flush(self):
        if self._buf.strip():
            self._proxy._append_event(self._agent_name, self._buf.strip()[:200])
        self._buf = ""


def _make_model(profile=None, region="us-east-1"):
    from strands.models.bedrock import BedrockModel
    import boto3
    from botocore.config import Config as BotocoreConfig
    from providers.strands_agents import _default_model

    session_kwargs = {}
    if profile:
        session_kwargs["profile_name"] = profile
    if region:
        session_kwargs["region_name"] = region

    return BedrockModel(
        boto_session=boto3.Session(**session_kwargs),
        boto_client_config=BotocoreConfig(read_timeout=300, connect_timeout=10),
        model_id=_default_model(),
        max_tokens=16384,
    )


class MultiAgentEngine:
    """Multi-agent scenario executor. Generator + Verifier."""

    def __init__(self, scenario, agent_space_id=None, namespace=None):
        self.run_id = str(uuid.uuid4())[:8]
        self.scenario = scenario
        self.scenario_id = scenario["id"]
        self.agent_space_id = agent_space_id or _AGENT_SPACE_ID
        self.namespace = namespace or scenario.get("namespace") or NAMESPACE
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._started_ts = time.time()

        self._exec_ctx = ExecutionContext.for_scenario(scenario, namespace=self.namespace)
        self._scenario_context = self._exec_ctx.kubectl_context or None
        self._scenario_profile = self._exec_ctx.profile or None

        self.completed_at = None
        self.status = "created"
        self.result = None
        self.trigger_output = ""
        self._incident_id = None
        self._investigation_task_id = None
        self._correction_summary = None
        self._corrections_applied = False

        self.steps = []
        self._cleanup_registry = CleanupRegistry(
            namespace=self.namespace,
            context=self._scenario_context,
            profile=self._scenario_profile,
        )

    # ── Main Pipeline ────────────────────────────────────────────────────

    def _run_pipeline(self):
        """Execute: App trigger → Verifier → result."""
        IDX_TRIGGER = 0
        IDX_VERIFY = 1
        IDX_INV_START = 2
        IDX_RESTORE = 3
        IDX_INV_END = 4

        try:
            self.status = "running"
            self.steps = [
                self._step("장애 주입 (Trigger)", "pending", "대기 중"),
                self._step("검증 (Verifier)", "pending", "대기 중"),
                self._step("DevOps Agent 조사 시작", "pending", "대기 중"),
                self._step("복원 (Restore)", "pending", "대기 중"),
                self._step("DevOps Agent 조사 종료", "pending", "대기 중"),
            ]
            self.save()

            from multi_agent_tools import VERIFIER_TOOLS, configure
            from multi_agent_prompts import VERIFIER_PROMPT_EXECUTION

            configure(
                kubectl_context=self._scenario_context or "",
                profile=self._scenario_profile or "",
                region=AWS_REGION,
                namespace=self.namespace,
            )

            model = _make_model(profile=self._scenario_profile, region=AWS_REGION)

            # ── Step 1: Execute trigger (App = subprocess) ──
            trigger_cmd = self.scenario.get("trigger", {}).get("command", "")
            if not trigger_cmd:
                self.steps[IDX_TRIGGER] = self._step("장애 주입 (Trigger)", "fail", "trigger command 없음")
                self.result = "fail"
                return

            self.steps[IDX_TRIGGER] = self._step("장애 주입 (Trigger)", "checking", "실행 중...")
            self._log(f"Trigger: {trigger_cmd[:80]}")
            cmd = self._inject_context(trigger_cmd)
            ok, stdout, stderr = _run_cmd(cmd, timeout=120)
            self.trigger_output = stdout if ok else f"FAIL: {stderr}"

            if ok:
                self.steps[IDX_TRIGGER] = self._step("장애 주입 (Trigger)", "pass", stdout[:100])
                self._log(f"Trigger 성공")
                restore_cmd = self.scenario.get("restore", {}).get("command", "")
                if restore_cmd:
                    self._cleanup_registry.register("kubectl", "trigger", cleanup_cmd=self._inject_context(restore_cmd))
            else:
                self.steps[IDX_TRIGGER] = self._step("장애 주입 (Trigger)", "fail", stderr[:100])
                self._log(f"Trigger 실패: {stderr[:80]}")
                self.result = "fail"
                return

            # ── Step 2: Verifier Agent (실행 후 검증) ──
            self.steps[IDX_VERIFY] = self._step("검증 (Verifier)", "checking", "효과 확인 중...")
            self.status = "verifying"
            self._log("Verifier Agent 호출")

            verifier = Agent(
                model=model,
                system_prompt=VERIFIER_PROMPT_EXECUTION,
                tools=list(VERIFIER_TOOLS),
                callback_handler=null_callback_handler,
            )

            verify_steps = self.scenario.get("verification", {}).get("steps", [])
            verify_prompt = f"""다음 verification steps를 확인하세요.

## Trigger 실행 결과
{self.trigger_output[:500]}

## Verification Steps
```json
{json.dumps(verify_steps, indent=2, ensure_ascii=False)}
```

각 step의 command를 check_command 도구로 확인하고, expected 매칭 여부를 판단하세요.
timeout 내에 매칭 안 되면 wait_seconds(10) 후 재시도하세요.
반드시 JSON으로 결과를 반환하세요."""

            verify_start = time.time()
            verify_text = str(verifier(verify_prompt))
            verify_elapsed = round(time.time() - verify_start, 1)

            verify_data = self._parse_json(verify_text)
            if verify_data and verify_data.get("result") == "pass":
                self.steps[IDX_VERIFY] = self._step("검증 (Verifier)", "pass", f"통과 ({verify_elapsed}s)")
                self.result = "pass"
            else:
                self.steps[IDX_VERIFY] = self._step("검증 (Verifier)", "fail", verify_text[:150])
                self.result = "fail"

            # Verifier sub-step을 events로 기록 (고정 인덱스 보호)
            if verify_data and "steps" in verify_data:
                for vs in verify_data["steps"]:
                    status_icon = "✓" if vs.get("status") == "pass" else "✗"
                    self.steps[IDX_VERIFY]["events"].append({
                        "t": time.time(),
                        "msg": f"{status_icon} {vs.get('name','')}: {vs.get('detail','')[:80]}"
                    })

            self._log(f"Verifier 완료: {self.result} ({verify_elapsed}s)")

            # ── 개선 루프: 검증 실패 시 Generator 재호출 → 재실행 (1회) ──
            if self.result == "fail" and not self._corrections_applied:
                self._corrections_applied = True
                self._log("개선 루프: Generator 재호출")
                self.steps.append(self._step("개선 (Retry)", "checking", "시나리오 재생성 중..."))

                # Restore first
                restore_cmd = self.scenario.get("restore", {}).get("command", "")
                if restore_cmd:
                    _run_cmd(self._inject_context(restore_cmd), timeout=60)
                self._cleanup_registry.drain()

                # Generator 재호출 with failure context
                from multi_agent_tools import GENERATOR_TOOLS
                from multi_agent_prompts import GENERATOR_PROMPT
                generator = Agent(
                    model=model, system_prompt=GENERATOR_PROMPT,
                    tools=list(GENERATOR_TOOLS), callback_handler=null_callback_handler,
                )
                failure_detail = verify_text[:500]
                retry_prompt = f"""이전 시나리오가 실패했습니다.

## 실패 이유
{failure_detail}

## 원래 시나리오
{json.dumps(self.scenario.get('trigger',{}), ensure_ascii=False)}

## 요구사항
같은 목적을 달성하되 **다른 방식**으로 시나리오를 재생성하세요.
실패한 방식(예: NetworkPolicy)은 사용하지 마세요.
namespace: {self.namespace}
"""
                gen_text = str(generator(retry_prompt))
                new_scenario = self._parse_json(gen_text)

                if new_scenario and new_scenario.get("trigger", {}).get("command"):
                    self.scenario = {**self.scenario, **new_scenario}
                    self.steps[-1] = self._step("개선 (Retry)", "pass", f"재생성: {new_scenario['trigger']['command'][:60]}")
                    self._log(f"재생성 완료: {new_scenario['trigger']['command'][:60]}")

                    # 재실행
                    new_trigger = self._inject_context(new_scenario["trigger"]["command"])
                    ok2, stdout2, stderr2 = _run_cmd(new_trigger, timeout=120)
                    self.trigger_output = stdout2 if ok2 else f"FAIL: {stderr2}"
                    self.steps.append(self._step("장애 재주입 (Trigger)", "pass" if ok2 else "fail", self.trigger_output[:80]))

                    if ok2:
                        # Register new cleanup
                        new_restore = new_scenario.get("restore", {}).get("command", "")
                        if new_restore:
                            self._cleanup_registry.register("kubectl", "retry-trigger", cleanup_cmd=self._inject_context(new_restore))

                        # 재검증
                        new_steps = new_scenario.get("verification", {}).get("steps", [])
                        verify_prompt2 = f"Trigger 결과: {self.trigger_output[:300]}\n\nVerification Steps:\n```json\n{json.dumps(new_steps, indent=2, ensure_ascii=False)}\n```\n\n확인하세요."
                        v2_text = str(verifier(verify_prompt2))
                        v2_data = self._parse_json(v2_text)
                        if v2_data and v2_data.get("result") == "pass":
                            self.steps.append(self._step("재검증 (Verifier)", "pass", "통과"))
                            self.result = "pass"
                            if v2_data.get("steps"):
                                for vs in v2_data["steps"]:
                                    self.steps.append(self._step(vs.get("name",""), vs.get("status",""), vs.get("detail","")[:80]))
                        else:
                            self.steps.append(self._step("재검증 (Verifier)", "fail", v2_text[:100]))
                else:
                    self.steps[-1] = self._step("개선 (Retry)", "fail", "재생성 실패")

        except Exception as e:
            import traceback
            self._log(f"예외: {e}\n{traceback.format_exc()[-300:]}")
            self.result = self.result or "fail"
        finally:
            # ── DevOps Agent 조사 먼저 트리거 (Agent가 장애 상태를 볼 수 있도록) ──
            self._trigger_investigation(idx_start=IDX_INV_START, idx_end=IDX_INV_END)

            # Restore (with retry) — always update index 2
            restore_cmd = self.scenario.get("restore", {}).get("command", "")
            if restore_cmd:
                self.steps[IDX_RESTORE] = self._step("복원 (Restore)", "checking", "실행 중...")
                self._log(f"Restore: {restore_cmd[:80]}")
                cmd = self._inject_context(restore_cmd)
                ok, stdout, stderr = _run_cmd(cmd, timeout=120)
                if not ok:
                    self._log(f"Restore 1차 실패, 10초 후 재시도: {stderr[:60]}")
                    time.sleep(10)
                    ok, stdout, stderr = _run_cmd(cmd, timeout=120)
                self.steps[IDX_RESTORE] = self._step("복원 (Restore)", "pass" if ok else "fail", (stdout if ok else stderr)[:80])
                if not ok:
                    self._log(f"Restore 최종 실패: {stderr[:80]}")
            else:
                self.steps[IDX_RESTORE] = self._step("복원 (Restore)", "skipped", "restore 명령 없음")

            if self._cleanup_registry.pending_count > 0:
                self._cleanup_registry.drain()

            self.status = "completed"
            self.completed_at = datetime.now(timezone.utc).isoformat()
            self.save()

    # ── Utilities ────────────────────────────────────────────────────────

    def _step(self, name, status, detail, elapsed=None):
        return {"name": name, "type": "agent", "tier": "primary", "status": status,
                "detail": detail, "elapsed": elapsed, "events": [], "config": {}}

    def _inject_context(self, command: str) -> str:
        if self._scenario_context and "kubectl" in command and "--context" not in command:
            command = command.replace("kubectl ", f"kubectl --context {self._scenario_context} ", 1)
        if self._scenario_profile and "aws " in command and "--profile" not in command:
            command += f" --profile {self._scenario_profile}"
        return command

    def _log(self, msg):
        print(f"[MultiAgentEngine] {self.scenario_id}: {msg}")
        if self.steps:
            active = next((s for s in reversed(self.steps) if s["status"] == "checking"), None)
            target = active or self.steps[-1]
            target["events"].append({"t": time.time(), "msg": msg})

    def _send_investigation_webhook(self, alarm_name, alarm_desc):
        """Send webhook using self.agent_space_id for secret lookup."""
        import hashlib as _hashlib, hmac as _hmac, base64 as _b64, urllib.request as _urllib
        import boto3
        from app_config import _profile_for_space
        try:
            secret_id = f"webhook-{self.agent_space_id}"
            profile = _profile_for_space(self.agent_space_id)
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
                "data": {"metadata": {"region": AWS_REGION, "environment": "frontier-agent-hub", "alarmName": alarm_name}}
            }
            body = json.dumps(payload)
            sig = _b64.b64encode(_hmac.new(creds['webhookSecret'].encode(), f"{ts}:{body}".encode(), _hashlib.sha256).digest()).decode()
            req = _urllib.Request(creds['webhookUrl'], data=body.encode(),
                headers={'Content-Type': 'application/json', 'x-amzn-event-timestamp': ts, 'x-amzn-event-signature': sig}, method='POST')
            with _urllib.urlopen(req, timeout=15) as r:
                self._log(f"Webhook {r.status} for {alarm_name} incident_id={iid}")
            return iid
        except Exception as e:
            self._log(f"Webhook send failed: {e}")
            return None

    def _trigger_investigation(self, idx_start=3, idx_end=4):
        """Trigger DevOps Agent investigation webhook, then poll for task in background."""
        if self._investigation_task_id:
            self.steps[idx_start] = self._step("DevOps Agent 조사 시작", "pass", "이미 연결됨")
            self.steps[idx_end] = self._step("DevOps Agent 조사 종료", "pass", f"task: {self._investigation_task_id[:20]}")
            return

        if not self.agent_space_id:
            self.steps[idx_start] = self._step("DevOps Agent 조사 시작", "skipped", "Agent Space 미설정")
            self.steps[idx_end] = self._step("DevOps Agent 조사 종료", "skipped", "")
            return

        self.steps[idx_start] = self._step("DevOps Agent 조사 시작", "checking", "웹훅 전송 중...")
        self.save()

        try:
            alarm_name = f"scenario-{self.scenario_id}"
            alarm_desc = self.scenario.get("purpose", self.scenario.get("name", ""))
            iid = self._send_investigation_webhook(alarm_name, alarm_desc)
            if not iid:
                self.steps[idx_start] = self._step("DevOps Agent 조사 시작", "warn", "웹훅 전송 실패")
                self.steps[idx_end] = self._step("DevOps Agent 조사 종료", "skipped", "")
                return
            self._incident_id = iid
            self.steps[idx_start] = self._step("DevOps Agent 조사 시작", "pass", f"웹훅 전송 완료")
            self.steps[idx_end] = self._step("DevOps Agent 조사 종료", "checking", "Agent 조사 진행 중...")
            self._log(f"Investigation triggered: {iid}")
            self.save()
            self._start_investigation_poll(iid, idx_end)
        except Exception as e:
            self.steps[idx_start] = self._step("DevOps Agent 조사 시작", "warn", f"웹훅 오류: {str(e)[:60]}")
            self.steps[idx_end] = self._step("DevOps Agent 조사 종료", "skipped", "")
            self._log(f"Investigation webhook failed: {e}")

    def _start_investigation_poll(self, incident_id, idx_end):
        """Background thread: poll DDB for investigation task_id using incident_id."""
        def _poll():
            max_attempts = 30  # 30 * 20s = 10min
            for attempt in range(max_attempts):
                time.sleep(20)
                try:
                    task_id, status = self._find_task(incident_id)
                    if task_id:
                        self._investigation_task_id = task_id
                        if status in ("COMPLETED", "completed", "done"):
                            self.steps[idx_end] = self._step("DevOps Agent 조사 종료", "pass", f"task: {task_id[:20]}")
                        else:
                            self.steps[idx_end] = self._step("DevOps Agent 조사 종료", "checking", f"진행 중 ({status})")
                            self.save()
                            continue
                        self.save()
                        return
                except Exception:
                    pass
            self.steps[idx_end] = self._step("DevOps Agent 조사 종료", "warn", "타임아웃 (10분)")
            self.save()

        t = threading.Thread(target=_poll, daemon=True, name=f"inv-poll-{self.run_id[:8]}")
        t.start()

    def _find_task(self, incident_id):
        """Find task_id by incident_id via Agent Space API (list_backlog_tasks)."""
        from app_config import _profile_for_space
        import boto3
        try:
            profile = _profile_for_space(self.agent_space_id)
            session = boto3.Session(profile_name=profile, region_name=AWS_REGION)
            client = session.client("devops-agent", region_name=AWS_REGION)
            resp = client.list_backlog_tasks(
                agentSpaceId=self.agent_space_id,
                filter={"taskType": ["INVESTIGATION"]},
                limit=20, order="DESC",
            )
            for t in resp.get("tasks", []):
                ref = t.get("reference", {})
                if ref.get("referenceId") == incident_id:
                    return t.get("taskId", ""), t.get("status", "")
            return None, None
        except Exception as e:
            self._log(f"find_task failed: {e}")
            return None, None

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        for pat in [re.compile(r'```json\s*\n(.*?)\n```', re.DOTALL),
                    re.compile(r'(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})', re.DOTALL)]:
            m = pat.search(text)
            if m:
                try:
                    return json.loads(m.group(1))
                except json.JSONDecodeError:
                    continue
        return None

    # ── External Contract ────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
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
            "self_correction": self._correction_summary,
            "cleanup_registry": self._cleanup_registry.entries if hasattr(self._cleanup_registry, 'entries') else [],
            "executor_type": "multi_agent",
            "steps": [
                {"index": i, "name": s["name"], "type": s["type"], "tier": s.get("tier", "primary"),
                 "pod": s.get("config", {}).get("pod", ""), "status": s["status"],
                 "detail": s["detail"], "elapsed": s["elapsed"], "events": s.get("events", [])}
                for i, s in enumerate(self.steps)
            ],
        }

    def save(self) -> str:
        try:
            from decimal import Decimal
            table = _agent_space_session().resource("dynamodb", region_name=AWS_REGION).Table(_RUNS_TABLE)
            d = self.to_dict()
            item = json.loads(json.dumps(d, default=str), parse_float=Decimal)
            item["run_id"] = self.run_id
            item["record_type"] = "run"
            item["scenario_id"] = self.scenario_id
            if self.agent_space_id:
                item["agent_space_id"] = self.agent_space_id
            table.put_item(Item=item)
        except Exception as e:
            print(f"[MultiAgentEngine] DynamoDB save failed: {e}")
            from verifier_utils import RESULTS_DIR
            os.makedirs(RESULTS_DIR, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            filepath = os.path.join(RESULTS_DIR, f"{ts}_{self.scenario_id}_{self.run_id}.json")
            with open(filepath, "w") as f:
                json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        return self.run_id


# ═══════════════════════════════════════════════════════════════════════════
# Scenario Generation (called from routes — async job)
# ═══════════════════════════════════════════════════════════════════════════

def generate_scenario(message: str, namespace: str = "default",
                      kubectl_context: str = "", profile: str = "", region: str = "us-east-1") -> dict:
    """Generate scenario with Generator Agent + Verifier review. Returns scenario dict or raises."""
    from multi_agent_tools import GENERATOR_TOOLS, VERIFIER_TOOLS, configure
    from multi_agent_prompts import GENERATOR_PROMPT, VERIFIER_PROMPT_REVIEW

    configure(kubectl_context=kubectl_context, profile=profile, region=region, namespace=namespace)
    model = _make_model(profile=profile, region=region)

    # Generator
    generator = Agent(
        model=model, system_prompt=GENERATOR_PROMPT,
        tools=list(GENERATOR_TOOLS), callback_handler=null_callback_handler,
    )
    prompt = f"{message}\n\nnamespace: {namespace}\n환경을 확인하고 시나리오 JSON을 생성하세요."
    gen_text = str(generator(prompt))
    scenario = _parse_json_static(gen_text)
    if not scenario:
        raise ValueError(f"Generator 출력에서 JSON 파싱 실패")

    # Verifier (1차 — 실행 가능성 검증)
    verifier = Agent(
        model=model, system_prompt=VERIFIER_PROMPT_REVIEW,
        tools=list(VERIFIER_TOOLS), callback_handler=null_callback_handler,
    )
    review_prompt = f"""다음 시나리오의 실행 가능성을 검증하세요.

```json
{json.dumps(scenario, indent=2, ensure_ascii=False)}
```

namespace: {namespace}
trigger command가 실행 가능한지, 참조 리소스가 존재하는지 tool로 확인하세요."""

    review_text = str(verifier(review_prompt))
    review = _parse_json_static(review_text)

    # Reject → Generator 재호출 (1회)
    if review and review.get("result") == "reject":
        reason = review.get("reject_reason", "")
        print(f"[GENERATE] Verifier reject: {reason}")
        retry_prompt = f"{message}\n\nnamespace: {namespace}\n\n이전 생성이 리젝트됨: {reason}\n이 문제를 해결한 새 시나리오를 생성하세요."
        gen_text = str(generator(retry_prompt))
        scenario = _parse_json_static(gen_text)
        if not scenario:
            raise ValueError("재생성 실패")

    scenario["executor"] = "multi_agent"
    scenario.setdefault("source", "multi-agent-generated")
    return scenario


def _parse_json_static(text: str) -> dict | None:
    for pat in [re.compile(r'```json\s*\n(.*?)\n```', re.DOTALL),
                re.compile(r'(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})', re.DOTALL)]:
        m = pat.search(text)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
    return None
