#!/usr/bin/env python3
"""Chaos Simulator CLI — discover, generate, run scenarios for any K8s app."""

import argparse
import json
import os
import sys

from simulator.config import load_config


def cmd_install(args):
    from simulator.engine.installer import install_all, verify_installation
    cfg = load_config(args.config)
    install_all(cfg, kubeshark=not args.no_kubeshark, chaos_mesh=not args.no_chaos_mesh)

    status = verify_installation(cfg)
    print(f"\nInstallation status: {json.dumps(status, indent=2)}")


def cmd_uninstall(args):
    from simulator.engine.installer import uninstall_all
    cfg = load_config(args.config)
    uninstall_all(cfg)


def cmd_status(args):
    from simulator.engine.installer import verify_installation
    cfg = load_config(args.config)
    status = verify_installation(cfg)
    print(json.dumps(status, indent=2))


def _make_discoverer(cfg, namespace, use_chat=False):
    if use_chat:
        from simulator.engine.chat_discovery import ChatTopologyDiscoverer
        return ChatTopologyDiscoverer(cfg, namespace)
    else:
        from simulator.engine.topology import TopologyDiscoverer
        return TopologyDiscoverer(cfg, namespace)


def cmd_discover(args):
    from simulator.engine.enricher import TopologyEnricher
    cfg = load_config(args.config)

    print(f"[DISCOVER] Namespace: {args.namespace} (method: {'chat' if args.chat else 'kubeshark'})")

    discoverer = _make_discoverer(cfg, args.namespace, use_chat=args.chat)
    graph = discoverer.discover()

    print(f"  Nodes: {len(graph.nodes)}")
    for n in graph.nodes:
        print(f"    - {n.name} ({n.service_type}) ports={n.ports}")

    print(f"  Edges: {len(graph.edges)}")
    for e in graph.edges:
        paths = f" paths={e.paths}" if e.paths else ""
        print(f"    - {e.source} → {e.target} ({e.protocol}:{e.port}){paths}")

    if args.enrich:
        enricher = TopologyEnricher(args.namespace)
        enriched = enricher.enrich(graph)
        print(f"\n  Enriched nodes:")
        for name, en in enriched.enriched_nodes.items():
            print(f"    - {name}: mem_limit={en.resources.memory_limit}, "
                  f"liveness={en.has_liveness_probe}, "
                  f"volumes={en.volume_paths}, "
                  f"configmaps={en.configmap_refs}")

    if args.output:
        with open(args.output, "w") as f:
            if args.enrich:
                json.dump(enriched.to_dict(), f, indent=2, ensure_ascii=False)
            else:
                json.dump(graph.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"\n[OK] Saved to {args.output}")


def cmd_generate(args):
    from simulator.engine.enricher import TopologyEnricher
    from simulator.engine.generator import ScenarioGenerator
    cfg = load_config(args.config)

    print(f"[DISCOVER] Namespace: {args.namespace} (method: {'chat' if args.chat else 'kubeshark'})")
    discoverer = _make_discoverer(cfg, args.namespace, use_chat=args.chat)
    graph = discoverer.discover()

    print(f"[ENRICH] {len(graph.nodes)} nodes")
    enricher = TopologyEnricher(args.namespace)
    enriched = enricher.enrich(graph)

    print(f"[GENERATE] Applying templates...")
    categories = args.categories.split(",") if args.categories else None
    generator = ScenarioGenerator(enriched, cfg)
    scenarios = generator.generate_all(
        categories=categories,
        max_scenarios=args.max_scenarios,
    )

    output_dir = args.output_dir or cfg.output_dir
    generator.save(scenarios, output_dir)

    for s in scenarios:
        print(f"  - {s['id']}: {s['name']}")


def cmd_run(args):
    from simulator.executor.runner import run_scenario
    cfg = load_config(args.config)

    scenario_path = args.scenario
    if not os.path.exists(scenario_path):
        search_dirs = [cfg.output_dir, os.path.join(os.path.dirname(__file__), "scenarios")]
        for d in search_dirs:
            candidate = os.path.join(d, f"{scenario_path}.json")
            if os.path.exists(candidate):
                scenario_path = candidate
                break

    if not os.path.exists(scenario_path):
        print(f"[ERROR] Scenario not found: {args.scenario}")
        sys.exit(1)

    with open(scenario_path) as f:
        scenario = json.load(f)

    print(f"[RUN] {scenario['id']}: {scenario['name']}")
    print(f"  Category: {scenario['category']}")
    print(f"  Namespace: {scenario['namespace']}")
    print()

    result = run_scenario(scenario, auto_restore=not args.no_restore)

    print(f"\n{'='*60}")
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


def cmd_list(args):
    cfg = load_config(args.config)
    scenario_dir = args.dir or cfg.output_dir

    if not os.path.exists(scenario_dir):
        print(f"[INFO] No scenarios directory: {scenario_dir}")
        return

    scenarios = []
    for f in sorted(os.listdir(scenario_dir)):
        if f.endswith(".json"):
            path = os.path.join(scenario_dir, f)
            with open(path) as fh:
                s = json.load(fh)
                scenarios.append(s)

    if not scenarios:
        print("[INFO] No scenarios found")
        return

    by_category = {}
    for s in scenarios:
        cat = s.get("category", "unknown")
        by_category.setdefault(cat, []).append(s)

    for cat, items in sorted(by_category.items()):
        print(f"\n[{cat.upper()}]")
        for s in items:
            print(f"  {s['id']}: {s['name']}")

    print(f"\nTotal: {len(scenarios)} scenarios")


