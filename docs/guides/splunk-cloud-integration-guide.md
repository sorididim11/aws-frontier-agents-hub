# Splunk Cloud + AWS DevOps Agent 통합 가이드

## 개요

AWS DevOps Agent에 Splunk Cloud를 옵저버빌리티 소스로 연결하는 가이드.
Splunk Cloud = Public SaaS. Private Connection 불필요.

---

## 사전 요구사항

### Splunk Cloud 측

1. **Splunk MCP Server 앱 설치**
   - Splunk Cloud 콘솔 → Apps → Find More Apps → "MCP Server" 검색
   - Splunkbase App #7931 설치 (splunk.com 계정 로그인 필요)

2. **MCP 토큰 생성**
   - 설치된 MCP Server 앱 진입
   - 토큰 생성 (audience=`mcp`)

### 확인된 엔드포인트 형식

| 엔드포인트 | 포트 | 외부 접근 | 용도 |
|------------|------|-----------|------|
| `https://<DEPLOYMENT>.splunkcloud.com:443/en-US/splunkd/__raw/services/mcp` | 443 | 가능 | **DevOps Agent 연결용 (이것 사용)** |
| `https://<DEPLOYMENT>.splunkcloud.com:8089/services/mcp` | 8089 | 차단됨 | 로컬 MCP 클라이언트용 (npx mcp-remote) |

> ⚠️ AWS 공식 문서의 `https://<DEPLOYMENT>.api.scs.splunk.com/<DEPLOYMENT>/mcp/v1/` 형식은 DNS 미존재. 사용하지 말것.

---

## CloudFormation 배포

### 리소스 구조

```yaml
# 1. Service 등록
ServiceSplunkCloud:
  Type: AWS::DevOpsAgent::Service
  Properties:
    ServiceType: mcpserversplunk
    ServiceDetails:
      MCPServerSplunk:
        Name: splunk-cloud
        Endpoint: https://<DEPLOYMENT>.splunkcloud.com:443/en-US/splunkd/__raw/services/mcp
        AuthorizationConfig:
          BearerToken:
            TokenName: splunk-cloud-token
            TokenValue: <JWT_TOKEN>

# 2. Agent Space에 연결
AssociationSplunkCloud:
  Type: AWS::DevOpsAgent::Association
  DependsOn:
  - ServiceSplunkCloud
  Properties:
    AgentSpaceId: <AGENT_SPACE_ID>
    ServiceId: !GetAtt ServiceSplunkCloud.ServiceId
    Configuration:
      MCPServerSplunk:
        Name: splunk-cloud
        EnableWebhookUpdates: true
```

### Association Configuration 스키마 (MCPServerSplunk)

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| Name | string | 아니오 | MCP 서버 이름 (`^[a-zA-Z0-9_-]+$`) |
| Endpoint | string | 아니오 | MCP 서버 엔드포인트 URL |
| Description | string | 아니오 | 설명 |
| EnableWebhookUpdates | boolean | 아니오 | Webhook 알림 활성화 |

> 주의: `ServiceInstanceId`는 이 스키마에 존재하지 않음. 다른 서비스 타입(GitLab 등)과 다름.

---

## Wizard 입력 항목

| 필드 | 설명 | 예시 |
|------|------|------|
| Deployment Name | Splunk Cloud 배포 이름 (로그인 URL에서 확인) | `splunk-deployment-name` |
| MCP Token | MCP Server 앱에서 생성한 JWT 토큰 (audience=mcp) | `eyJraWQ...` |

엔드포인트는 Deployment Name으로부터 자동 생성:
`https://{deployment}.splunkcloud.com:443/en-US/splunkd/__raw/services/mcp`

---

## 텔레메트리 수집 (OTEL Instrumentation)

DevOps Agent가 Splunk에서 데이터를 조회하려면 앱에서 Splunk Cloud로 텔레메트리를 보내야 함.

### Splunk OTEL Collector 설치 (Helm)

```bash
helm repo add splunk-otel-collector-chart https://signalfx.github.io/splunk-otel-collector-chart
helm repo update

helm install splunk-otel-collector splunk-otel-collector-chart/splunk-otel-collector \
  --namespace splunk-otel --create-namespace \
  --set cloudProvider=aws \
  --set distribution=eks \
  --set clusterName=<CLUSTER_NAME> \
  --set splunkPlatform.endpoint=https://<DEPLOYMENT>.splunkcloud.com:8088 \
  --set splunkPlatform.token=<HEC_TOKEN> \
  --set splunkPlatform.index=main \
  --set splunkPlatform.insecureSkipVerify=true \
  --set splunkPlatform.metricsEnabled=true \
  --set splunkPlatform.metricsIndex=main \
  --set splunkPlatform.logsEnabled=true \
  --set clusterReceiver.enabled=false \
  --set environment=frontier-agent-hub
```

