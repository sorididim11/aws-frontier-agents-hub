"""PyInstaller runtime hook — sets environment for bundled mode."""
import os
import sys

if getattr(sys, "frozen", False):
    os.environ["DEVOPS_AGENT_BUNDLED"] = "1"
    os.environ["DEVOPS_AGENT_BUNDLE_DIR"] = sys._MEIPASS
    os.environ["DASHBOARD_MODE"] = "local"
