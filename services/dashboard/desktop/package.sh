#!/bin/bash
# Quick package: rebuild + clean dev config from bundle
set -euo pipefail
cd "$(dirname "$0")"
source .venv-build/bin/activate
pyinstaller --clean --noconfirm devops_agent.spec 2>&1 | grep -E "ERROR|WARNING.*not found|Build complete"
find dist -name "config.yaml" ! -name "*.example" -delete 2>/dev/null || true
echo ""
echo "Done: dist/DevOps Agent.app ($(du -sh 'dist/DevOps Agent.app' | cut -f1))"
