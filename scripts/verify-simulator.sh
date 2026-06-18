#!/bin/bash
# ============================================================
# DevOps Agent Test Simulator — 자동 빌드/배포/검증 스크립트
# 사용법: bash scripts/verify-simulator.sh [--skip-build] [--skip-deploy]
# ============================================================
set -euo pipefail

ENV_PROFILE="${ENV_PROFILE:-member1-acc}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../config/load-config.sh" "$ENV_PROFILE"
REGION="$AWS_REGION"
PROFILE="$AWS_PROFILE"
ECR_REPO="${ECR_PREFIX}/dashboard"
LOCAL_PORT=8081
NAMESPACE="dashboard"
PASS=0
FAIL=0
SKIP_BUILD=false
SKIP_DEPLOY=false

for arg in "$@"; do
  case $arg in
    --skip-build) SKIP_BUILD=true ;;
    --skip-deploy) SKIP_DEPLOY=true ;;
  esac
done

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

check() {
  local name="$1"
  local result="$2"
  if [ "$result" = "true" ]; then
    echo -e "  ${GREEN}✅ PASS${NC} $name"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}❌ FAIL${NC} $name"
    FAIL=$((FAIL + 1))
  fi
}

cleanup_port_forward() {
  if [ -n "${PF_PID:-}" ]; then
    kill "$PF_PID" 2>/dev/null || true
    wait "$PF_PID" 2>/dev/null || true
  fi
}
trap cleanup_port_forward EXIT

BASE="http://localhost:${LOCAL_PORT}"

# ── Phase 1: Build ──
if [ "$SKIP_BUILD" = false ]; then
  echo -e "\n${CYAN}═══ Phase 1: Build ═══${NC}"
  echo "ECR 로그인..."
  aws ecr get-login-password --region $REGION --profile $PROFILE | \
    docker login --username AWS --password-stdin ${ECR_REGISTRY} 2>/dev/null
  echo "Docker 빌드..."
  docker build --platform linux/amd64 -t devops-dashboard services/dashboard/ -q
  docker tag devops-dashboard:latest ${ECR_REPO}:latest
  echo "ECR 푸시..."
  docker push ${ECR_REPO}:latest -q
  echo -e "${GREEN}빌드 완료${NC}"
else
  echo -e "\n${YELLOW}═══ Phase 1: Build (SKIPPED) ═══${NC}"
fi

# ── Phase 2: Deploy ──
if [ "$SKIP_DEPLOY" = false ]; then
  echo -e "\n${CYAN}═══ Phase 2: Deploy ═══${NC}"
  kubectl rollout restart deployment/dashboard -n $NAMESPACE
  kubectl rollout status deployment/dashboard -n $NAMESPACE --timeout=90s
  echo -e "${GREEN}배포 완료${NC}"
else
  echo -e "\n${YELLOW}═══ Phase 2: Deploy (SKIPPED) ═══${NC}"
fi

# ── Phase 3: Port Forward ──
echo -e "\n${CYAN}═══ Phase 3: Port Forward ═══${NC}"
# Kill any existing port-forward on this port
lsof -ti:${LOCAL_PORT} 2>/dev/null | xargs kill 2>/dev/null || true
sleep 1
kubectl port-forward svc/dashboard ${LOCAL_PORT}:80 -n $NAMESPACE &
PF_PID=$!
sleep 5

# Quick connectivity check — retry up to 3 times
PF_OK=false
for i in 1 2 3; do
  if curl -sf ${BASE}/health > /dev/null 2>&1; then
    PF_OK=true
    break
  fi
  sleep 2
done
if [ "$PF_OK" = false ]; then
  echo -e "${RED}Port forward 실패 — 종료${NC}"
  exit 1
fi
echo -e "${GREEN}Port forward 연결됨 (localhost:${LOCAL_PORT})${NC}"

# ── Phase 4: API 검증 ──
echo -e "\n${CYAN}═══ Phase 4: API 검증 ═══${NC}"

# 4-1. Health
echo -e "\n${YELLOW}[4-1] Health Check${NC}"
HEALTH=$(curl -sf ${BASE}/health | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
check "GET /health → healthy" "$([ "$HEALTH" = "healthy" ] && echo true || echo false)"

# 4-2. Scenarios
echo -e "\n${YELLOW}[4-2] Scenarios API${NC}"
SCENARIO_COUNT=$(curl -sf ${BASE}/api/scenarios | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
check "GET /api/scenarios → 시나리오 로드 (${SCENARIO_COUNT}개)" "$([ "$SCENARIO_COUNT" -ge 10 ] && echo true || echo false)"

# Check specific scenario
SCENARIO_ID=$(curl -sf ${BASE}/api/scenarios/C07-corrupted-data | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
check "GET /api/scenarios/C07-corrupted-data → 개별 조회" "$([ "$SCENARIO_ID" = "C07-corrupted-data" ] && echo true || echo false)"

# 4-3. Environment
echo -e "\n${YELLOW}[4-3] Environment API${NC}"
ENV_DATA=$(curl -sf ${BASE}/api/environment 2>/dev/null || echo "{}")
NODE_COUNT=$(echo "$ENV_DATA" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('nodes',[])))" 2>/dev/null || echo "0")
POD_COUNT=$(echo "$ENV_DATA" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('pods',[])))" 2>/dev/null || echo "0")
ALARM_COUNT=$(echo "$ENV_DATA" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('alarms',[])))" 2>/dev/null || echo "0")
check "Nodes 감지 (${NODE_COUNT}개)" "$([ "$NODE_COUNT" -ge 1 ] && echo true || echo false)"
check "Pods 감지 (${POD_COUNT}개)" "$([ "$POD_COUNT" -ge 3 ] && echo true || echo false)"
check "Alarms 감지 (${ALARM_COUNT}개)" "$([ "$ALARM_COUNT" -ge 1 ] && echo true || echo false)"

