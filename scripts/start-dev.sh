#!/bin/bash
# Development environment launcher
# Usage: ./scripts/start-dev.sh [--no-gitlab] [--port PORT]
#
# Starts:
#   1. GitLab port-forward (localhost:8443 → gitlab svc in EKS)
#   2. Dashboard app (overview_app.py)
#
# Stops all on Ctrl+C.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DASHBOARD_DIR="$PROJECT_ROOT/services/dashboard"

APP_PORT="${APP_PORT:-5003}"
GITLAB_LOCAL_PORT="${GITLAB_LOCAL_PORT:-8443}"
GITLAB_NAMESPACE="${GITLAB_NAMESPACE:-gitlab}"
GITLAB_SVC="${GITLAB_SVC:-gitlab-webservice-default}"
GITLAB_SVC_PORT="${GITLAB_SVC_PORT:-8181}"
KUBE_CONTEXT="${KUBE_CONTEXT:-m1-590}"

NO_GITLAB=false
for arg in "$@"; do
    case $arg in
        --no-gitlab) NO_GITLAB=true ;;
        --port) shift; APP_PORT="$1" ;;
    esac
done

cleanup() {
    echo ""
    echo "[dev] Shutting down..."
    kill $PF_PID 2>/dev/null || true
    kill $APP_PID 2>/dev/null || true
    wait 2>/dev/null
    echo "[dev] Done."
}
trap cleanup EXIT INT TERM

# 1. GitLab port-forward
PF_PID=""
if [ "$NO_GITLAB" = false ]; then
    echo "[dev] Starting GitLab port-forward (localhost:$GITLAB_LOCAL_PORT → $GITLAB_SVC:$GITLAB_SVC_PORT in ns/$GITLAB_NAMESPACE)..."
    (
        while true; do
            kubectl port-forward "svc/$GITLAB_SVC" "$GITLAB_LOCAL_PORT:$GITLAB_SVC_PORT" \
                -n "$GITLAB_NAMESPACE" --context="$KUBE_CONTEXT" 2>&1 | sed 's/^/[gitlab-pf] /'
            echo "[gitlab-pf] Connection lost. Reconnecting in 3s..."
            sleep 3
        done
    ) &
    PF_PID=$!
    sleep 1
    echo "[dev] GitLab port-forward PID: $PF_PID"
fi

# 2. Dashboard app
echo "[dev] Starting dashboard (port $APP_PORT)..."
cd "$DASHBOARD_DIR"
python overview_app.py &
APP_PID=$!
echo "[dev] Dashboard PID: $APP_PID"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Dashboard:  http://localhost:$APP_PORT"
if [ "$NO_GITLAB" = false ]; then
echo "  GitLab PF:  localhost:$GITLAB_LOCAL_PORT → $GITLAB_SVC"
fi
echo "  Press Ctrl+C to stop all"
echo "═══════════════════════════════════════════════════"
echo ""

wait
