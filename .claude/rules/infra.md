---
paths:
  - services/dashboard/cluster_manager.py
  - services/dashboard/account_registry.py
  - services/dashboard/topology_provider.py
  - services/dashboard/credential_resolver.py
  - services/dashboard/execution_context.py
---

# Infrastructure / Multi-Account 규칙

- AccountRegistry: config.yaml + env + Agent Space associations 병합 (Agent Space = single source of truth)
- TopologyProvider: kubectl scan, service→(account, context, profile), 60s refresh
- CredentialResolver: profile 우선 → STS fallback, 55min cache
- Cross-account DevOps Agent 호출: `Configuration.SourceAws` 사용 (Aws 아님)
- AWS association 시 EKS access entry 자동 생성됨
- 누락 인프라는 분류 + fix command 포함해서 surface — 무시 금지
- 인프라 상태 확인은 IaC(CFn stack outputs, CDK context)와 app API를 통해서만 — 거짓 응답/더미 데이터 생성 절대 금지
