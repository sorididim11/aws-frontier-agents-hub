#!/usr/bin/env bash
# deploy-skill.sh — Agent Space 스킬을 채팅 API로 배포
# Usage: ./scripts/deploy-skill.sh <skill-dir> [--verify]
#   skill-dir: skills/ 하위 디렉토리 (예: skills/arch-discover)
#   --verify:  업데이트 후 get_skill로 내용 검증
#
# 환경변수:
#   SPACE_ID    — Agent Space ID (기본: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee)
#   AWS_PROFILE — boto3 프로필 (기본: member1-acc)
#   AWS_REGION  — 리전 (기본: us-east-1)

set -euo pipefail

SKILL_DIR="${1:?Usage: $0 <skill-dir> [--verify]}"
VERIFY="${2:-}"
SPACE_ID="${SPACE_ID:-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}"
AWS_PROFILE="${AWS_PROFILE:-member1-acc}"
AWS_REGION="${AWS_REGION:-us-east-1}"

BUILD_SCRIPT="${SKILL_DIR}/build.sh"
if [[ -f "$BUILD_SCRIPT" ]]; then
  echo "  Building from src/..."
  bash "$BUILD_SCRIPT"
fi

SKILL_FILE="${SKILL_DIR}/SKILL.md"
if [[ ! -f "$SKILL_FILE" ]]; then
  echo "ERROR: $SKILL_FILE not found" >&2
  exit 1
fi

SKILL_NAME=$(grep -m1 '^name:' "$SKILL_FILE" | sed 's/name: *//')
echo "=== Deploying skill: $SKILL_NAME ==="
echo "  Source: $SKILL_FILE"
echo "  Space:  $SPACE_ID"
echo "  Profile: $AWS_PROFILE"

PYTHONUNBUFFERED=1 python3 - "$SKILL_FILE" "$SPACE_ID" "$AWS_PROFILE" "$AWS_REGION" "$SKILL_NAME" "$VERIFY" << 'PYTHON'
import boto3, sys, json

skill_file = sys.argv[1]
space_id = sys.argv[2]
profile = sys.argv[3]
region = sys.argv[4]
skill_name = sys.argv[5]
verify = sys.argv[6] == "--verify"

with open(skill_file, 'r') as f:
    skill_content = f.read()

print(f"  Content: {len(skill_content)} chars")

session = boto3.Session(profile_name=profile, region_name=region)
client = session.client('devops-agent')

def send_and_collect(exec_id, content):
    resp = client.send_message(
        agentSpaceId=space_id,
        executionId=exec_id,
        content=content
    )
    event_stream = resp.get('events')
    full_text = ''
    for event in event_stream:
        if 'contentBlockDelta' in event:
            delta = event['contentBlockDelta'].get('delta', {})
            if 'textDelta' in delta:
                full_text += delta['textDelta'].get('text', '')
    return full_text

# Step 1: Create chat session
print("\n[1/3] Creating chat session...")
chat = client.create_chat(agentSpaceId=space_id)
exec_id = chat['executionId']
print(f"  Session: {exec_id[:12]}...")

# Step 2: Update skill via manage-skills
print(f"\n[2/3] Updating skill '{skill_name}'...")
update_msg = f"""manage-skills 스킬을 사용해서 '{skill_name}' 스킬을 아래 내용으로 업데이트해줘.
knowledge_item_id를 먼저 list_skills로 찾은 후 update_skill을 실행해.

```markdown
{skill_content}
```"""

text = send_and_collect(exec_id, update_msg)

# Check for success indicators
if '업데이트' in text and ('성공' in text or '버전' in text or 'ACTIVE' in text):
    print("  ✓ Update successful")
    # Extract version if present
    if '버전' in text:
        for line in text.split('\n'):
            if '버전' in line:
                print(f"  {line.strip()}")
                break
else:
    print("  ✗ Update may have failed")
    print(f"  Response: {text[:500]}")
    sys.exit(1)

# Step 3: Verify (optional)
if verify:
    print(f"\n[3/3] Verifying update...")
    verify_text = send_and_collect(exec_id,
        f"get_skill로 '{skill_name}' 스킬을 읽어서 boundary_nodes 또는 핵심 키워드가 있는지 확인해줘. 버전 번호도 알려줘."
    )
    if 'boundary_nodes' in verify_text or '버전' in verify_text:
        print("  ✓ Verification passed")
    else:
        print("  ⚠ Could not verify content")
        print(f"  Response: {verify_text[:300]}")
else:
    print("\n[3/3] Skipping verification (use --verify to enable)")

print(f"\n=== Done ===")
PYTHON
