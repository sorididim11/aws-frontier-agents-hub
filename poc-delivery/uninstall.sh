#!/bin/bash
# DevOps Agent Builder — 제거 스크립트

set -e

KIRO_AGENTS_DIR="$HOME/.kiro/agents"

echo "🗑️  DevOps Agent Builder 제거"
echo ""

rm -f "$KIRO_AGENTS_DIR/devops-agent-supervisor.json"
rm -f "$KIRO_AGENTS_DIR/devops-agent-builder.json"
rm -f "$KIRO_AGENTS_DIR/devops-agent-verifier.json"

echo "✓ Agent 3개 제거 완료"
echo ""
echo "프로젝트의 .kiro/steering/ 과 skills/ 는 수동으로 삭제하세요."
