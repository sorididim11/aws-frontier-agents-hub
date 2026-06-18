#!/usr/bin/env python3
"""
DevOps Agent Test Simulator
Flask app for managing, triggering, and verifying test scenarios.
"""
import json
import os
import re
import glob

from flask import Flask, render_template, jsonify, request

from verifier import (
    start_run, get_active_run, list_active_runs,
    confirm_manual_step, cancel_run, get_history,
    get_environment_status, get_slack_messages, init_slack_config,
)
from evidence import create_blueprint as _create_evidence_bp
import cluster_manager

# Load config (env vars take precedence via config.get())
try:
    from config import get as _cfg
    _AWS_REGION = _cfg("aws.region", os.environ.get("AWS_REGION", "us-east-1"))
    _AGENT_SPACE_ID = _cfg("agent.space_id", os.environ.get("AGENT_SPACE_ID", ""))
    _EVENTS_TABLE = _cfg("dynamodb.events_table", os.environ.get("EVENTS_TABLE", ""))
    _RUNS_TABLE = _cfg("dynamodb.runs_table", os.environ.get("RUNS_TABLE", ""))
    _PROJECT_NAME = _cfg("project.name", os.environ.get("PROJECT_NAME", "devops-agent-test"))
except ImportError:
    _AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
    _AGENT_SPACE_ID = os.environ.get("AGENT_SPACE_ID", "")
    _EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "")
    _RUNS_TABLE = os.environ.get("RUNS_TABLE", "")
    _PROJECT_NAME = os.environ.get("PROJECT_NAME", "devops-agent-test")

app = Flask(__name__)
app.register_blueprint(_create_evidence_bp())

SCENARIOS_DIR = os.path.join(os.path.dirname(__file__), "dockercoins-scenarios")


# Server-side cache for message summaries (ts -> {summary_ko, approach})
_summary_cache = {}


# ── Scenario CRUD helpers ──────────────────────────────────────────

def _load_scenarios():
    """Load all scenario JSON files from disk, resolving ${PROJECT_NAME} placeholders."""
    _account_id = os.environ.get("AWS_ACCOUNT_ID", "111111111111")
    ecr_registry = os.environ.get("ECR_REGISTRY", f"{_account_id}.dkr.ecr.{_AWS_REGION}.amazonaws.com")
    from verifier import NAMESPACE as _ns
    subs = {
        "${PROJECT_NAME}": _PROJECT_NAME,
        "${AWS_REGION}": _AWS_REGION,
        "${ECR_REGISTRY}": ecr_registry,
        "${AWS_ACCOUNT_ID}": _account_id,
        "${NAMESPACE}": _ns,
    }
    scenarios = []
    for filepath in sorted(glob.glob(os.path.join(SCENARIOS_DIR, "*.json"))):
        try:
            with open(filepath) as f:
                raw = f.read()
            for placeholder, value in subs.items():
                raw = raw.replace(placeholder, value)
            s = json.loads(raw)
            s["_file"] = os.path.basename(filepath)
            scenarios.append(s)
        except (json.JSONDecodeError, IOError):
            continue
    return scenarios


def _load_scenario(scenario_id):
    """Load a single scenario by id."""
    for s in _load_scenarios():
        if s["id"] == scenario_id:
            return s
    return None


def _save_scenario(scenario):
    """Save scenario to JSON file."""
    os.makedirs(SCENARIOS_DIR, exist_ok=True)
    filename = f"{scenario['id']}.json"
    filepath = os.path.join(SCENARIOS_DIR, filename)
    # Remove internal fields before saving
    data = {k: v for k, v in scenario.items() if not k.startswith("_")}
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return filename


def _delete_scenario(scenario_id):
    """Delete scenario JSON file."""
    for filepath in glob.glob(os.path.join(SCENARIOS_DIR, "*.json")):
        try:
            with open(filepath) as f:
                s = json.load(f)
                if s.get("id") == scenario_id:
                    os.remove(filepath)
                    return True
        except (json.JSONDecodeError, IOError):
            continue
    return False


def _group_by_category(scenarios):
    """Group scenarios by category for UI display, ordered by layer (top=composite, bottom=aws)."""
    categories = {}
    cat_meta = {
        "multi-service": {"name": "Multiple Microservices", "icon": "🔗", "order": 0,
                      "color": "#7c3aed", "desc": "서비스 간 연쇄 장애"},
        "single-service": {"name": "Single Microservice", "icon": "🔥", "order": 1,
                        "color": "#dc2626", "desc": "단일 서비스 내부 문제"},
        "kubernetes": {"name": "Kubernetes Platform", "icon": "☸️", "order": 2,
                       "color": "#2563eb", "desc": "K8s 플랫폼 장애"},
        "aws": {"name": "AWS Infrastructure", "icon": "☁️", "order": 3,
                "color": "#d97706", "desc": "인프라 레벨 장애"},
        "cleanup": {"name": "Cleanup & Restore", "icon": "🧹", "order": 4,
                    "color": "#475569", "desc": "복원 유틸리티"},
    }
    for s in scenarios:
        cat = s.get("category", "other")
        if cat not in categories:
            meta = cat_meta.get(cat, {"name": cat.title(), "icon": "📦",
                                      "order": 99, "color": "#334155", "desc": ""})
            categories[cat] = {
                "name": meta["name"], "icon": meta["icon"],
                "order": meta["order"], "color": meta["color"],
                "desc": meta["desc"], "scenarios": []
            }
        categories[cat]["scenarios"].append(s)
    # Sort by layer order
    return dict(sorted(categories.items(), key=lambda x: x[1]["order"]))


# ── Routes ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    scenarios = _load_scenarios()
    categories = _group_by_category(scenarios)
    return render_template("index.html", categories=categories)


@app.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200


# ── Scenario CRUD API ──────────────────────────────────────────────

@app.route("/api/scenarios")
def api_list_scenarios():
    return jsonify(_load_scenarios())


@app.route("/api/scenarios/<scenario_id>")
def api_get_scenario(scenario_id):
    s = _load_scenario(scenario_id)
    if not s:
        return jsonify({"error": "Not found"}), 404
    return jsonify(s)


@app.route("/api/scenarios", methods=["POST"])
def api_create_scenario():
    data = request.get_json()
    if not data or "id" not in data:
        return jsonify({"error": "id is required"}), 400
    # Check duplicate
    if _load_scenario(data["id"]):
        return jsonify({"error": f"Scenario {data['id']} already exists"}), 409
    filename = _save_scenario(data)
    return jsonify({"success": True, "file": filename}), 201


