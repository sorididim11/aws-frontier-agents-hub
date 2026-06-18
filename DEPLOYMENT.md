# Deployment Guide

Frontier Agent Hub — 멀티 어카운트 인프라 프로비저닝 가이드.

## Prerequisites

| 도구 | 용도 |
|------|------|
| AWS CLI v2 | CloudFormation, EKS, ECR 등 |
| Docker (colima 또는 Docker Desktop) | 이미지 빌드, `linux/amd64` 플랫폼 |
| kubectl | EKS 클러스터 관리 |
| kustomize | K8s 오버레이 생성 |
| envsubst | 오버레이 템플릿 치환 (brew install gettext) |

AWS CLI에 named profile 설정 필요:
```bash
aws configure --profile member1-acc
aws configure --profile member2-acc   # 멀티 어카운트 시
```

## 1. Config 설정

```bash
# 기본 계정
cp config/member1-acc.env.example config/member1-acc.env
vi config/member1-acc.env

# 멀티 어카운트 시 — 세컨더리 계정
cp config/member2-acc.env.example config/member2-acc.env
vi config/member2-acc.env
```

필수 항목:

| 변수 | 설명 | 예시 |
|------|------|------|
| `AWS_ACCOUNT_ID` | 12자리 AWS 계정 번호 | `111111111111` |
| `AWS_PROFILE` | AWS CLI 프로필 이름 | `member1-acc` |
| `AWS_REGION` | AWS 리전 | `us-east-1` |
| `PROJECT_NAME` | CFN 스택 접두사 | `frontier-agent-hub` |
| `GITHUB_ORG` | GitHub 조직 | `sorididim11` |
| `GITHUB_REPO` | GitHub 리포지토리 | `frontier-devops-agent-test-app` |
| `DOMAIN` | Route53 도메인 | `example-domain.com` |

선택 항목 (Agent 데이터 소스):

| 변수 | 설명 | 예시 |
|------|------|------|
| `GITHUB_REPO_NAME` | GitHub 리포지토리 이름 | `frontier-devops-agent-test-app` |
| `GITHUB_REPO_ID` | GitHub 리포지토리 숫자 ID | `123456789` |
| `GITHUB_OWNER` | GitHub 소유자 (org 또는 user) | `sorididim11` |
| `GITHUB_OWNER_TYPE` | 소유자 유형 | `user` 또는 `organization` |
| `SLACK_WORKSPACE_ID` | Slack 워크스페이스 ID | `T0XXXXXXX` |
| `SLACK_WORKSPACE_NAME` | Slack 워크스페이스 이름 | `my-team` |
| `SLACK_AGENT_CHANNEL_ID` | Slack 채널 ID | `C0XXXXXXX` |
| `SLACK_AGENT_CHANNEL_NAME` | Slack 채널 이름 | `#devops-alerts` |

> GitHub RepoId 확인: `curl -s -H "Authorization: token $GH_TOKEN" https://api.github.com/repos/{owner}/{repo} | jq .id`

`ECR_REGISTRY`, `EKS_CLUSTER_NAME`은 빈 칸이면 자동 계산됨.

## 2. 단일 계정 배포

### 전체 배포

```bash
./infrastructure/deploy.sh \
  --profile member1-acc \
  --project frontier-agent-hub
```

이 한 줄이 아래 8개 Phase를 순서대로 실행:

| Phase | 스택/작업 | CFN 템플릿 |
|-------|----------|-----------|
| 1 | VPC Foundation | `01-vpc-foundation.yml` |
| 2 | EKS Platform | `02-eks-platform.yml` |
| 3 | RDS Database | `03-rds-database.yml` |
| 4 | CloudWatch Alarms, Transaction Search, GitHub Actions | `06`, `05`, `08-github-actions` |
| 5 | DevOps Agent (DynamoDB, Lambda, IAM) | `04-devops-agent.yml` |
| 6 | FIS, Security Agent | `07`, `08-security-agent`, `10` |
| 7 | Docker Build → ECR Push | 5개 서비스 (hasher, rng, worker, webui, dashboard) |
| 8 | K8s 배포 | Kustomize base + 동적 overlay 생성 → kubectl apply |