> ⚠️ **필수 설정:**
> - `splunkPlatform.insecureSkipVerify=true`: Splunk Cloud HEC 인증서가 CN/SAN 미설정 이슈 있음
> - `clusterReceiver.enabled=false`: EC2 IMDS 의존으로 CrashLoopBackOff 발생 방지
> - `splunkObservability.*` 미설정: Splunk Observability Cloud (별도 제품) 연동 시에만 필요

### HEC 토큰 생성 (Splunk Cloud)

1. Splunk Cloud 콘솔 → Settings → Data Inputs → HTTP Event Collector
2. New Token → Name: `otel-collector`, Default Index: `main`
3. 생성된 토큰을 `<HEC_TOKEN>`에 사용

### HEC 연결 검증

```bash
curl -sk -X POST "https://<DEPLOYMENT>.splunkcloud.com:8088/services/collector" \
  -H "Authorization: Splunk <HEC_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"event":"test","sourcetype":"_json","index":"main"}'
# 기대 응답: {"text":"Success","code":0}
```

### Java 앱 인스트루멘테이션 (Petclinic 예시)

```bash
# OTEL Java auto-instrumentation 활성화
kubectl patch deployment petclinic -n petclinic --type=json -p='[
  {"op":"replace","path":"/spec/template/metadata/annotations/instrumentation.opentelemetry.io~1inject-java","value":"true"}
]'

# Pod 재시작 (OTEL init container 주입을 위해 필수)
kubectl rollout restart deployment petclinic -n petclinic
```

### OTEL Collector 상태 확인

```bash
# Pod 상태
kubectl get pods -n splunk-otel

# 에러 로그 확인 (에러 없으면 정상)
kubectl logs -n splunk-otel -l app=splunk-otel-collector --tail=50 | grep -i error
```

---

## 검증

### 1. MCP 연결 테스트

```bash
curl -s -X POST "https://<DEPLOYMENT>.splunkcloud.com:443/en-US/splunkd/__raw/services/mcp" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <TOKEN>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
```

기대 응답:
```json
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18","capabilities":{"tools":{}},"serverInfo":{"name":"Splunk_MCP_Server","version":"1.1.3"}}}
```

### 2. DevOps Agent에서 Splunk 도구 확인

Agent Space → Capabilities → Telemetry에서 Splunk 활성화 확인.
Investigation 시작 시 Splunk MCP 도구 사용 가능:
- 검색 실행 (SPL)
- 지식 객체 탐색
- AI 기반 SPL 생성

---

## 트러블슈팅

### MCP 연결 (DevOps Agent)

| 증상 | 원인 | 해결 |
|------|------|------|
| "MCP Server not reachable" | 엔드포인트 URL 틀림 | 포트 443 + `/en-US/splunkd/__raw/services/mcp` 경로 사용 |
| "not reachable" (8089) | Splunk Cloud 8089 외부 차단 | 443 엔드포인트로 변경 |
| DNS 해석 실패 (`api.scs.splunk.com`) | 레거시/미존재 도메인 | `splunkcloud.com:443` 경로 사용 |
| Association validation 실패 | `ServiceInstanceId` 사용 | `MCPServerSplunk` 스키마는 Name/EnableWebhookUpdates만 허용 |
| 토큰 인증 실패 | audience 불일치 | audience=`mcp` 토큰 재발급 |

### OTEL Collector

| 증상 | 원인 | 해결 |
|------|------|------|
| `x509: certificate is not valid for any names` | Splunk Cloud HEC 인증서 CN/SAN 미설정 | `splunkPlatform.insecureSkipVerify=true` |
| Cluster Receiver CrashLoopBackOff | EC2 IMDS role 없음 (resourcedetection) | `clusterReceiver.enabled=false` |
| Agent pod Pending (node 부족) | DaemonSet이 모든 노드에 배포 시도 | 필요한 노드만 Running이면 정상 |
| 에러 없지만 데이터 미수신 | HEC 토큰/인덱스 불일치 | curl로 HEC 직접 테스트 확인 |

---

## 테스트 환경 정보

| 항목 | 값 |
|------|-----|
| Account | 111111111111 (member1) |
| Agent Space ID | aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee |
| Splunk Deployment | splunk-deployment-name |
| Stack Name | devops-splunk-cloud-test |
| 상태 | CREATE_COMPLETE ✓ |
