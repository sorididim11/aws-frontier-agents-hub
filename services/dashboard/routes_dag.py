"""
DAG routes — Flask Blueprint extracted from overview_app.py.
Investigation journal analysis and DAG verification endpoints.
"""
import json
import re
import traceback

from flask import Blueprint, Response, render_template, jsonify, request

from app_config import (
    _CFG, _cfg_get, AWS_REGION, AGENT_SPACE_ID, RUNS_TABLE,
    _req_space_id, _agent_space_id, _boto_session, _space_session, _tag_key_for_space,
    _get_aws_associations, _session_for_association,
    AVAILABLE_MODELS,
)

dag_bp = Blueprint("dag", __name__)

# Bedrock 분석 결과 캐시 (task_id → response dict, 완료된 조사는 불변)
_analysis_cache = {}


@dag_bp.route("/dag")
def dag_index():
    return render_template("dag.html")


@dag_bp.route("/api/render-report", methods=["POST"])
def render_report():
    data = request.get_json(silent=True) or {}
    phase = data.get("phase", "?")
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    positions = data.get("positions", {})
    layout = data.get("layout", "?")

    lines = [f"\n{'='*60}", f"RENDER REPORT — Phase {phase} ({layout})", f"{'='*60}"]
    type_counts = {}
    for n in nodes:
        t = n.get("type", "?")
        type_counts[t] = type_counts.get(t, 0) + 1
    lines.append(f"Nodes: {len(nodes)}  {type_counts}")
    lines.append(f"Edges: {len(edges)}")

    missing_pos = [n["id"] for n in nodes if n["id"] not in positions]
    if missing_pos:
        lines.append(f"  !! MISSING POSITIONS: {missing_pos}")

    node_ids = {n["id"] for n in nodes}
    bad_edges = []
    for e in edges:
        if e.get("from") not in node_ids:
            bad_edges.append(f"  edge from={e['from']} NOT IN nodes")
        if e.get("to") not in node_ids:
            bad_edges.append(f"  edge to={e['to']} NOT IN nodes")
    if bad_edges:
        lines.append("  !! BAD EDGES:")
        lines.extend(bad_edges)

    empty_obs = []
    for n in nodes:
        if n.get("type") == "observation":
            if not n.get("target") and not n.get("evidence"):
                empty_obs.append(n["id"])
    if empty_obs:
        lines.append(f"  !! EMPTY OBS (no target/evidence): {empty_obs}")

    lines.append("Positions:")
    for n in nodes:
        p = positions.get(n["id"], {})
        extra = ""
        if n.get("type") == "observation":
            extra = f'  [{n.get("target","")}/{n.get("resource","")}/{n.get("evidence","")[:30]}]'
        elif n.get("type") == "terminated":
            extra = f'  [{n.get("label","")}]'
        lines.append(f'  {n["id"]:40s} ({n.get("type","?"):12s}) x={p.get("x","?"):>5}  y={p.get("y","?"):>5}{extra}')

    et_counts = {}
    for e in edges:
        t = e.get("edgeType", "?")
        et_counts[t] = et_counts.get(t, 0) + 1
    lines.append(f"Edge types: {et_counts}")

    report = "\n".join(lines)
    print(report, flush=True)
    return jsonify({"ok": True, "report": report})


@dag_bp.route("/api/agent-space-info")
def api_agent_space_info():
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    try:
        session = _space_session(space_id)
        client = session.client("devops-agent")
        resp = client.list_executions(agentSpaceId=space_id, taskId="00000000-0000-0000-0000-000000000000", limit=1)
        return jsonify({"ok": True, "space_id": space_id, "region": AWS_REGION, "message": "연결 성공"})
    except Exception as e:
        err_msg = str(e)
        if "ResourceNotFoundException" in err_msg:
            return jsonify({"ok": True, "space_id": space_id, "region": AWS_REGION, "message": "Space 연결 확인 (task 없음)"})
        return jsonify({"ok": False, "space_id": space_id, "region": AWS_REGION, "error": err_msg})