### 유닛별 선택 배포

3개의 독립 배포 단위를 `--only` 옵션으로 개별 배포할 수 있습니다:

| Unit | 내용 | 옵션 |
|------|------|------|
| **Foundation** | VPC, EKS, RDS, Alarms, GitHub OIDC, FIS | `--only foundation` |
| **Agent** | DevOps Agent Space, Transaction Search, Security Agent | `--only agent` |
| **Test App** | DockerCoins 빌드 + ECR Push + K8s 배포 | `--only test-app` |
| **Dashboard** | Frontier Agent Hub + Simulator 빌드 + K8s 배포 | `--only dashboard` |

```bash
# Foundation 인프라만
./infrastructure/deploy.sh --profile member1-acc --project frontier-agent-hub --only foundation

# DevOps Agent만
./infrastructure/deploy.sh --profile member1-acc --project frontier-agent-hub --only agent

# DockerCoins만 빌드 + 배포
./infrastructure/deploy.sh --profile member1-acc --project frontier-agent-hub --only test-app

# Dashboard만 빌드 + 배포
./infrastructure/deploy.sh --profile member1-acc --project frontier-agent-hub --only dashboard
```

> Foundation → Agent → Test App / Dashboard 순서로 의존성이 있습니다. 처음 배포 시 이 순서를 지켜야 합니다.

### 옵션 플래그

```bash
--region us-west-2          # 리전 변경 (기본: us-east-1)
--vpc-cidr-prefix 10.1      # VPC CIDR (기본: 10.0 → 10.0.0.0/16)
--nodes 5                   # EKS 노드 수 (기본: 3)
--skip-infra                # CFN 스킵 (K8s만 재배포할 때)
--skip-build                # Docker 빌드 스킵
--skip-k8s                  # K8s 배포 스킵
--enable-slack              # Slack 연동
--slack-bot-token TOKEN     # Slack 봇 토큰
--slack-channel-id ID       # Slack 채널 ID

# Agent 데이터 소스 (GitHub / Slack Association)
--github-repo-name NAME     # GitHub 연동 활성화
--github-repo-id ID         # GitHub 리포 숫자 ID
--github-owner OWNER        # GitHub 소유자
--github-owner-type TYPE    # organization | user (기본: user)
--slack-workspace-id ID     # Slack 연동 활성화
--slack-workspace-name NAME # Slack 워크스페이스 이름
--slack-agent-channel-id ID # Slack 채널 ID
--slack-agent-channel-name NAME  # Slack 채널 이름
```

### Agent 데이터 소스 연동

GitHub / Slack을 DevOps Agent에 연동하려면 플래그를 추가:

```bash
./infrastructure/deploy.sh \
  --profile member1-acc \
  --project frontier-agent-hub \
  --github-repo-name frontier-devops-agent-test-app \
  --github-repo-id 123456789 \
  --github-owner sorididim11 \
  --github-owner-type user \
  --slack-workspace-id T0XXXXXXX \
  --slack-workspace-name my-team \
  --slack-agent-channel-id C0XXXXXXX \
  --slack-agent-channel-name devops-alerts
```

플래그를 생략하면 해당 Association은 생성되지 않음 (Conditional).

## 3. 멀티 어카운트 배포 (PrivateLink)

### 3a. Primary 계정 (Member 1)

```bash
./infrastructure/deploy.sh \
  --profile member1-acc \
  --project frontier-agent-hub \
  --multi-account \
  --peer-profile member2-acc \
  --peer-project frontier-agent-hub-m2 \
  --provider-services hasher
```

`--multi-account` 모드는 Phase 8 이후에 추가로:
- PrivateLink Provider 스택 배포 (`11-privatelink-provider.yml`)
- Peer 계정에 Consumer 스택 배포 (`12-privatelink-consumer.yml`)
- Peer 클러스터에서 해당 서비스를 ExternalName으로 전환

