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
