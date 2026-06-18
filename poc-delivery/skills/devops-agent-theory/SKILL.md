---
name: devops-agent-theory
description: >
  DevOps Agent 이론, 아키텍처, 설계 원칙.
  "이게 뭐야?", "왜?", "어떤 구조야?" 류의 질문에 적용.
---

# DevOps Agent 이론

## 핵심 개념

| 개념 | 비유 | 실체 |
|------|------|------|
| Agent Space | 에이전트의 사무실 | 모니터링 대상 앱의 최상위 컨테이너 |
| Association | 전화기 연결 | 데이터소스를 Space에 바인딩 |
| Service | 전화기 개통 | 데이터소스 접속 정보 등록 (URL, 토큰) |
| Private Connection | VPN 전용선 | VPC Lattice 기반 리소스 게이트웨이. Agent ENI가 VPC 내부 접근 |
| Skill | 업무 매뉴얼 | Knowledge Item으로 배포되는 Agent 행동 규칙 |
| Session | 진행 중인 대화 | executionId 기반 영구 채팅 |
| Memory | 업무 노트 | 세션 간 맥락 유지 |
| App 태그 | 담당 구역 | Agent 접근 범위 제한 메커니즘 |

## 설계 원칙

1. **Agent = 읽기 전용** — 분석만. 변경은 앱/사람이 실행
2. **App 태그 = 경계** — 태그가 없는 리소스는 Agent가 볼 수 없음
3. **Skill = 배포된 지식** — 프롬프트는 휘발, Skill은 영구
4. **Session = 영구 재사용** — executionId는 영구 ID
5. **Cross-account = SourceAws** — Secondary 계정은 Aws가 아닌 SourceAws

## 아키텍처

```
Agent Space
├── AWS Association (CloudWatch, X-Ray, EKS)
├── GitLab Association (코드 분석)
├── Splunk Association (APM 트레이스)
└── Skills (행동 규칙)
         │
         ▼ InvokeAgent API
   Foundation Model (Claude via Bedrock)
         │ Tool Use (읽기 전용)
    ┌────┼────┐
    ▼    ▼    ▼
  AWS  GitLab Splunk
```

## 데이터소스 연결 방식

| 대상 | 접근 | Private Connection |
|------|------|-------------------|
| AWS (CloudWatch, EKS) | IAM Role assume | 불필요 |
| GitLab (Public) | Token | 불필요 |
| GitLab (Private/VPC) | Token + PC | **필수** (VPC Lattice) |
| Splunk Cloud | MCP JWT | 불필요 (Public SaaS) |
| On-Prem 도구 (MCP 지원) | Token + PC | **필수** (VPC Lattice + VPN/DX) |

## Private Connection 상세

VPC Lattice 기반 리소스 게이트웨이:
- Agent가 VPC 내 서브넷에 ENI 배치
- ENI를 통해 내부 리소스에 직접 접근
- 인터넷 노출 없이 보안 연결
- self-managed / service-managed 두 모드 존재