# 4-4. CRUD Test
echo -e "\n${YELLOW}[4-4] Scenario CRUD${NC}"
# Create
CREATE_RESULT=$(curl -sf -X POST ${BASE}/api/scenarios \
  -H 'Content-Type: application/json' \
  -d '{"id":"_test-auto","name":"Auto Test","category":"cleanup","layer":"Test","purpose":"자동 검증용","flow":["step1"],"trigger":{"type":"kubectl","command":"echo test"},"verification":{"steps":[]}}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('success',False))" 2>/dev/null || echo "False")
check "POST /api/scenarios → 생성" "$([ "$CREATE_RESULT" = "True" ] && echo true || echo false)"

# Read back
READ_RESULT=$(curl -sf ${BASE}/api/scenarios/_test-auto | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
check "GET /api/scenarios/_test-auto → 조회" "$([ "$READ_RESULT" = "_test-auto" ] && echo true || echo false)"

# Delete
DELETE_RESULT=$(curl -sf -X DELETE ${BASE}/api/scenarios/_test-auto | python3 -c "import sys,json; print(json.load(sys.stdin).get('success',False))" 2>/dev/null || echo "False")
check "DELETE /api/scenarios/_test-auto → 삭제" "$([ "$DELETE_RESULT" = "True" ] && echo true || echo false)"

# Verify deleted
DELETE_VERIFY=$(curl -s -o /dev/null -w "%{http_code}" ${BASE}/api/scenarios/_test-auto 2>/dev/null || echo "000")
check "삭제 확인 → 404" "$([ "$DELETE_VERIFY" = "404" ] && echo true || echo false)"

# 4-5. Async Run Test (restore-hasher — 무해한 시나리오)
echo -e "\n${YELLOW}[4-5] 비동기 실행 테스트 (restore-hasher)${NC}"
RUN_RESPONSE=$(curl -sf -X POST ${BASE}/api/run/restore-hasher 2>/dev/null || echo "{}")
RUN_ID=$(echo "$RUN_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('run_id',''))" 2>/dev/null || echo "")
RUN_STATUS=$(echo "$RUN_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
check "POST /api/run/restore-hasher → 즉시 응답" "$([ -n "$RUN_ID" ] && echo true || echo false)"
check "초기 상태 = running" "$([ "$RUN_STATUS" = "running" ] && echo true || echo false)"

# Poll until completed (max 30s)
if [ -n "$RUN_ID" ]; then
  FINAL_STATUS=""
  FINAL_RESULT=""
  for i in $(seq 1 15); do
    sleep 2
    POLL=$(curl -sf ${BASE}/api/run/${RUN_ID}/status 2>/dev/null || echo "{}")
    FINAL_STATUS=$(echo "$POLL" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
    FINAL_RESULT=$(echo "$POLL" | python3 -c "import sys,json; print(json.load(sys.stdin).get('result',''))" 2>/dev/null || echo "")
    if [ "$FINAL_STATUS" = "completed" ] || [ "$FINAL_STATUS" = "cancelled" ]; then
      break
    fi
  done
  check "실행 완료 (status=${FINAL_STATUS})" "$([ "$FINAL_STATUS" = "completed" ] && echo true || echo false)"
  check "결과 = pass (result=${FINAL_RESULT})" "$([ "$FINAL_RESULT" = "pass" ] && echo true || echo false)"
fi

# 4-6. History
echo -e "\n${YELLOW}[4-6] History API${NC}"
HISTORY_COUNT=$(curl -sf ${BASE}/api/history?limit=10 | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
check "GET /api/history → 기록 존재 (${HISTORY_COUNT}건)" "$([ "$HISTORY_COUNT" -ge 1 ] && echo true || echo false)"

# 4-7. HTML Rendering
echo -e "\n${YELLOW}[4-7] HTML 렌더링${NC}"
HTML=$(curl -sf ${BASE}/ 2>/dev/null || echo "")
HTML_CARDS=$(echo "$HTML" | grep -c 'scenario-card' 2>/dev/null || echo "0")
HTML_JS=$(echo "$HTML" | grep -c 'function switchTab' 2>/dev/null || echo "0")
HTML_TABS=$(echo "$HTML" | grep -c 'tab-content' 2>/dev/null || echo "0")
check "시나리오 카드 렌더링 (${HTML_CARDS}개)" "$([ "$HTML_CARDS" -ge 10 ] && echo true || echo false)"
check "JavaScript 포함" "$([ "$HTML_JS" -ge 1 ] && echo true || echo false)"
check "탭 구조 렌더링" "$([ "$HTML_TABS" -ge 3 ] && echo true || echo false)"

# ── Summary ──
echo -e "\n${CYAN}═══════════════════════════════════════${NC}"
TOTAL=$((PASS + FAIL))
if [ "$FAIL" -eq 0 ]; then
  echo -e "${GREEN}✅ ALL PASS: ${PASS}/${TOTAL} 검증 통과${NC}"
else
  echo -e "${RED}❌ ${FAIL} FAILED: ${PASS}/${TOTAL} 통과, ${FAIL}/${TOTAL} 실패${NC}"
fi
echo -e "${CYAN}═══════════════════════════════════════${NC}"

exit $FAIL
