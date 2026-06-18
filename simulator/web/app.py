"""Simulator Web UI — Flask application."""

import json
import os
import queue
import threading
import traceback

from flask import Flask, Response, jsonify, render_template, request

from simulator.config import load_config
from simulator.engine.topology import TopologyDiscoverer
from simulator.engine.enricher import TopologyEnricher
from simulator.engine.generator import ScenarioGenerator


app = Flask(__name__, template_folder="templates", static_folder="static")

_state = {
    "graph": None,
    "enriched": None,
    "scenarios": [],
    "recommendations": None,
    "namespace": "dockercoins",
}


def _cfg():
    return load_config()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/discover", methods=["POST"])
def api_discover():
    ns = request.json.get("namespace", _state["namespace"]) if request.json else _state["namespace"]
    method = request.json.get("method", "kubeshark") if request.json else "kubeshark"
    _state["namespace"] = ns
    try:
        cfg = _cfg()
        if method == "chat":
            from simulator.engine.chat_discovery import ChatTopologyDiscoverer
            disc = ChatTopologyDiscoverer(cfg, ns)
        else:
            disc = TopologyDiscoverer(cfg, ns)
        graph = disc.discover()
        _state["graph"] = graph

        enricher = TopologyEnricher(cfg)
        enriched = enricher.enrich(graph)
        _state["enriched"] = enriched

        conversation = None
        if method == "chat" and hasattr(disc, "get_conversation_log"):
            conversation = disc.get_conversation_log()

        nodes = [{"name": n.name, "namespace": n.namespace, "type": n.service_type,
                   "labels": n.labels, "ports": n.ports} for n in graph.nodes]
        edges = [{"source": e.source, "target": e.target, "protocol": e.protocol,
                   "port": e.port, "paths": e.paths} for e in graph.edges]
        result = {"ok": True, "nodes": nodes, "edges": edges, "namespace": ns, "method": method}
        if conversation:
            result["conversation"] = conversation
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/discover/stream")
def api_discover_stream():
    """SSE endpoint: streams Q&A progress during chat-based discovery."""
    ns = request.args.get("namespace", _state["namespace"])
    _state["namespace"] = ns

    event_q = queue.Queue()

    def on_progress(phase, question, data):
        event_q.put({"type": "qa", "phase": phase, "question": question, "data": data})

    def run_discovery():
        try:
            cfg = _cfg()
            from simulator.engine.chat_discovery import ChatTopologyDiscoverer
            disc = ChatTopologyDiscoverer(cfg, ns, on_progress=on_progress)
            graph = disc.discover()
            _state["graph"] = graph

            enricher = TopologyEnricher(cfg)
            enriched = enricher.enrich(graph)
            _state["enriched"] = enriched

            nodes = [{"name": n.name, "namespace": n.namespace, "type": n.service_type,
                       "labels": n.labels, "ports": n.ports} for n in graph.nodes]
            edges = [{"source": e.source, "target": e.target, "protocol": e.protocol,
                       "port": e.port, "paths": e.paths} for e in graph.edges]

            conversation = disc.get_conversation_log()

            event_q.put({
                "type": "complete",
                "nodes": nodes,
                "edges": edges,
                "namespace": ns,
                "conversation": conversation,
            })
        except Exception as e:
            event_q.put({"type": "error", "error": str(e), "trace": traceback.format_exc()})

    threading.Thread(target=run_discovery, daemon=True).start()

    def generate():
        while True:
            try:
                event = event_q.get(timeout=300)
            except queue.Empty:
                yield "data: {\"type\": \"error\", \"error\": \"timeout\"}\n\n"
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event["type"] in ("complete", "error"):
                break

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/generate", methods=["POST"])
def api_generate():
    if not _state["enriched"]:
        return jsonify({"ok": False, "error": "Run discover first"}), 400
    try:
        cfg = _cfg()
        gen = ScenarioGenerator(_state["enriched"], cfg)
        categories = request.json.get("categories") if request.json else None
        scenarios = gen.generate_all(categories=categories)
        gen.save(scenarios)
        _state["scenarios"] = scenarios
        return jsonify({"ok": True, "count": len(scenarios),
                        "scenarios": [_scenario_summary(s) for s in scenarios]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/scenarios")
def api_scenarios():
    scenarios = _state["scenarios"]
    if not scenarios:
        scenarios = _load_saved_scenarios()
        _state["scenarios"] = scenarios
    return jsonify({"scenarios": [_scenario_summary(s) for s in scenarios]})


@app.route("/api/scenarios/<scenario_id>")
def api_scenario_detail(scenario_id):
    scenario = _find_scenario(scenario_id)
    if not scenario:
        return jsonify({"ok": False, "error": "Scenario not found"}), 404
    return jsonify({"ok": True, "scenario": scenario})


@app.route("/api/scenarios/<scenario_id>", methods=["PUT"])
def api_scenario_update(scenario_id):
    scenario = _find_scenario(scenario_id)
    if not scenario:
        return jsonify({"ok": False, "error": "Scenario not found"}), 404
    updates = request.json
    for key in ["summary", "normal_flow", "fault_flow", "verification"]:
        if key in updates:
            scenario[key] = updates[key]
    _save_scenario(scenario)
    return jsonify({"ok": True, "scenario": scenario})


@app.route("/api/recommend", methods=["POST"])
def api_recommend():
    if not _state["graph"]:
        return jsonify({"ok": False, "error": "Run discover first"}), 400
    try:
        from simulator.engine.recommender import ScenarioRecommender
        cfg = _cfg()
        model_id = request.json.get("model", "us.anthropic.claude-opus-4-6-v1") if request.json else "us.anthropic.claude-opus-4-6-v1"
        recommender = ScenarioRecommender(cfg, model_id=model_id)
        result = recommender.recommend(_state["graph"])
        _state["recommendations"] = result
        return jsonify({"ok": True, "result": result.to_dict()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/topology")
def api_topology():
    graph = _state["graph"]
    if not graph:
        return jsonify({"ok": True, "nodes": [], "edges": []})
    nodes = [{"name": n.name, "namespace": n.namespace, "type": n.service_type,
               "labels": n.labels, "ports": n.ports} for n in graph.nodes]
    edges = [{"source": e.source, "target": e.target, "protocol": e.protocol,
               "port": e.port} for e in graph.edges]
    return jsonify({"ok": True, "nodes": nodes, "edges": edges,
                    "namespace": graph.namespace})


def _scenario_summary(s):
    return {
        "id": s["id"],
        "name": s["name"],
        "category": s["category"],
        "layer": s["layer"],
        "namespace": s.get("namespace", ""),
        "summary": s.get("summary", {}),
        "verification_count": len(s.get("verification", [])),
        "normal_flow_count": len(s.get("normal_flow", [])),
        "fault_flow_count": len(s.get("fault_flow", [])),
    }


def _find_scenario(scenario_id):
    for s in _state["scenarios"]:
        if s["id"] == scenario_id:
            return s
    saved = _load_saved_scenarios()
    for s in saved:
        if s["id"] == scenario_id:
            _state["scenarios"] = saved
            return s
    return None


def _load_saved_scenarios():
    cfg = _cfg()
    out = cfg.output_dir
    scenarios = []
    if os.path.isdir(out):
        for fname in sorted(os.listdir(out)):
            if fname.endswith(".json"):
                with open(os.path.join(out, fname)) as f:
                    scenarios.append(json.load(f))
    return scenarios


def _save_scenario(scenario):
    cfg = _cfg()
    path = os.path.join(cfg.output_dir, f"{scenario['id']}.json")
    os.makedirs(cfg.output_dir, exist_ok=True)
    with open(path, "w") as f:
        json.dump(scenario, f, indent=2, ensure_ascii=False)


LAYER_ORDER = [
    "AWS Infrastructure",
    "Kubernetes Platform",
    "Network",
    "Application",
    "Composite",
]


def run(host="0.0.0.0", port=5001, debug=True):
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run()
