---
name: devops-agent-reference
description: >
  AWS DevOps Agent 공식 CloudFormation 리소스 스키마 레퍼런스.
  코드 생성과 검증 시 이 스키마를 기준으로 한다.
  불확실하면 @awsknowledge로 최신 문서를 조회한다.
---

# DevOps Agent — 공식 리소스 레퍼런스

CFn 템플릿 생성/검증 시 이 스키마를 기준으로 한다.
여기에 없는 속성은 @awsknowledge로 조회하여 확인한다.

---

## AWS::DevOpsAgent::AgentSpace

```yaml
Type: AWS::DevOpsAgent::AgentSpace
Properties:
  Name: String                # Required.
  Description: String         # Optional.
```

Return: `!Ref` → AgentSpaceId

---

## AWS::DevOpsAgent::PrivateConnection

**Amazon VPC Lattice 기반 리소스 게이트웨이.**
Agent가 VPC 내부 리소스에 접근하기 위해 서브넷에 ENI를 배치한다.

두 가지 모드:
- **service-managed**: AWS가 리소스 게이트웨이를 관리
- **self-managed**: 고객이 직접 관리

```yaml
Type: AWS::DevOpsAgent::PrivateConnection
Properties:
  Name: String                # Required.
  VpcId: String               # Required.
  SubnetIds:                  # Required. 2개 이상 권장.
    - String
  SecurityGroupIds:           # Required.
    - String
```

Return: `!GetAtt PrivateConnection.Name`, `!GetAtt PrivateConnection.Status`

**핵심:** VPC Lattice 기반. NLB가 아님. Agent ENI가 직접 대상에 접근.

---

## AWS::DevOpsAgent::Service

```yaml
Type: AWS::DevOpsAgent::Service
Properties:
  ServiceType: String         # Required. gitlab | github | slack | mcpserversplunk | mcpserver | dynatrace | servicenow | pagerduty
  ServiceDetails:             # Required.
    <ServiceTypeDetails>
  PrivateConnectionName: String  # Optional. Private Connection 참조.
```

Return: `!GetAtt Service.ServiceId`

### ServiceDetails: GitLab

```yaml
ServiceDetails:
  GitLab:
    Name: String              # Required.
    TargetUrl: String         # Required. GitLab 서버 URL.
    TokenType: String         # Required. "personal" | "group" | "project"
    Token: String             # Required. Personal Access Token.
```

### ServiceDetails: MCPServerSplunk

```yaml
ServiceDetails:
  MCPServerSplunk:
    Name: String              # Required.
    Endpoint: String          # Required. MCP 엔드포인트 URL.
    AuthorizationConfig:      # Required.
      BearerToken:
        TokenName: String         # Required.
        TokenValue: String        # Required. JWT 토큰.
```

---

## AWS::DevOpsAgent::Association

```yaml
Type: AWS::DevOpsAgent::Association
Properties:
  AgentSpaceId: String        # Required.
  ServiceId: String           # Required.
  LinkedAssociationIds:       # Optional.
    - String
  Configuration:              # Required. 아래 중 정확히 하나.
    <ServiceConfiguration>
```

Return: `!Ref` → AssociationId

---

## Configuration 타입별

### Aws (Primary 모니터 계정)

```yaml
Configuration:
  Aws:
    AccountId: String             # Required. 12자리.
    AccountType: String           # Required. "monitor"
    AssumableRoleArn: String      # Required.
    Resources:                    # Optional.
      - ResourceArn: String
        ResourceType: String      # AWS::CloudFormation::Stack | AWS::ECR::Repository | AWS::S3::Bucket | AWS::S3::Object
        ResourceMetadata: Json
    Tags:                         # Optional. 리소스 스코핑.
      - Key: String
        Value: String
```

### SourceAws (Secondary/Cross-account)

**CRITICAL: 추가 계정은 반드시 SourceAws. Aws 아님!**

```yaml
Configuration:
  SourceAws:
    AccountId: String             # Required. 12자리.
    AccountType: String           # Required. "source"
    AssumableRoleArn: String      # Required.
    Resources:                    # Optional.
      - ResourceArn: String
        ResourceType: String
        ResourceMetadata: Json
    Tags:                         # Optional.
      - Key: String
        Value: String
```

### GitLab

```yaml
Configuration:
  GitLab:
    ProjectId: String             # Required. 숫자 프로젝트 ID.
    ProjectPath: String           # Required. "namespace/project-name"
    InstanceIdentifier: String    # Optional.
    EnableWebhookUpdates: Boolean # Optional.
```

### MCPServerSplunk

```yaml
Configuration:
  MCPServerSplunk:
    Name: String                  # Optional.
    Endpoint: String              # Optional.
    Description: String           # Optional.
    EnableWebhookUpdates: Boolean # Optional.
```

**CRITICAL:**
- ServiceInstanceId 필드 없음 (다른 서비스와 다름)
- Endpoint 포트 443만: `https://{deployment}.splunkcloud.com:443/en-US/splunkd/__raw/services/mcp`

---

## 알려진 공식 문서 오류 (실전 검증됨)

아래 정보는 AWS 공식 문서에 존재하나 실제로는 동작하지 않음:

| 문서 내용 | 실제 | 검증 방법 |
|-----------|------|-----------|
| `https://{deployment}.api.scs.splunk.com/{deployment}/mcp/v1/` | DNS 미존재, 연결 불가 | `nslookup {deployment}.api.scs.splunk.com` → NXDOMAIN |
| Splunk 포트 8089 | 외부 차단됨 | `curl https://{deployment}.splunkcloud.com:8089` → timeout |

**검증 시 이 테이블과 충돌하는 정보가 awsknowledge에서 나오면 FAIL로 판정한다.**
올바른 Splunk 엔드포인트: `https://{deployment}.splunkcloud.com:443/en-US/splunkd/__raw/services/mcp`

### GitHub

```yaml
Configuration:
  GitHub:
    RepoName: String              # Required.
    RepoId: String                # Required. 숫자.
    Owner: String                 # Required.
    OwnerType: String             # Required. "organization" | "user"
```

### Slack

```yaml
Configuration:
  Slack:
    WorkspaceId: String           # Required. Pattern: ^[TE][A-Z0-9]+$
    WorkspaceName: String         # Required.
    TransmissionTarget:
      IncidentResponseTarget:
        ChannelId: String         # Required. Pattern: ^[CGD][A-Z0-9]+$
        ChannelName: String       # Optional.
```

---

## IAM Trust Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "devops-agent.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
```

---

## 리소스 의존 순서

```
IAM Role → Agent Space → Private Connection → Service → Association
```

DependsOn 필수.

---

## 지원 데이터소스 전체 목록

Aws, SourceAws, GitHub, GitLab, Slack, MCPServer, MCPServerSplunk, MCPServerDatadog, MCPServerNewRelic, MCPServerGrafana, MCPServerSigV4, Dynatrace, ServiceNow, PagerDuty, EventChannel, Azure
