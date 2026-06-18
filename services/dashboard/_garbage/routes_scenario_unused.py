# ---------------------------------------------------------------------------
# V2: Harness-based scenario generation (submit_tool pattern)
# ---------------------------------------------------------------------------

@scenario_bp.route("/api/scenario-generate-v2", methods=["POST"])
def api_scenario_generate_v2():
    """submit_tool 기반 시나리오 생성 — 자동 검증 + 재시도 루프."""
    from ai_provider import get_provider
    from generation.scenario.config import create_scenario_harness, build_generation_context

    body = request.json or {}
    message = body.get("message", "").strip()
    space_id = _req_space_id("json")
    template_id = body.get("template_id")
    app_name = body.get("app_name", "")

    if not message:
        return jsonify({"ok": False, "error": "message required"}), 400

    try:
        skill_prompt = _build_scenario_chat_context(
            space_id, template_id=template_id, app_name=app_name,
            include_script=False,
        )
        prompt = f"{message}\n\n{skill_prompt}"

        kubectl_context = _cfg_get(_CFG, "clusters.primary.context", "")
        namespace = _cfg_get(_CFG, "kubernetes.namespace", "dockercoins")

        context = build_generation_context(
            kubectl_context=kubectl_context,
            namespace=namespace,
            aws_profile=AWS_PROFILE,
            aws_region=AWS_REGION,
        )

        provider = get_provider()
        harness = create_scenario_harness(provider)
        result = harness.generate(prompt=prompt, context=context)

        if result.success:
            scenario = result.artifact
            _ensure_evaluation_rubric(scenario)
            # topology 기반 architecture 보완 (기존 _fix_scenario의 topology 로직)
            if not scenario.get("architecture"):
                try:
                    scenario["_space_id"] = space_id
                    _fix_scenario(scenario)
                    scenario.pop("_space_id", None)
                except Exception:
                    pass

            resp = {
                "ok": True,
                "scenario": scenario,
                "rounds": result.rounds,
                "has_json": True,
                "reply": f"시나리오 생성 완료 ({result.rounds}회 검증). submit_tool 기반.",
            }
            if result.validation_history:
                last = result.validation_history[-1]
                if last.warnings:
                    resp["warnings"] = [w.message for w in last.warnings]
                if last.auto_fixes:
                    resp["fixes"] = [f"[자동교정] {f}" for f in last.auto_fixes]
            return jsonify(resp)
        else:
            errors = []
            if result.validation_history:
                errors = [i.message for i in result.validation_history[-1].errors]
            return jsonify({
                "ok": False,
                "error": f"검증 실패 ({result.rounds}회 시도)",
                "validation_errors": errors,
                "rounds": result.rounds,
            }), 422

    except Exception as e:
        print(f"[SCEN-V2] 오류: {e}")
        return jsonify({"ok": False, "error": str(e),
                        "trace": traceback.format_exc()}), 500


# ---------------------------------------------------------------------------
# Script generation prompt & helpers
# ---------------------------------------------------------------------------
SCRIPT_GEN_PROMPT = _prompts.load("generate_script")
STEPS_GEN_PROMPT = _prompts.load("generate_steps")


MAX_STEPS_DRY_RUN_ROUNDS = 2


# ---------------------------------------------------------------------------
# Scenario sanitization for code generation
# ---------------------------------------------------------------------------

def _command_to_intent(command: str, trigger_type: str, scenario: dict) -> str:
    """trigger bash 명령에서 의도(intent) 문자열 추출."""
    target = scenario.get("target_service", "")
    parts = []

    if "inject-latency" in command:
        parts.append(f"{target} 서비스에 지연 주입")
    elif "kubectl run" in command:
        m = re.search(r'run\s+([\w-]+)', command)
        pod_name = m.group(1) if m else "pod"
        m_img = re.search(r'--image=([\w/:.-]+)', command)
        image = m_img.group(1) if m_img else ""
        parts.append(f"Pod '{pod_name}' 생성 (image: {image})")
        if "wget" in command or "curl" in command:
            parts.append(f"{target} 서비스에 대량/비정상 요청 전송")
        if "/dev/urandom" in command or "base64" in command:
            parts.append("비정상 페이로드 포함")
    elif "aws fis" in command:
        m = re.search(r'--experiment-template-id\s+([\w-]+)', command)
        tmpl = m.group(1) if m else ""
        parts.append(f"FIS 실험 시작 (template: {tmpl})")
    elif "port-forward" in command:
        parts.append(f"{target} 서비스 port-forward 접근")

    if not parts:
        parts.append(f"{trigger_type} 기반 장애 주입")

    purpose = scenario.get("purpose", "")[:80]
    return f"[{trigger_type}] " + "; ".join(parts) + (f" — {purpose}" if purpose else "")


