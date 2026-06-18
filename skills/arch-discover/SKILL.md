---
name: arch-discover
description: >
  아키텍처 분석 요청 시 적용. #arch-q1 또는 #arch-q2 트리거를 보내면
  발견 전략(태그+흐름 기반 소속 판단)에 따라 앱의 서비스 토폴로지를 탐색하고,
  표준 JSON 포맷으로 앱 식별(Q1) 또는 서비스 상세(Q2) 데이터를 구조화하여 응답한다.
agent_types:
  - Generic
version: "3.0"
---

# arch-discover — 아키텍처 분석 스킬

앱에서 `#arch-q1` 또는 `#arch-q2` 트리거를 보내면, 이 스킬의 포맷과 규칙에 따라 응답합니다.

## 트리거

| 트리거 | 목적 | 참조 섹션 |
|--------|------|-----------|
| `#arch-q1` | 환경의 앱 식별 | Q1 출력 포맷 |
| `#arch-q2 {app_id} {app_name}` | 특정 앱의 서비스 상세 | Q2 출력 포맷 |

## 공통 규칙
- 모든 description, role, name 필드는 **한국어** 작성
- JSON은 렌더링에 직접 사용되므로 **형식을 정확히** 따를 것
- `\`\`\`json` 코드블록 안에 JSON 작성
- 응답 전 반드시 가드레일 규칙과 검증 체크리스트를 확인할 것
## Q1: 앱 식별 — 출력 포맷

### 역할
이 환경에서 관리되는 애플리케이션 목록, 앱 간 관계, 비즈니스 워크플로우를 식별합니다.

### Q1 출력 포맷
```json
{
  "system_name": "환경 이름",
  "description": "전체 환경의 비즈니스 목적과 핵심 E2E 흐름을 포함한 2~3문장 (한국어)",
  "apps": [
    {
      "id": 1,
      "name": "앱제품명",
      "description": "앱 목적 (한국어)",
      "classification_criteria": "이 앱에 속하는 서비스와 그 이유 (한국어)"
    }
  ],
  "app_edges": [
    {
      "source": "앱이름1",
      "target": "앱이름2",
      "description": "관계 설명 — 이벤트 기반 연결은 트리거→목적지 포함 (한국어)"
    }
  ],
  "app_workflows": [
    {
      "name": "E2E 워크플로우 제목 (한국어)",
      "hops": ["앱이름1", "앱이름2", "앱이름3"]
    }
  ],
  "tag_gaps": [
    {
      "resource": "리소스명",
      "expected_app": "속해야 할 앱",
      "current_tag": "현재 태그 또는 null",
      "impact": "이 갭이 흐름에 미치는 영향 (한국어)"
    }
  ]
}
```

### Q1 필드 규칙
- **필수 필드**: system_name, description, apps, app_edges
- **앱 필수 필드**: id, name, description
- **앱 이름** = 리소스에 붙은 App 태그 값을 그대로 사용. 태그가 없으면 제품/프로젝트 고유 이름.
- 앱 전용 데이터 저장소, 큐, 알람은 해당 앱 그룹에 포함
- 운영 파이프라인(알람→알림→조사)은 해당 앱에 포함하거나, 여러 앱을 서빙하면 별도 앱으로 분리
- app_edges: source = 시작하는 앱, target = 받는 앱. 이벤트 기반 서비스가 연결하면 트리거 소스 앱 → 목적지 앱으로 edge 생성.
- app_workflows: 연결된 E2E 흐름은 하나의 워크플로우로 통합. hops = 비즈니스 흐름이 통과하는 앱 이름의 순서 배열. 태그 경계로 쪼개지 않음.
- tag_gaps: 1단계 흐름에는 있지만 2단계 태그 매핑에서 누락/끊어지는 리소스. 갭 없으면 빈 배열.
- 각 앱에 숫자 id 부여 (1부터 시작, Q2 참조용)
## Q2: 앱별 서비스 상세 — 출력 포맷

### 역할
특정 앱의 **모든 실행 단위**를 빠짐없이 발견하고, 연결 관계와 워크플로우를 graph 포맷으로 수집합니다.

