# Requirements Document

## Introduction

DevOps Agent가 장애를 조사하는 과정을 실시간으로 DAG(Directed Acyclic Graph) 형태로 시각화하는 기능이다. 기존 장애 흐름 다이어그램(`flowDiagram`) 아래에 별도 섹션으로 배치되며, 에이전트의 Triage → 가설 분기 → 검증 → 기각/확인 과정을 좌→우 방향 경로 그래프로 표현한다. 조사 진행 중에는 journal 메시지가 추가될 때마다 Bedrock API로 가설을 구조화하여 DAG를 실시간 업데이트하고, 조사 완료 후에는 최종 DAG를 DynamoDB에 저장하여 이력 조회 시 DB에서 읽기만 한다.

## Glossary

- **DAG_Renderer**: 프론트엔드에서 가설 구조 데이터를 받아 좌→우 방향의 DAG를 순수 HTML div + CSS로 렌더링하는 모듈
- **DAG_Section**: `agentFlowSection` div 아래에 새로 추가되는 DAG 시각화 영역 (`investigationDagSection`)
- **Hypothesis_Structurer**: 백엔드에서 journal 메시지를 Bedrock API로 분석하여 가설 기반 DAG 구조 JSON을 반환하는 엔드포인트 (기존 `/api/investigation-journal?analyze=true`)
- **DAG_Node**: DAG 내 개별 노드. Triage, 가설, 검증 행동, 결과(기각/확인/Root Cause) 등을 나타냄
- **DAG_Edge**: DAG 노드 간 방향성 연결선
- **Journal_Poller**: `pollRunStatus` 함수 내에서 journal 메시지를 주기적으로 가져오는 기존 폴링 로직
- **DAG_Cache**: 프론트엔드에서 이전 Bedrock 응답의 해시를 저장하여 메시지 변경이 없으면 재호출을 방지하는 캐시 메커니즘
- **DAG_Store**: DynamoDB `devops-agent-test-scenario-runs` 테이블에 `record_type=investigation_dag`로 저장되는 최종 DAG 데이터

## Requirements

### Requirement 1: DAG 섹션 배치

**User Story:** As a DevOps 엔지니어, I want 조사 흐름 DAG가 장애 흐름 다이어그램 아래에 별도 섹션으로 표시되기를, so that 장애 전파 경로와 에이전트 조사 경로를 한 화면에서 비교할 수 있다.

#### Acceptance Criteria

1. THE DAG_Section SHALL render below the existing `agentFlowSection` div inside the scenario detail page
2. WHILE no investigation is active, THE DAG_Section SHALL remain hidden (`display:none`)
3. WHEN journal messages are available for the current run, THE DAG_Section SHALL become visible with a section header labeled "🔬 조사 흐름 DAG"
4. THE DAG_Section SHALL NOT modify or overlap with the existing `hypothesisContent` section

### Requirement 2: 좌→우 DAG 레이아웃

**User Story:** As a DevOps 엔지니어, I want 조사 과정이 좌에서 우로 흐르는 DAG 그래프로 표시되기를, so that 시간 순서대로 조사 흐름을 직관적으로 파악할 수 있다.

#### Acceptance Criteria

1. THE DAG_Renderer SHALL lay out DAG_Node elements in a left-to-right direction using CSS flexbox or CSS grid
2. THE DAG_Renderer SHALL render the Triage node as the leftmost root node of the DAG
3. WHEN multiple hypotheses branch from Triage, THE DAG_Renderer SHALL render each hypothesis as a separate vertical branch extending to the right
4. THE DAG_Renderer SHALL connect DAG_Node elements with DAG_Edge elements using the existing `flow-arrow` CSS class or equivalent horizontal connector divs
5. THE DAG_Renderer SHALL use only pure HTML div elements and CSS for rendering without any external visualization library

### Requirement 3: 가설별 분기 구조

**User Story:** As a DevOps 엔지니어, I want 각 가설이 독립적인 분기로 표시되기를, so that 에이전트가 어떤 가설을 탐색하고 기각하거나 확인했는지 한눈에 볼 수 있다.

#### Acceptance Criteria

1. THE DAG_Renderer SHALL render each hypothesis from the Hypothesis_Structurer response as a separate horizontal branch
2. WHEN a hypothesis has status "rejected", THE DAG_Renderer SHALL apply a red color (`#ef4444`) to the hypothesis branch nodes and the terminal "기각" node
3. WHEN a hypothesis has status "confirmed", THE DAG_Renderer SHALL apply a green color (`#22c55e`) to the hypothesis branch nodes and the terminal "Root Cause" node
4. WHILE a hypothesis has status "partial" or the investigation is in progress, THE DAG_Renderer SHALL apply a yellow color (`#f59e0b`) to the hypothesis branch nodes
5. THE DAG_Renderer SHALL render each step within a hypothesis as a sequential DAG_Node along that branch, showing the action text and data_source

### Requirement 4: 노드 표현

