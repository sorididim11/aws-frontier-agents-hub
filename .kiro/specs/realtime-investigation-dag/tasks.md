# Tasks: Realtime Investigation DAG

## Task 1: Backend - DAG 저장/조회 API 엔드포인트

- [x] 1.1 Add `GET /api/run/<run_id>/dag` route in `app.py` that queries DynamoDB with `run_id` + `record_type="investigation_dag"` and returns the DAG JSON (or 404 if not found)
- [x] 1.2 Add `POST /api/run/<run_id>/dag` route in `app.py` that saves DAG JSON (`hypotheses`, `alarm`, `root_cause`, `raw_count`, `scenario_id`) to DynamoDB with `record_type="investigation_dag"`
- [x] 1.3 Add auto-save logic: when `pollRunStatus` detects investigation completion (`status=completed`), the frontend calls `POST /api/run/<run_id>/dag` with the last cached DAG data

## Task 2: Frontend - DAG Section HTML & CSS

- [x] 2.1 Add `investigationDagSection` div in `index.html` below `agentFlowSection`, initially hidden (`display:none`), with section header "조사 흐름 DAG"
- [x] 2.2 Add DAG-specific CSS styles in `index.html`: `.dag-container` (flexbox row), `.dag-branch` (flexbox column for each hypothesis branch), `.dag-node` (based on existing `flow-node` pattern, max-width 160px, text-overflow ellipsis), `.dag-connector` (horizontal arrow based on `flow-arrow`), `.dag-branch-connector` (vertical connector from triage to branches)
- [x] 2.3 Add status color CSS classes: `.dag-node-rejected` (border/text #ef4444), `.dag-node-confirmed` (border/text #22c55e), `.dag-node-partial` (border/text #f59e0b), `.dag-node-key` (solid 2px border in branch color)
- [x] 2.4 Add pulsing "진행 중..." indicator CSS: `.dag-node-progress` with pulse animation (reuse existing `@keyframes pulse`)

## Task 3: Frontend - DAG Data Parser (`parseHypothesesToDag`)

- [x] 3.1 Implement `parseHypothesesToDag(data)` function in `app.js` that takes `{hypotheses, alarm, root_cause}` and returns `{nodes: DAGNode[], edges: DAGEdge[]}`
- [x] 3.2 Generate triage root node from `data.alarm` with `type="triage"`, `branch_index=0`, `color="#94a3b8"`
- [x] 3.3 For each hypothesis, generate: hypothesis node (`type="hypothesis"`), step nodes (`type="step"` with label=action, sublabel=data_source, is_key from source), and result node (`type="result"` with "기각" or "Root Cause" label)
- [x] 3.4 Apply status-to-color mapping: `rejected→#ef4444`, `confirmed→#22c55e`, `partial→#f59e0b` to all nodes in each hypothesis branch
- [x] 3.5 Generate edges: triage→each hypothesis node, sequential step connections within each branch, and cross-branch edges from `leads_to` fields

## Task 4: Frontend - DAG Renderer (`renderInvestigationDag`)

- [x] 4.1 Implement `renderInvestigationDag(data, isReadOnly)` function in `app.js` that calls `parseHypothesesToDag`, then builds HTML and inserts into `investigationDagSection`
- [x] 4.2 Render triage node as leftmost column, then each hypothesis branch as a row of nodes flowing left-to-right, connected by arrow divs
- [x] 4.3 When `isReadOnly=false` and investigation is in progress, append a pulsing "진행 중..." node at the end of the active (partial status) branch
- [x] 4.4 Show/hide `investigationDagSection` based on whether data has hypotheses (show if hypotheses.length > 0, hide otherwise)

## Task 5: Frontend - Real-time DAG Updates (Cache + Polling Integration)

- [x] 5.1 Add `window._dagMessageCount = 0` and `window._dagData = null` cache variables, reset both in `openScenario()` and `runScenario()`
- [x] 5.2 In `pollRunStatus()`, after fetching journal messages (skip_classify=true), compare `raw_messages.length` with `_dagMessageCount`
- [x] 5.3 When message count increases: call `/api/investigation-journal?analyze=true` with task_id and scenario_id, update `_dagMessageCount` and `_dagData`, call `renderInvestigationDag(data, false)`
- [x] 5.4 When message count unchanged: skip Bedrock call, retain current DAG
- [x] 5.5 On Bedrock API error: retain previous DAG (`_dagData`), show non-blocking error text below DAG section

## Task 6: Frontend - History DAG Loading

- [x] 6.1 In `loadHistoryDetail()`, call `GET /api/run/<run_id>/dag` to fetch saved DAG
- [x] 6.2 If DAG exists, call `renderInvestigationDag(data, true)` in read-only mode (no progress indicator)
- [x] 6.3 If DAG not found (404), keep `investigationDagSection` hidden

## Task 7: Frontend - Auto-save DAG on Investigation Completion

- [x] 7.1 In `pollRunStatus()`, when investigation completes (status transitions to completed), call `POST /api/run/<run_id>/dag` with `_dagData` to persist the final DAG
- [x] 7.2 Include `scenario_id` from `currentScenario.id` in the POST body

## Task 8: Property-Based Tests

- [ ] 8.1 Set up fast-check test file `services/dashboard/static/js/dag.test.js` with hypothesis data generators (random hypothesis count 1-5, random steps 1-4 per hypothesis, random status, random leads_to)
- [ ] 8.2 Property test 1: Triage root node invariant - for any valid hypothesis data, exactly one triage node exists with no incoming edges
- [ ] 8.3 Property test 2: Structural preservation - branch count equals hypothesis count, step count per branch matches
- [ ] 8.4 Property test 3: Status-to-color mapping - all nodes in a branch have the correct color for their hypothesis status
- [ ] 8.5 Property test 4: Node fields faithfully represent source data - label, sublabel, is_key match source
- [ ] 8.6 Property test 5: Well-formed DAG output - all nodes have required fields, all edges reference valid node IDs
- [ ] 8.7 Property test 6: Cross-branch edges from leads_to - edges exist for all leads_to references
- [ ] 8.8 Property test 7: Cache comparison correctness - skip when equal, call when different
