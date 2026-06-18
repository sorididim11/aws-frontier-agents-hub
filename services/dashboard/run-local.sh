#!/bin/bash
# 로컬에서 Dashboard 실행 (이미지 빌드 없이)

echo "=== DevOps Agent Test Dashboard (Local) ==="
echo ""

# Flask 설치 확인
if ! python3 -c "import flask" 2>/dev/null; then
    echo "Flask 설치 중..."
    pip3 install flask
fi

# 현재 kubectl 컨텍스트 확인
echo "현재 kubectl 컨텍스트:"
kubectl config current-context
echo ""

echo "Pod 상태:"
kubectl get pods -n dockercoins
echo ""

echo "Dashboard 시작: http://localhost:8080"
echo "종료: Ctrl+C"
echo ""

# Flask 실행
SCRIPT_DIR="$(dirname "$0")"
python3 "$SCRIPT_DIR/app.py"