@dag_bp.route("/api/investigation-journal-raw")
def api_journal_raw():
    task_id = request.args.get("task_id")
    space_id = _agent_space_id()
    if not task_id:
        return jsonify({"error": "task_id is required"}), 400
    try:
        session = _space_session(space_id)
        client = session.client("devops-agent")
        exec_resp = client.list_executions(agentSpaceId=space_id, taskId=task_id, limit=10)
        executions = exec_resp.get("executions", [])
        if not executions:
            return jsonify({"ok": True, "task_id": task_id, "records": []})

        all_records = []
        for exe in executions:
            exec_id = exe["executionId"]
            next_token = None
            while True:
                kwargs = {"agentSpaceId": space_id, "executionId": exec_id, "order": "ASC"}
                if next_token:
                    kwargs["nextToken"] = next_token
                jr_resp = client.list_journal_records(**kwargs)
                for r in jr_resp.get("records", []):
                    content = r.get("content", {})
                    raw_text = content.get("text", "") if isinstance(content, dict) else str(content)
                    parsed = None
                    try:
                        parsed = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
                    except (json.JSONDecodeError, TypeError):
                        pass
                    created_at = r.get("createdAt", "")
                    if hasattr(created_at, "isoformat"):
                        created_at = created_at.isoformat()
                    all_records.append({
                        "record_type": r.get("recordType", ""),
                        "created_at": str(created_at)[:19],
                        "parsed": parsed,
                        "raw_text": raw_text[:3000],
                    })
                next_token = jr_resp.get("nextToken")
                if not next_token:
                    break
        return jsonify({"ok": True, "task_id": task_id, "records": all_records})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@dag_bp.route("/api/investigation-journal")
