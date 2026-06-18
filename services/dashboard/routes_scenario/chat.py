"""Scenario chat generation routes: templates, scenario-chat, gen-job status, multi-agent."""
import json
import re
import time
import traceback
import threading as _gen_threading

from flask import jsonify, request

import prompts as _prompts

from app_config import (
    _CFG, _cfg_get, AWS_REGION, AGENT_SPACE_ID, AWS_PROFILE,
    _req_space_id, _agent_space_id, _boto_session,
)
from routes_arch import (
    _load_latest_arch, _list_scenarios,
)
from routes_scenario import scenario_bp
from routes_scenario.crud import (
    _fix_scenario, _validate_scenario, _ensure_evaluation_rubric,
)


@scenario_bp.route("/api/scenario-templates")
def api_scenario_templates():
    from arch_analysis import FAILURE_MODES
    return jsonify({"ok": True, "templates": FAILURE_MODES})


def _build_scenario_chat_context(space_id=None, template_id=None, app_name=None,
                                  include_script=False) -> str:
    """Skill 트리거 기반 경량 프롬프트 생성.

    Agent Space에 등록된 scenario-generate 스킬이 포맷/규칙을 제공하므로,
    앱은 동적 데이터(장애 모드, 앱 스코프, 알람, FIS, 기존 시나리오)만 전달.
    """
    from arch_analysis import FAILURE_MODES

    if template_id:
        selected = [fm for fm in FAILURE_MODES if fm["id"] == template_id]
        if not selected:
            selected = FAILURE_MODES
        fm = selected[0]
        trigger_tag = f"#scenario-generate {fm['id']}"
        fm_section = (
            f"## 장애 모드 정보\n"
            f"- ID: {fm['id']}\n"
            f"- 이름: {fm['name']}\n"
            f"- 카테고리: infrastructure\n"
            f"- 레이어: {fm.get('layer', '')}\n"
            f"- 트리거 모드: {fm.get('trigger_mode', 'reactive')}\n"
            f"- 설명: {fm['description']}\n"
            f"- 트리거 메커니즘: {', '.join(fm.get('trigger_mechanism', []))}\n"
            f"- 적용 조건: {fm.get('applicable_when', '')}"
        )
        obs = fm.get("observation_signals")
        if obs:
            fm_section += "\n\n## 관측 신호 (observation_signals) — verification steps 생성 기준\n"
            fm_section += "아래 신호를 현재 환경(EKS/CloudWatch/etc)에 맞는 구체적 verification step으로 변환하세요.\n"
            fm_section += "effect_type이 infra_state인 signal은 반드시 kubectl_check로 직접 확인. metric_observed는 확인 우선 원칙 적용.\n\n"
            for phase, signals in obs.items():
                fm_section += f"### {phase}\n"
                for sig in signals:
                    if isinstance(sig, dict):
                        line = f"- [{sig['effect_type']}] {sig['signal']}: {sig['description']}"
                        if sig.get("verification_hint"):
                            line += f"\n  힌트: {sig['verification_hint']}"
                        if sig.get("metric_hint"):
                            mh = sig["metric_hint"]
                            line += f"\n  metric_hint: {mh['namespace']}/{mh['metric_name']} ({mh.get('statistic','')}, {mh.get('direction','')})"
                        if sig.get("fallback"):
                            line += f"\n  fallback: {sig['fallback']}"
                        fm_section += f"{line}\n"
                    else:
                        fm_section += f"- {sig}\n"
    else:
        trigger_tag = "#scenario-generate"
        fm_section = (
            "## 장애 모드\n"
            "사용자 메시지에 따라 적절한 장애 모드를 선택하여 시나리오를 생성하세요.\n\n"
            "### 가용 장애 모드\n" +
            "\n".join(f"- {fm['id']}: {fm['name']} — {fm['description'][:60]}"
                      for fm in FAILURE_MODES)
        )

    app_section = ""
    topo_section = ""
    cluster_name = _cfg_get(_CFG, "kubernetes.cluster_name", "devops-simulator")
    namespace = _cfg_get(_CFG, "kubernetes.namespace", "dockercoins")
    if app_name:
        try:
            saved = _load_latest_arch(space_id or AGENT_SPACE_ID)
            if saved and saved.get("graph"):
                nodes = saved["graph"].get("nodes", [])
                edges = saved["graph"].get("edges", [])
                svc_names = [n["name"] for n in nodes if (n.get("group") or "") == app_name]
                if svc_names:
                    app_section = (
                        f"## 대상 앱: {app_name}\n"
                        f"- 서비스: {', '.join(svc_names)}\n"
                        f"- EKS 클러스터: {cluster_name}\n"
                        f"- 네임스페이스: {namespace}"
                    )
                # 토폴로지 그래프 — 서비스 간 통신 경로 (architecture/flow 생성의 근거)
                node_set = {n["name"] for n in nodes}
                topo_lines = []
                for e in edges:
                    src, tgt = e.get("source", ""), e.get("target", "")
                    if src in node_set and tgt in node_set:
                        proto = e.get("protocol", "")
                        port = e.get("port", "")
                        label = f"{proto}:{port}" if proto and port else (proto or "")
                        topo_lines.append(f"  {src} → {tgt}" + (f" ({label})" if label else ""))
                if topo_lines:
                    topo_section = (
                        "## 아키텍처 토폴로지 (분석 결과)\n"
                        "서비스 간 실제 통신 경로:\n" + "\n".join(topo_lines)
                    )
        except Exception:
            pass
    if not app_section:
        app_section = (
            f"## 대상 인프라\n"
            f"- EKS 클러스터: {cluster_name}\n"
            f"- 네임스페이스: {namespace}"
        )

    # 인프라 readiness probe — 검증된 리소스 테이블 생성
    readiness_section = ""
    try:
        from readiness_probe import probe_scenario_readiness
        probe_scenario = {}
        if template_id:
            # 템플릿 기반이면 알람 패턴으로 검색
            probe_scenario = {"target_service": app_name or "", "verification": {"alarms": []}}
        # 기본 알람 목록은 여전히 조회하되 readiness probe 결과로 보강
        session = _boto_session()
        cw = session.client("cloudwatch")
        paginator = cw.get_paginator("describe_alarms")
        alarms_raw = []
        for page in paginator.paginate(MaxRecords=100):
            for a in page.get("MetricAlarms", []):
                alarms_raw.append(a)
            if len(alarms_raw) > 50:
                break
        # probe할 알람 설정
        alarm_defs = [{"name": a["AlarmName"]} for a in alarms_raw]
        probe_scenario["verification"] = {"alarms": alarm_defs}
        # 알람 메트릭 조건표 생성 (Agent가 trigger↔alarm 인과 관계 판단 가능)
        _alarm_metric_table = (
            "\n\n## 알람 메트릭 조건표 (trigger↔alarm 인과 관계 판단용)\n"
            "| 알람 이름 | 메트릭 | Namespace | Statistic | Comparison | Threshold | Period |\n"
            "|-----------|--------|-----------|-----------|------------|-----------|--------|\n"
        )
        for _a in alarms_raw:
            _comp = _a.get("ComparisonOperator", "").replace("GreaterThanThreshold", ">").replace("LessThanThreshold", "<").replace("GreaterThanOrEqualToThreshold", ">=")
            _alarm_metric_table += (
                f"| {_a['AlarmName']} | {_a.get('MetricName', '?')} | "
                f"{_a.get('Namespace', '?')} | {_a.get('Statistic', '?')} | "
                f"{_comp} | {_a.get('Threshold', '?')} | {_a.get('Period', '?')}s |\n"
            )
        if not probe_scenario.get("target_service") and app_name:
            probe_scenario["target_service"] = app_name

        report = probe_scenario_readiness(
            scenario=probe_scenario,
            aws_profile=AWS_PROFILE,
            aws_region=AWS_REGION,
            namespace=namespace,
            kubectl_context=_cfg_get(_CFG, "clusters.primary.context", ""),
            service_port=80,
        )
        readiness_section = report.summary_table + _alarm_metric_table
    except Exception as e:
        # fallback: 기존 방식 — 메트릭 조건표 포함
        alarms_fallback = []
        try:
            session = _boto_session()
            cw = session.client("cloudwatch")
            paginator = cw.get_paginator("describe_alarms")
            _fb_alarms_raw = []
            for page in paginator.paginate(StateValue="OK", MaxRecords=100):
                for a in page.get("MetricAlarms", []):
                    alarms_fallback.append(a["AlarmName"])
                    _fb_alarms_raw.append(a)
                for a in page.get("CompositeAlarms", []):
                    alarms_fallback.append(a["AlarmName"])
                if len(alarms_fallback) > 50:
                    break
        except Exception:
            _fb_alarms_raw = []
        _fb_table = (
            "\n\n## 알람 메트릭 조건표 (trigger↔alarm 인과 관계 판단용)\n"
            "| 알람 이름 | 메트릭 | Namespace | Statistic | Comparison | Threshold | Period |\n"
            "|-----------|--------|-----------|-----------|------------|-----------|--------|\n"
        )
        for _a in _fb_alarms_raw:
            _comp = _a.get("ComparisonOperator", "").replace("GreaterThanThreshold", ">").replace("LessThanThreshold", "<").replace("GreaterThanOrEqualToThreshold", ">=")
            _fb_table += (
                f"| {_a['AlarmName']} | {_a.get('MetricName', '?')} | "
                f"{_a.get('Namespace', '?')} | {_a.get('Statistic', '?')} | "
                f"{_comp} | {_a.get('Threshold', '?')} | {_a.get('Period', '?')}s |\n"
            )
        readiness_section = "## 가용 CloudWatch 알람\n" + (
            "\n".join(f"- {a}" for a in alarms_fallback) if alarms_fallback else "- (없음)"
        ) + _fb_table
    alarm_section = readiness_section
    alarm_section += (
        "\n\n⚠️ 기존 알람 중 trigger 효과와 일치하는 것이 있으면 `alarm_name`으로 재사용하세요.\n"
        "   일치하는 알람이 없으면 `alarm_spec`으로 새 알람 조건을 정의하세요.\n"
    )

    fis_templates = []
    try:
        session = _boto_session()
        fis = session.client("fis")
        resp = fis.list_experiment_templates(maxResults=20)
        for t in resp.get("experimentTemplates", []):
            desc = t.get("description", "")
            fis_templates.append(f"{t['id']}: {desc}" if desc else t["id"])
    except Exception:
        pass
    fis_section = "## 가용 FIS 실험 템플릿\n" + (
        "\n".join(f"- {t}" for t in fis_templates) if fis_templates else "- (없음)"
    )

    existing_items = _list_scenarios(space_id or AGENT_SPACE_ID)
    existing_section = "## 기존 시나리오\n" + (
        "\n".join(f"- {s.get('id','')}: {s.get('name','')}" for s in existing_items)
        if existing_items else "- (없음)"
    )

    if include_script:
        script_section = (
            "\n\n#include-script\n\n"
            "## 스크립트 필수 제약\n"
            "- 알람 ALARM 대기: MAX_WAIT **최소 300초** (알람 설정에서 Period*EvalPeriods*5로 동적 계산)\n"
            "- 복원 후 OK 대기: OK_WAIT **최소 180초**\n"
            "- 모든 외부 호출(curl, kubectl port-forward)에 retry 함수 적용 (최소 3회)\n"
            "- 장애 주입 후 보조 트래픽 생성 (백그라운드 curl 루프)\n"
            "- 알람 대기 중 REINJECT_INTERVAL=Period 간격으로 장애 재주입"
        )
    else:
        script_section = ""

    format_reminder = (
        "\n\n## 필수 출력 규칙 (위반 시 교정 요청됨)\n"
        "- architecture: {components: [{id, label, type}], edges: [{from, to, label}], fault_path: [노드명, ...]} 필수\n"
        "- normal_flow: [{step: \"1. A → B\", desc: \"설명\"}, ...] 필수\n"
        "- fault_flow: [{step: \"1. A → B\", desc: \"장애 설명\"}, ...] 필수\n"
        "- target_service, trigger_mode, category, layer, purpose 필수\n"
        "- skill_version: \"2.1\"\n"
        "- verification.steps 각 step에 phase 필수\n"
    )

    parts = [trigger_tag, "", fm_section, "", app_section, ""]
    if topo_section:
        parts.extend([topo_section, ""])
    parts.extend([alarm_section, "", fis_section, "", existing_section, script_section, format_reminder])
    return "\n".join(parts)