### Q2 출력 포맷
```json
{
  "app_name": "{app_name}",
  "nodes": [
    {
      "name": "고유-kebab-case-id",
      "namespace": "",
      "kind": "Amazon EKS Deployment",
      "service_type": "app",
      "group": "{app_name}",
      "labels": { "role": "역할 설명 (한국어)" },
      "ports": [8080]
    }
  ],
  "boundary_nodes": [
    {
      "name": "외부-앱-이름-kebab",
      "app_name": "외부 앱의 App 태그 이름",
      "kind": "External App",
      "labels": { "role": "이 앱과의 관계 설명 (한국어)" },
      "connection_point": "이 앱에서 연결이 나가는/들어오는 내부 서비스 name"
    }
  ],
  "edges": [
    {
      "source": "호출자-서비스명-또는-경계노드명",
      "target": "대상-서비스명-또는-경계노드명",
      "protocol": "http",
      "port": 80,
      "paths": ["/path"],
      "description": "목적 (한국어)"
    }
  ],
  "workflows": [
    {
      "name": "워크플로우 제목 (한국어)",
      "hops": [
        { "from": "서비스-a", "to": "서비스-b" },
        { "from": "서비스-b", "to": "경계노드명" }
      ]
    }
  ]
}
```

### Node 필드 규칙
| 필드 | 규칙 |
|------|------|
| name | 고유 kebab-case 식별자 |
| namespace | K8s 워크로드: `""`, AWS 관리형: `"managed"` |
| kind | `"<AWS 서비스> <리소스 타입>"` (예: "Amazon EKS Deployment", "Amazon DynamoDB Table", "AWS Lambda Function", "Amazon CloudWatch Alarm", "Amazon SNS Topic") |
| service_type | 비즈니스 역할만: `app`, `cache`, `db`, `gateway`, `queue`, `worker`, `observe`, `ops`, `platform`. **"managed" 사용 금지** — kind에서 유추 가능 |
| group | 이 앱 내부 서비스는 모두 `"{app_name}"` |
| labels.role | 서비스 목적 (한국어) |
| ports | 리스닝 포트 배열. 해당 없으면 `[]` |

### Boundary Node 규칙
- 다른 앱과의 접점. 해당 앱 전체를 1개 노드로 축약.
- `app_name`: 해당 외부 앱의 App 태그 이름 (사용자가 제공한 앱 목록과 정확히 일치).
- `connection_point`: 이 앱 내부에서 외부 앱으로 연결되는 서비스의 name.
- boundary_node는 edge의 source 또는 target으로 사용 가능 (incoming/outgoing 모두).

### Edge 규칙
- source = 데이터를 시작/전송하는 서비스, target = 받는 서비스 (boundary_node 포함)
- protocol: `http`, `https`, `tcp`, `grpc`, `sns`, `eventbridge`, `redis`

### Workflow 규칙
- hops[].from/to = nodes[].name 또는 boundary_nodes[].name과 정확히 일치
- 각 hop = 실제 네트워크 호출 또는 이벤트 1건
- 배열 순서 = 실행 시간 순서
- 워크플로우가 외부 앱에서 시작하면 boundary_node에서 시작
- 워크플로우가 외부 앱으로 나가면 boundary_node에서 끝남

### Q2 필수 필드
- **필수**: app_name, nodes, edges
- **노드 필수**: name, namespace, kind, service_type, group, labels
- **Boundary 노드 필수**: name, app_name, kind, labels, connection_point
- **Edge 필수**: source, target, description
## Q2: 발견 전략

`#arch-q2 {app_id} {app_name}` 트리거를 받으면 아래 원칙에 따라 탐색하세요.

### 핵심 원칙
**앱 경계(App boundary)에서 멈추세요.** 이 앱에 속하는 서비스와 리소스만 상세히 분석합니다. 다른 앱의 리소스를 만나면 **boundary_nodes 1개**로 표현하고 내부를 추적하지 마세요.

이 앱의 **모든 비즈니스 흐름을 발견**하고, 각 흐름에 참여하는 실행 단위를 빠짐없이 찾아 연결하세요. 도구 호출은 최소한으로, 그러나 발견은 빠짐없이.

### 앱 소속 판단 기준
1. **태그 기반**: AWS 리소스 태그 `App={app_name}` 일치 → 소속
2. **태그가 없더라도** 유사한 시스템 레이블이 있으면 포함 (예: K8s label `app.kubernetes.io/part-of`, `app`, namespace 연관 등)
3. 위 어느 것에도 해당하지 않지만 비즈니스 흐름에서 도달 가능 → 소속

다른 앱의 태그/label을 가진 리소스 → boundary_node 1개로 축약

### 비즈니스 흐름 발견
이 앱이 처리하는 E2E 흐름을 모두 식별하세요. 각 흐름에서 데이터가 어떤 컴포넌트를 어떤 순서로 통과하는지 추적하고, workflows로 표현하세요.
- 흐름의 시작이 외부에서 오면 → boundary_node에서 시작
- 흐름이 외부로 나가면 → boundary_node에서 종료
- 어떤 워크플로우에도 매핑되지 않는 노드가 있다면, 그 노드를 위한 흐름을 추가하세요