def _restore_to_intent(command: str, scenario: dict) -> str:
    """restore 명령에서 의도 추출."""
    target = scenario.get("target_service", "")
    parts = []
    if "clear-latency" in command:
        parts.append(f"{target} 지연 해제")
    if "delete pod" in command:
        m = re.search(r'delete\s+pods?\s+([\w-]+)', command)
        parts.append(f"{m.group(1) if m else '장애 pod'} 삭제")
    if "rollout restart" in command:
        m = re.search(r'deployment/([\w-]+)', command)
        parts.append(f"{m.group(1) if m else target} 롤아웃 재시작")
    return "; ".join(parts) if parts else "장애 원상복구"


def _cleanup_to_intent(command: str, scenario: dict) -> str:
    """pre_cleanup 명령에서 의도 추출."""
    parts = []
    if "delete pod" in command:
        m = re.search(r'delete\s+pods?\s+([\w-]+)', command)
        parts.append(f"{m.group(1) if m else '잔여 pod'} 삭제")
    if "clear-latency" in command:
        parts.append("이전 지연 해제")
    return "; ".join(parts) if parts else "이전 실행 잔여물 정리"


def _sanitize_scenario_for_codegen(scenario: dict) -> dict:
    """코드 생성 Agent에게 보내기 전, bash command를 intent로 교체."""
    import copy
    s = copy.deepcopy(scenario)

    trigger = s.get("trigger", {})
    if isinstance(trigger, dict) and trigger.get("command"):
        trigger["intent"] = _command_to_intent(trigger["command"], trigger.get("type", ""), s)
        del trigger["command"]

    restore = s.get("restore", {})
    if isinstance(restore, dict) and restore.get("command"):
        restore["intent"] = _restore_to_intent(restore["command"], s)
        del restore["command"]

    pre_cleanup = s.get("pre_cleanup", {})
    if isinstance(pre_cleanup, dict) and pre_cleanup.get("command"):
        pre_cleanup["intent"] = _cleanup_to_intent(pre_cleanup["command"], s)
        del pre_cleanup["command"]

    return s


