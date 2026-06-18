#!/usr/bin/env python3
"""Flask server entry point — run as subprocess from launcher."""
import os
import sys

# Setup paths before any app imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from desktop.path_resolver import setup_paths, ensure_config_exists
setup_paths()
ensure_config_exists()

os.environ.setdefault("DASHBOARD_MODE", "local")

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5003
    from overview_app import app
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)
