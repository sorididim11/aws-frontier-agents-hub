#!/usr/bin/env python3
"""
Overview App — unified Flask app combining Agent Space + Investigation DAG.

Run: python overview_app.py [--port 5003]
"""
import os
import sys

from flask import Flask

sys.path.insert(0, os.path.dirname(__file__))

from app_config import (  # noqa: F401, E402
    _CFG, _cfg_get, AWS_REGION, AGENT_SPACE_ID, RUNS_TABLE, AWS_PROFILE,
    _req_space_id, _agent_space_id,
    _boto_session, _assumed_session, _get_or_create_session,
    _session_for_association, _get_aws_associations, _tag_key_for_space,
    _fetch_tagged_resources,
)

app = Flask(__name__, template_folder="templates", static_folder="static")

# ── Register Blueprints ──────────────────────────────────────────────────────

from routes_space import space_bp  # noqa: E402
app.register_blueprint(space_bp)

from routes_dag import dag_bp  # noqa: E402
app.register_blueprint(dag_bp)

from routes_arch import arch_bp  # noqa: E402
app.register_blueprint(arch_bp)

from routes_scenario import scenario_bp  # noqa: E402
app.register_blueprint(scenario_bp)

from routes_settings import settings_bp  # noqa: E402
app.register_blueprint(settings_bp)

from routes_security_targets import security_targets_bp  # noqa: E402
app.register_blueprint(security_targets_bp)

from routes_security_insights import security_insights_bp  # noqa: E402
app.register_blueprint(security_insights_bp)

from routes_space_cfn_import import cfn_import_bp  # noqa: E402
app.register_blueprint(cfn_import_bp)

from routes_skills import skills_bp  # noqa: E402
app.register_blueprint(skills_bp)

from routes_expert import expert_bp  # noqa: E402
app.register_blueprint(expert_bp)

from routes_setup import setup_bp  # noqa: E402
app.register_blueprint(setup_bp)

from routes_simulation import simulation_bp  # noqa: E402
app.register_blueprint(simulation_bp)


# ===================================================================
# Initialize simulation engine (cluster_manager + verifier)
# ===================================================================
def _init_simulator():
    import cluster_manager
    from verifier import init_slack_config
    cluster_manager.init()
    init_slack_config()
    print("[overview_app] Simulator engine initialized (direct import, no proxy)", flush=True)

_init_simulator()


# ===================================================================
# Pre-warm catalog index (background)
# ===================================================================
def _prewarm_catalog():
    """앱 시작 시 카탈로그 인덱스 선작업 (git sparse clone + 파싱)."""
    try:
        from catalog_manager import get_catalog_manager
        cat = get_catalog_manager()
        cat.get_index()
        print("[overview_app] Catalog index ready", flush=True)
    except Exception as e:
        print(f"[overview_app] Catalog prewarm failed (non-fatal): {e}", flush=True)


import threading
threading.Thread(target=_prewarm_catalog, daemon=True, name="catalog-prewarm").start()


# ===================================================================
# Entry point
# ===================================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5003)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--no-debug", action="store_true")
    args = parser.parse_args()
    debug = not args.no_debug and os.environ.get("FLASK_DEBUG", "1") != "0"

    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        from ai_provider import init_provider
        _cw_profile = _cfg_get(_CFG, "aws.profile", "member1-acc")
        init_provider(profile=_cw_profile, region=AWS_REGION)

    print(f"Overview App (Space + DAG) running on http://localhost:{args.port} (debug={debug})")
    app.run(host=args.host, port=args.port, debug=debug, threaded=True)
