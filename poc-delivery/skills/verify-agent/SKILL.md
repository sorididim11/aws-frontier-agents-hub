---
name: verify-agent
description: >
  배포 후 검증 및 트러블슈팅 워크플로우.
  "확인해줘", "테스트", "안 돼요" 요청 시 적용.
---

# Agent 검증 워크플로우

## 검증 순서

### 1. Space 상태

```bash
aws devops-agent get-agent-space --agent-space-id {ID} \
  --profile {PROFILE} --region {REGION} --no-cli-pager
```

기대: `ACTIVE`

### 2. Association 상태

```bash
aws devops-agent list-associations --agent-space-id {ID} \
  --profile {PROFILE} --region {REGION} --no-cli-pager
```

모든 Association이 `ACTIVE`인지 확인.

### 3. Agent 대화 테스트

```bash
aws bedrock-agent-runtime invoke-agent \
  --agent-id {AGENT_ID} --agent-alias-id TSTALIASID \
  --session-id verify-001 \
  --input-text "연결된 데이터소스를 확인해주세요" \
  --profile {PROFILE} --region {REGION} --no-cli-pager
```

## 진단 트리

```
Association FAILED
├─ gitlab: "timeout" → SG 인바운드 / PC 프로비저닝 대기
├─ gitlab: "401" → Token scope (read_api 필요)
├─ mcpserversplunk: "refused" → 포트 8089 사용 (443으로 변경)
├─ mcpserversplunk: "401" → 토큰 audience (mcp 필요)
├─ aws: "access denied" → Trust Policy (devops-agent.amazonaws.com)
└─ aws: "no resources" → App 태그 미부착
```

## 트러블슈팅 표

| 증상 | 원인 | 해결 |
|------|------|------|
| Agent 응답 없음 | Space INACTIVE | Space 재생성 |
| K8s 접근 불가 | EKS Access Entry 없음 | Association 삭제→재생성 |
| cross-account 실패 | Aws 사용 | SourceAws로 변경 |
| "no resources" | App 태그 없음 | 리소스에 태그 부착 |