### 출력
본 스킬의 **Q2 출력 포맷**을 정확히 따르세요. nodes, boundary_nodes, edges, workflows 구조를 포함한 JSON을 제공하세요.
## 가드레일 — 절대 위반 금지 규칙

아래 규칙을 위반하면 분석 결과가 무효화됩니다. 응답 생성 시 각 규칙을 확인하세요.

### G1: 양방향 Edge 금지 [CRITICAL]
- A→B edge가 있으면 B→A edge 생성 **절대 금지**
- "응답"은 별도 edge가 아님 — 요청 방향 1개만
- ❌ `cw-alarm → sns-topic` + `sns-topic → cw-alarm`
- ✅ `cw-alarm → sns-topic` (알람이 알림 전송, 단방향)

### G2: 앱 이름에 기술 카테고리 사용 금지 [HIGH]
- 금지 이름: UserManagement, Observability, ChaosEngineering, Monitoring, Security, Networking, Storage, Compute
- ❌ `"name": "Observability"`
- ✅ `"name": "CloudWatchOps"` 또는 App 태그 값 그대로

### G3: K8s Service 리소스 노드 생성 금지 [MEDIUM]
- ClusterIP, LoadBalancer, NodePort Service는 노드가 아님
- Deployment, StatefulSet 등 실제 워크로드만 노드
- ❌ `"kind": "Amazon EKS Service"`
- ✅ `"kind": "Amazon EKS Deployment"`

### G4: 앱 소속 판단 규칙 [CRITICAL]

**우선순위 순:**
1. **App 태그 일치 = 무조건 소속** — 모니터링 대상이 여러 앱이어도, App 태그가 이 앱이면 nodes[]에 포함
2. EKS 워크로드: 같은 앱의 Deployment/StatefulSet이거나 같은 Pod 내 컨테이너 → 소속
3. 태그 없지만 이 앱의 흐름(edge)에서 도달 가능 → 소속
4. **다른 앱의 App 태그** → boundary_node 1개로 축약, 내부 추적 금지
5. 태그 없고 흐름에도 없음 → 무시

- ❌ App 태그가 이 앱인데 "다른 앱도 사용하니까" boundary로 분류
- ❌ 다른 앱의 Deployment, Lambda를 상세 나열
- ✅ App 태그 일치하면 무조건 nodes[]에 포함
- ✅ 다른 앱은 `"kind": "External App"` 경계 노드 1개

### G5: 내부 노드와 Boundary Node 중복 금지 [HIGH]
- 이미 내부 노드(nodes[])로 존재하는 서비스를 boundary_nodes[]에도 추가하지 말 것
- ❌ nodes에 `hasher` + boundary_nodes에도 `hasher`
- ✅ 내부 노드이면 nodes에만

### G6: 이벤트 기반 서비스 방향 규칙 [MEDIUM]
| 관계 | Edge 방향 | 설명 |
|------|-----------|------|
| 알람 → SNS | alarm → sns | 알람이 알림 전송 |
| EventBridge → Lambda | eventbridge → lambda | 이벤트가 Lambda 트리거 |
| Lambda → EventBridge | lambda → eventbridge | Lambda가 이벤트 발행할 때만 |
| App → DB (읽기/쓰기) | app → db | 앱이 DB에 요청 |
| SNS → SQS | sns → sqs | SNS가 SQS로 메시지 전달 |

### G7: service_type "managed" 사용 금지 [LOW]
- 허용값: `app`, `cache`, `db`, `gateway`, `queue`, `worker`, `observe`, `ops`, `platform`
- kind 필드에서 관리형 여부 유추 가능하므로 service_type은 비즈니스 역할만 표현
- ❌ `"service_type": "managed"`
- ✅ `"service_type": "db"` (DynamoDB), `"service_type": "observe"` (CloudWatch)

### G8: 모니터링/소유 관계 ≠ Edge [HIGH]
- "모니터링한다", "참조한다", "소유한다" = edge가 아님
- **실제 데이터가 이동하는 방향만** edge로 표현
- ❌ EKS → CloudWatch (모니터링 관계를 edge로)
- ✅ CloudWatch Agent → CloudWatch (메트릭을 실제 전송하는 Agent가 source)
## 환각 패턴 — 자주 발생하는 AI 실수

아래는 아키텍처 분석 시 AI가 자주 범하는 오류 패턴입니다. 응답 전 해당 패턴에 빠지지 않았는지 확인하세요.

