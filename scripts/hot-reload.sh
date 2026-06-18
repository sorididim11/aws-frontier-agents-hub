#!/bin/bash
# 빌드 없이 소스 파일만 교체 + gunicorn reload
# 사용법: bash scripts/hot-reload.sh
set -e

NAMESPACE="dashboard"
APP="dashboard"

POD=$(kubectl get pods -n $NAMESPACE -l app=$APP -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [ -z "$POD" ]; then
    echo "❌ dashboard pod를 찾을 수 없습니다."
    exit 1
fi
echo "📦 Pod: $POD"

# Python 소스 복사
echo "📄 Python 소스 복사..."
for f in app.py verifier.py evaluator.py evidence.py config.py config.yaml; do
    [ -f "services/dashboard/$f" ] && kubectl cp "services/dashboard/$f" "$NAMESPACE/$POD:/app/$f"
done

# 템플릿 복사
echo "📄 템플릿 복사..."
kubectl cp services/dashboard/templates/index.html "$NAMESPACE/$POD:/app/templates/index.html"

# 정적 파일 복사
echo "📄 JS/CSS 복사..."
for f in services/dashboard/static/js/*.js services/dashboard/static/css/*.css; do
    [ -f "$f" ] && kubectl cp "$f" "$NAMESPACE/$POD:/app/${f#services/dashboard/}"
done

# 시나리오 복사
echo "📄 시나리오 복사..."
for f in services/dashboard/scenarios/*.json; do
    [ -f "$f" ] && kubectl cp "$f" "$NAMESPACE/$POD:/app/scenarios/$(basename $f)"
done

# Gunicorn reload (PID 1에 HUP 시그널)
echo "🔄 Gunicorn reload..."
kubectl exec -n $NAMESPACE $POD -- python3 -c "import os,signal; os.kill(1, signal.SIGHUP)"

echo "✅ Hot reload 완료"
