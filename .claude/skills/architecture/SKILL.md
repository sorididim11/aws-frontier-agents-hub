---
name: architecture
description: >
  Universal draw.io architecture diagram skill. Generates, verifies, and exports
  multi-level diagrams (L1 + L2 + N×L3 focal pages) for any project. Three input
  modes: direct request, code/infra scan, or knowledge DB. All project data lives
  in knowledge.json — the skill itself is project-agnostic. L3 pages are dynamically
  generated from knowledge.json's l3_pages array (one page per focal area).
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep
  - Agent
user-invocable: true
argument-hint: "[draw <description>|scan [--aws] [--observe]|knowledge [show|collect|edit]|verify|export|status|observe]"
---

# Universal Architecture Diagram Skill

You are a universal architecture diagram generator. You produce draw.io (mxGraph XML)
diagrams at three zoom levels for **any** project, using data-driven rules.

## Core Principle: Skill ≠ Data

- **Skill** (this file + verify_drawio.py + component_library.json + icon_registry.json) = generic tooling
- **knowledge.json** (in project's output dir) = project-specific data that drives everything
- The skill NEVER hardcodes project names, service names, namespaces, or file paths
- All rendering and verification decisions derive from knowledge.json
- **Claude generates draw.io XML directly** — no Python renderer scripts, no external code generators
- XML generation follows the draw.io official best practices (ELK auto-routing, rigid grid, proper containment)

## Foundation: UML (C4 Model) × AWS Architecture Icons

This skill uses a hybrid approach: **UML/C4 Model** as the structural foundation, with
**AWS Architecture Icons** as visual anchors for immediate technology recognition.

| Level | C4 Equivalent | UML Elements | AWS Elements |
|-------|--------------|--------------|--------------|
| **L1** | System Context | Actor, System Boundary, external systems | Service icons (simplified) |
| **L2** | Container | Component Diagram, Interface, Stereotype | K8s icons, AWS icons, namespace boundary |
| **L3** | Component | Internal Component Hierarchy, Swimlane | Config detail + resource breakdown |

**Principle**: AWS icons = "What" (instant technology recognition). UML notation = "Why/How" (role and relationship semantics).

---

## draw.io XML Generation Rules

Claude generates mxGraph XML directly by reading knowledge.json. No Python scripts,
no external renderers. These rules come from draw.io's official AI generation guide.

### Core XML Structure

```xml
<mxGraphModel adaptiveColors="auto">
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <!-- vertices and edges here -->
  </root>
</mxGraphModel>
```

- Always include the two structural cells: `id="0"` (root) and `id="1"` (default layer)
- All IDs must be unique
- Vertices require `vertex="1"`, edges require `edge="1"`
- `html=1` on ALL cell styles (enables rich text rendering)

### Rigid Grid — Use for Every Diagram

Do NOT compute coordinates in prose. Use this grid:

- Column x = `col_index * 180 + 40` (col 0 = 40, col 1 = 220, col 2 = 400, …)
- Row y = `row_index * 120 + 40` (row 0 = 40, row 1 = 160, row 2 = 280, …)
- Node sizes: rectangles `140×60`, diamonds `140×80`, circles `60×60`, cylinders `100×70`

Pick a `(col, row)` for each node. ELK handles routing — slight misalignment is invisible.

### Edge Rules — Let ELK Route

**Do NOT** add `<Array as="points">` waypoints or set exitX/exitY/entryX/entryY.
ELK auto-routes edges after render. Just declare source and target:

```xml
<mxCell id="e1" edge="1" source="svc1" target="svc2" parent="1"
        style="edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
```

**Every edge must contain `<mxGeometry relative="1" as="geometry"/>` as a child element.**
Self-closing edge cells are invalid.

### Container Containment (Not Visual Stacking)

Use `parent="containerId"` on child cells — do NOT just place shapes on top of larger shapes.

```xml
<!-- Container -->
<mxCell id="ns1" value="ns: dockercoins"
        style="swimlane;startSize=24;fillColor=#FFF8E1;strokeColor=#FF8F00;html=1;"
        vertex="1" parent="1">
  <mxGeometry x="100" y="100" width="600" height="400" as="geometry"/>
</mxCell>
<!-- Child (coordinates relative to container) -->
<mxCell id="worker" value="Worker" style="..." vertex="1" parent="ns1">
  <mxGeometry x="20" y="40" width="48" height="48" as="geometry"/>
</mxCell>
```

- Cross-container edges must use `parent="1"`
- Add `pointerEvents=0` to containers that shouldn't capture connections

### HTML Labels

- Escape special characters: `&lt;` `&gt;` `&amp;` `&quot;`
- Line breaks: use `&#xa;` or `&lt;br&gt;`
- Never use `\n` (renders as literal text)

### What Claude Does NOT Do

- Do NOT compute x/y coordinates in prose ("let me adjust spacing to 160px...")
- Do NOT add waypoints or connection-point overrides
- Do NOT narrate "building the XML / now let me finalize..." — just emit XML
- Do NOT re-derive draw.io mechanics — use the templates in component_library.json

### What Claude DOES

1. Read knowledge.json → filter services and hops for the target level
2. Pick (col, row) for each service based on phases and layout strategy
3. Emit `<mxCell>` elements directly — vertices, then edges
4. Use `component_library.json` templates for styles
5. Use `icon_registry.json` for icon lookups

---

## Reasoning Process — Think Before You Draw

**This is the most important section of the entire skill.** Before generating ANY diagram
page, you MUST follow this mandatory thinking process. Skipping these steps is the #1 cause
of broken, incomprehensible diagrams.

### Step 1: Understand the System Story

Read `knowledge.json → system` BEFORE touching any XML:

1. **Read `system.purpose`** — What does this system do? Summarize it in one sentence.
2. **Read `system.phases`** — What is the sequence of events in this system? List them in
   order (e.g., "Phase 1: Inject Fault → Phase 2: Detect & Alert → Phase 3: AI Investigation → Phase 4: Evaluate Quality").
3. **Read `system.level_purpose[level]`** — What question must THIS specific diagram level answer?
   - L1 answers: "What does this system do at a high level?"
   - L2 answers: "How are components deployed and connected?"
   - L3 answers: "What's inside this specific service?"
4. **Read `system.scenarios`** — What are the key use cases? The diagram must support
   understanding of these scenarios.

**STOP CHECK**: If you cannot verbally explain the system's 4-phase flow, you do NOT
understand the system well enough to draw it. Re-read `system` until you can.

### Step 2: Filter and Validate Data

For the target level (L1, L2, or L3):

1. **Filter services**: Only include `services where target_level in service.levels`
2. **Filter data_flows**: Only include `data_flows where target_level in flow.levels`
3. **Validate ALL references**: Every `from` and `to` in filtered flows MUST reference
   a service that exists in the filtered service list
4. **If a flow references a service not at this level → SKIP that flow entirely**
   - Do NOT invent collapsed/proxy edges to "make it work"
   - Do NOT add services just to make a broken flow valid
   - A missing L1 flow means knowledge.json needs fixing, not the diagram
5. **Write down your counts**: "I have N services and M flows for this level"
   - These numbers are your contract — the final diagram must match exactly

### Step 3: Design Layout by Phase

Use `system.phases` to organize the spatial layout:

**IMPORTANT**: Before placing ANY cell, read `component_library.json → layout_formulas → {level}`
for exact coordinates and formulas. Do NOT guess positions — use the formulas.

**L1 Layout Strategy — Two-Row Horizontal Flow**:

Read `layout_formulas.L1` for exact coordinates.

- **Top row** (y=110): The main incident flow as a single horizontal line
  - Place services in phase order: Phase 1 → Phase 2 → Phase 3 services
  - Coordinate formula: `x_i = 80 + i * ((canvas_w - 160) / (N - 1))`
  - Where N = number of top-row services, i = 0-based index
  - This row MUST be a STRAIGHT horizontal line — the primary visual path
- **Bottom row** (y=320): Supporting/backend services (Bedrock, DynamoDB)
  - X position: center under the related top-row service they connect to
- **Phase labels** (y=65): Simple text labels ABOVE the main flow
  - Use text only — NO colored background rectangles/zones (those add noise)
  - Format: `① Phase Name` with phase color
- **Phase colors**: Match edge_style colors — red=fault, orange=alarm, green=investigation, purple=evaluation
- **Title** (y=5): From `system.name` and a short purpose line
- **Evaluation edges**: Route BELOW the main flow (exit Dashboard downward, then
  horizontal to Bedrock/DynamoDB). Never cross the main horizontal flow.
- **Legend** (y=canvas_h-60): Single-line edge color legend at bottom
- **Canvas**: 1000×520

**L2 Layout Strategy — Zone-Based**:

Read `layout_formulas.L2` for exact zone coordinates.

L2 uses a **strict zone system**. Every service MUST be placed within its designated zone:

```
┌────────────────────────────────────────────────────────────────────┐
│ ingress    │  k8s_cluster (VPC > EKS > namespaces)  │  aws_managed │
│ x: 20-100  │  x: 100-1380                           │  x: 1400-2180│
│            │  ┌ dashboard_ns (120,160) ─────────┐    │  ┌ monitoring │
│ Operator   │  │ Dashboard, dashboard-svc         │   │  │ CW,SNS,EB  │
│ ALB        │  └──────────────────────────────────┘    │  └────────────│
│            │  ┌ dockercoins_ns (120,460) ────────┐    │  ┌ lambda ────│
│            │  │ WebUI Worker Hasher RNG Redis     │   │  │ 3 functions │
│            │  │ (grid: col_spacing=180)          │    │  └────────────│
│            │  └──────────────────────────────────┘    │  ┌ backends ──│
│            │  ┌ kube_system_ns (120,1000) ──┐        │  │ Agent,Bedrock│
│            │  │ AWS LB Controller            │       │  │ DDB,RDS,ECR │
│            │  └─────────────────────────────┘         │  └────────────│
└────────────────────────────────────────────────────────────────────┘
```

- **Cross-zone edge corridor** at x≈1390 separates K8s from AWS zones
- Boundaries: AWS Account (outermost) > VPC > EKS Cluster > namespaces
- Inside dockercoins namespace: deploy+svc pairs in a grid (col_spacing=180, row_spacing=150)
- Stereotypes below each service icon (fontSize=9, italic)
- Canvas: 2200×1600

### L2 Edge Routing Guidelines

L2 has many cross-zone edges (K8s ↔ AWS services). These are the #1 cause of visual clutter.

1. **No bottom highways**: NEVER route edges along the canvas floor (y > 1400).
   Route through the gap between services, not under them.
2. **Cross-zone corridor**: x ≈ 1380-1400 is the dedicated edge corridor between
   the K8s cluster zone and the AWS managed zone. Edges crossing this boundary
   should exit the cluster heading right, cross the corridor, and enter the AWS zone.
3. **Edge stacking**: When multiple edges cross the same corridor, stack them
   vertically with 20px spacing. Don't overlap edges on the same y-coordinate.
4. **Dashboard evaluation edges**: Route RIGHT through the corridor (not downward
   U-turn). Exit Dashboard → cross corridor → enter Bedrock/DynamoDB from the left.
5. **Agent↔CloudWatch**: Route within the AWS managed zone's vertical space.
   Do NOT route around the entire diagram perimeter.

**L3 Layout Strategy — Focal + Collapsed**:

Read `layout_formulas.L3` for exact zone coordinates.

- **Focal zone** (x: 20-1500, 70% width): Detail cards or component breakdowns
  - Card spacing: 200px horizontal, 250px vertical
  - K8s deploy+svc pairs: svc icon 70px below deploy icon
  - Namespace containers wrap focal K8s services
- **Collapsed zone** (x: 1550-2180, 30% width): Right sidebar with AWS icons
  - Icons stacked vertically, 120px spacing
  - Annotation text 60px to the right of each icon
- **Canvas**: 2200×1800

### Step 4: Draw ONLY What Exists — The Golden Rule

**This is the most critical rule. Violation of this rule produces garbage diagrams.**

- **NEVER add edges** that don't exist in the filtered `data_flows` for this level
- **NEVER add services** that aren't in the filtered `services` list for this level
- **NEVER "invent" a reasonable-looking edge** just because it seems logically plausible
- If a flow only has `"levels": ["L2"]`, it does NOT exist at L1. Period.
- **NEVER create shortcut flows** that skip intermediate services. If the real path is
  `CloudWatch → SNS → Lambda → Agent`, you CANNOT create a direct `CloudWatch → Agent`
  flow for L1. Instead, all 3 intermediate flows must have L1 in their levels, and
  all 4 services must be L1 services. Shortcut flows = phantom edges = lies.

**After drawing, COUNT your edges and services. They MUST match Step 2's counts.**
If the counts don't match, you have phantom edges or missing services. Fix them.

### Step 5: Self-Check Before Finishing

Ask yourself these questions. If ANY answer is "no", fix the diagram before proceeding:

1. **Story test**: Can someone who has never seen this system understand what it does
   from this diagram alone?
2. **Edge audit**: Does every edge in the diagram correspond to a `data_flow` entry
   at this level? (No phantom edges)
3. **Reverse audit**: Does every `data_flow` entry at this level have a corresponding
   edge in the diagram? (No missing edges)
4. **Flow test**: Does the story flow logically from left to right following the phase sequence?
5. **Level test**: Are there any services or details that belong to a different level?
   (No K8s icons on L1, no port numbers on L1, etc.)
6. **Purpose test**: Does this diagram answer the `system.level_purpose` question for this level?
7. **Phase test**: Can you visually identify all 4 phases by looking at the diagram?

---

## Service Naming Rules

**name = 서비스 이름 (technology brand), stereotype = 역할 (role).**
역할은 stereotype 필드에만 기록한다. name에 역할을 중복하지 않는다.
다이어그램에서 역할은 `<<stereotype>>` 라벨로 아이콘 아래에 표시된다.

| 필드 | 용도 | 예시 |
|------|------|------|
| name | 서비스 고유 이름 (짧고 명확) | "DynamoDB", "Bedrock", "SNS", "Hasher" |
| stereotype | 이 서비스의 역할 | "Persistence", "AI:Reasoning", "Messaging", "Stateless:HTTP" |

### 수집 시 name 결정 기준

- AWS 관리형 서비스 → **서비스 브랜드명**: `"CloudWatch"`, `"DynamoDB"`, `"Bedrock"`, `"SNS"`, `"EventBridge"`
- K8s 워크로드 → **deployment 이름**: `"Hasher"`, `"Worker"`, `"RNG"`, `"Redis"`
- 커스텀/프로덕트 → **프로덕트명**: `"DevOps Agent"`, `"Simulator"`, `"DockerCoins"`
- **금지**: name에 역할 접두어/접미어 붙이기 (`"Investigation DB (DynamoDB)"` → `"DynamoDB"`)
- **이유**: stereotype이 이미 역할을 담당하므로, name에도 넣으면 다이어그램에서 이중 표시됨

### knowledge.json `name` Field

The `name` field in knowledge.json should follow these naming rules. Optionally use
`display_name_l1` for an even simpler L1 name if `name` includes technology references.

---

## Edge Label Rules — Action-Oriented Labeling

Every edge label must describe the **business action**, not just the protocol or technology.

### Pattern: What enters + What this service transforms/decides

| Level | Must Answer | Example |
|-------|------------|---------|
| L1 | What data enters + what changes at this hop | "threshold breach → alarm fired", "context + evidence → root cause analysis" |
| L2 | L1 meaning + protocol/method | "threshold breach → alarm fired (SNS Publish)" |
| L3 | Full technical description | "CloudWatch evaluates metric against threshold, fires SNS Publish to alarm topic" |

### Label Quality Self-Test

Each label must pass: **"Can a new team member read ONLY this edge and understand what
this specific service does to the data flowing through it?"**

```
✅ GOOD (shows what data enters and what THIS service produces):
  "threshold breach → alarm fired"           ← input: metric breach, output: alarm
  "subscription match → Lambda invoked"      ← input: topic message, decision: route to subscriber
  "alarm payload → webhook POST forwarded"   ← input: SNS event, output: HTTP call
  "context + evidence → root cause analysis" ← input: alarm + data, output: findings
  "analysis results → persisted by event_id" ← input: findings, output: DB record

❌ BAD (generic verbs — could apply to ANY system):
  "forwards alarm"      ← forwards HOW? what changes at this service?
  "routes to forwarder" ← what routing decision is made?
  "delivers alarm"      ← identical meaning to "forwards" — no new info
  "stores results"      ← what results? what structure? what key?
  "triggers handler"    ← what triggers? what handler does what?
```

### Anti-Pattern: Synonym Chain

If 3+ consecutive hops all use synonyms of the same verb ("forwards X" → "routes X" →
"delivers X"), the labels are too abstract. Each hop passes through a DIFFERENT service
that does something DIFFERENT — the labels must reflect that difference.

### Rules
1. L1: Each label shows what data enters this service and what comes out changed
2. L2: L1 meaning + protocol/method in parentheses
3. L3: Full descriptive labels with technical specifics
4. Every label must answer: "What does THIS service transform or decide?"

---

## Layout Rules

### Flow Direction — Left to Right

All diagrams follow **left-to-right** (LTR) primary flow direction:

- **L1**: `Operator (left) → Application System (center) → External Services (right)`
- **L2**: Three-tier layering:
  ```
  Layer 1 (left):   Ingress — User, ALB, Route53
  Layer 2 (center): Application — K8s workloads, Lambda functions
  Layer 3 (right):  Backend — Databases, AI services, Monitoring
  ```
- **L3**: Focal area flows left-to-right. Collapsed services on right sidebar.

### Anti-Overlap Rules

- Edge routing: `edgeStyle=orthogonalEdgeStyle;rounded=1;jettySize=auto;orthogonalLoop=1`
- **Minimum spacing**: 60px horizontal, 40px vertical between nodes
- **Edge bundling**: Parallel edges going same direction should share route segments
- **No node penetration**: Edges must route around nodes, never through them
- **Crossing minimization**: If crossing is unavoidable, one edge curves to reduce visual confusion
- **Label placement**: Edge labels positioned at midpoint, away from crossings
- **Consistent direction**: Within a tier, edges should flow consistently (avoid back-arrows where possible; if needed, style them distinctly as dashed/gray)

### Canvas Sizes
- L1: ~1400×780 (compact service overview)
- L2: ~2200×1600 (full component view with room for stereotypes)
- L3: ~2200×1800 (component breakdown needs more vertical space)

---

## UML Stereotype Rules

On L2 and L3, every service node gets a **stereotype label** indicating its architectural role.

### Stereotype Categories

| Stereotype | Meaning | Examples |
|-----------|---------|---------|
| `<<Stateless:HTTP>>` | HTTP-based stateless workload | Hasher, RNG |
| `<<Stateful:Queue>>` | Queue-consuming stateful workload | Worker |
| `<<WebUI>>` | User-facing web interface | WebUI, Dashboard |
| `<<Persistence>>` | Data store | DynamoDB, RDS, Redis (when primary store) |
| `<<Cache>>` | Caching layer | Redis (when used as cache) |
| `<<EventHandler>>` | Event-triggered compute | Lambda functions |
| `<<Orchestrator>>` | Workflow/investigation orchestrator | DevOps Agent |
| `<<Monitoring>>` | Observability/monitoring | CloudWatch, X-Ray |
| `<<Messaging>>` | Async messaging/routing | SNS, EventBridge |
| `<<AI:Reasoning>>` | AI/ML inference | Bedrock |
| `<<Gateway>>` | Traffic ingress | ALB, API Gateway |
| `<<CI/CD>>` | Build/deploy pipeline | ECR, CodePipeline |

### Rendering in draw.io

- Place italic text cell below the service icon: `text;fontSize=9;fontColor=#666;fontStyle=2`
- Format: `<<StereotypeName>>` (with angle brackets)
- Example: K8s Deploy icon "Hasher" with `<<Stateless:HTTP>>` below

### knowledge.json `stereotype` Field

Each service in knowledge.json should have a `stereotype` string field:
```json
{"id": "hasher", "name": "Hasher", "stereotype": "Stateless:HTTP", ...}
```

---

## L3 Component Decomposition Rules

L3's core purpose: show the **internal structure** of focal services hierarchically.
Go beyond config cards (image, cpu, port) to **architectural decomposition**.

### K8s Workload Focal Service

Render config card (existing) PLUS internal component breakdown:

1. **Interface Layer**: Exposed ports, API endpoints, protocols
2. **Core Logic**: Key business logic components
3. **Dependencies**: External services called (from env vars / code)
4. **Instrumentation**: OTel, logging, tracing configuration
5. **Chaos Points**: Test/fault-injection endpoints (if applicable)

### Cloud Managed Focal Service

AWS icon + internal structure decomposition:

1. **Configuration**: Core settings (model ID, table schema, runtime, etc.)
2. **Input Interfaces**: How data enters (HTTP webhook, event trigger, SDK call)
3. **Processing**: What it does internally (reasoning, transformation, routing)
4. **Auth/Permissions**: IAM roles, policies, security boundaries
5. **Output Interfaces**: Where results go (event emission, data storage, notifications)

### knowledge.json `component_breakdown` Type

A new `l3_detail.type` value `"component_breakdown"` enables hierarchical rendering:
```json
{
  "l3_detail": {
    "type": "component_breakdown",
    "components": [
      {"name": "Webhook Interface", "role": "input", "description": "POST /webhook with HMAC-SHA256 auth"},
      {"name": "Reasoning Engine", "role": "processing", "description": "Claude via Bedrock InvokeModel"},
      {"name": "Tool Invoker", "role": "processing", "description": "CW Logs, EKS kubectl, FIS"},
      {"name": "Lifecycle Manager", "role": "output", "description": "EventBridge + DynamoDB + Slack"}
    ],
    "fields": { ... }
  }
}
```

### Rendering `component_breakdown` in draw.io

Render as a **container with internal swimlanes**:
```
┌─ Service Name <<Stereotype>> ─────────────────────────────┐
│  ┌─ Input ──────────────────────────────────────────────┐ │
│  │  [component descriptions from role=input]            │ │
│  └──────────────────────────────────────────────────────┘ │
│  ┌─ Processing ─────────────────────────────────────────┐ │
│  │  [component descriptions from role=processing]       │ │
│  └──────────────────────────────────────────────────────┘ │
│  ┌─ Output ─────────────────────────────────────────────┐ │
│  │  [component descriptions from role=output]           │ │
│  └──────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────┘
```

- Container: uses `boundary_box` or `group_container` template
- Internal sections: labeled swimlanes (Input/Processing/Output)
- Each component: text line with name + description
- AWS icon (48×48) positioned at top-left of container

---

## File Locations

Skill files (generic, project-agnostic):
```
<skill_dir>/
  SKILL.md                    # This file — orchestrator + draw.io XML generation rules
  verify_drawio.py            # Deterministic verifier
  component_library.json      # Reusable mxGraph style templates
  icon_registry.json          # Multi-provider icon lookup
  observe.py                  # Runtime metrics collector (optional, for --observe)
```

**No renderer scripts.** Claude reads knowledge.json and generates XML directly
using the rules in this file and the style templates in component_library.json.

Project output files (project-specific, per project):
```
<output_dir>/                 # Default: docs/architecture/
  knowledge.json              # Project knowledge DB
  <project>_architecture.drawio
  architecture_L1_service.png
  architecture_L2_component.png
  architecture_<l3_page_id>.png   # One per l3_pages entry (e.g., architecture_l3-eks.png)
  verification_report.json
```

Resolve `<skill_dir>` by locating the directory containing this SKILL.md file.
Resolve `<output_dir>` from `knowledge.json → meta.output_dir` or default to `docs/architecture/`.

---

## Sub-command Router

Parse `$ARGUMENTS` to determine action:

| Command | Mode | Action |
|---------|------|--------|
| `/architecture draw <description>` | Direct | Natural language → knowledge.json → drawio → verify → export → visual review |
| `/architecture scan [--aws] [--observe]` | Code scan | Scan codebase → knowledge.json → (optionally enrich with runtime data) → drawio → verify → export → visual review |
| `/architecture observe` | Observe | Collect runtime metrics (CloudWatch, X-Ray, Alarms) → enrich knowledge.json |
| `/architecture knowledge show` | Knowledge DB | Display current knowledge.json contents |
| `/architecture knowledge collect` | Knowledge DB | Scan codebase → generate/update knowledge.json only |
| `/architecture knowledge edit` | Knowledge DB | Interactive edit of knowledge.json |
| `/architecture verify` | Standalone | Verify existing drawio against knowledge.json |
| `/architecture export` | Standalone | Export drawio pages to PNG |
| `/architecture status` | Standalone | Show knowledge freshness, verification state, PNG dates |
| `/architecture` (no args) | Full pipeline | Equivalent to `scan` → full pipeline |

---

## knowledge.json Schema

This is the **single source of truth** for all diagram generation and verification.
Every entity has a `levels` array that determines which diagram pages include it.

```json
{
  "meta": {
    "project_name": "string — human-readable project name",
    "generated_at": "ISO 8601 timestamp",
    "source_mode": "scan | direct | manual",
    "source_files_hash": "SHA-256 hex string | null (only for scan mode)",
    "cloud_provider": "aws | gcp | azure | none",
    "region": "string | null",
    "output_dir": "docs/architecture",
    "drawio_filename": "<project>_architecture.drawio"
  },

  "system": {
    "name": "string — system display name (used for diagram titles)",
    "purpose": "string — 1-2 sentence description of what this system does and why",
    "key_capabilities": [
      "string — each capability is one line describing a major function"
    ],
    "scenarios": [
      {
        "name": "string — scenario display name",
        "description": "string — what happens in this scenario end-to-end",
        "fault_target": "service-id — which service is affected",
        "expected_alarm": "string — alarm name that fires"
      }
    ],
    "phases": [
      {
        "id": "string — phase identifier (e.g., inject, detect, investigate, evaluate)",
        "name": "string — phase display name (e.g., Inject Fault)",
        "sequence": "number — 1-based order",
        "description": "string — what happens in this phase",
        "services": ["service-id — services involved in this phase"]
      }
    ],
    "level_purpose": {
      "L1": "string — the question L1 diagram must answer",
      "L2": "string — the question L2 diagram must answer",
      "L3": "string — the question L3 diagram must answer"
    }
  },

  "flows": [
    {
      "id": "string — unique flow identifier (kebab-case)",
      "name": "string — human-readable flow name",
      "purpose": "string — WHY this flow exists (L1 edge label)",
      "phase": "string | null — phase id this flow belongs to",
      "hops": [
        {
          "from": "service-id",
          "to": "service-id",
          "label": "string — SHORT action (≤20자). 동사+목적어만. 괄호·예시·경로·API 세부 금지",
          "detail": "string — protocol/API 세부 (e.g., '(POST /hash)', '(InvokeModel) context')",
          "edge_style": "app_internal | fault_injection | ...",
          "levels": ["L1", "L2"]
        }
      ]
    }
  ],
  // flows[] is the SOURCE OF TRUTH for all service-to-service communication.
  // data_flows[] is DERIVED by flattening flows[].hops[] (kept for verifier compatibility).
  // hop.label / hop.detail 분리 규칙:
  //   label: 짧은 동작 (≤20자). 동사+목적어만. 예: "chaos API 호출", "조사 결과 저장"
  //   detail: 프로토콜, API 경로, 파라미터. 예: "(POST /hash)", "(PutItem) findings"
  //   금지: label에 괄호, 예시 목록, API 경로 포함 (→ detail로 이동)
  // To construct L1 labels: use hop.label only
  // To construct L2/L3 labels: use hop.label + " " + hop.detail

  "services": [
    {
      "id": "unique-kebab-id",
      "name": "Service brand name only — NO role prefix/suffix (see Service Naming Rules)",
      "stereotype": "Stateless:HTTP | Persistence | EventHandler | Orchestrator | ... (see UML Stereotype Rules)",
      "category": "cloud_managed | k8s_workload | k8s_service | external | user | custom",
      "provider_type": "aws.lambda | k8s.deployment | k8s.service | external.github | user.operator | ...",
      "icon_template": "aws_icon | aws_icon_custom | k8s_deployment | k8s_service | k8s_resource | user_actor | service_box",
      "icon_params": {
        "service": "lambda",
        "fill": "#ED7100"
      },
      "namespace": "string | null — K8s namespace this belongs to",
      "levels": ["L1", "L2", "L3"],
      "l3_detail": {
        "type": "detail_card | collapsed_icon | component_breakdown",
        "components": [
          {
            "name": "Component Name",
            "role": "input | processing | output",
            "description": "What this component does"
          }
        ],
        "fields": {
          "image": "ECR/worker:latest",
          "cpu": "50m / 200m",
          "memory": "64Mi / 128Mi",
          "port": "80",
          "probes": "readiness: GET /:80:5s",
          "replicas": "1",
          "otel": "inject-python",
          "env": "REDIS_HOST=redis"
        }
      },
      "group": "string | null — group name this belongs to"
    }
  ],

  "namespaces": [
    {
      "name": "my-namespace",
      "display_label": "ns: my-namespace",
      "color_index": 0,
      "levels": ["L2", "L3"]
    }
  ],

  "boundaries": [
    {
      "type": "account | region | vpc | cluster | subnet",
      "label": "AWS Account 123456789",
      "levels": ["L2"],
      "nesting_order": 1
    }
  ],

  "data_flows": [
    {
      "from": "service-id",
      "to": "service-id",
      "label": "POST /hash",
      "edge_style": "app_internal | fault_injection | alarm_pipeline | investigation | llm_evaluation | data_query | observability | deployment",
      "levels": ["L1", "L2"],
      "sequence": 1
    }
  ],
  // ⚠ DATA FLOW INTEGRITY RULE:
  // Every data_flow MUST represent an ACTUAL service-to-service communication.
  // The `levels` array is a FILTER (show/hide), NOT a license to create alternate versions.
  // If the real path is A → B → C, there MUST be two flows (A→B, B→C).
  // Creating a shortcut flow A→C is FORBIDDEN — it is a phantom edge.
  // The same flow entry can appear at multiple levels (L1, L2, L3).
  // There must NEVER be two different flow entries for the same logical connection
  // with different from/to values at different levels.

  "groups": [
    {
      "name": "monitoring",
      "display_label": "Monitoring",
      "color": {"fill": "#FFF3E0", "stroke": "#FF9800"},
      "members": ["cloudwatch", "sns", "eventbridge"],
      "levels": ["L2"]
    }
  ],

  "l3_pages": [
    {
      "id": "l3-eks",
      "name": "L3 — EKS Resource View",
      "focal_service_ids": ["dashboard", "webui", "worker", "hasher", ...],
      "collapsed_service_ids": ["cloudwatch", "bedrock", "dynamodb", ...]
    },
    {
      "id": "l3-serverless",
      "name": "L3 — Serverless Pipeline",
      "focal_service_ids": ["lambda-event-handler", "sns", "eventbridge", ...],
      "collapsed_service_ids": ["cloudwatch", "agent-space", ...]
    }
  ]
}
```

---

## Input Mode: Direct (`/architecture draw <description>`)

1. Parse the natural language description to identify:
   - Services/components mentioned
   - Cloud provider (default: none)
   - Relationships/data flows
   - Scale hints (microservice, monolith, serverless, etc.)

2. Build knowledge.json from the description:
   - Create service entries with appropriate categories
   - Assign levels: all mentioned services get L1+L2, detailed ones get L3
   - Infer data flows from the description
   - Use sensible defaults for missing info

3. Write knowledge.json to `<output_dir>/knowledge.json`

4. Proceed to **Generate → Verify Loop → Export**

---

## Input Mode: Code Scan (`/architecture scan [--aws]`)

### Scan Playbook

1. **Detect project type** — scan for these patterns:
   ```
   infrastructure/cloudformation/*.yml  → CloudFormation
   *.tf, terraform/                     → Terraform
   kubernetes/, k8s/, helm/             → Kubernetes manifests
   docker-compose.yml                   → Docker Compose
   cdk.json, lib/*.ts                   → AWS CDK
   serverless.yml                       → Serverless Framework
   Dockerfile, services/*/Dockerfile    → Container services
   .github/workflows/                   → GitHub Actions CI/CD
   ```

2. **Parse infrastructure files** — extract:
   - Resource types and names (e.g., `AWS::EKS::Cluster`, `AWS::RDS::DBInstance`)
   - Configuration details (instance types, versions, scaling, ports)
   - IAM roles and policies
   - Security groups and networking

3. **Parse Kubernetes manifests** — extract:
   - Deployments, Services, CronJobs, ConfigMaps
   - Container images, resource requests/limits
   - Probes, ports, environment variables
   - RBAC, ServiceAccounts
   - Instrumentation/OTEL annotations

4. **Parse application source** — scan `services/` or `src/` for:
   - Exposed ports (Dockerfile EXPOSE, Flask/Express listen)
   - API endpoints
   - Database connections (connection strings in env)
   - Inter-service communication patterns

5. **Trace scenario flows** (TOP-DOWN) — this is the most critical step:

   For each `system.phase` (or `system.scenario` if phases aren't defined):
   a. Ask: "What is the end-to-end path through the system for this phase?"
   b. Trace the ACTUAL path from trigger to final destination, recording every service touched
   c. Each service-to-service hop becomes an entry in `flow.hops[]`
   d. For each hop, write TWO labels:
      - `label`: WHAT data enters this service and WHAT comes out changed. Each hop
        must show what THIS service transforms or decides — not just "passes through".
        Self-test: if the label could apply to any system ("forwards X", "stores Y"),
        it's too abstract. Show what's specific to THIS hop.
      - `detail`: the PROTOCOL/METHOD used (how it works) — e.g., "(SNS Publish)"
   e. The flow's `purpose` field is the one-sentence summary of the entire flow

   **This step REPLACES guessing connections from env vars.** Env var scanning (step 6)
   is supplementary — it discovers data-access flows (DB queries, cache reads) that aren't
   part of the main scenario paths. But the primary scenario flows MUST come from
   actually tracing what happens when a scenario runs.

   **Key principle**: if you can't trace the flow by reading the code/infra, the architecture
   is not understood well enough to draw it. Don't guess — investigate.

6. **Infer data flows** from:
   - Environment variable references (`REDIS_HOST=redis`, `DB_HOST=rds-endpoint`)
   - CloudFormation `Ref`/`GetAtt`/`Fn::Sub` cross-references
   - Terraform `module.X.output` / `data.X` references
   - K8s Service selectors → Deployment labels
   - SNS→Lambda subscriptions, EventBridge rules

   **CRITICAL: Each flow = one real hop.** If CloudWatch sends an alarm that goes through
   SNS → Lambda → Agent, that is 3 separate flows (CW→SNS, SNS→Lambda, Lambda→Agent),
   NOT 1 shortcut flow (CW→Agent). Trace the full path through every intermediate service.
   Never collapse intermediate services into a direct connection.

7. **Classify each component**:
   | Source | category | icon_template |
   |--------|----------|---------------|
   | AWS managed service (RDS, DynamoDB, etc.) | `cloud_managed` | `aws_icon` |
   | K8s Deployment | `k8s_workload` | `k8s_deployment` |
   | K8s Service | `k8s_service` | `k8s_service` |
   | K8s CronJob/Job | `k8s_workload` | `k8s_resource` (prIcon=cronjob/job) |
   | External service (GitHub, Slack) | `external` | `service_box` |
   | Human operator | `user` | `user_actor` |
   | In-cluster datastore (Redis, etc.) | `k8s_workload` | `k8s_deployment` |

8. **Assign levels** — L1 follows a strict "flow tracing" method:
   - **L1 assignment procedure**:
     1. Trace each end-to-end flow from trigger to final destination
     2. Every service on that path is an L1 service — no exceptions
     3. If `A → B → C → D` is the real path, then A, B, C, D are ALL L1 services
     4. NEVER skip intermediate services. If B and C are "just" SNS and Lambda, they
        are still L1 services because the flow physically passes through them
     5. The L1 data_flows are the same real flows, just tagged with "L1" in levels
   - L2: Everything (all services, namespaces, boundaries)
   - L3: Services with rich configuration (K8s workloads with probes/resources/env, cloud services with detailed config)
   - L3 detail type: `detail_card` for K8s workloads with config, `collapsed_icon` for cloud services shown in sidebar

9. **Assign icon_params** — look up each service in `icon_registry.json`:
   - For AWS: match service name → get `fill` color and category
   - For K8s: match resource type → get `prIcon`
   - Use `component_library.json` template variables

10. **Compute source hash** and write knowledge.json

### `--aws` Flag
When `--aws` is specified, additionally:
- Attempt to discover live AWS resources (if AWS CLI configured)
- Cross-reference discovered resources with code-defined ones
- Flag drift between code and live state

---

## Input Mode: Knowledge DB

### `/architecture knowledge show`
Read and display `<output_dir>/knowledge.json` as a formatted summary:
- Meta info (project, provider, mode, freshness)
- Service count by category
- Namespace list
- Data flow summary
- Groups

### `/architecture knowledge collect`
Run the **Scan Playbook** above, write knowledge.json, but do NOT generate diagrams.
Report what was discovered.

### `/architecture knowledge edit`
Interactive mode:
1. Read current knowledge.json
2. Ask user what to change (add service, modify flow, rename, etc.)
3. Apply changes
4. Write updated knowledge.json
5. Set `source_mode` to `manual` and clear `source_files_hash`

---

## Runtime Observability (`/architecture observe` or `--observe` flag)

### Overview

The `--observe` mode queries live AWS APIs to collect runtime metrics and enriches
knowledge.json with actual performance data. This enables "living" diagrams that
show real throughput, latency, error rates, and alarm states.

### Usage

```bash
# Standalone: collect runtime data only (enrich knowledge.json)
/architecture observe

# Combined: scan + observe + generate + verify + export
/architecture scan --observe
```

### How It Works

Run `observe.py` from the skill directory:
```bash
python3 <skill_dir>/observe.py \
  --knowledge <output_dir>/knowledge.json \
  --output <output_dir>/knowledge.json \
  --region us-east-1 \
  --cluster devops-agent-test-cluster \
  --namespace dockercoins \
  --hours 3
```

Config can also be loaded from `services/dashboard/config.yaml` via `--config`.

### Data Sources (3 collectors)

| Collector | AWS API | Data Produced |
|-----------|---------|---------------|
| `collect_service_metrics()` | CloudWatch (Namespace: ApplicationSignals) | Per-service: error_count, fault_count, avg/p99 latency, request_count, health |
| `collect_trace_topology()` | X-Ray (get_trace_summaries + batch_get_traces) | Per-edge: call_count, avg_latency, error_rate; shadow dependency discovery |
| `collect_alarm_states()` | CloudWatch (describe_alarms) | Per-alarm: name, state (OK/ALARM/INSUFFICIENT_DATA), reason |

### knowledge.json Schema Extensions

**Service `observed` field** (added to each service that has Application Signals data):
```json
{
  "id": "hasher",
  "observed": {
    "collected_at": "2026-04-15T08:00:00Z",
    "window_hours": 3,
    "error_count": 5,
    "fault_count": 0,
    "avg_latency_ms": 12.3,
    "p99_latency_ms": 45.1,
    "request_count": 16200,
    "health": "healthy"
  }
}
```

**Data flow `observed` field** (added to flows with matching X-Ray traces):
```json
{
  "from": "worker", "to": "hasher-svc",
  "observed": {
    "collected_at": "2026-04-15T08:00:00Z",
    "call_count": 16200,
    "avg_latency_ms": 12.3,
    "error_rate": 0.003,
    "last_seen": "2026-04-15T07:58:12Z",
    "source": "xray"
  }
}
```

**`observed_flows`** (top-level, new — shadow dependencies found in X-Ray but not in static flows):
```json
"observed_flows": [
  {
    "from": "webui", "to": "rng-svc",
    "call_count": 50,
    "avg_latency_ms": 8.1,
    "note": "shadow dependency - discovered via X-Ray, not in static data_flows"
  }
]
```

**`alarms`** (top-level, current CloudWatch alarm states):
```json
"alarms": [
  {"name": "hasher-error-rate", "state": "OK", "reason": "..."},
  {"name": "rng-latency-p99", "state": "ALARM", "reason": "Threshold exceeded"}
]
```

**`meta` extensions**: `last_observed_at`, `observe_window_hours`

### Observed Data Rendering Rules

When knowledge.json contains `observed` fields, apply these visual modifiers on L2 and L3:

| Visual Property | Source | Rule |
|----------------|--------|------|
| **Edge thickness** | `flow.observed.call_count` | 1-4px, log scale (1→1px, 10→2px, 100→3px, 1000+→4px) |
| **Edge color overlay** | `flow.observed.error_rate` | > 0.05 → red dashed overlay (`strokeColor=#D32F2F;dashed=1`) |
| **Node border** | `service.observed.health` | healthy=default, degraded=orange(`#FF9800`), unhealthy=red(`#D32F2F`) |
| **Alarm badge** | `alarms[].state == "ALARM"` | Red warning icon overlay on the affected service node |
| **Shadow dependency** | `observed_flows[]` | Dashed line + "discovered" label (`dashed=1;strokeColor=#9E9E9E`) |
| **Metric annotation** | `service.observed` | L3 detail card gets extra rows: `latency: Xms / throughput: Y req/s / errors: Z%` |

### Requirements

- `boto3` (Python) — AWS SDK
- Valid AWS credentials (env vars, AWS profile, or IRSA)
- IAM permissions: `cloudwatch:GetMetricStatistics`, `cloudwatch:DescribeAlarms`, `xray:GetTraceSummaries`, `xray:BatchGetTraces`

---

## Diagram Generation Rules

Claude generates mxGraph XML directly by reading these three files:
1. `knowledge.json` — what to render (services, flows, boundaries)
2. `component_library.json` — how to style it (templates, edge styles, colors)
3. `icon_registry.json` — icon lookups (AWS, K8s, GCP shapes)

### General Rules (ALL levels)
- Wrap all pages in: `<mxfile host="Claude" agent="Claude Code — /architecture">`
- Use `<mxGraphModel adaptiveColors="auto">` for dark mode support
- Page count: `2 + len(l3_pages)` `<diagram>` pages: L1, L2, then one L3 per `l3_pages` entry
  - If `l3_pages` is not defined, fall back to a single L3 page (backward compatible)
  - Page names: use `l3_pages[i].name` for each L3 page (e.g., "L3 — EKS Resource View")
- All vertex cells: `sketch=0;html=1` for clean rendering with HTML labels
- Edges: `edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;` — ELK auto-routes, no waypoints needed
- Every edge must contain `<mxGeometry relative="1" as="geometry"/>` child element
- Use rigid grid for placement (see draw.io XML Generation Rules above)
- Font sizes from `component_library.json → layout_guidelines.font_sizes`
- No XML comments (`<!-- -->`) — they waste tokens and can cause parse errors

### L1 Rules — Service View (1km)

**Filter**: `knowledge.services` where `"L1" in levels`
**Filter**: `knowledge.data_flows` where `"L1" in levels`

Rendering:
- Each service rendered using its `icon_template` from `component_library.json`
  - `cloud_managed` → provider icon (aws_icon, gcp_icon, etc.)
  - `user` → user_actor template
  - `custom`/`external` → service_box template
- **NO** K8s icons (deploy/svc) on L1
- **NO** namespace containers on L1
- **NO** port numbers or internal details
- Edge labels from `flows[].hops[].label` (what-flows + what-changes, NOT protocol)
  - GOOD L1 labels show what data enters and what THIS service produces:
    "threshold breach → alarm", "subscription match → Lambda invoked"
  - BAD L1 labels use generic verbs: "forwards alarm", "delivers alarm", "stores results"
  - Protocol details (SNS Publish, PutItem) live in hop.detail, shown at L2 only
- Flow legend at bottom showing edge colors and meanings
- Canvas size from `layout_guidelines.canvas_defaults.L1` (default ~1400×780)

### L2 Rules — Component View (100m)

**Filter**: All entities where `"L2" in levels`

Rendering:
- **Boundaries**: Render `knowledge.boundaries` as nested boxes (sorted by `nesting_order`)
  - Use `boundary_box` template from component_library
  - Colors from `color_palettes.boundary_colors`

- **Namespaces**: Render `knowledge.namespaces` as colored containers
  - Use `namespace_container` template
  - Color by `color_index` → `color_palettes.namespace_colors[index]`
  - Label: `display_label` (e.g., "ns: dockercoins")

- **Services inside namespaces**:
  - `k8s_workload` → `k8s_deployment` icon (prIcon=deploy, fill=#326CE5)
  - `k8s_service` → `k8s_service` icon (prIcon=svc, fill=#326CE5)
  - K8s CronJob/Job → `k8s_resource` icon with appropriate prIcon
  - In-cluster datastores (Redis, etc.) → `k8s_deployment` icon (**NEVER** cloud provider icon)

- **Services outside cluster boundary**:
  - `cloud_managed` → provider icon from icon_registry
  - `external` → service_box

- **Groups**: Render `knowledge.groups` as dashed containers
  - Use `group_container` template
  - Include member services inside

- **Data flows**: Render all L2 hops from `flows[].hops[]` where `"L2" in hop.levels`
  - Edge labels: `hop.label + " " + hop.detail` (e.g., "forwards alarm (SNS Publish)")
  - Match `edge_style` field to `component_library.edge_styles`

- **Legend**: Auto-generate from edge_styles used in the diagram
- Canvas size from `layout_guidelines.canvas_defaults.L2` (default ~2000×1400)

### L3 Rules — Resource View (1m) — Multi-Page

**Key principle**: Each major service area gets its own L3 page. One page shows ONE focal
area in resource-level detail; everything else on that page is collapsed to service-level icons.

**Page generation**: Iterate `knowledge.json → l3_pages`:
```
for each l3_page in knowledge.l3_pages:
    create <diagram name="{l3_page.name}" id="{l3_page.id}">
    focal_services  = services where id in l3_page.focal_service_ids
    collapsed_services = services where id in l3_page.collapsed_service_ids
    relevant_flows  = data_flows where (from in all_ids AND to in all_ids AND "L3" in levels)
```

Each L3 page has two zones:

**Focal Area (left, ~70% width)** — resource-level detail of the focal services:
- For `k8s_workload` focal services:
  - Render K8s deploy icon (48×48) as the **primary visual element**
  - Detail card beside/below with fields from `l3_detail.fields`
  - K8s icon sizes: deploy=48×48, svc=40×40 (MUST be large enough to be visible in exported PNG)
- For `k8s_service` focal services:
  - Render K8s svc icon (40×40) + compact service card
- For `cloud_managed` focal services (e.g., Lambda, Bedrock, DynamoDB):
  - Render AWS provider icon (48×48) as primary visual
  - Detail card beside with fields from `l3_detail.fields` (runtime, memory, table config, etc.)
- Render relevant namespace boundaries if focal services have namespaces
- Add deploy-to-svc connection arrows within the focal area
- Data flow arrows between focal services

**Collapsed Area (right, ~30% width)** — service-level sidebar:
- For each service in `collapsed_service_ids`:
  - Render provider icon (48×48) + brief annotation text
  - These are context services shown at L1 abstraction level
- Connect collapsed services to focal services with cross-cutting arrows

**Canvas size**: from `layout_guidelines.canvas_defaults.L3` (default ~2000×1700)

**Backward compatibility**: If `l3_pages` is not defined in knowledge.json, generate a
single L3 page using all services where `"L3" in levels`, with `detail_card` types in
the focal area and `collapsed_icon` types in the sidebar.

### CRITICAL Icon Rules

These are **mandatory** and the verifier will catch violations:

1. **AWS managed service** → `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{service}`
   - Look up `fill` color from `icon_registry.json → providers.aws.services.{name}.fill`

2. **K8s Deployment** → `shape=mxgraph.kubernetes.icon2;kubernetesLabel=1;prIcon=deploy;fillColor=#326CE5`

3. **K8s Service** → `shape=mxgraph.kubernetes.icon2;kubernetesLabel=1;prIcon=svc;fillColor=#326CE5`

4. **K8s other resource** → `shape=mxgraph.kubernetes.icon2;kubernetesLabel=1;prIcon={prIcon};fillColor=#326CE5`
   - Look up `prIcon` from `icon_registry.json → kubernetes.resources.{type}.prIcon`

5. **In-cluster datastore** (Redis, Memcached, etc.) → K8s deploy icon, **NEVER** cloud-managed icon (e.g., NEVER `aws4.elasticache`)

6. **L3 K8s workloads** → MUST have K8s icons alongside detail cards. A detail card alone is NOT sufficient.

7. **Custom shape services** (ALB, NLB) → Use `aws_icon_custom` template with `override_style` from icon_registry

---

## Verification Loop

After generating the drawio file, run verification:

```
attempt = 0, max_attempts = 3
LOOP:
  Run verifier:
    python3 <skill_dir>/verify_drawio.py \
      --drawio <output_dir>/<drawio_filename> \
      --knowledge <output_dir>/knowledge.json \
      --icons <skill_dir>/icon_registry.json \
      --library <skill_dir>/component_library.json \
      --output <output_dir>/verification_report.json

  Read verification_report.json

  IF status == "PASS":
    Log "✓ Verification passed on attempt {attempt+1}"
    Proceed to Export
    BREAK

  IF attempt >= max_attempts:
    Log "✗ FAILED after 3 attempts. Remaining failures:"
    List each failure with category, check, message, suggestion
    STOP — do not export broken diagrams
    BREAK

  FOR each failure in report.failures:
    Read the suggestion field
    Apply the fix to the drawio XML:
      icon_consistency → find the cell, replace style with correct one from icon_registry
      l3_compliance → add missing K8s icons paired with detail cards
      l2_compliance → add missing namespace containers or icons
      completeness → add missing service to the appropriate level page
      forbidden_combo → replace forbidden icon with the correct template
      structure → fix page names or add missing pages

  Write corrected drawio file
  attempt += 1
  GOTO LOOP
```

---

## Export

Convert each diagram page to PNG. Page count is dynamic: L1 + L2 + N×L3 pages.

```bash
DRAWIO_PATH="<output_dir>/<drawio_filename>"

# Fixed pages: L1 and L2
/opt/homebrew/bin/drawio --export --format png --scale 2 --border 20 \
  --page-index 1 --output "<output_dir>/architecture_L1_service.png" \
  "$DRAWIO_PATH" || true

/opt/homebrew/bin/drawio --export --format png --scale 2 --border 20 \
  --page-index 2 --output "<output_dir>/architecture_L2_component.png" \
  "$DRAWIO_PATH" || true

# Dynamic L3 pages: iterate knowledge.json → l3_pages
# page-index is 1-based, so L3 pages start at index 3
for i, l3_page in enumerate(knowledge["l3_pages"]):
    page_index = 3 + i   # 1-based
    slug = l3_page["id"]  # e.g., "l3-eks", "l3-serverless"
    /opt/homebrew/bin/drawio --export --format png --scale 2 --border 20 \
      --page-index $page_index \
      --output "<output_dir>/architecture_${slug}.png" \
      "$DRAWIO_PATH" || true

# If no l3_pages defined (backward compat), export single L3:
/opt/homebrew/bin/drawio --export --format png --scale 2 --border 20 \
  --page-index 3 --output "<output_dir>/architecture_L3_resource.png" \
  "$DRAWIO_PATH" || true
```

**IMPORTANT**: `--page-index` is **1-based** (1=first page). `|| true` because draw.io CLI sometimes exits non-zero despite success.

After export, verify PNGs:
```bash
ls -la <output_dir>/architecture_*.png
md5 <output_dir>/architecture_*.png
```
All PNGs must exist, be non-zero size, and have **different** MD5 hashes.

---

## Visual Quality Review — Look Before You Ship

After exporting PNGs, visually inspect EACH page before declaring success.
This step mimics what a human architect does: look at the output and fix issues.

### Mandatory Visual Check Process

For each exported PNG:
1. **Read the PNG** using the Read tool (multimodal image inspection)
2. **Ask these questions** while looking at the image:
   - **Readability**: Can you read ALL service names and edge labels without zooming?
   - **Spacing**: Are there areas where icons/boxes overlap or are too cramped?
   - **Edge routing**: Do any edges take unnecessarily long detour paths?
     Look for: giant U-shapes, "bottom highway" edges that run along the canvas floor,
     edges that route around the entire diagram perimeter
   - **Balance**: Is the diagram balanced, or is one side dense and the other empty?
   - **Flow**: Does the left-to-right phase flow read naturally?
   - **Boundaries**: Do namespace/VPC/cluster containers tightly wrap their contents
     without excessive empty padding?

3. **If ANY issue is found**:
   - Identify the specific cells/edges that need coordinate adjustment
   - Fix the coordinates in the drawio XML (move vertices, adjust edge waypoints)
   - Re-export that specific page's PNG
   - Re-inspect the new PNG
   - Maximum 2 visual fix iterations per page

### Common Visual Fixes

| Issue | Root Cause | Fix |
|-------|-----------|-----|
| Giant U-shape edge | Source and target in different zones | Add explicit waypoints for a shorter path through the cross-zone corridor |
| Bottom highway edges | Edges routed along canvas floor (y > canvas_h - 200) | Reroute through intermediate y-band between services |
| Cramped namespace | Too many icons, container too small | Increase container width/height, redistribute internal spacing |
| Unbalanced density | K8s cluster left, AWS services far right, empty center | Reduce gap between zones; use full canvas width evenly |
| Tiny text in PNG | Font sizes too small at export scale | Increase fontSize on labels; minimum 10 for service names, 9 for stereotypes |
| Overlapping labels | Edge labels collide with service names | Move edge label position (use mxGeometry relative=1 with x/y offset) |

### Pipeline With Visual Review

The complete pipeline is now:
```
Read knowledge.json → Claude generates mxGraph XML directly
    ↓
Write .drawio file
    ↓
Verify Loop (structural + layout quality) — max 3 attempts
    ↓
Export PNGs (sequentially, one page at a time)
    ↓
Visual Review (read each PNG, check quality)
    ↓
If issues found → Fix XML → Re-export → Re-inspect (max 2 per page)
    ↓
Done — report results
```

**No Python renderers, no external scripts.** Claude reads knowledge.json,
applies the draw.io XML rules from this skill, and writes XML directly.
ELK handles edge routing automatically.

---

## Status (`/architecture status`)

Display a summary table:

1. Check `<output_dir>/knowledge.json`:
   - Exists? Read `meta.generated_at`, `meta.source_mode`, `meta.project_name`
   - If `source_mode == "scan"`: run staleness check (compare `source_files_hash`)

2. Check `<output_dir>/<drawio_filename>`:
   - Exists? Show modification date

3. Check `<output_dir>/verification_report.json`:
   - Exists? Read `status`, `summary.passed`, `summary.total`

4. Check PNGs:
   - L1 and L2 PNGs — existence and modification dates
   - For each `l3_pages` entry: check `architecture_{id}.png` existence

Format:
```
📊 Architecture Status — <project_name>
═══════════════════════════════════════════
Component              Status          Last Modified
────────────────────   ──────────────  ─────────────
Knowledge DB           FRESH (scan)    2026-04-15
draw.io XML            EXISTS (5 pg)   2026-04-15
Verification           PASS (95/95)    2026-04-15
L1 PNG                 EXISTS          2026-04-15
L2 PNG                 EXISTS          2026-04-15
L3: EKS Resource       EXISTS          2026-04-15
L3: Serverless         EXISTS          2026-04-15
L3: AI Pipeline        EXISTS          2026-04-15
```

---

## Standalone Verify (`/architecture verify`)

1. Locate knowledge.json in `<output_dir>/`
2. Locate drawio file from `knowledge.json → meta.drawio_filename` or scan for `*.drawio`
3. Run verifier with full paths
4. Display results

---

## Error Handling

- If knowledge.json does not exist → prompt user: "No knowledge.json found. Run `/architecture knowledge collect` or `/architecture draw <description>` first."
- If drawio file does not exist → prompt user to generate first
- If draw.io CLI not found at `/opt/homebrew/bin/drawio` → check `which drawio`, try `npx -p @mxgraph/drawio-desktop drawio`
- If verifier python3 not available → suggest `brew install python3`

---

## Template Reference (Quick Lookup)

When generating mxGraph XML, use these exact style strings from `component_library.json`:

### Icons
| Template | Usage | Key Style Fragment |
|----------|-------|--------------------|
| `aws_icon` | AWS managed service | `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{service}` |
| `aws_icon_custom` | ALB, NLB, etc. | `{shape_override}` from icon_registry |
| `gcp_icon` | GCP service | `shape=mxgraph.gcp2.{service}` |
| `k8s_deployment` | K8s Deployment | `prIcon=deploy;fillColor={fill}` |
| `k8s_service` | K8s Service | `prIcon=svc;fillColor={fill}` |
| `k8s_resource` | K8s Pod/CronJob/etc. | `prIcon={prIcon};fillColor={fill}` |
| `user_actor` | Human operator | `resIcon=mxgraph.aws4.user` |

### Containers
| Template | Usage |
|----------|-------|
| `namespace_container` | K8s namespace boundary |
| `boundary_box` | Account/VPC/Cluster boundary |
| `group_container` | Service group (monitoring, lambda) |

### Cards (L3)
| Template | Usage |
|----------|-------|
| `detail_card` | Resource detail (multi-line fields) |
| `service_card` | Compact service detail |
| `collapsed_annotation` | Annotation text for collapsed icons |

### Edges
| Style Name | Color | Pattern | Usage |
|------------|-------|---------|-------|
| `app_internal` | #1565C0 (blue) | solid | Application traffic |
| `fault_injection` | #D32F2F (red) | dashed | Chaos/fault injection |
| `alarm_pipeline` | #E65100 (orange) | solid | Monitoring/alarms |
| `investigation` | #2E7D32 (green) | solid | Observability/investigation |
| `llm_evaluation` | #7B1FA2 (purple) | solid | AI/LLM processing |
| `data_query` | #999 (gray) | dashed | Data flow/storage |
| `observability` | #E91E63 (pink) | dashed | Metrics/traces/logs |
| `deployment` | #FF9800 (amber) | dashed | CI/CD / image pull |