def cmd_recommend(args):
    from simulator.engine.recommender import ScenarioRecommender
    from simulator.engine.topology import ServiceGraph
    cfg = load_config(args.config)

    if args.topology:
        print(f"[RECOMMEND] Loading topology from {args.topology}")
        with open(args.topology) as f:
            data = json.load(f)
        graph = ServiceGraph.from_dict(data)
    else:
        print(f"[RECOMMEND] Discovering topology: {args.namespace} (method: {'chat' if args.chat else 'kubeshark'})")
        discoverer = _make_discoverer(cfg, args.namespace, use_chat=args.chat)
        graph = discoverer.discover()

    print(f"[RECOMMEND] Analyzing {len(graph.nodes)} services, {len(graph.edges)} edges...")
    recommender = ScenarioRecommender(cfg, model_id=args.model)
    result = recommender.recommend(graph)

    print(f"\n{'='*60}")
    print(f"[ARCHITECTURE ANALYSIS]")
    analysis = result.architecture_analysis
    if analysis.get("critical_path"):
        print(f"  Critical path: {analysis['critical_path']}")
    for spof in analysis.get("single_points_of_failure", []):
        print(f"  SPOF: {spof}")
    for risk in analysis.get("risk_areas", []):
        print(f"  Risk: {risk}")

    print(f"\n[RECOMMENDATIONS] {len(result.recommendations)} scenarios")
    for i, rec in enumerate(result.recommendations, 1):
        icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(rec.priority, "⚪")
        print(f"\n  {i}. [{icon} {rec.priority.upper()}] {rec.name}")
        print(f"     Template: {rec.template_id}")
        print(f"     Target: {rec.target}")
        print(f"     Rationale: {rec.rationale}")
        if rec.additional_data_needed:
            print(f"     Needs: {rec.additional_data_needed}")

    if args.output:
        recommender.save(result, args.output)


def cmd_cleanup(args):
    from simulator.executor.chaos_mesh import cleanup_all
    print(f"[CLEANUP] Deleting all Chaos Mesh experiments in {args.namespace}...")
    cleanup_all(args.namespace)
    print("[OK] Cleanup complete")


def cmd_web(args):
    from simulator.web.app import run
    print(f"[WEB] Starting Chaos Simulator UI on http://0.0.0.0:{args.port}")
    run(host="0.0.0.0", port=args.port, debug=args.debug)


def main():
    parser = argparse.ArgumentParser(
        prog="simulator",
        description="Reusable K8s Chaos Simulator Engine",
    )
    parser.add_argument("--config", default=None, help="Config YAML path")
    sub = parser.add_subparsers(dest="command", required=True)

    # install
    p = sub.add_parser("install", help="Install Kubeshark + Chaos Mesh")
    p.add_argument("--no-kubeshark", action="store_true")
    p.add_argument("--no-chaos-mesh", action="store_true")
    p.set_defaults(func=cmd_install)

    # uninstall
    p = sub.add_parser("uninstall", help="Uninstall all simulator dependencies")
    p.set_defaults(func=cmd_uninstall)

    # status
    p = sub.add_parser("status", help="Check installation status")
    p.set_defaults(func=cmd_status)

    # discover
    p = sub.add_parser("discover", help="Discover service topology")
    p.add_argument("namespace", help="K8s namespace to discover")
    p.add_argument("--chat", action="store_true", help="Use DevOps Agent chat API for discovery")
    p.add_argument("--enrich", action="store_true", help="Enrich with resource details")
    p.add_argument("--output", "-o", help="Save topology to JSON file")
    p.set_defaults(func=cmd_discover)

    # generate
    p = sub.add_parser("generate", help="Generate scenarios from topology")
    p.add_argument("namespace", help="K8s namespace")
    p.add_argument("--chat", action="store_true", help="Use DevOps Agent chat API for discovery")
    p.add_argument("--categories", help="Comma-separated: network,application,aws")
    p.add_argument("--max-scenarios", type=int, default=0, help="Max scenarios to generate")
    p.add_argument("--output-dir", help="Output directory for scenarios")
    p.set_defaults(func=cmd_generate)

    # run
    p = sub.add_parser("run", help="Execute a scenario")
    p.add_argument("scenario", help="Scenario file path or ID")
    p.add_argument("--no-restore", action="store_true", help="Skip auto-restore after run")
    p.set_defaults(func=cmd_run)

    # list
    p = sub.add_parser("list", help="List generated scenarios")
    p.add_argument("--dir", help="Scenarios directory")
    p.set_defaults(func=cmd_list)

    # recommend
    p = sub.add_parser("recommend", help="Get AI-recommended chaos scenarios")
    p.add_argument("namespace", nargs="?", default="dockercoins", help="K8s namespace")
    p.add_argument("--chat", action="store_true", help="Use DevOps Agent chat API for discovery")
    p.add_argument("--topology", help="Load topology from JSON file instead of discovering")
    p.add_argument("--model", default="us.anthropic.claude-opus-4-6-v1", help="Bedrock model ID")
    p.add_argument("--output", "-o", help="Save recommendations to JSON file")
    p.set_defaults(func=cmd_recommend)

    # cleanup
    p = sub.add_parser("cleanup", help="Delete all Chaos Mesh experiments")
    p.add_argument("namespace", help="K8s namespace")
    p.set_defaults(func=cmd_cleanup)

    # web
    p = sub.add_parser("web", help="Start the Simulator Web UI")
    p.add_argument("--port", type=int, default=5001, help="Port (default: 5001)")
    p.add_argument("--no-debug", dest="debug", action="store_false", default=True)
    p.set_defaults(func=cmd_web)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
