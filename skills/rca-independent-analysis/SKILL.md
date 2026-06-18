---
name: rca-independent-analysis
description: >
  Always perform root cause analysis independently using only current
  observable data. Never reference previous investigation history or
  past incident patterns.
agent_types:
  - Incident RCA
  - Generic
---

# Independent RCA Analysis

## Core Rule
Every root cause analysis MUST be performed independently using only current data.

## Do NOT
- Reference previous investigation conclusions or runbook history
- Assume root cause based on past incidents
- Skip analysis steps because a similar issue was seen before
- Use phrases like "previously identified", "known pattern", or "consistent with prior findings"

## DO
- Analyze current metrics, logs, traces, and code from scratch
- Form hypotheses based only on current observable evidence
- Verify each hypothesis with live data
- Treat every incident as a new, unique event