def api_journal_analyze():
    task_id = request.args.get("task_id")
    space_id = _agent_space_id()
    if not task_id:
        return jsonify({"ok": False, "error": "task_id is required"}), 400

    try:
        session = _space_session(space_id)
        client = session.client("devops-agent")
        exec_resp = client.list_executions(agentSpaceId=space_id, taskId=task_id, limit=10)
        executions = exec_resp.get("executions", [])
        if not executions:
            return jsonify({"ok": False, "error": "no executions found"})

        all_records = []
        for exe in executions:
            exec_id = exe["executionId"]
            next_token = None
            while True:
                kwargs = {"agentSpaceId": space_id, "executionId": exec_id, "order": "ASC"}
                if next_token:
                    kwargs["nextToken"] = next_token
                jr_resp = client.list_journal_records(**kwargs)
                for r in jr_resp.get("records", []):
                    content = r.get("content", {})
                    raw_text = content.get("text", "") if isinstance(content, dict) else str(content)
                    created_at = r.get("createdAt", "")
                    if hasattr(created_at, "isoformat"):
                        created_at = created_at.isoformat()
                    all_records.append({
                        "record_type": r.get("recordType", ""),
                        "created_at": str(created_at)[:19],
                        "raw_text": raw_text[:3000],
                    })
                next_token = jr_resp.get("nextToken")
                if not next_token:
                    break
    except Exception as e:
        return jsonify({"ok": False, "error": f"journal fetch failed: {e}"}), 500

    if not all_records:
        return jsonify({"ok": False, "error": "no journal records"})

    journal_text = "\n\n".join([
        f"[{r['created_at']}] [{r['record_type']}]\n{r['raw_text'][:2000]}"
        for r in all_records
        if r["record_type"] in ("symptom", "observation", "finding", "investigation_summary")
    ])

    prompt = f"""You are a DevOps investigation analyst. Below are journal records from an automated DevOps Agent investigation.

Reconstruct the Agent's investigation as a hypothesis-driven DAG.

## Journal Records ({len(all_records)} total)
{journal_text}

## Key concept: What is a "hypothesis"?
A hypothesis is a COMPETING THEORY about the root cause — NOT a step in the causal chain.

Examples:
- CORRECT hypotheses: "배포 변경이 원인", "RNG 데이터 오염이 원인", "OOM Kill이 원인" — these are 3 different theories
- WRONG: splitting one confirmed theory into substeps like "RNG 주입", "RNG 빈 응답", "Worker 무검증" — these are ONE hypothesis with multiple observations

The investigation should show: Agent explored N competing theories → rejected some → confirmed one.

## Instructions
1. Group observations into hypotheses (competing root cause theories)
2. Each hypothesis = a different candidate root cause the Agent investigated
3. A confirmed hypothesis may have multiple observations showing different aspects of the SAME root cause
4. Rejected hypotheses: Agent investigated but ruled out (data contradicted the theory)
5. There should typically be 2-5 hypotheses (not 1 per observation)

Respond with ONLY this JSON:

{{
  "hypotheses": [
    {{
      "id": "short-kebab-id",
      "label": "가설 이름 (Korean, root cause candidate)",
      "status": "confirmed|rejected|partial",
      "reason": "기각/확인 사유 (Korean, 1 line)",
      "steps": [
        {{
          "signal_type": "metric|trace|log|code_snippet|change_event",
          "obs_id": "original observation id from journal",
          "insight": "이 신호가 보여주는 것 (Korean, 1-2 lines)",
          "is_key": true/false
        }}
      ],
      "findings": ["finding_id_1"]
    }}
  ],
  "causal_chain": [
    {{"step": 1, "service": "ServiceName", "event": "Root Cause 행동 (Korean)", "why": "최초 원인 — 사용자/시스템 행동"}},
    {{"step": 2, "service": "ServiceName", "event": "다음 영향 (Korean)", "why": "step 1이 이것을 유발한 이유 (Korean)"}},
    {{"step": N, "service": "ServiceName", "event": "최종 증상 (Korean)", "why": "이전 step이 이것을 유발한 이유 (Korean)"}}
  ],
  "root_cause": {{
    "title": "Root cause (Korean)",
    "description": "Root cause 설명 (Korean, 1-2 lines)"
  }},
  "evaluation": {{
    "root_cause_match": {{"score": 0-100, "label": "Root Cause"}},
    "causal_chain": {{"score": 0-100, "label": "Causal Chain"}},
    "data_sources": {{"score": 0-100, "label": "Data Sources"}},
    "false_leads": {{"score": 0-100, "label": "False Lead"}}
  }},
  "corrections": [
    "Phase 1 대비 Phase 2가 보정한 사항 (Korean, 1 line each)"
  ]
}}

Rules:
- Only include hypotheses the agent actually explored (no speculation)
- is_key=true only for signals decisive for root cause
- One confirmed hypothesis can have MANY steps — don't split it into sub-hypotheses
- A rejected hypothesis has steps that show WHY it was rejected
- Causal chain: step 1 = root cause 행동 (최초 원인), 마지막 step = 알람 직전 최종 증상
- 인과 전파 순서: root cause → 중간 영향 → 최종 증상 (위→아래 방향 시각화)
- 각 step에 "why" 필드 추가: 이전 step이 어떻게 다음 step을 유발했는지 설명"""

    # 캐시 확인 (완료된 조사는 결과 불변)
    if task_id in _analysis_cache:
        return jsonify(_analysis_cache[task_id])

    try:
        model = AVAILABLE_MODELS.get(
            request.args.get("model", "sonnet"),
            AVAILABLE_MODELS["sonnet"]
        )
        from botocore.config import Config as BotoConfig
        bedrock = session.client("bedrock-runtime", region_name=AWS_REGION,
                                  config=BotoConfig(read_timeout=120, connect_timeout=10))
        resp = bedrock.invoke_model(
            modelId=model,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 8000,
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

        response_data = {
            "ok": True,
            "task_id": task_id,
            "hypotheses": result.get("hypotheses", []),
            "causal_chain": result.get("causal_chain", []),
            "root_cause": result.get("root_cause"),
            "evaluation": result.get("evaluation"),
            "corrections": result.get("corrections", []),
        }
        _analysis_cache[task_id] = response_data
        return jsonify(response_data)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": f"Bedrock analysis failed: {e}"}), 500


@dag_bp.route("/api/dag-verify")
def api_dag_verify():
    task_id = request.args.get("task_id")
    space_id = request.args.get("space_id", AGENT_SPACE_ID)
    if not task_id:
        return jsonify({"error": "task_id required"}), 400

    try:
        session = _space_session(space_id)
        client = session.client("devops-agent")
        exec_resp = client.list_executions(agentSpaceId=space_id, taskId=task_id, limit=10)
        executions = exec_resp.get("executions", [])
        if not executions:
            return jsonify({"error": "no executions"})

        all_records = []
        for exe in executions:
            exec_id = exe["executionId"]
            jr_resp = client.list_journal_records(agentSpaceId=space_id, executionId=exec_id, order="ASC")
            for r in jr_resp.get("records", []):
                content = r.get("content", {})
                raw_text = content.get("text", "") if isinstance(content, dict) else str(content)
                parsed = None
                try:
                    parsed = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
                except (json.JSONDecodeError, TypeError):
                    pass
                created_at = r.get("createdAt", "")
                if hasattr(created_at, "isoformat"):
                    created_at = created_at.isoformat()
                all_records.append({
                    "record_type": r.get("recordType", ""),
                    "created_at": str(created_at)[:19],
                    "parsed": parsed,
                    "raw_text": raw_text[:3000],
                })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    TYPE_RANK = {"unknown": 0, "hypothesis": 1, "impact": 2, "cause": 3, "root_cause": 4}

    alarm = None
    observations = []
    findings_raw = []
    summary_findings = []
    summary_gaps = []
    messages = []

    for r in all_records:
        p = r.get("parsed")
        if not p:
            continue
        rtype = r.get("record_type", "")
        time_str = (r.get("created_at") or "")[11:16]
        ts = r.get("created_at", "")

        if rtype == "symptom":
            alarm = {"id": p.get("id", "symptom"), "title": p.get("title", "Alarm"), "time": time_str, "ts": ts}
        elif rtype == "observation":
            observations.append({
                "id": p.get("id", f"obs-{len(observations)}"),
                "title": p.get("title", ""),
                "time": time_str, "ts": ts,
                "activity": p.get("activity_id", ""),
                "signals": p.get("signals", []),
            })
        elif rtype == "finding":
            findings_raw.append({
                "id": p.get("id", f"fin-{len(findings_raw)}"),
                "title": p.get("title", ""),
                "time": time_str, "ts": ts,
                "finding_type": p.get("finding_type", "unknown"),
                "supporting": p.get("supporting_observations", []),
            })
        elif rtype == "investigation_summary":
            summary_findings = p.get("findings", [])
            summary_gaps = p.get("investigation_gaps", [])
        elif rtype == "message":
            txt = ""
            if isinstance(p.get("content"), list) and p["content"]:
                txt = p["content"][0].get("text", "")
            elif isinstance(p.get("content"), str):
                txt = p["content"]
            if txt:
                messages.append({"time": time_str, "ts": ts, "text": txt[:200]})

    issues = []

    deduped = {}
    for f in findings_raw:
        fid = f["id"]
        if fid in deduped:
            existing = deduped[fid]
            if TYPE_RANK.get(f["finding_type"], 0) > TYPE_RANK.get(existing["finding_type"], 0):
                f["supporting"] = list(set(existing["supporting"]) | set(f["supporting"]))
                deduped[fid] = f
            else:
                existing["supporting"] = list(set(existing["supporting"]) | set(f["supporting"]))
        else:
            deduped[fid] = f
    findings = list(deduped.values())

    sf_map = {sf["id"]: sf for sf in summary_findings}
    cascade_graph = {sf["id"]: sf.get("cascades_to", []) for sf in summary_findings}

    root_cause_id = None
    for sf in summary_findings:
        if sf.get("type") == "root_cause":
            root_cause_id = sf["id"]
            break

    chain_order = []
    chain_edges = []
    terminates_at_alarm = False
    if root_cause_id:
        current = root_cause_id
        visited = set()
        while current and current not in visited:
            visited.add(current)
            if current in sf_map:
                chain_order.append(current)
                targets = cascade_graph.get(current, [])
                if targets:
                    nxt = targets[0]
                    if nxt in sf_map:
                        chain_edges.append({"from": current, "to": nxt})
                        current = nxt
                    else:
                        chain_edges.append({"from": current, "to": "alarm"})
                        terminates_at_alarm = True
                        break
                else:
                    break
            else:
                break

    chain_set = set(chain_order)

    activity_groups = {}
    for obs in observations:
        act = obs["activity"] or "unknown"
        activity_groups.setdefault(act, []).append(obs["id"])

    obs_finding_edges = []
    connected_obs = set()
    for fin in findings:
        for oid in fin["supporting"]:
            obs_id = f"obs_{oid}"
            obs_finding_edges.append({"from": obs_id, "to": f"fin_{fin['id']}", "in_chain": fin["id"] in chain_set})
            connected_obs.add(obs_id)

    orphan_obs = [f"obs_{o['id']}" for o in observations if f"obs_{o['id']}" not in connected_obs]

    node_counts = {
        "alarm": 1 if alarm else 0,
        "observation": len(observations),
        "finding": len(findings),
        "terminated": 1 if orphan_obs else 0,
    }
    total_nodes = sum(node_counts.values())

    edge_count = (
        len(observations)
        + len(obs_finding_edges)
        + len(chain_edges)
        + len(orphan_obs)
    )

    return jsonify({
        "ok": True,
        "layout": "Causal Chain: Alarm → Observations (by activity) → Findings (chain) → Alarm",
        "raw_record_counts": {
            "total": len(all_records),
            "symptom": sum(1 for r in all_records if r["record_type"] == "symptom"),
            "observation": len(observations),
            "finding_records": len(findings_raw),
            "finding_deduped": len(findings),
            "summary_findings": len(summary_findings),
            "message": len(messages),
        },
        "causal_chain": {
            "ordered": chain_order,
            "edges": chain_edges,
            "root_cause_id": root_cause_id,
            "terminates_at_alarm": terminates_at_alarm,
        },
        "activity_groups": activity_groups,
        "dag_model": {
            "total_nodes": total_nodes,
            "total_edges": edge_count,
            "node_counts": node_counts,
            "obs_finding_edges": len(obs_finding_edges),
            "orphan_observations": len(orphan_obs),
        },
        "observations_detail": [
            {
                "id": obs["id"],
                "activity": obs["activity"],
                "signal_count": len(obs["signals"]),
                "connected_to_finding": f"obs_{obs['id']}" in connected_obs,
            }
            for obs in observations
        ],
        "findings_detail": [
            {
                "id": fin["id"],
                "title": fin["title"][:80],
                "finding_type": fin["finding_type"],
                "in_causal_chain": fin["id"] in chain_set,
                "supporting_observations": fin["supporting"],
            }
            for fin in findings
        ],
        "issues": issues + ([f"ORPHAN OBS ({len(orphan_obs)}): {orphan_obs[:5]}"] if orphan_obs else []),
    })