@app.route("/api/scenarios/<scenario_id>", methods=["PUT"])
def api_update_scenario(scenario_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    existing = _load_scenario(scenario_id)
    if not existing:
        return jsonify({"error": "Not found"}), 404
    data["id"] = scenario_id
    _save_scenario(data)
    return jsonify({"success": True})


@app.route("/api/scenarios/<scenario_id>", methods=["DELETE"])
def api_delete_scenario(scenario_id):
    if _delete_scenario(scenario_id):
        return jsonify({"success": True})
    return jsonify({"error": "Not found"}), 404


# ── Run / Verify API ───────────────────────────────────────────────

@app.route("/api/run/<scenario_id>", methods=["POST"])
def api_run_scenario(scenario_id):
    """Start a scenario run with verification."""
    scenario = _load_scenario(scenario_id)
    if not scenario:
        return jsonify({"error": "Scenario not found"}), 404
    run = start_run(scenario)
    return jsonify({"run_id": run.run_id, "status": run.status})


@app.route("/api/run/<run_id>/status")
def api_run_status(run_id):
    """Poll current verification status of a run."""
    run = get_active_run(run_id)
    if not run:
        # Check history
        items, _ = get_history(limit=100)
        for h in items:
            if h.get("run_id") == run_id:
                return jsonify(h)
        return jsonify({"error": "Run not found"}), 404
    return jsonify(run.to_dict())


@app.route("/api/run/<run_id>/manual/<int:step_index>", methods=["POST"])
def api_manual_confirm(run_id, step_index):
    """Confirm or fail a manual verification step."""
    data = request.get_json() or {}
    passed = data.get("passed", True)
    if confirm_manual_step(run_id, step_index, passed):
        return jsonify({"success": True})
    return jsonify({"error": "Invalid run or step"}), 400


@app.route("/api/run/<run_id>/cancel", methods=["POST"])
def api_cancel_run(run_id):
    """Cancel an active run."""
    if cancel_run(run_id):
        return jsonify({"success": True})
    return jsonify({"error": "Run not found or already completed"}), 404


@app.route("/api/run/<run_id>/restore", methods=["POST"])
def api_restore(run_id):
    """Execute restore command for a run's scenario."""
    run = get_active_run(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    restore_cmd = run.scenario.get("restore", {}).get("command", "")
    if not restore_cmd:
        return jsonify({"success": True, "message": "복원 명령 없음"})
    restore_cmd = cluster_manager.inject_context(restore_cmd)
    import subprocess
    try:
        result = subprocess.run(
            ["bash", "-c", restore_cmd],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "AWS_PAGER": ""}
        )
        return jsonify({
            "success": result.returncode == 0,
            "output": result.stdout,
            "error": result.stderr,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ── Active Runs ────────────────────────────────────────────────────

@app.route("/api/runs")
def api_active_runs():
    return jsonify(list_active_runs())


# ── History API ────────────────────────────────────────────────────

@app.route("/api/history")
def api_history():
    limit = request.args.get("limit", 10, type=int)
    last_key_str = request.args.get("last_key")
    last_key = json.loads(last_key_str) if last_key_str else None
    space_id = request.args.get("space_id")
    items, next_key = get_history(limit, last_key, agent_space_id=space_id)
    return jsonify({"items": items, "next_key": json.dumps(next_key) if next_key else None})


@app.route("/api/run/<run_id>/save-summary", methods=["POST"])
def api_save_summary(run_id):
    """Save investigation summary to DynamoDB."""
    data = request.get_json() or {}
    summary = data.get("investigation_summary")
    if not summary:
        return jsonify({"error": "investigation_summary is required"}), 400
    # Save to active run
    run = get_active_run(run_id)
    if run:
        run.investigation_summary = summary
        run.save()
        return jsonify({"success": True})
    # Save to DynamoDB directly
    try:
        import boto3
        from decimal import Decimal
        table = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")).Table(_RUNS_TABLE)
        summary_item = json.loads(json.dumps(summary), parse_float=Decimal) if isinstance(summary, (dict, list)) else summary
        table.update_item(
            Key={"run_id": run_id, "record_type": "run"},
            UpdateExpression="SET investigation_summary = :s",
            ExpressionAttributeValues={":s": summary_item},
        )
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/history/<scenario_id>")
def api_history_by_scenario(scenario_id):
    """Get run history for a specific scenario from DynamoDB GSI."""
    limit = request.args.get("limit", 20, type=int)
    try:
        import boto3
        table = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")).Table(_RUNS_TABLE)
        resp = table.query(
            IndexName="scenario-id-index",
            KeyConditionExpression=boto3.dynamodb.conditions.Key("scenario_id").eq(scenario_id),
            ScanIndexForward=False,
            Limit=limit,
        )
        items = json.loads(json.dumps(resp.get("Items", []), default=str))
        return jsonify(items)
    except Exception as e:
        # Fallback to full history filter
        all_history, _ = get_history(limit=200)
        filtered = [h for h in all_history if h.get("scenario_id") == scenario_id]
        return jsonify(filtered[:limit])


# ── Environment Status API ─────────────────────────────────────────

@app.route("/api/environment")
def api_environment():
    return jsonify(get_environment_status())


@app.route("/api/auto-cleanup", methods=["POST"])
def api_auto_cleanup():
    """Auto-cleanup triggered by Lambda when investigation completes.
    Runs restore commands for active runs, or generic cleanup if none."""
    import subprocess
    results = []
    active = list_active_runs()
    for run_info in active:
        rid = run_info.get("run_id", "")
        run = get_active_run(rid)
        if run and run.scenario:
            restore_cmd = run.scenario.get("restore", {}).get("command", "")
            if restore_cmd:
                try:
                    result = subprocess.run(
                        ["bash", "-c", restore_cmd],
                        capture_output=True, text=True, timeout=60,
                        env={**os.environ, "AWS_PAGER": ""}
                    )
                    results.append({"run_id": rid, "success": result.returncode == 0,
                                    "output": result.stdout[:200]})
                except Exception as e:
                    results.append({"run_id": rid, "success": False, "error": str(e)})
    if not results:
        cleanup_scenario = _load_scenario("cleanup")
        if cleanup_scenario:
            cleanup_cmd = cleanup_scenario.get("restore", cleanup_scenario.get("trigger", {})).get("command", "")
            if cleanup_cmd:
                for sub in cleanup_cmd.split("&&"):
                    sub = sub.strip()
                    if not sub:
                        continue
                    try:
                        cmd = cluster_manager.inject_context(sub)
                        subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=30,
                                       env={**os.environ, "AWS_PAGER": ""})
                    except Exception:
                        pass
        results.append({"message": "Generic cleanup executed"})
    return jsonify({"ok": True, "results": results})


@app.route("/api/investigation-events")
def api_investigation_events():
    """Query investigation events from DynamoDB (populated by EventBridge)."""
    try:
        import boto3
        table_name = _EVENTS_TABLE
        region = _AWS_REGION
        table = boto3.resource("dynamodb", region_name=region).Table(table_name)

        task_id = request.args.get("task_id")
        if task_id:
            resp = table.query(KeyConditionExpression=boto3.dynamodb.conditions.Key("task_id").eq(task_id))
            return jsonify({"events": resp.get("Items", [])})

        # Scan recent events (last 50)
        resp = table.scan(Limit=50)
        items = sorted(resp.get("Items", []), key=lambda x: x.get("received_at", ""), reverse=True)
        return jsonify({"events": items})
    except Exception as e:
        return jsonify({"error": str(e), "events": []}), 500


AGENT_SPACE_ID = _AGENT_SPACE_ID


@app.route("/api/investigation-journal-raw")
def api_investigation_journal_raw():
    """Return raw journal records with full structured data preserved.
    No text extraction, no data loss. For external tools and evidence extraction."""
    task_id = request.args.get("task_id")
    if not task_id:
        return jsonify({"error": "task_id is required"}), 400
    try:
        import boto3
        region = os.environ.get("AWS_REGION", "us-east-1")
        client = boto3.client("devops-agent", region_name=region)

        # task_id → executions → journal records
        exec_resp = client.list_executions(
            agentSpaceId=AGENT_SPACE_ID, taskId=task_id, limit=10
        )
        executions = exec_resp.get("executions", [])
        if not executions:
            return jsonify({"ok": True, "task_id": task_id, "records": [], "message": "No executions"})

        all_records = []
        for exe in executions:
            exec_id = exe["executionId"]
            jr_resp = client.list_journal_records(
                agentSpaceId=AGENT_SPACE_ID, executionId=exec_id, limit=200, order="ASC"
            )
            for r in jr_resp.get("records", []):
                content = r.get("content", {})
                raw_text = content.get("text", "") if isinstance(content, dict) else str(content)
                record_type = r.get("recordType", "")
                created_at = str(r.get("createdAt", ""))

                # Parse structured JSON if possible, but keep raw too
                parsed = None
                try:
                    parsed = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
                except (json.JSONDecodeError, TypeError):
                    pass

                all_records.append({
                    "record_type": record_type,
                    "created_at": created_at[:19],
                    "execution_id": exec_id,
                    "raw_text": raw_text[:3000],
                    "parsed": parsed,
                    "content_keys": list(parsed.keys()) if isinstance(parsed, dict) else None,
                })

        return jsonify({
            "ok": True,
            "task_id": task_id,
            "execution_count": len(executions),
            "record_count": len(all_records),
            "records": all_records,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/investigation-journal")
def api_investigation_journal():
    """Fetch investigation journal records via DevOps Agent API, then structure into hypotheses using Bedrock."""
    task_id = request.args.get("task_id")
    analyze = request.args.get("analyze", "true").lower() == "true"
    scenario_id = request.args.get("scenario_id", "")
    if not task_id:
        return jsonify({"error": "task_id is required"}), 400

    try:
        import boto3
        region = os.environ.get("AWS_REGION", "us-east-1")
        client = boto3.client("devops-agent", region_name=region)

        # 1. task_id → executionId
        exec_resp = client.list_executions(
            agentSpaceId=AGENT_SPACE_ID, taskId=task_id, limit=10
        )
        executions = exec_resp.get("executions", [])
        if not executions:
            return jsonify({"ok": True, "task_id": task_id, "raw_messages": [],
                            "hypotheses": [], "message": "No executions found"})

        # 2. executionId → journal records (assistant + 구조화된 레코드)
        raw_messages = []
        linked_ids = []
        for exe in executions:
            exec_id = exe["executionId"]
            jr_resp = client.list_journal_records(
                agentSpaceId=AGENT_SPACE_ID, executionId=exec_id, limit=100, order="ASC"
            )
            for r in jr_resp.get("records", []):
                content = r.get("content", {})
                record_type = r.get("recordType", "")
                raw_text = content.get("text", "") if isinstance(content, dict) else str(content)
                created_at = str(r.get("createdAt", ""))

                # 구조화된 레코드 (observation, finding, investigation_summary 등)
                if record_type in ("observation", "finding", "symptom", "investigation_summary", "investigation_summary_md"):
                    try:
                        structured = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
                        if isinstance(structured, dict):
                            title = structured.get("title", "")
                            desc = structured.get("description", "")
                            text = f"[{record_type.upper()}] {title}\n{desc}".strip()
                            if record_type == "investigation_summary_md":
                                text = raw_text[:1500]  # markdown 전문
                            raw_messages.append({
                                "text": text[:1500],
                                "time": created_at[:19],
                                "record_type": record_type,
                            })
                        elif isinstance(structured, str):
                            raw_messages.append({"text": structured[:1500], "time": created_at[:19], "record_type": record_type})
                    except (json.JSONDecodeError, TypeError):
                        raw_messages.append({"text": raw_text[:1500], "time": created_at[:19], "record_type": record_type})
                    continue

                # assistant 메시지
                try:
                    msg = json.loads(raw_text)
                    role = msg.get("role", "")
                    parts = msg.get("content", [])
                    text = ""
                    for p in (parts if isinstance(parts, list) else []):
                        if isinstance(p, dict) and p.get("text"):
                            text += p["text"] + " "
                    text = text.strip()
                except (json.JSONDecodeError, TypeError):
                    role = ""
                    text = raw_text[:500]

                if role == "assistant" and text:
                    raw_messages.append({"text": text[:800], "time": created_at[:19], "record_type": "message"})
                # linked IDs
                linked_match = re.search(r"investigation.*?ID:\s*([a-f0-9-]+)", text or "")
                if linked_match:
                    linked_ids.append(linked_match.group(1))

        if not raw_messages:
            return jsonify({"ok": True, "task_id": task_id, "raw_messages": [],
                            "hypotheses": [], "message": "No messages found"})

        # 3. analyze=false면 분류 + 요약 반환 (Bedrock 기반)
        if not analyze:
            skip_classify = request.args.get("skip_classify", "false").lower() == "true"
            if skip_classify:
                return jsonify({"ok": True, "task_id": task_id,
                                "raw_messages": raw_messages, "classified": [],
                                "linked_investigation_ids": list(set(linked_ids))})
            model = request.args.get("model", AVAILABLE_MODELS["opus"])
            if model in AVAILABLE_MODELS:
                model = AVAILABLE_MODELS[model]
            classified = _classify_raw_messages(raw_messages, model_id=model)
            return jsonify({"ok": True, "task_id": task_id,
                            "raw_messages": raw_messages,
                            "classified": classified,
                            "hypotheses": [],
                            "linked_investigation_ids": list(set(linked_ids))})

        # 4. Bedrock으로 가설 구조화
        model = request.args.get("model", AVAILABLE_MODELS["opus"])
        if model in AVAILABLE_MODELS:
            model = AVAILABLE_MODELS[model]
        scenario = _load_scenario(scenario_id) if scenario_id else ""
        expected_rc = scenario.get("expected_root_cause", "") if scenario else ""
        flow = scenario.get("flow", []) if scenario else []

        msgs_text = "\n".join([f"[{m['time']}] {m['text']}" for m in raw_messages])
        flow_text = "\n".join([f"  {i+1}. {f}" for i, f in enumerate(flow)]) or "없음"

        prompt = f"""당신은 DevOps 조사 과정 분석 전문가입니다.
아래는 DevOps Agent가 장애 조사 중 생성한 메시지들입니다. 이 메시지들을 분석하여 조사 과정을 "가설 기반 구조"로 재구성하세요.

## 시나리오 컨텍스트
- 예상 근본 원인: {expected_rc}
- 장애 전파 흐름: {flow_text}

## 조사 메시지 ({len(raw_messages)}건)
{msgs_text}

## 요청: 다음 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만.

{{
  "alarm": "알람 요약 (한국어 1줄)",
  "hypotheses": [
    {{
      "id": 1,
      "title": "가설 제목 (한국어)",
      "category": "코드변경|서비스장애|인프라|데이터|설정 중 하나",
      "status": "rejected|partial|confirmed",
      "status_reason": "기각/부분확인/확인 이유 (한국어 1줄)",
      "leads_to": null 또는 다음 가설 id,
      "steps": [
        {{
          "action": "에이전트가 수행한 조사 행동 (한국어 1줄)",
          "data_source": "메트릭|로그|트레이스|K8s|코드|배포이력 중 하나",
          "insight": "발견한 인사이트 (한국어 1줄)",
          "is_key": true/false (핵심 발견 여부),
          "source_times": ["HH:MM"] (해당 원본 메시지의 시간, 여러 개 가능)
        }}
      ]
    }}
  ],
  "root_cause": {{
    "summary": "근본 원인 요약 (한국어 2-3줄)",
    "matched": true/false (예상 근본 원인과 일치 여부)
  }},
  "evaluation": {{
    "total_hypotheses": 가설 수,
    "rejected": 기각 수,
    "confirmed": 확인 수,
    "efficiency": "높음|보통|낮음 (잘못된 방향에 소비한 시간 기준)",
    "data_sources_used": ["사용한 데이터소스 목록"],
    "score": 1~10,
    "summary": "종합 평가 (한국어 2-3줄)"
  }}
}}

규칙:
- 가설은 에이전트가 실제로 탐색한 방향만 포함 (추측 금지)
- 각 가설의 steps는 시간순으로 정렬
- LINKED 반복 메시지("동일한 근본 원인", "새로운 조사 사항 없음")는 무시
- 핵심 발견(is_key=true)은 근본 원인 도달에 결정적인 인사이트만"""

        bedrock = boto3.client("bedrock-runtime", region_name=region)
        resp = bedrock.invoke_model(
            modelId=model,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4000,
                "messages": [{"role": "user", "content": prompt}],
            }),
            contentType="application/json",
            accept="application/json",
        )
        raw = json.loads(resp["body"].read())["content"][0]["text"].strip()
        if raw.startswith("```"):
            raw = re.sub(r'^```json?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
        result = json.loads(raw)

        return jsonify({
            "ok": True,
            "task_id": task_id,
            "raw_count": len(raw_messages),
            "raw_messages": raw_messages,
            "alarm": result.get("alarm", ""),
            "hypotheses": result.get("hypotheses", []),
            "root_cause": result.get("root_cause"),
            "evaluation": result.get("evaluation"),
            "linked_investigation_ids": list(set(linked_ids)),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "hypotheses": []}), 500


AVAILABLE_MODELS = {
    "haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "sonnet": "us.anthropic.claude-sonnet-4-6",
    "opus": "us.anthropic.claude-opus-4-6-v1",
}


def _classify_raw_messages(messages, model_id=None):
    """Classify and summarize journal messages using Bedrock."""
    if not messages:
        return []
    model = model_id or AVAILABLE_MODELS["opus"]
    region = os.environ.get("AWS_REGION", "us-east-1")

    msgs_text = "\n".join([f"[{m.get('time','')}] [{m.get('record_type','message')}] {m['text'][:600]}" for m in messages])

    prompt = f"""당신은 DevOps 조사 메시지 분석 전문가입니다.

아래 {len(messages)}건의 메시지를 **모두 빠짐없이** 분석하세요.

## 메시지
{msgs_text}

## 각 메시지에 대해 다음을 추출하세요:

1. **type**: Symptom | Observation | Finding | Conclusion | System
   - System: "시작하겠습니다", "기다리겠습니다", "완료되었습니다", "Investigation completed" 등 진행 상태만 있는 메시지
   - 실제 데이터/인사이트가 있는 메시지만 다른 타입으로 분류

2. **source**: 이 정보의 출처. 다음 중 하나:
   - "CloudWatch": 메트릭, 알람 상태
   - "K8s": Pod 상태, 재시작, OOMKilled, deployment
   - "Logs": 애플리케이션 로그, 에러 로그
   - "Traces": X-Ray 트레이스
   - "Code": 소스코드 분석, 파일/라인 참조
   - "CloudTrail": 인프라 변경, API 호출 이력
   - "Deploy": 배포 이력, 이미지 변경
   - "Agent": 에이전트 자체 판단/종합

3. **summary**: 한국어 1줄 요약. 구체적 수치, 파일명, 줄 번호 포함

4. **code_ref**: 코드 분석인 경우만. null이 아니면:
   {{"file": "services/dockercoins/hasher/hasher.py", "lines": "72-81", "symbol": "_result_buffer", "description": "버퍼 eviction 없이 무한 증가"}}

## 응답: JSON 배열만. {len(messages)}개 항목. 다른 텍스트 없이.
[
  {{"time": "원본시간", "type": "Finding", "source": "Code", "summary": "[Code] hasher.py:72-81 _result_buffer 무한 증가 발견", "code_ref": {{"file": "...", "lines": "72-81", "symbol": "...", "description": "..."}}, "original_index": 0}}
]"""

    try:
        import boto3
        client = boto3.client("bedrock-runtime", region_name=region)
        resp = client.invoke_model(
            modelId=model,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4000,
                "messages": [{"role": "user", "content": prompt}],
            }),
            contentType="application/json",
            accept="application/json",
        )
        raw = json.loads(resp["body"].read())["content"][0]["text"].strip()
        if raw.startswith("```"):
            raw = re.sub(r'^```json?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
        items = json.loads(raw)

        # Group by type
        TYPE_ORDER = ["Symptom", "Observation", "Finding", "Conclusion", "System"]
        TYPE_ICONS = {"Symptom": "🔴", "Observation": "🔍", "Finding": "💡", "Conclusion": "✅", "System": "⚙️"}
        SOURCE_ICONS = {"CloudWatch": "📊", "K8s": "☸️", "Logs": "📝", "Traces": "🔍", "Code": "💻", "CloudTrail": "🔐", "Deploy": "🚀", "Agent": "🤖"}
        grouped = {}
        for item in items:
            t = item.get("type", "System")
            idx = item.get("original_index", 0)
            original = messages[idx]["text"] if idx < len(messages) else ""
            source = item.get("source", "Agent")
            source_icon = SOURCE_ICONS.get(source, "📋")
            code_ref = item.get("code_ref")
            grouped.setdefault(t, []).append({
                "summary": item.get("summary", ""),
                "original": original,
                "time": item.get("time", ""),
                "type": t,
                "source": source,
                "source_icon": source_icon,
                "code_ref": code_ref,
                "record_type": messages[idx].get("record_type", "message") if idx < len(messages) else "message",
            })
        result = []
        for t in TYPE_ORDER:
            msgs = grouped.get(t, [])
            if msgs:
                result.append({"type": t, "icon": TYPE_ICONS.get(t, ""), "messages": msgs, "count": len(msgs)})
        return result
    except Exception as e:
        # Fallback to simple classification
        return [{"type": "Error", "icon": "❌", "messages": [{"summary": f"분류 실패: {str(e)[:200]}", "original": "", "time": "", "data_sources": ""}], "count": 1}]


@app.route("/api/code/<path:filepath>")
def api_code(filepath):
    """Fetch code from GitHub repository."""
    line_start = request.args.get("start", type=int)
    line_end = request.args.get("end", type=int)
    repo = "sorididim11/frontier-devops-agent-test-app"
    try:
        import urllib.request
        url = f"https://raw.githubusercontent.com/{repo}/main/{filepath}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode()
        lines = content.split("\n")
        if line_start and line_end:
            snippet = "\n".join(lines[max(0, line_start-1):line_end])
            return jsonify({"ok": True, "file": filepath, "lines": f"{line_start}-{line_end}",
                            "snippet": snippet, "total_lines": len(lines)})
        elif line_start:
            snippet = "\n".join(lines[max(0, line_start-3):line_start+7])
            return jsonify({"ok": True, "file": filepath, "lines": f"{line_start}",
                            "snippet": snippet, "total_lines": len(lines)})
        return jsonify({"ok": True, "file": filepath, "content": content[:5000], "total_lines": len(lines)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 404


@app.route("/api/models")
def api_models():
    """List available Bedrock models for analysis."""
    return jsonify({
        "models": [
            {"id": "haiku", "name": "Claude 3.5 Haiku", "model_id": AVAILABLE_MODELS["haiku"], "desc": "빠르고 저렴"},
            {"id": "sonnet", "name": "Claude Sonnet 4", "model_id": AVAILABLE_MODELS["sonnet"], "desc": "균형"},
            {"id": "opus", "name": "Claude Opus 4.6", "model_id": AVAILABLE_MODELS["opus"], "desc": "최고 품질"},
        ],
        "default": "opus"
    })


@app.route("/api/evaluate/<run_id>", methods=["POST"])
def api_evaluate(run_id):
    """Rubric-based evaluation of an investigation run."""
    from evaluator import evaluate_investigation, compare_with_history
    data = request.get_json() or {}
    model = data.get("model", AVAILABLE_MODELS.get("opus"))
    task_id = data.get("task_id")
    scenario_id = data.get("scenario_id")

    # Get task_id from run if not provided
    if not task_id:
        run = get_active_run(run_id)
        if run:
            task_id = run._investigation_task_id
            scenario_id = scenario_id or run.scenario_id
        else:
            # Check DynamoDB history
            try:
                import boto3
                tbl = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")).Table(_RUNS_TABLE)
                resp = tbl.get_item(Key={"run_id": run_id, "record_type": "run"})
                item = resp.get("Item", {})
                task_id = item.get("investigation_task_id")
                scenario_id = scenario_id or item.get("scenario_id")
            except Exception:
                pass

    if not task_id:
        return jsonify({"error": "task_id not found for this run"}), 400

    # Get scenario
    scenario = _load_scenario(scenario_id) if scenario_id else None
    if not scenario or not scenario.get("evaluation_rubric"):
        return jsonify({"error": f"시나리오 {scenario_id}에 evaluation_rubric 없음"}), 400

    # Get journal messages
    try:
        import boto3
        client = boto3.client("devops-agent", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        exec_resp = client.list_executions(agentSpaceId=AGENT_SPACE_ID, taskId=task_id, limit=10)
        messages = []
        for exe in exec_resp.get("executions", []):
            jr = client.list_journal_records(agentSpaceId=AGENT_SPACE_ID, executionId=exe["executionId"], limit=100, order="ASC")
            for r in jr.get("records", []):
                content = r.get("content", {})
                raw_text = content.get("text", "") if isinstance(content, dict) else str(content)
                messages.append({"text": raw_text, "time": str(r.get("createdAt", ""))[:19], "record_type": r.get("recordType", "")})
    except Exception as e:
        return jsonify({"error": f"journal 조회 실패: {e}"}), 500

    if not messages:
        return jsonify({"error": "조사 메시지 없음"}), 400

    # Evaluate
    result = evaluate_investigation(messages, scenario, model_id=model)

    # Save to DynamoDB
    try:
        import boto3
        from decimal import Decimal
        tbl = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")).Table(_RUNS_TABLE)
        eval_item = json.loads(json.dumps(result), parse_float=Decimal)
        tbl.put_item(Item={
            "run_id": run_id,
            "record_type": "evaluation",
            "scenario_id": scenario_id,
            "task_id": task_id,
            **eval_item,
        })
    except Exception as e:
        result["save_error"] = str(e)

    return jsonify(result)


@app.route("/api/evaluate/<run_id>")
def api_get_evaluation(run_id):
    """Load saved rubric evaluation from DynamoDB."""
    try:
        import boto3
        tbl = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")).Table(_RUNS_TABLE)
        resp = tbl.get_item(Key={"run_id": run_id, "record_type": "evaluation"})
        item = resp.get("Item")
        if not item:
            return jsonify({"found": False}), 404
        return jsonify(json.loads(json.dumps(item, default=str)))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/slack/messages")
def api_slack_messages():
    """Fetch Slack messages, optionally filtered by alarm_name or timestamp."""
    since = request.args.get("since", None) or request.args.get("since_ts", None)
    limit = request.args.get("limit", 20, type=int)
    alarm_name = request.args.get("alarm_name", None)
    thread_ts = request.args.get("thread_ts", None)
    return jsonify(get_slack_messages(since_ts=since, limit=limit, alarm_name=alarm_name, thread_ts=thread_ts))


@app.route("/api/translate", methods=["POST"])
def api_translate():
    """Translate English text to Korean using Bedrock Claude."""
    data = request.get_json()
    if not data or not data.get("text"):
        return jsonify({"translated": None, "error": "text is required"}), 400
    text = data["text"]
    try:
        import boto3
        client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": [{
                "role": "user",
                "content": f"다음 영어 텍스트를 한국어로 번역해주세요. 번역문만 출력하고 다른 설명은 하지 마세요.\n\n{text}"
            }]
        })
        resp = client.invoke_model(
            modelId=AVAILABLE_MODELS["opus"],
            body=body,
            contentType="application/json",
            accept="application/json"
        )
        result = json.loads(resp["body"].read())
        translated = result["content"][0]["text"].strip()
        return jsonify({"translated": translated})
    except Exception as e:
        return jsonify({"translated": None, "error": str(e)}), 500


@app.route("/api/investigation-summary", methods=["POST"])
def api_investigation_summary():
    """Fetch Slack investigation thread, classify by phase, summarize with cache & evaluate."""
    import boto3

    MODEL_ID = AVAILABLE_MODELS["opus"]
    REGION = os.environ.get("AWS_REGION", "us-east-1")

    data = request.get_json() or {}
    alarm_name = data.get("alarm_name")
    task_id = data.get("task_id")  # EventBridge task_id로 직접 조회
    since_ts = data.get("since_ts")
    expected_root_cause = data.get("expected_root_cause", "")
    flow = data.get("flow", [])

    if not alarm_name and not task_id:
        # alarm_name 없으면 since_ts 기반 시간 매칭 사용
        if since_ts:
            alarm_name = "__time_based__"  # sentinel: 시간 기반 fallback
        else:
            return jsonify({"error": "alarm_name, task_id, or since_ts is required"}), 400

    # task_id가 있으면 DynamoDB에서 agent_space_id 조회 후 Slack 검색
    if task_id and not alarm_name:
        try:
            import boto3 as _boto3
            table = _boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")).Table(_EVENTS_TABLE)
            resp = table.query(KeyConditionExpression=_boto3.dynamodb.conditions.Key("task_id").eq(task_id))
            items = resp.get("Items", [])
            if items:
                raw = json.loads(items[0].get("raw_event", "{}"))
                # raw_event에서 alarm_name 추출 시도
                desc = raw.get("detail", {}).get("data", {}).get("description", "")
                import re as _re
                m = _re.search(r"CloudWatch Alarm '([^']+)'", desc)
                if m:
                    alarm_name = m.group(1)
        except Exception:
            pass

    # ── 1. Slack 메시지 가져오기 ──
    slack_result = get_slack_messages(alarm_name=alarm_name, since_ts=since_ts)
    if not slack_result.get("ok"):
        return jsonify({"error": slack_result.get("error", "Slack 조회 실패"), "phases": []})

    messages = slack_result.get("messages", [])
    parent_ts = slack_result.get("parent_ts")
    if not messages:
        return jsonify({"ok": True, "parent_ts": parent_ts, "alarm_name": alarm_name,
                        "raw_count": 0, "phases": [], "overall": None})

    # ── 2. 메시지 분류 (Symptom / Observation / Finding / Conclusion / Other) ──
    PHASE_ICONS = {
        "Symptom": "🔴", "Observation": "🔍", "Finding": "💡",
        "Conclusion": "✅", "Other": "ℹ️",
    }
    PHASE_ORDER = ["Symptom", "Observation", "Finding", "Conclusion", "Other"]

    def classify(text):
        if re.search(r'\[Symptom\]', text):
            return "Symptom"
        if re.search(r'\[Observation\]', text):
            return "Observation"
        if re.search(r'\[Finding\]', text):
            return "Finding"
        if re.search(r'Investigation complete', text):
            return "Conclusion"
        return "Other"

    classified = {}
    for m in messages:
        phase = classify(m["text"])
        classified.setdefault(phase, []).append({
            "text": m["text"][:500],
            "ts": m["ts"],
            "phase": phase,
        })

    # ── 3. 캐시에 없는 새 메시지만 모아서 배치 요약 요청 ──
    new_msgs = []
    for phase in PHASE_ORDER:
        for m in classified.get(phase, []):
            if m["ts"] not in _summary_cache:
                new_msgs.append(m)

    if new_msgs:
        lines = []
        for m in new_msgs:
            clean = re.sub(r'<[^>]+>', '', m["text"])
            for emoji in (":rotating_light:", ":mag:", ":bar_chart:", ":white_check_mark:"):
                clean = clean.replace(emoji, "")
            lines.append(f'[{m["phase"]}] ts={m["ts"]} | {clean.strip()[:300]}')

        batch_prompt = f"""당신은 DevOps 조사 메시지 요약 전문가입니다.
아래 메시지 각각에 대해 JSON 배열로 응답하세요. 다른 텍스트 없이 JSON만 출력하세요.

메시지 목록 ({len(lines)}건):
{chr(10).join(lines)}

응답 형식 (JSON 배열만):
[
  {{"ts": "원본ts", "summary_ko": "한국어 1줄 요약", "approach": "조사 관점 한국어 1줄"}}
]

규칙:
- summary_ko: 원본 메시지 핵심을 한국어 1줄로 요약
- approach: 이 메시지가 조사에서 어떤 관점/접근인지 한국어 1줄"""

        try:
            client = boto3.client("bedrock-runtime", region_name=REGION)
            resp = client.invoke_model(
                modelId=MODEL_ID,
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 4000,
                    "messages": [{"role": "user", "content": batch_prompt}],
                }),
                contentType="application/json",
                accept="application/json",
            )
            raw = json.loads(resp["body"].read())["content"][0]["text"].strip()
            if raw.startswith("```"):
                raw = re.sub(r'^```json?\s*', '', raw)
                raw = re.sub(r'\s*```$', '', raw)
            summaries = json.loads(raw)

            # ── 4. 캐시에 저장 ──
            for s in summaries:
                ts = s.get("ts", "")
                if ts:
                    _summary_cache[ts] = {
                        "summary_ko": s.get("summary_ko", ""),
                        "approach": s.get("approach", ""),
                    }
        except Exception:
            # 요약 실패 시 원본 텍스트로 폴백
            for m in new_msgs:
                clean = re.sub(r'<[^>]+>', '', m["text"]).strip()[:100]
                _summary_cache[m["ts"]] = {
                    "summary_ko": clean,
                    "approach": "요약 실패 – 원본 참조",
                }

    # ── 5. 단계별 그룹핑 ──
    phases_result = []
    for phase in PHASE_ORDER:
        phase_msgs = classified.get(phase, [])
        if not phase_msgs:
            continue
        enriched = []
        for m in phase_msgs:
            cached = _summary_cache.get(m["ts"], {})
            enriched.append({
                "summary_ko": cached.get("summary_ko", m["text"][:100]),
                "approach": cached.get("approach", ""),
                "original": m["text"][:200],
                "ts": m["ts"],
            })
        phases_result.append({
            "phase": phase,
            "icon": PHASE_ICONS.get(phase, ""),
            "messages": enriched,
            "phase_assessment": "",
        })

    # ── 6. 메시지 3건 이상이면 전체 평가 수행 ──
    overall = None
    if len(messages) >= 3:
        flow_str = "\n".join([f"  {i+1}. {f}" for i, f in enumerate(flow)]) or "없음"
        # 시나리오 아키텍처 컴포넌트
        scenario_data = None
        try:
            scenario_data = _load_scenario(alarm_name.replace('devops-agent-test-', '').split('-')[0] if alarm_name else '')
        except Exception:
            pass

        phase_summary_lines = []
        for pr in phases_result:
            for em in pr["messages"]:
                phase_summary_lines.append(f'[{pr["phase"]}] {em["summary_ko"]}')

        eval_prompt = f"""당신은 DevOps AI 에이전트 조사 품질 평가 전문가입니다.

## 평가 대상
DevOps Agent가 CloudWatch 알람을 받고 자동으로 수행한 장애 조사입니다.

## 시나리오 컨텍스트
- 예상 근본 원인: {expected_root_cause}
- 장애 전파 흐름:
{flow_str}

## 에이전트 조사 내용 ({len(phase_summary_lines)}건)
{chr(10).join(phase_summary_lines)}

## 평가 기준 (5개 축)
1. 근본 원인 식별 정확도: 예상 근본 원인과 에이전트 결론 일치 여부, 표면 증상이 아닌 진짜 원인을 찾았는가
2. 장애 전파 추적: 장애 전파 경로를 추적했는가, 영향받은 컴포넌트를 식별했는가
3. 조사 체계성: Symptom→Observation→Finding→Conclusion 순서가 논리적인가, 가설 검증 과정이 있는가
4. 데이터소스 활용도: App Topology(서비스 간 의존성)를 이해하고 관련 서비스를 추적했는가, CloudWatch 메트릭/X-Ray 트레이스/Pod 로그/배포 이력/Runbook 등 사용 가능한 데이터소스를 적절히 활용했는가, 불필요한 데이터에 시간을 낭비하지 않았는가
5. 조사 완결성: 조사가 완결됐는가(시작→결론), 미해결 질문이 없는가, 재발 방지 권고가 있는가

## 요청사항
다음 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만 출력하세요.

{{
  "phase_assessments": {{
    "Symptom": "이 단계 평가 한국어 1줄",
    "Observation": "평가",
    "Finding": "평가",
    "Conclusion": "평가"
  }},
  "overall": {{
    "root_cause_match": "정확/부분적/오진 중 하나",
    "scores": {{
      "root_cause_accuracy": 1~10,
      "fault_propagation": 1~10,
      "methodology": 1~10,
      "data_utilization": 1~10,
      "completeness": 1~10
    }},
    "score": 1~10,
    "summary_ko": "종합 평가 한국어 3-4줄. 잘한 점과 개선점을 구체적으로."
  }}
}}"""

        try:
            client = boto3.client("bedrock-runtime", region_name=REGION)
            resp = client.invoke_model(
                modelId=MODEL_ID,
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 2000,
                    "messages": [{"role": "user", "content": eval_prompt}],
                }),
                contentType="application/json",
                accept="application/json",
            )
            raw = json.loads(resp["body"].read())["content"][0]["text"].strip()
            if raw.startswith("```"):
                raw = re.sub(r'^```json?\s*', '', raw)
                raw = re.sub(r'\s*```$', '', raw)
            eval_result = json.loads(raw)

            # phase_assessment 채우기
            pa = eval_result.get("phase_assessments", {})
            for pr in phases_result:
                if pr["phase"] in pa:
                    pr["phase_assessment"] = pa[pr["phase"]]

            overall = eval_result.get("overall")
        except Exception as e:
            overall = {"root_cause_match": "평가실패", "score": 0,
                       "summary_ko": f"평가 중 오류: {str(e)[:150]}"}

    return jsonify({
        "ok": True,
        "parent_ts": parent_ts,
        "alarm_name": alarm_name,
        "raw_count": len(messages),
        "phases": phases_result,
        "overall": overall,
    })