def _dry_run_steps(scenario: dict, steps_code: str) -> dict:
    """steps.py를 임시 파일에 쓰고 --dry-run으로 실행. 30초 타임아웃."""
    import tempfile
    import sys

    runner_path = os.path.join(os.path.dirname(__file__), "scenario_runner.py")
    scenario_json_str = json.dumps(scenario, ensure_ascii=False)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, dir="/tmp") as f:
        f.write(steps_code)
        tmp_path = f.name

    try:
        env = {**os.environ, "AWS_PAGER": "", "PYTHONUNBUFFERED": "1"}
        if AWS_PROFILE:
            env["AWS_PROFILE"] = AWS_PROFILE
        if AWS_REGION:
            env["AWS_REGION"] = AWS_REGION
        path = env.get("PATH", "")
        for p in ("/opt/homebrew/bin", "/usr/local/bin"):
            if p not in path:
                path = p + ":" + path
        env["PATH"] = path

        namespace = _cfg_get(_CFG, "kubernetes.namespace", "dockercoins")
        cluster_name = _cfg_get(_CFG, "kubernetes.cluster_name", "devops-simulator")
        kubectl_context = _cfg_get(_CFG, "clusters.primary.context", "")

        cmd = [
            sys.executable, runner_path, tmp_path,
            "--dry-run",
            "--namespace", namespace,
            "--aws-profile", AWS_PROFILE or "",
            "--aws-region", AWS_REGION or "us-east-1",
        ]
        if kubectl_context:
            cmd.extend(["--kubectl-context", kubectl_context])

        # alarm_name from scenario
        alarm_name = ""
        verification = scenario.get("verification", {})
        alarm_list = verification.get("alarms", [])
        if alarm_list:
            alarm_name = alarm_list[0].get("name", "")
        if alarm_name:
            cmd.extend(["--alarm-name", alarm_name])

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, env=env,
        )

        stdout = result.stdout
        stderr = result.stderr
        exit_code = result.returncode

        error_msg = ""
        if exit_code != 0:
            for line in (stdout + "\n" + stderr).split("\n"):
                if "StopScenario" in line or "FAIL" in line or "Error" in line:
                    error_msg += line + "\n"
            if not error_msg:
                error_msg = stderr[-500:] if stderr else stdout[-500:]

        return {
            "exit_code": exit_code,
            "error": error_msg.strip(),
            "stdout": stdout[-1000:],
            "stderr": stderr[-500:],
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": 1, "error": "dry-run 30초 타임아웃", "stdout": "", "stderr": ""}
    except Exception as e:
        return {"exit_code": 1, "error": str(e), "stdout": "", "stderr": ""}
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _steps_fix_loop(scenario: dict, steps_code: str, dry_run_result: dict,
                    session_id: str, space_id: str) -> tuple[str, str, list]:
    """Dry-run 실패 → Agent에게 에러+코드+fix hint 전달 → 수정된 코드 수신 → 재검증."""
    from ai_provider import get_provider
    fix_log = []

    for round_num in range(MAX_STEPS_DRY_RUN_ROUNDS):
        fix_prompt = (
            f"생성된 steps.py의 dry-run 검증이 실패했습니다. 에러를 수정해주세요.\n\n"
            f"## 에러\n```\n{dry_run_result['error']}\n```\n\n"
            f"## 현재 코드\n```python\n{steps_code}\n```\n\n"
            f"수정한 전체 코드를 ```python 블록으로 반환하세요."
        )

        print(f"[DRY-RUN-FIX] Round {round_num + 1}: Agent에게 수정 요청")
        resp_data = get_provider().send_raw(
            space_id=space_id,
            session_id=session_id,
            prompt=fix_prompt,
        )
        reply = resp_data.get("reply", "")
        fixed_code = _extract_python_block(reply)

        if not fixed_code:
            fix_log.append({"round": round_num + 1, "status": "no_code", "error": "Agent가 python 코드를 반환하지 않음"})
            continue

        steps_code = fixed_code
        dry_run_result = _dry_run_steps(scenario, steps_code)
        fix_log.append({
            "round": round_num + 1,
            "status": "pass" if dry_run_result["exit_code"] == 0 else "fail",
            "error": dry_run_result.get("error", ""),
        })

        if dry_run_result["exit_code"] == 0:
            print(f"[DRY-RUN-FIX] Round {round_num + 1}: PASS")
            return steps_code, "fixed", fix_log

    print(f"[DRY-RUN-FIX] {MAX_STEPS_DRY_RUN_ROUNDS}회 시도 후에도 실패")
    return steps_code, "still_failing", fix_log


@scenario_bp.route("/api/scenario-generate-script", methods=["POST"])
def api_scenario_generate_script():
    """Agent에게 시나리오 실행 스크립트 생성 요청. ChatWorker를 통해 Agent 호출.

    Python steps의 경우:
    1. readiness probe → enriched context
    2. Agent 호출 → 코드 추출
    3. validate_steps_code() 정적 검증
    4. _dry_run_steps() 런타임 검증 (8초)
    5. 실패 시 _steps_fix_loop() 교정 (최대 2회)
    """
    body = request.json or {}
    scenario_id = body.get("scenario_id", "").strip()
    space_id = _req_space_id("json")
    session_id = body.get("session_id")
    script_type = body.get("script_type", "python")

    if not scenario_id:
        return jsonify({"ok": False, "error": "scenario_id required"}), 400

    scenario = _get_scenario(space_id, scenario_id)
    if not scenario:
        return jsonify({"ok": False, "error": "Scenario not found"}), 404

    profile = _cfg_get(_CFG, "aws.profile", os.environ.get("AWS_PROFILE", ""))
    region = AWS_REGION

    if script_type == "python":
        sanitized = _sanitize_scenario_for_codegen(scenario)
        scenario_json = json.dumps(sanitized, indent=2, ensure_ascii=False)
    else:
        scenario_json = json.dumps(scenario, indent=2, ensure_ascii=False)

    # Readiness probe for python steps
    readiness_context = ""
    if script_type == "python":
        try:
            from readiness_probe import probe_scenario_readiness
            namespace = _cfg_get(_CFG, "kubernetes.namespace", "dockercoins")
            kubectl_context = _cfg_get(_CFG, "clusters.primary.context", "")
            report = probe_scenario_readiness(
                scenario=scenario,
                aws_profile=AWS_PROFILE,
                aws_region=AWS_REGION,
                namespace=namespace,
                kubectl_context=kubectl_context,
            )
            readiness_context = "\n\n" + report.summary_table
            if not report.ready:
                blocked = [r for r in report.resources if not r.ok]
                hints = "; ".join(r.fix_hint for r in blocked if r.fix_hint)
                print(f"[SCRIPT-GEN] Readiness probe: NOT READY — {hints}")
        except Exception as e:
            print(f"[SCRIPT-GEN] Readiness probe 오류 (계속 진행): {e}")

    if script_type == "python":
        prompt = STEPS_GEN_PROMPT
        full_prompt = (
            f"다음 시나리오를 확인해줘:\n```json\n{scenario_json}\n```\n"
            f"{readiness_context}\n\n{prompt}"
        )
    else:
        prompt = SCRIPT_GEN_PROMPT.format(profile=profile, region=region)
        full_prompt = f"다음 시나리오를 확인해줘:\n```json\n{scenario_json}\n```\n\n{prompt}"

    try:
        from ai_provider import get_provider
        print(f"[SCRIPT-GEN] ChatWorker 호출: scenario={scenario_id}, type={script_type}")
        resp_data = get_provider().send_raw(
            space_id=space_id,
            session_id=session_id or "",
            prompt=full_prompt,
        )

        reply = resp_data["reply"]
        new_session_id = resp_data.get("session_id", session_id)

        if script_type == "python":
            script = _extract_python_block(reply)
            if not script:
                return jsonify({"ok": False, "error": "Agent가 python 스크립트를 반환하지 않음", "reply": reply}), 422

            # Static validation
            from validate_steps import validate_steps_code
            errors, warnings = validate_steps_code(script)
            if errors:
                print(f"[SCRIPT-GEN] 정적 검증 실패: {errors}")
                # Agent에게 수정 요청
                fix_prompt = (
                    f"생성된 steps.py에 정적 검증 오류가 있습니다:\n"
                    f"{chr(10).join('- ' + e for e in errors)}\n\n"
                    f"```python\n{script}\n```\n\n수정한 전체 코드를 ```python 블록으로 반환하세요."
                )
                fix_resp = get_provider().send_raw(
                    space_id=space_id, session_id=new_session_id, prompt=fix_prompt)
                fixed = _extract_python_block(fix_resp.get("reply", ""))
                if fixed:
                    script = fixed

            # Dry-run gate
            dry_run_result = _dry_run_steps(scenario, script)
            dry_run_status = "pass" if dry_run_result["exit_code"] == 0 else "fail"
            fix_log = []

            if dry_run_result["exit_code"] != 0:
                print(f"[SCRIPT-GEN] Dry-run 실패: {dry_run_result['error'][:200]}")
                script, fix_status, fix_log = _steps_fix_loop(
                    scenario, script, dry_run_result, new_session_id, space_id)
                dry_run_status = fix_status

            _save_scenario_script(scenario_id, script, script_type="python")

            resp = {
                "ok": True,
                "scenario_id": scenario_id,
                "session_id": new_session_id,
                "script": script,
                "script_type": script_type,
                "length": len(script),
                "dry_run": dry_run_status,
            }
            if fix_log:
                resp["fix_rounds"] = fix_log
            return jsonify(resp)
        else:
            script = _extract_bash_block(reply)
            if not script:
                return jsonify({"ok": False, "error": "Agent가 bash 스크립트를 반환하지 않음", "reply": reply}), 422
            _save_scenario_script(scenario_id, script, script_type="bash")

            return jsonify({
                "ok": True,
                "scenario_id": scenario_id,
                "session_id": new_session_id,
                "script": script,
                "script_type": script_type,
                "length": len(script),
            })
    except TimeoutError:
        return jsonify({"ok": False, "error": "Agent 응답 타임아웃 (600초)"}), 504
    except Exception as e:
        print(f"[SCRIPT-GEN] 오류: {e}")
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


def _extract_bash_block(text):
    blocks = re.findall(r'```(?:bash|sh)\s*\n(.*?)```', text, re.DOTALL)
    if not blocks:
        return None
    return max(blocks, key=len).strip()


def _extract_python_block(text):
    blocks = re.findall(r'```(?:python)\s*\n(.*?)```', text, re.DOTALL)
    if not blocks:
        return None
    return max(blocks, key=len).strip()


def _save_scenario_script(scenario_id, script, script_type="bash"):
    base = os.path.join(os.path.dirname(__file__), "scenarios", scenario_id)
    os.makedirs(base, exist_ok=True)
    filename = "steps.py" if script_type == "python" else "run.sh"
    with open(os.path.join(base, filename), "w") as f:
        f.write(script)
    print(f"[SCRIPT] saved scenarios/{scenario_id}/{filename} ({len(script)} chars)")


def _get_scenario_script(scenario_id):
    """Return (script_content, script_type). steps.py takes priority over run.sh."""
    base = os.path.join(os.path.dirname(__file__), "scenarios", scenario_id)
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
# Improvement loop prompt & routes
# ---------------------------------------------------------------------------
IMPROVE_PROMPT_TEMPLATE = _prompts.load("improve")


@scenario_bp.route("/api/scenario-improvements", methods=["POST"])
def api_scenario_improvements():
    """실패 분석 + 개선 제안. ChatWorker를 통해 Agent 호출."""
    body = request.json or {}
    scenario_id = body.get("scenario_id", "")
    space_id = _req_space_id("json")
    run_id = body.get("run_id")

    scenario = _get_scenario(space_id, scenario_id)
    if not scenario:
        return jsonify({"ok": False, "error": "Scenario not found"}), 404

    run_data = None
    if run_id:
        from verifier import get_active_run, get_history
        run = get_active_run(run_id)
        if run:
            run_data = run.to_dict()
        else:
            try:
                items, _ = get_history(limit=100)
                for h in items:
                    if h.get("run_id") == run_id:
                        run_data = h
                        break
            except Exception:
                pass

    existing_rules = _load_prompt_rules(space_id)
    script_content = _get_scenario_script(scenario_id) or "스크립트 없음"

    scenario_summary = {k: v for k, v in scenario.items()
                        if k in ("id", "name", "category", "trigger", "verification", "pre_cleanup", "restore")}
    run_summary = None
    if run_data:
        run_summary = {
            "result": run_data.get("result"),
            "status": run_data.get("status"),
            "checkpoints": run_data.get("checkpoints", []),
            "alarm_results": run_data.get("alarm_results", []),
            "script_output": {
                "exit_code": run_data.get("script_output", {}).get("exit_code"),
                "stdout": run_data.get("script_output", {}).get("stdout", "")[-2000:],
                "stderr": run_data.get("script_output", {}).get("stderr", "")[-1000:],
            },
        }

    prompt = IMPROVE_PROMPT_TEMPLATE.format(
        scenario_json=json.dumps(scenario_summary, indent=2, ensure_ascii=False),
        script_content=f"```bash\n{script_content[-3000:]}\n```" if script_content != "스크립트 없음" else script_content,
        review_result="리뷰 미수행",
        execution_result=json.dumps(run_summary, indent=2, ensure_ascii=False) if run_summary else "실행 미수행",
        existing_rules="\n".join(f"- {r}" for r in existing_rules) if existing_rules else "없음",
    )

    try:
        from ai_provider import get_provider
        print(f"[IMPROVE] ChatWorker 호출: scenario={scenario_id}, run={run_id}, prompt_size={len(prompt)}")
        resp_data = get_provider().send_raw(
            space_id=space_id,
            session_id="",
            prompt=prompt,
        )

        reply = resp_data["reply"]
        improvements = _extract_json_from_text(reply) or {}

        infra_gaps = improvements.get("infrastructure_gaps", [])
        blocking = [g for g in infra_gaps if g.get("blocking")]
        if blocking:
            improvements["_blocked"] = True
            improvements["_blocking_summary"] = "; ".join(
                f"{g['resource']}: {g.get('fix_command','(수동 조치 필요)')}" for g in blocking
            )

        return jsonify({"ok": True, "improvements": improvements})
    except TimeoutError:
        return jsonify({"ok": False, "error": "Agent 응답 타임아웃 (600초)"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _extract_json_from_text(text):
    """Agent 응답 텍스트에서 JSON 블록 추출."""
    for pattern in [r"```json\s*\n(.*?)\n```", r"```\s*\n(.*?)\n```"]:
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


@scenario_bp.route("/api/scenario-improvements/accept", methods=["POST"])
def api_accept_improvements():
    body = request.json or {}
    space_id = _req_space_id("json")
    scenario_id = body.get("scenario_id", "")

    new_rules = body.get("prompt_rules", [])
    if new_rules:
        existing = _load_prompt_rules(space_id)
        existing.extend(new_rules)
        _save_prompt_rules(space_id, existing)

    scenario_fixes = body.get("scenario_fixes", [])
    fixes_applied = 0
    if scenario_fixes:
        scenario = _get_scenario(space_id, scenario_id)
        if scenario:
            for fix in scenario_fixes:
                field = fix.get("field", "")
                new_val = fix.get("new")
                if field and new_val is not None:
                    try:
                        keys = field.split(".")
                        obj = scenario
                        for k in keys[:-1]:
                            if isinstance(obj, list) and k.isdigit():
                                obj = obj[int(k)]
                            elif isinstance(obj, dict):
                                obj = obj.setdefault(k, {})
                            else:
                                raise KeyError(f"cannot traverse {type(obj).__name__} with key {k}")
                        last = keys[-1]
                        if isinstance(obj, list) and last.isdigit():
                            obj[int(last)] = new_val
                        elif isinstance(obj, dict):
                            obj[last] = new_val
                        fixes_applied += 1
                    except (KeyError, IndexError, TypeError) as e:
                        print(f"[ACCEPT] skip fix {field}: {e}")
            _save_scenario(space_id, scenario)

    script_updated = False
    script_fix = body.get("script_fix")
    if script_fix and scenario_id:
        _save_scenario_script(scenario_id, script_fix)
        script_updated = True

    infra_gaps = body.get("infrastructure_gaps", [])

    return jsonify({
        "ok": True,
        "rules_added": len(new_rules),
        "fixes_applied": fixes_applied,
        "script_updated": script_updated,
        "infrastructure_gaps": infra_gaps,
    })


# ---------------------------------------------------------------------------
# Prompt rules persistence helpers
# ---------------------------------------------------------------------------
_PROMPT_RULES_CACHE = {}


def _load_prompt_rules(space_id):
    if space_id in _PROMPT_RULES_CACHE:
        return list(_PROMPT_RULES_CACHE[space_id])
    try:
        resp = _arch_table().get_item(Key={"scenario_id": space_id, "run_id": "prompt-rules"})
        rules = resp.get("Item", {}).get("rules", [])
        _PROMPT_RULES_CACHE[space_id] = list(rules)
        return list(rules)
    except Exception:
        return []


def _save_prompt_rules(space_id, rules):
    _PROMPT_RULES_CACHE[space_id] = list(rules)
    try:
        _arch_table().put_item(Item={
            "scenario_id": space_id,
            "run_id": "prompt-rules",
            "rules": rules,
        })
    except Exception as e:
        print(f"[RULES] save failed: {e}")