MAX_SCENARIO_FIX_ROUNDS = 3


def _extract_json_block(text):
    """Reply에서 ```json 블록 추출 -> dict 또는 None. trailing comma 자동 수정."""
    m = re.search(r'```json\s*\n(.*?)```', text, re.DOTALL)
    if not m:
        m = re.search(r'```\s*\n(\{.*?\})\s*```', text, re.DOTALL)
    if not m:
        return None
    raw = m.group(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    fixed = re.sub(r",\s*([}\]])", r"\1", raw)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return None


def _extract_bash_block(text):
    """Reply에서 ```bash 블록 추출 -> str 또는 None."""
    blocks = re.findall(r'```(?:bash|sh)\s*\n(.*?)```', text, re.DOTALL)
    if not blocks:
        return None
    return max(blocks, key=len).strip()


def _scenario_fix_loop(scenario, session_id, space_id, include_script):
    """시나리오 검증 -> 기계적 교정 -> Agent 교정 루프.
    Returns (scenario, fixes_log, reply_parts)."""
    from ai_provider import get_provider
    scenario["_space_id"] = space_id
    fixes_log = []
    reply_parts = []

    for round_num in range(1, MAX_SCENARIO_FIX_ROUNDS + 1):
        auto_fixes = _fix_scenario(scenario)
        if auto_fixes:
            fixes_log.extend([f"[자동교정] {f}" for f in auto_fixes])

        errors, warnings = _validate_scenario(scenario)
        if warnings:
            fixes_log.extend([f"[경고] {w}" for w in warnings])
        # phase/skill_version 누락은 교정 대상
        critical_warnings = [w for w in warnings if "phase" in w or "skill_version" in w]
        if not errors and not critical_warnings:
            break
        if not errors and critical_warnings:
            errors = critical_warnings

        # phase/skill_version 누락 시 구체적 예시 포함
        hint = ""
        if any("phase" in e for e in errors) or any("phase" in w for w in warnings):
            hint += (
                '\n\n## phase 필드 필수\n'
                'verification.steps 각 step에 phase 추가:\n'
                '- trigger_active: kubectl_check, pod_status, fis_experiment\n'
                '- effect_observed: alarm_state, metric_check, log_pattern\n'
                '- reaction_confirmed: investigation_event, agent_investigation\n'
            )
        if any("skill_version" in w for w in warnings):
            hint += '\n\n## skill_version 필수\nJSON 최상위에 `"skill_version": "2.1"` 추가\n'
        if any("target_service" in e for e in errors):
            hint += '\n\n## target_service 필수\nJSON 최상위에 `"target_service": "서비스명"` 추가\n'

        fix_msg = (
            f"생성한 시나리오에 다음 오류가 있습니다. 같은 JSON 포맷으로 수정해서 다시 보내주세요.\n\n"
            f"## 오류 ({len(errors)}개)\n" +
            "\n".join(f"- {e}" for e in errors) +
            hint +
            "\n\n수정된 ```json 블록을 포함해서 응답해주세요."
        )
        if include_script:
            fix_msg += " 스크립트(```bash)도 포함해주세요."

        print(f"[SCEN-FIX] round {round_num}: errors={errors}")
        fixes_log.append(f"[교정 라운드 {round_num}] Agent에게 수정 요청: {errors}")

        resp_data = get_provider().send_raw(
            space_id=space_id, session_id=session_id, prompt=fix_msg,
        )
        fix_reply = resp_data["reply"]
        reply_parts.append(f"\n\n---\n**[자동 교정 라운드 {round_num}]**\n{fix_reply}")

        fixed = _extract_json_block(fix_reply)
        if fixed:
            fixed["id"] = scenario.get("id", fixed.get("id", ""))
            scenario.update(fixed)
        else:
            fixes_log.append(f"[라운드 {round_num}] Agent 응답에 JSON 없음 — 교정 중단")
            break
    else:
        remaining_errors, _ = _validate_scenario(scenario)
        if remaining_errors:
            fixes_log.append(f"[최대 라운드 도달] 남은 오류: {remaining_errors}")

    scenario.pop("_space_id", None)
    return scenario, fixes_log, reply_parts


@scenario_bp.route("/api/scenario-chat", methods=["POST"])
def api_scenario_chat():
    """Agent 채팅 (시나리오 생성). ChatWorker를 통해 Agent 호출."""
    body = request.json or {}
    message = body.get("message", "").strip()
    session_id = body.get("session_id")
    space_id = _req_space_id("json")
    template_id = body.get("template_id")
    app_name = body.get("app_name", "")
    include_script = body.get("include_script", False)
    executor_mode = body.get("executor_mode", "") or _cfg_get(_CFG, "executor.default", "classic")

    if not message:
        return jsonify({"ok": False, "error": "message required"}), 400

    # Multi Agent mode: Generator Agent로 시나리오 생성
    if executor_mode == "multi_agent" and not session_id:
        return _scenario_chat_multi_agent(message, space_id, template_id, app_name)

    try:
        if not session_id:
            skill_prompt = _build_scenario_chat_context(
                space_id, template_id=template_id, app_name=app_name,
                include_script=include_script,
            )
            prompt = f"{message}\n\n{skill_prompt}"
        else:
            prompt = message

        from ai_provider import get_provider
        print(f"[SCEN-CHAT] ChatWorker 호출, app: {app_name or '(all)'}, include_script={include_script}")
        resp_data = get_provider().send_raw(
            space_id=space_id,
            session_id=session_id or "",
            prompt=prompt,
        )

        reply = resp_data["reply"]
        new_session_id = resp_data.get("session_id", session_id)
        has_json = "```json" in reply or "```\n{" in reply
        has_script = bool(_extract_bash_block(reply)) if include_script else False
        print(f"[SCEN-CHAT] 응답: {len(reply)} chars, has_json={has_json}, has_script={has_script}, session={new_session_id[:16] if new_session_id else '?'}")

        # 시나리오 JSON이 있으면 검증 + 교정 루프
        fixes_log = []
        if has_json:
            scenario = _extract_json_block(reply)
            if scenario:
                scenario, fixes_log, fix_reply_parts = _scenario_fix_loop(
                    scenario, new_session_id, space_id, include_script)
                if fix_reply_parts:
                    reply += "".join(fix_reply_parts)
                    has_script = has_script or bool(_extract_bash_block(reply))

        resp = {"ok": True, "reply": reply,
                "session_id": new_session_id, "has_json": has_json}
        if has_json and scenario:
            resp["scenario"] = scenario
        if fixes_log:
            resp["fixes"] = fixes_log
        if include_script and has_script:
            resp["has_script"] = True
            resp["script"] = _extract_bash_block(reply)
        return jsonify(resp)
    except TimeoutError:
        return jsonify({"ok": False, "error": "Agent 응답 타임아웃 (600초)"}), 504
    except Exception as e:
        print(f"[SCEN-CHAT] 오류: {e}")
        return jsonify({"ok": False, "error": str(e),
                        "trace": traceback.format_exc()}), 500


# ── Multi Agent 비동기 생성 (polling 기반) ────────────────────────────────

_gen_jobs = {}  # job_id → {status, events, scenario, error}
_gen_jobs_lock = _gen_threading.Lock()


@scenario_bp.route("/api/scenario-gen-job/<job_id>/status")
def api_scenario_gen_job_status(job_id):
    """생성 job 상태 polling."""
    with _gen_jobs_lock:
        job = _gen_jobs.get(job_id)
    if not job:
        return jsonify({"ok": False, "error": "job not found"}), 404
    return jsonify({"ok": True, **job})


def _scenario_chat_multi_agent(message, space_id, template_id, app_name):
    """Multi Agent 모드: 즉시 job_id 반환, 백그라운드에서 Generator Agent 실행."""
    import uuid as _uuid
    job_id = f"gen-{_uuid.uuid4().hex[:8]}"

    with _gen_jobs_lock:
        _gen_jobs[job_id] = {"status": "generating", "events": [], "scenario": None, "error": None}

    def _run_gen():
        import re as _re
        from multi_agent_tools import GENERATOR_TOOLS, configure as configure_tools
        from multi_agent_prompts import GENERATOR_PROMPT
        from multi_agent_engine import LiveCallbackHandler, _make_model
        from execution_context import ExecutionContext

        job = _gen_jobs[job_id]

        class GenCallbackProxy:
            """LiveCallbackHandler proxy that writes to job events."""
            def _append_event(self, phase, msg):
                with _gen_jobs_lock:
                    job["events"].append({"t": round(time.time(), 1), "phase": phase, "msg": msg})

        proxy = GenCallbackProxy()
        cb = LiveCallbackHandler(proxy, "Generator")

        exec_ctx = ExecutionContext.for_scenario({"namespace": "default"})
        configure_tools(
            kubectl_context=exec_ctx.kubectl_context or "",
            profile=exec_ctx.profile or "",
            region=exec_ctx.region or AWS_REGION,
            namespace=exec_ctx.namespace or "default",
        )

        model = _make_model(profile=exec_ctx.profile, region=exec_ctx.region or AWS_REGION)
        agent = Agent(
            model=model,
            system_prompt=GENERATOR_PROMPT,
            tools=list(GENERATOR_TOOLS),
            callback_handler=cb,
        )

        prompt = f"{message}\n\n환경을 kubectl_query/aws_query로 확인한 후 시나리오 JSON을 생성하세요. 반드시 JSON만 출력하세요."
        print(f"[SCEN-GEN-ASYNC] Generator Agent 시작, app={app_name}, template={template_id}")

        try:
            result_text = str(agent(prompt))
            print(f"[SCEN-GEN-ASYNC] 완료: {len(result_text)} chars")

            scenario = None
            for pat in [_re.compile(r'```json\s*\n(.*?)\n```', _re.DOTALL), _re.compile(r'(\{.*\})', _re.DOTALL)]:
                m = pat.search(result_text)
                if m:
                    try:
                        scenario = json.loads(m.group(1))
                        break
                    except json.JSONDecodeError:
                        continue

            if scenario:
                scenario["executor"] = "multi_agent"
                scenario.setdefault("id", f"MA-{template_id or 'gen'}-{int(time.time()) % 10000}")
                scenario.setdefault("source", "multi-agent-generated")
                _ensure_evaluation_rubric(scenario)

            with _gen_jobs_lock:
                job["status"] = "completed"
                job["scenario"] = scenario
                job["reply"] = result_text
        except Exception as e:
            print(f"[SCEN-GEN-ASYNC] 오류: {e}")
            with _gen_jobs_lock:
                job["status"] = "failed"
                job["error"] = str(e)

    from strands import Agent
    t = _gen_threading.Thread(target=_run_gen, daemon=True)
    t.start()

    return jsonify({"ok": True, "job_id": job_id, "status": "generating"})