@app.route("/api/analyze/<run_id>", methods=["POST"])
def api_analyze(run_id):
    """AI analysis of investigation results using Bedrock Claude."""
    run = get_active_run(run_id)
    run_data = None
    if run:
        run_data = run.to_dict()
    else:
        items, _ = get_history(limit=100)
        for h in items:
            if h.get("run_id") == run_id:
                run_data = h
                break
    if not run_data:
        return jsonify({"error": "Run not found"}), 404

    try:
        import boto3
        scenario_id = run_data.get("scenario_id", "")
        run_steps = run_data.get("steps", [])

        # Load full scenario JSON for rich context
        scenario_data = _load_scenario(scenario_id) or {}
        scenario_name = run_data.get("scenario_name", scenario_id)
        expected_root_cause = scenario_data.get("expected_root_cause", "")
        flow = scenario_data.get("flow", [])
        arch = scenario_data.get("architecture", {})
        components_desc = "\n".join([
            f"  - {c['label']} ({c['type']}): {c.get('desc', '')}"
            for c in arch.get("components", [])
        ]) or "  (아키텍처 정보 없음)"

        # Extract alarm_name
        alarm_name = None
        for step in run_steps:
            if step.get("type") == "cw_alarm":
                alarm_name = step.get("config", {}).get("alarm") or step.get("alarm")
                break

        # Get Slack investigation messages
        slack_messages = []
        if alarm_name:
            slack_result = get_slack_messages(alarm_name=alarm_name)
            slack_messages = slack_result.get("messages", [])

        # Classify messages by investigation phase
        def classify_msg(text):
            if re.search(r'\[Symptom\]', text): return 'Symptom'
            if re.search(r'\[Finding\]', text): return 'Finding'
            if re.search(r'\[Observation\]', text): return 'Observation'
            if re.search(r'Investigation started', text): return 'Start'
            if re.search(r'Investigation complete', text): return 'Conclusion'
            if re.search(r'Root Cause|root cause', text): return 'RootCause'
            return 'Other'

        classified = {}
        for m in slack_messages:
            t = classify_msg(m['text'])
            classified.setdefault(t, []).append(m['text'][:400])

        def fmt_classified():
            out = []
            order = ['Start', 'Symptom', 'Observation', 'Finding', 'RootCause', 'Conclusion', 'Other']
            for t in order:
                msgs = classified.get(t, [])
                if not msgs:
                    continue
                out.append(f"\n**[{t}] ({len(msgs)}건)**")
                for msg in msgs[:4]:
                    out.append(f"  - {msg}")
            return "\n".join(out) or "조사 메시지 없음"

        steps_summary = "\n".join([
            f"  [{s['status'].upper():8}] {s['name']}: {s.get('detail', '')}"
            for s in run_steps
        ])
        flow_summary = "\n".join([f"  {i+1}. {f}" for i, f in enumerate(flow)]) or "  없음"

        prompt = f"""당신은 DevOps 에이전트 조사 품질 평가 전문가입니다.
아래 시나리오 정보와 AI 에이전트의 실제 조사 결과를 분석하여 **한국어**로 단계적 평가를 제공하세요.

---
## 시나리오 컨텍스트
- **이름**: {scenario_name}
- **예상 근본 원인**: {expected_root_cause}

### 장애 전파 흐름 (시나리오 설계)
{flow_summary}

### 관련 컴포넌트
{components_desc}

---
## 시뮬레이션 검증 결과 (자동화 검증)
{steps_summary}
- **최종 결과**: {run_data.get('result', '').upper()}

---
## AI 에이전트 조사 메시지 (단계별 분류)
{fmt_classified()}

---
## 평가 요청

다음 구조로 **한국어**로 평가해주세요:

### 1. 조사 단계별 요약 및 평가
각 단계(Start → Symptom → Observation → Finding → Conclusion)에서 에이전트가 발견한 내용을 1-2줄로 요약하고, 해당 단계의 품질을 평가하세요.

### 2. 근본 원인 식별 정확도
- 예상 근본 원인: {expected_root_cause}
- 에이전트가 실제로 식별한 근본 원인
- 정확도 평가: 정확 / 부분적 / 오진

### 3. 장애 전파 추적 평가
장애 전파 흐름({' → '.join([f.split(' ')[0] for f in flow[:5]])})을 에이전트가 올바르게 추적했는지 평가하세요.

### 4. 종합 점수
| 항목 | 점수 | 근거 |
|------|------|------|
| 근본 원인 식별 | X/10 | |
| 조사 체계성 | X/10 | |
| 장애 전파 추적 | X/10 | |
| **종합** | **X/10** | |

### 5. 개선 제안
에이전트가 놓친 부분이나 더 잘할 수 있었던 점을 구체적으로 제안하세요."""

        client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 3000,
            "messages": [{"role": "user", "content": prompt}]
        })
        resp = client.invoke_model(
            modelId=AVAILABLE_MODELS["opus"],
            body=body,
            contentType="application/json",
            accept="application/json"
        )
        result = json.loads(resp["body"].read())
        summary = result["content"][0]["text"].strip()
        return jsonify({
            "run_id": run_id,
            "summary": summary,
            "status": "completed",
            "message_stats": {k: len(v) for k, v in classified.items()}
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── DAG Save/Load API ──────────────────────────────────────────────

@app.route("/api/run/<run_id>/dag")
def api_get_dag(run_id):
    """Load saved investigation DAG from DynamoDB."""
    try:
        import boto3
        tbl = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")).Table(_RUNS_TABLE)
        resp = tbl.get_item(Key={"run_id": run_id, "record_type": "investigation_dag"})
        item = resp.get("Item")
        if not item:
            return jsonify({"error": "DAG not found"}), 404
        return jsonify(json.loads(json.dumps(item, default=str)))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/run/<run_id>/dag", methods=["POST"])
def api_save_dag(run_id):
    """Save investigation DAG to DynamoDB."""
    data = request.get_json() or {}
    hypotheses = data.get("hypotheses", [])
    alarm = data.get("alarm", "")
    root_cause = data.get("root_cause")
    raw_count = data.get("raw_count", 0)
    scenario_id = data.get("scenario_id", "")
    try:
        import boto3
        from decimal import Decimal
        tbl = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")).Table(_RUNS_TABLE)
        item = {
            "run_id": run_id,
            "record_type": "investigation_dag",
            "scenario_id": scenario_id,
            "hypotheses": json.loads(json.dumps(hypotheses), parse_float=Decimal),
            "alarm": alarm,
            "raw_count": raw_count,
        }
        if root_cause:
            item["root_cause"] = json.loads(json.dumps(root_cause), parse_float=Decimal)
        tbl.put_item(Item=item)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Multi-Cluster API ─────────────────────────────────────────────

@app.route("/api/cluster-info")
def api_cluster_info():
    """Return multi-cluster configuration and current service placement."""
    return jsonify({
        "multi_cluster": cluster_manager.is_multi_cluster(),
        "clusters": cluster_manager.get_clusters(),
        "service_map": cluster_manager.get_service_map(),
    })


@app.route("/api/discover-services", methods=["POST"])
def api_discover_services():
    """Trigger re-discovery of services across clusters."""
    service_map = cluster_manager.discover_services()
    return jsonify({"ok": True, "service_map": service_map})


# ── Main ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    cluster_manager.init()
    init_slack_config()
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
else:
    # gunicorn: init on import
    cluster_manager.init()
    init_slack_config()
