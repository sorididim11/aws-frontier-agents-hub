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
