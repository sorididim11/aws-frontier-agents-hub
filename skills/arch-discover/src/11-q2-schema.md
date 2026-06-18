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
