---
name: connect-splunk
description: >
  Splunk Cloud를 DevOps Agent에 연결하는 워크플로우.
  MCP 프로토콜 기반. Private Connection 불필요.
---

# Splunk Cloud 연결 워크플로우

## 필요 정보

| 항목 | 필수 | 예시 |
|------|------|------|
| Splunk Cloud Deployment 이름 | ✓ | my-org |
| MCP JWT 토큰 (audience=mcp) | ✓ | eyJhbG... |
| Agent Space ID | ✓ | (이전 단계) |
| AWS Profile / Region | ✓ | my-profile / us-east-1 |

## 워크플로우

### Step 1: 엔드포인트 구성

```
https://{DEPLOYMENT}.splunkcloud.com:443/en-US/splunkd/__raw/services/mcp
```

**CRITICAL:**
- 포트 443 + 전체 경로 필수
- 8089 사용 금지 (외부 차단)
- api.scs.splunk.com 사용 금지 (DNS 미존재)

### Step 2: 사전 테스트

```bash
curl -sk -H "Authorization: Bearer {TOKEN}" \
  "https://{DEPLOYMENT}.splunkcloud.com:443/en-US/splunkd/__raw/services/mcp" \
  -o /dev/null -w "%{http_code}"
```

200 또는 405 → 정상

### Step 3: CFn 템플릿

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: DevOps Agent - Splunk Cloud MCP Connection

Parameters:
  ProjectName:
    Type: String
  AgentSpaceId:
    Type: String
  SplunkDeployment:
    Type: String
  SplunkToken:
    Type: String
    NoEcho: true

Resources:
  ServiceSplunk:
    Type: AWS::DevOpsAgent::Service
    Properties:
      ServiceType: mcpserversplunk
      ServiceDetails:
        MCPServerSplunk:
          Name: !Sub "${ProjectName}-splunk"
          Endpoint: !Sub "https://${SplunkDeployment}.splunkcloud.com:443/en-US/splunkd/__raw/services/mcp"
          AuthorizationConfig:
            BearerToken:
              TokenName: !Sub "${ProjectName}-splunk-token"
              TokenValue: !Ref SplunkToken

  AssociationSplunk:
    Type: AWS::DevOpsAgent::Association
    DependsOn: ServiceSplunk
    Properties:
      AgentSpaceId: !Ref AgentSpaceId
      ServiceId: !GetAtt ServiceSplunk.ServiceId
      Configuration:
        MCPServerSplunk:
          Name: !Sub "${ProjectName}-splunk"
          EnableWebhookUpdates: true

Outputs:
  ServiceId:
    Value: !GetAtt ServiceSplunk.ServiceId
  AssociationId:
    Value: !Ref AssociationSplunk
```

### Step 4: 배포

```bash
aws cloudformation deploy \
  --template-file splunk-connection.yaml \
  --stack-name {PROJECT_NAME}-splunk-connection \
  --parameter-overrides SplunkToken="{TOKEN}" \
  --profile {PROFILE} --region {REGION} --no-cli-pager
```

### Step 5: 검증

```bash
aws devops-agent list-associations --agent-space-id {ID} \
  --profile {PROFILE} --region {REGION} --no-cli-pager \
  | jq '.associations[] | select(.serviceType=="mcpserversplunk")'
```

## 핵심 규칙

1. **Service + Association 함께 생성** — 단독 금지
2. **포트 443 + 전체 경로** — 도메인만 쓰면 실패
3. **토큰 audience=mcp** — 다른 audience면 401
4. **Private Connection 불필요** — Public SaaS
5. **ServiceInstanceId 없음** — MCPServerSplunk에는 이 필드 없음