### 3b. Secondary 계정 (Member 2)

```bash
./infrastructure/deploy-member2.sh \
  --xaccount-role \
  --primary-account-id 111111111111 \
  --primary-project frontier-agent-hub
```

이 스크립트는:
- `config/member2-acc.env` 기반으로 Member 2 인프라 전체 배포 (VPC → EKS → RDS → 앱)
- `--xaccount-role` 시 Cross-Account IAM 역할 배포:
  - `13-devops-agent-secondary-role.yml` — Agent가 M2 EKS를 조회할 수 있는 역할
  - `14-dashboard-cross-account-access.yml` — Dashboard가 M2에 fault injection할 수 있는 역할

### 멀티 어카운트 배포 순서

```
Member 1: deploy.sh (Phase 1-8)
    ↓
Member 2: deploy-member2.sh --xaccount-role
    ↓
Member 1: deploy.sh --multi-account --skip-infra --skip-build
    (PrivateLink provider/consumer 설정)
```

## 4. CI/CD (GitHub Actions)

`.github/workflows/build-push-ecr.yml`이 자동 빌드/배포를 처리.

### GitHub 설정

1. Repository Settings → Environments에서 `member1-acc`, `member2-acc` 환경 생성
2. 각 환경에 변수 설정:
   - `AWS_ACCOUNT_ID`
   - `PROJECT_NAME`
   - `IAM_ROLE_ARN` — `08-github-actions.yml` 스택이 생성한 OIDC 역할 ARN

### 트리거

