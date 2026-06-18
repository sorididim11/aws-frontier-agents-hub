"""Scenario generation routes: /api/scenario-apps, /api/arch/generate-scenario."""
import re
import traceback

from flask import jsonify, request

from app_config import (
    _CFG, _cfg_get, AWS_REGION, AGENT_SPACE_ID,
    _req_space_id, _boto_session,
    AVAILABLE_MODELS,
)
from routes_arch import (
    _load_latest_arch, _list_scenarios, _get_scenario,
)
from routes_scenario import scenario_bp


@scenario_bp.route("/api/scenario-apps")
def api_scenario_apps():
    """시나리오 대상 앱 목록. 환경의 App 태그에서 직접 조회."""
    space_id = _req_space_id()
    apps = []

    # 1) 토폴로지 분석 결과에서 앱+서비스 목록
    try:
        saved = _load_latest_arch(space_id)
        if saved:
            nodes = saved.get("graph", {}).get("nodes", [])
            groups = {}
            for n in nodes:
                g = n.get("group", "")
                if g and n.get("service_type") != "boundary":
                    groups.setdefault(g, []).append(n["name"])
            for g_name, svcs in groups.items():
                apps.append({"name": g_name, "count": len(svcs),
                             "services": svcs, "source": "topology"})
    except Exception:
        pass

    # 2) 분석 결과 없으면 Space App 태그
    if not apps:
        try:
            from app_config import _tag_value_for_space
            tag_val = _tag_value_for_space(space_id)
            if tag_val:
                apps.append({"name": tag_val, "count": 0,
                             "services": [], "source": "tag"})
        except Exception:
            pass

    return jsonify({"ok": True, "apps": apps, "space_id": space_id})


# ===================================================================
# Scenario Generation from Recommendations
# ===================================================================

@scenario_bp.route("/api/arch/generate-scenario", methods=["POST"])
def api_arch_generate_scenario():
    """Generate executable scenario JSON from an architecture recommendation."""
    from arch_analysis import ScenarioGenerator, ServiceGraph
    from botocore.config import Config as BotoConfig

    body = request.json or {}
    recommendation = body.get("recommendation")
    if not recommendation:
        return jsonify({"ok": False, "error": "recommendation required"}), 400

    space_id = _req_space_id("json")
    model_key = body.get("model", "opus")
    model_id = AVAILABLE_MODELS.get(model_key, AVAILABLE_MODELS["opus"])

    saved = None
    try:
        saved = _load_latest_arch(space_id)
    except Exception:
        pass
    if not saved or not saved.get("graph"):
        return jsonify({"ok": False, "error": "Run architecture discovery first"}), 400

    try:
        graph = ServiceGraph.from_dict(saved["graph"])

        existing_items = _list_scenarios(space_id)
        existing_ids = [s.get("id", "") for s in existing_items]
        all_scenarios = {}
        for sid in existing_ids:
            sc = _get_scenario(space_id, sid)
            if sc:
                all_scenarios[sid] = sc

        scenario_id = ScenarioGenerator.next_generated_id(existing_ids)
        short_name = (recommendation.get("name", "generated") or "generated")
        short_name = re.sub(r"[^a-zA-Z0-9가-힣\-]", "-", short_name)[:30].strip("-").lower()
        scenario_id = f"{scenario_id}-{short_name}"

        alarms = []
        try:
            session = _boto_session()
            cw = session.client("cloudwatch")
            paginator = cw.get_paginator("describe_alarms")
            for page in paginator.paginate(StateValue="OK", MaxRecords=100):
                for a in page.get("MetricAlarms", []):
                    alarms.append({
                        "name": a["AlarmName"],
                        "metric": a.get("MetricName", ""),
                        "namespace": a.get("Namespace", ""),
                        "dimensions": a.get("Dimensions", []),
                        "threshold": a.get("Threshold"),
                        "period": a.get("Period"),
                        "eval_periods": a.get("EvaluationPeriods"),
                        "statistic": a.get("Statistic", ""),
                    })
                if len(alarms) > 50:
                    break
        except Exception:
            pass

        fis_templates = []
        try:
            from credential_resolver import credentials
            for acct in credentials.list_accounts():
                try:
                    sess = resolver.get_session(acct.account_id)
                    fis = sess.client("fis")
                    resp = fis.list_experiment_templates(maxResults=20)
                    for t in resp.get("experimentTemplates", []):
                        fis_templates.append({
                            "id": t["id"],
                            "description": t.get("description", ""),
                            "tags": t.get("tags", {}),
                            "account_id": acct.account_id,
                            "profile": acct.profile,
                        })
                except Exception:
                    continue
        except Exception:
            pass

        context = {
            "scenario_id": scenario_id,
            "scenarios": all_scenarios,
            "alarms": alarms,
            "fis_templates": fis_templates,
        }

        session = _boto_session()
        bedrock = session.client("bedrock-runtime", config=BotoConfig(read_timeout=300))
        generator = ScenarioGenerator(bedrock, model_id=model_id)
        scenario = generator.generate(recommendation, graph, context)

        return jsonify({"ok": True, "scenario": scenario, "model": model_key})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500
