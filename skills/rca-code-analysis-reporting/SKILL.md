---
name: rca-code-analysis-reporting
description: >
  When investigating incidents that may involve code bugs, memory leaks,
  or application-level issues, always perform source code analysis using
  the connected GitHub repository. Include specific code snippets, file paths,
  and line numbers in investigation findings.
agent_types:
  - Incident RCA
  - Generic
---

# Code Analysis Reporting

When investigating incidents, always check the source code for root cause evidence.

## When to Analyze Code
- OOMKilled or memory-related issues → look for memory leaks, unbounded caches, missing cleanup
- HTTP errors (4xx, 5xx) → look for input validation, error handling
- Performance issues → look for sleep(), blocking calls, inefficient algorithms
- Configuration issues → look for environment variable usage, feature flags

## Required Output Format
When you find relevant code, ALWAYS include in your findings:

1. **File path**: Full path from repository root
2. **Line numbers**: Specific line range
3. **Code snippet**: The actual code block causing or related to the issue
4. **Explanation**: Why this code is relevant to the incident