- `services/dashboard/**` 변경 시 자동 실행
- Actions 탭에서 수동 실행 (workflow_dispatch)
- DockerCoins 빌드/배포는 [frontier-agent-test-dockercoins](https://github.com/sorididim11/frontier-agent-test-dockercoins) 리포로 이관됨

### 파이프라인 흐름

```
코드 Push → 변경 감지 → Docker 빌드 → ECR Push → Kustomize 오버레이 → kubectl apply → Rollout 대기
```

## 5. 오버레이 수동 생성

CI/CD 없이 이미지 태그만 바꿔서 배포할 때:

```bash
./infrastructure/generate-overlays.sh member1-acc --tag abc1234

# 결과:
# infrastructure/kubernetes/overlays/member1-acc/dockercoins/kustomization.yaml
# infrastructure/kubernetes/overlays/member1-acc/dashboard/kustomization.yaml

kubectl apply -k infrastructure/kubernetes/overlays/member1-acc/dockercoins
kubectl apply -k infrastructure/kubernetes/overlays/member1-acc/dashboard
```

## 6. 배포 검증

```bash
# CFN 스택 상태
aws cloudformation list-stacks \
  --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \
  --query "StackSummaries[?contains(StackName,'devops-agent-test')].StackName" \
  --output table --profile member1-acc

# EKS 클러스터
aws eks describe-cluster --name devops-agent-test-cluster \
  --query 'cluster.{Status:status,Version:version}' \
  --profile member1-acc

# K8s 워크로드
kubectl get pods -n dockercoins
kubectl get pods -n dashboard

# 배포 완료 대기
kubectl wait --for=condition=available deployment --all -n dockercoins --timeout=300s
kubectl wait --for=condition=available deployment --all -n dashboard --timeout=120s
```

## 7. 환경 정리 (Teardown)

### 전체 삭제

```bash
./infrastructure/cleanup.sh \
  --profile member1-acc \
  --project frontier-agent-hub \
  --force
```

### 유닛별 삭제

```bash
# Agent 스택만 삭제
./infrastructure/cleanup.sh --profile member1-acc --project frontier-agent-hub --only agent

# Test App만 삭제 (K8s dockercoins namespace)
./infrastructure/cleanup.sh --profile member1-acc --project frontier-agent-hub --only test-app

# Dashboard만 삭제 (K8s dashboard namespace + ECR repo)
./infrastructure/cleanup.sh --profile member1-acc --project frontier-agent-hub --only dashboard
```

### 멀티 어카운트 삭제

```bash
./infrastructure/cleanup.sh \
  --profile member1-acc \
  --project frontier-agent-hub \
  --multi-account \
  --peer-profile member2-acc \
  --peer-project frontier-agent-hub-m2 \
  --provider-services hasher \
  --force
```

### cleanup.sh 옵션

| 옵션 | 설명 |
|------|------|
| `--profile` | (필수) AWS 프로파일 |
| `--project` | (필수) 프로젝트명 |
| `--region` | AWS 리전 (기본: us-east-1) |
| `--only` | `foundation` / `agent` / `test-app` / `dashboard` |
| `--dry-run` | 실제 삭제 없이 대상 목록만 출력 |
| `--force` | 확인 프롬프트 건너뛰기 |
| `--multi-account` | 피어 계정 리소스도 삭제 |
| `--peer-profile` | 피어 계정 프로파일 |
| `--peer-project` | 피어 계정 프로젝트명 |
| `--provider-services` | PrivateLink provider 서비스 목록 (쉼표 구분) |

### 삭제 순서 (자동 처리)

CFN 스택 간 ImportValue 의존성을 고려하여 Wave 기반 병렬 삭제:

```
Pre-Wave:  K8s namespaces (dockercoins, dashboard, splunk) — finalizer 자동 처리
Wave 0:    Peer account stacks (PrivateLink consumer, xaccount roles) — 멀티 어카운트만
Wave 1:    Leaf stacks (security-*, transaction-search, github-actions, fis, privatelink-provider-*)
Wave 2:    devops-agent + rds-database
Wave 3:    alarms
Wave 4:    ECR repos force-delete + EKS Log Group 삭제 + eks-platform
Wave 5:    Orphaned NLBs/ENIs/SGs 정리 + vpc-foundation
Post:      Secrets Manager secrets
```

### 알려진 삭제 이슈 (자동 처리됨)

| 원인 | cleanup.sh 자동 처리 |
|------|---------------------|
| ECR repo에 이미지 남음 | `ecr delete-repository --force` |
| PrivateLink 활성 연결 | `ec2 reject-vpc-endpoint-connections` |
| K8s NLB가 EKS 삭제 후 남음 | `elbv2 delete-load-balancer` + ENI 대기 |
| K8s가 생성한 SG가 VPC에 남음 | 비-default SG 자동 삭제 |
| EKS CloudWatch Log Group | `logs delete-log-group` |
| Dashboard ECR (DeletionPolicy: Retain) | `ecr delete-repository --force` |
| K8s namespace Terminating 상태 | finalizer 자동 제거 |

## 8. 프로젝트 구조

```
config/                          # 계정별 환경 설정 (.env)
infrastructure/
  cloudformation/                # 15개 CFN 템플릿 (01~14)
  kubernetes/
    base/                        # Kustomize base 매니페스트
      dockercoins/               #   hasher, rng, worker, webui, redis
      dashboard/                 #   dashboard + RBAC
    overlays/                    #   envsubst 템플릿 + 계정별 생성 오버레이
  deploy.sh                      # 단일/멀티 어카운트 전체 배포 (--only 지원)
  cleanup.sh                     # 환경 정리 (deploy.sh의 역순, --only/--dry-run 지원)
  deploy-member2.sh              # 세컨더리 계정 배포
  generate-overlays.sh           # 오버레이 수동 생성
scripts/
  build-and-push.sh              # Docker 이미지 빌드 + ECR 푸시
services/
  dockercoins/                   # 5개 마이크로서비스 소스
  dashboard/                     # Simulator Dashboard 소스
.github/workflows/
  build-push-ecr.yml             # CI/CD 파이프라인
docs/architecture/               # 아키텍처 다이어그램 (L1/L2/L3)
```