**User Story:** As a DevOps 엔지니어, I want 각 노드가 에이전트의 실제 행동을 텍스트와 색상으로 표현하기를, so that 아이콘 없이도 조사 단계를 명확히 구분할 수 있다.

#### Acceptance Criteria

1. THE DAG_Node SHALL display the action text as the primary label using the existing `flow-node` CSS class or equivalent styling
2. THE DAG_Node SHALL display the data_source value as a secondary label below the action text in a smaller font size
3. THE DAG_Renderer SHALL NOT use any icon or emoji inside DAG_Node elements
4. WHEN a DAG_Node represents a key finding (`is_key=true`), THE DAG_Renderer SHALL apply a highlighted border style (solid 2px border in the branch color) to distinguish the node from non-key nodes
5. THE DAG_Node SHALL have a maximum width of 160px and truncate overflow text with an ellipsis

### Requirement 5: 실시간 DAG 업데이트

**User Story:** As a DevOps 엔지니어, I want 조사 진행 중 DAG가 실시간으로 업데이트되기를, so that 에이전트의 현재 조사 진행 상황을 라이브로 모니터링할 수 있다.

#### Acceptance Criteria

1. WHEN the Journal_Poller receives new journal messages during an active run, THE DAG_Renderer SHALL call the Hypothesis_Structurer endpoint to obtain updated hypothesis structure
2. THE DAG_Renderer SHALL compare the current journal message count with the previous count and call the Hypothesis_Structurer endpoint only when new messages have been added
3. WHEN the Hypothesis_Structurer returns updated hypothesis data, THE DAG_Renderer SHALL re-render the entire DAG with the new structure
4. WHILE the investigation is in progress, THE DAG_Renderer SHALL append a pulsing "진행 중..." indicator node at the rightmost end of the active hypothesis branch
5. IF the Hypothesis_Structurer endpoint returns an error, THEN THE DAG_Renderer SHALL retain the previously rendered DAG and display a non-blocking error indicator below the DAG

### Requirement 6: Bedrock 호출 캐싱

**User Story:** As a 시스템 운영자, I want Bedrock API 호출이 메시지 변경 시에만 발생하기를, so that 불필요한 API 비용과 지연을 방지할 수 있다.

#### Acceptance Criteria

1. THE DAG_Cache SHALL store the count of raw journal messages from the most recent Hypothesis_Structurer response
2. WHEN the Journal_Poller fetches journal messages, THE DAG_Cache SHALL compare the new message count with the stored count
3. WHEN the new message count equals the stored count, THE DAG_Cache SHALL skip the Hypothesis_Structurer API call and retain the current DAG
4. WHEN the new message count differs from the stored count, THE DAG_Cache SHALL trigger a new Hypothesis_Structurer API call and update the stored count
5. WHEN a new run starts, THE DAG_Cache SHALL reset the stored message count to zero

### Requirement 7: 완료 후 DAG 저장

**User Story:** As a DevOps 엔지니어, I want 조사 완료 후 최종 DAG가 DB에 저장되기를, so that 이력에서 과거 조사의 DAG를 다시 볼 수 있다.

#### Acceptance Criteria

1. WHEN the investigation status transitions to "completed", THE Dashboard_Backend SHALL save the final DAG structure JSON to DynamoDB with `run_id` as partition key and `record_type` set to "investigation_dag"
2. THE Dashboard_Backend SHALL expose a GET endpoint `/api/run/<run_id>/dag` that returns the saved DAG structure from DynamoDB
3. WHEN a user loads a historical run via `loadHistoryDetail`, THE DAG_Renderer SHALL fetch the DAG from the GET endpoint and render the DAG in read-only mode
4. IF the GET endpoint returns no DAG data for a historical run, THEN THE DAG_Section SHALL remain hidden
5. THE Dashboard_Backend SHALL store the DAG JSON including the fields: `hypotheses`, `alarm`, `root_cause`, and `raw_count`

### Requirement 8: DAG 데이터 구조 파싱

**User Story:** As a 개발자, I want Hypothesis_Structurer의 응답 JSON을 DAG 노드/엣지 구조로 변환하기를, so that 렌더러가 일관된 데이터 형식으로 DAG를 그릴 수 있다.

#### Acceptance Criteria

1. THE DAG_Renderer SHALL parse the Hypothesis_Structurer response JSON and produce a list of DAG_Node objects with fields: `id`, `label`, `sublabel`, `type` (triage|hypothesis|step|result), `color`, and `branch_index`
2. THE DAG_Renderer SHALL produce a list of DAG_Edge objects with fields: `from_id` and `to_id` connecting sequential nodes
3. WHEN the Hypothesis_Structurer response contains a `leads_to` field linking one hypothesis to another, THE DAG_Renderer SHALL render a DAG_Edge from the source hypothesis result node to the target hypothesis node
4. FOR ALL valid Hypothesis_Structurer response JSON objects, parsing into DAG_Node and DAG_Edge lists and then rendering back to a visual structure SHALL preserve the hypothesis count, step count per hypothesis, and status of each hypothesis (round-trip structural integrity)
