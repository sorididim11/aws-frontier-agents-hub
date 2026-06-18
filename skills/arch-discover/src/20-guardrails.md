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