### H1: 존재하지 않는 서비스 생성 [HIGH]
- **증상**: 제공된 태그 목록/도구 결과에 없는 서비스를 "있을 것"이라고 추론하여 nodes에 추가
- **예시**: 태그에 Redis가 없는데 "캐시가 있을 것"이라고 가정하여 ElastiCache 노드 생성
- **교정**: nodes에는 도구 호출 결과 또는 제공된 리소스 목록에서 확인된 것만 포함

### H2: 카테고리를 앱 이름으로 사용 [HIGH]
- **증상**: "Monitoring", "DataPipeline", "Infrastructure" 같은 기술 카테고리를 앱으로 분류
- **예시**: CloudWatch + X-Ray를 "Observability" 앱으로 묶음
- **교정**: 앱 = 독립 배포/운영되는 제품. App 태그 값 사용. G2 참조

### H3: 양방향 연결 생성 [MEDIUM]
- **증상**: A→B 호출 관계에 B→A "응답 반환" edge를 추가하여 양방향으로 만듦
- **예시**: `worker → redis` + `redis → worker` (응답을 별도 edge로)
- **교정**: 요청-응답은 단일 방향 edge. 응답은 별도 데이터 흐름이 아님. G1 참조

### H4: Orphan 노드 생성 [MEDIUM]
- **증상**: 노드를 나열하되 해당 노드로의 edge를 누락하여 고립된 노드 발생
- **예시**: DynamoDB Table 노드는 있지만 어떤 서비스가 접근하는지 edge가 없음
- **교정**: 모든 노드에 최소 1개 edge 필수. 연결을 찾을 수 없으면 분석 누락 의미

### H5: 경계 넘어 내부 탐색 [MEDIUM]
- **증상**: boundary_node 대신 다른 앱의 내부 서비스(Deployment, Lambda, DB)를 상세히 나열
- **예시**: App-B의 Lambda 3개, DynamoDB 2개를 App-A 분석에 nodes로 포함
- **교정**: 다른 앱 = boundary_node 1개로 축약. G4 참조

### H6: 단일 컨테이너 가정 [MEDIUM]
- **증상**: 멀티컨테이너 Pod에서 메인 컨테이너만 보고하고 사이드카(OTEL, envoy, fluentd 등) 누락
- **예시**: Pod에 app + otel-collector 2개 컨테이너인데 app만 노드로 생성
- **교정**: 모든 Pod에서 컨테이너 목록 확인 → 역할이 다르면 별도 노드

### H7: 워크플로우 hop 불일치 [LOW]
- **증상**: workflows[].hops[].from/to에 nodes[].name과 다른 이름 사용
- **예시**: node name이 `hasher`인데 workflow hop에서 `hasher-service`로 참조
- **교정**: hop의 from/to = nodes[].name 또는 boundary_nodes[].name과 정확히 일치
## 응답 전 자기검증 체크리스트

JSON 응답을 생성한 후, 제출 전에 아래 항목을 확인하세요.

### Q1 검증
- [ ] apps 배열에 최소 1개 앱이 있는가
- [ ] 모든 앱 이름이 App 태그 값 또는 제품 고유 이름인가 (기술 카테고리 아닌가 → G2)
- [ ] app_edges에 양방향이 없는가 (A→B와 B→A 동시 존재 → G1)
- [ ] app_workflows의 hops가 apps[].name에 존재하는가
- [ ] tag_gaps가 실제 갭만 포함하는가 (없으면 빈 배열)

### Q2 검증
- [ ] 모든 내부 노드에 최소 1개 edge가 있는가 (고아 노드 없음 → H4)
- [ ] 양방향 edge가 없는가 → G1
- [ ] 모든 edge의 source/target이 nodes[] 또는 boundary_nodes[]에 존재하는가
- [ ] 모든 워크플로우 hop의 from/to가 nodes[] 또는 boundary_nodes[]에 존재하는가 → H7
- [ ] 다른 앱의 내부를 상세히 나열하지 않았는가 (boundary_node 1개로 축약 → G4, H5)
- [ ] 내부 노드와 동일한 서비스를 boundary_node로도 만들지 않았는가 → G5
- [ ] 모든 노드에 kind와 group이 있는가
- [ ] 모든 EKS Pod에서 컨테이너를 1개만 보고하지 않았는가 → H6

### 공통 검증
- [ ] 모든 텍스트 필드(description, role, name)가 한국어인가
- [ ] JSON이 올바른 형식인가 (`\`\`\`json` 코드블록 안)
- [ ] K8s Service 리소스(ClusterIP/LB)를 노드로 만들지 않았는가 → G3
