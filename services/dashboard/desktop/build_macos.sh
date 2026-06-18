#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DASHBOARD_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DASHBOARD_DIR/../.." && pwd)"

echo "=== DevOps Agent Desktop Build (macOS) ==="
echo "Dashboard: $DASHBOARD_DIR"
echo ""

# ─── Step 1: Prerequisites ───────────────────────────────────────────────────
echo "[1/6] Checking prerequisites..."

command -v python3 >/dev/null || { echo "ERROR: python3 not found"; exit 1; }
command -v node >/dev/null || { echo "ERROR: node not found (needed for sidecar build)"; exit 1; }
command -v npm >/dev/null || { echo "ERROR: npm not found"; exit 1; }

ARCH=$(uname -m)
echo "  Architecture: $ARCH"

# ─── Step 2: Python venv ─────────────────────────────────────────────────────
echo "[2/6] Setting up Python environment..."

VENV_DIR="$SCRIPT_DIR/.venv-build"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"

pip install --quiet --upgrade pip
pip install --quiet -r "$DASHBOARD_DIR/requirements.txt"
pip install --quiet pywebview pyinstaller "pyobjc-framework-WebKit>=10.0"

# ─── Step 3: Build expert_sidecar ────────────────────────────────────────────
echo "[3/6] Building expert_sidecar..."

SIDECAR_DIR="$DASHBOARD_DIR/expert_sidecar"
cd "$SIDECAR_DIR"

npm ci --quiet 2>/dev/null
npm run build

# Production-only node_modules
PROD_DIR="$SIDECAR_DIR/node_modules_prod"
rm -rf "$PROD_DIR"
mkdir -p "$PROD_DIR"

# Temporary directory for clean install
TMPINSTALL=$(mktemp -d)
cp package.json package-lock.json "$TMPINSTALL/"
cd "$TMPINSTALL"
npm ci --omit=dev --quiet 2>/dev/null
mv "$TMPINSTALL/node_modules" "$PROD_DIR/../node_modules_prod_tmp"
rm -rf "$TMPINSTALL"
rm -rf "$PROD_DIR"
mv "$SIDECAR_DIR/node_modules_prod_tmp" "$PROD_DIR"

echo "  Sidecar built: $(du -sh "$PROD_DIR" | cut -f1) (production deps)"

# ─── Step 4: Node.js binary ──────────────────────────────────────────────────
echo "[4/6] Preparing Node.js runtime..."

NODE_VERSION="v20.18.0"
NODE_DIR="$SCRIPT_DIR/node"
NODE_BIN="$NODE_DIR/bin/node"

if [ ! -f "$NODE_BIN" ]; then
    mkdir -p "$NODE_DIR/bin"
    if [ "$ARCH" = "arm64" ]; then
        NODE_ARCH="arm64"
    else
        NODE_ARCH="x64"
    fi
    TARBALL="node-${NODE_VERSION}-darwin-${NODE_ARCH}.tar.gz"
    echo "  Downloading $TARBALL..."
    curl -sL "https://nodejs.org/dist/${NODE_VERSION}/${TARBALL}" -o "/tmp/${TARBALL}"
    tar -xzf "/tmp/${TARBALL}" -C /tmp
    cp "/tmp/node-${NODE_VERSION}-darwin-${NODE_ARCH}/bin/node" "$NODE_BIN"
    chmod +x "$NODE_BIN"
    rm -rf "/tmp/node-${NODE_VERSION}-darwin-${NODE_ARCH}" "/tmp/${TARBALL}"
fi

echo "  Node: $NODE_BIN ($(file "$NODE_BIN" | grep -oE 'arm64|x86_64'))"

# ─── Step 5: PyInstaller ─────────────────────────────────────────────────────
echo "[5/6] Running PyInstaller..."

cd "$SCRIPT_DIR"
pyinstaller --clean --noconfirm devops_agent.spec

# Remove dev config.yaml from bundle (only config.yaml.example should ship)
find "$SCRIPT_DIR/dist" -name "config.yaml" ! -name "*.example" -delete 2>/dev/null || true

# ─── Step 6: Verify ──────────────────────────────────────────────────────────
echo "[6/6] Build complete!"

APP_PATH="$SCRIPT_DIR/dist/DevOps Agent.app"
if [ -d "$APP_PATH" ]; then
    # Ad-hoc code sign for local execution
    codesign --deep --force --sign - "$APP_PATH" 2>/dev/null || true
    echo ""
    echo "  Output: $APP_PATH"
    echo "  Size:   $(du -sh "$APP_PATH" | cut -f1)"
    echo ""
    echo "  Run:  open \"$APP_PATH\""
else
    echo "ERROR: .app not found"
    exit 1
fi
