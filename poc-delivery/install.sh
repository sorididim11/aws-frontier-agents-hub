#!/bin/bash
# DevOps Agent Builder — 설치 스크립트
# Kiro CLI에 3-Agent 시스템(Supervisor + Builder + Verifier) 배포

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KIRO_AGENTS_DIR="$HOME/.kiro/agents"

echo "🏗️  DevOps Agent Builder 설치"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 1. Kiro agents 디렉토리 확인
if [ ! -d "$KIRO_AGENTS_DIR" ]; then
  echo "❌ ~/.kiro/agents 디렉토리가 없습니다. Kiro CLI가 설치되어 있는지 확인하세요."
  exit 1
fi

# 2. Agent JSON 배포
echo "📦 Agent 배포 (3개)..."
cp "$SCRIPT_DIR/agents/devops-agent-supervisor.json" "$KIRO_AGENTS_DIR/"
cp "$SCRIPT_DIR/agents/devops-agent-builder.json" "$KIRO_AGENTS_DIR/"
cp "$SCRIPT_DIR/agents/devops-agent-verifier.json" "$KIRO_AGENTS_DIR/"
echo "   ✓ devops-agent-supervisor (오케스트레이터)"
echo "   ✓ devops-agent-builder (코드 생성)"
echo "   ✓ devops-agent-verifier (팩트체크)"

# 3. 프로젝트에 Skills + Steering 배포
TARGET_DIR="${1:-.}"
echo ""
echo "📚 Skills + Steering 배포 → $TARGET_DIR"

mkdir -p "$TARGET_DIR/.kiro/steering"
mkdir -p "$TARGET_DIR/skills"

cp "$SCRIPT_DIR/.kiro/steering/devops-agent-expert.md" "$TARGET_DIR/.kiro/steering/"
cp -r "$SCRIPT_DIR/skills/"* "$TARGET_DIR/skills/"

echo "   ✓ .kiro/steering/devops-agent-expert.md"
echo "   ✓ skills/devops-agent-reference/"
echo "   ✓ skills/devops-agent-theory/"
echo "   ✓ skills/connect-gitlab-private/"
echo "   ✓ skills/connect-splunk/"
echo "   ✓ skills/verify-agent/"

# 4. 검증
echo ""
echo "🔍 Agent 검증..."
kiro-cli agent validate --path "$KIRO_AGENTS_DIR/devops-agent-supervisor.json" 2>/dev/null && echo "   ✓ supervisor valid"
kiro-cli agent validate --path "$KIRO_AGENTS_DIR/devops-agent-builder.json" 2>/dev/null && echo "   ✓ builder valid"
kiro-cli agent validate --path "$KIRO_AGENTS_DIR/devops-agent-verifier.json" 2>/dev/null && echo "   ✓ verifier valid"

# 5. 완료
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ 설치 완료!"
echo ""
echo "사용법:"
echo "  cd $TARGET_DIR"
echo "  kiro-cli chat --agent devops-agent-supervisor"
echo ""
echo "테스트:"
echo '  "DevOps Agent란 뭐야?"'
echo '  "Private GitLab 연결해줘"'
echo '  "Splunk Cloud 붙여줘"'
echo ""
