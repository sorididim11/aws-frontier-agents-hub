# GitLab Private Connection 설정 가이드

## 개요

EKS 위에 배포된 GitLab CE를 DevOps Agent의 데이터소스로 연결하는 절차.
Private Connection(VPC 내부 ENI)을 통해 Agent가 내부 NLB로 GitLab에 접근.

---

## 1. 사전 준비 (IaC)

### 1.1 EBS CSI Driver 설치

```bash
aws cloudformation create-stack \
  --stack-name devops-agent-test-m2-ebs-csi-driver \
  --template-body file://<dockercoins-repo>/infrastructure/cloudformation/17-m2-ebs-csi-driver.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1
```

- IAM Role (IRSA) + EKS Addon 생성
- GitLab PVC(EBS gp2)가 바인딩되려면 필수

### 1.2 GitLab CE 배포

```bash
kubectl apply -k <dockercoins-repo>/infrastructure/kubernetes/base/gitlab/
```

리소스:
- Namespace: `gitlab`
- PVC: `gitlab-data` (10Gi), `gitlab-config` (1Gi) — EBS gp2
- Deployment: `gitlab/gitlab-ce:17.0.0-ce.0` — HTTPS(443), init container(self-signed cert)
- Service: Internal NLB, TCP 443

초기화 완료까지 약 5~10분 소요 (DB migration + reconfigure).

### 1.3 GitLab 초기 설정

초기화 완료(5~10분) 후 순서대로 실행:

#### Step A: Bot 유저 생성 (라이센스 불필요)

```bash
kubectl -n gitlab exec deploy/gitlab -- gitlab-rails runner '
user = User.find_by(username: "root")
if user.nil?
  user = User.new(
    username: "root", email: "admin@local.host", name: "Administrator",
    password: "PLACEHOLDER_PASSWORD", password_confirmation: "PLACEHOLDER_PASSWORD", admin: true
  )
  user.skip_confirmation!
  user.save!(validate: false)
  user.create_namespace!(name: user.username, path: user.username, owner: user)
  puts "root user created"
else
  puts "root user exists (id=#{user.id})"
end
'
```

> GitLab CE는 Personal Access Token으로 bot 역할 수행 — 별도 라이센스 불필요.

#### Step B: Personal Access Token (PAT) 생성

```bash
kubectl -n gitlab exec deploy/gitlab -- gitlab-rails runner '
token = PersonalAccessToken.create!(
  user: User.find_by(username: "root"),
  name: "devops-agent-bot",
  scopes: ["api", "read_repository", "write_repository"],
  expires_at: Date.new(2027, 5, 26)
)
puts "PAT: #{token.token}"
'
```

PAT scopes:
| Scope | 용도 | 필수 |
|-------|------|------|
| `api` | 프로젝트 목록 조회, 서비스 등록 시 연결 테스트 | ✅ |
| `admin_mode` | Agent Space가 project 접근 검증 시 필요 | ✅ |
| `read_repository` | Agent가 소스코드 읽기 | ✅ |
| `write_repository` | Agent가 PR/MR 생성 | 권장 |

> **중요:** `admin_mode` scope가 없으면 "project not accessible to this GitLab token" 에러 발생.
> PAT 소유자는 GitLab Administrator여야 합니다.

#### Step C: 프로젝트 생성

```bash
# NLB DNS 확인
NLB=$(kubectl -n gitlab get svc gitlab -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')

# 프로젝트 생성
curl -sk --header "PRIVATE-TOKEN: <PAT>" \
  -X POST "https://${NLB}:443/api/v4/projects" \
  --data "name=petshop&visibility=public"
```

#### Step D: 코드 Push (spring-petclinic)

```bash
git clone https://github.com/spring-projects/spring-petclinic.git
cd spring-petclinic
git remote add gitlab "https://root:<PAT>@${NLB}/root/petshop.git"
git push gitlab main --force
```

> self-signed cert이므로 `git -c http.sslVerify=false push ...` 또는 git config 설정 필요.

#### 검증

```bash
# 프로젝트 목록 조회 (PAT으로)
curl -sk --header "PRIVATE-TOKEN: <PAT>" "https://${NLB}:443/api/v4/projects?membership=true"
# → [{id:1, path_with_namespace:"root/petshop", ...}]
```

---

## 2. Private Connection 등록

### 2.1 Private Connection CFn 배포

위자드에서 "등록" 클릭 시 자동 생성되는 CFn 템플릿:

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Resources:
  PrivateConnection:
    Type: AWS::DevOpsAgent::PrivateConnection
    Properties:
      Name: gitlab-pc
      ConnectionConfiguration:
        ServiceManaged:
          HostAddress: "nlb-gitlab-placeholder.elb.us-east-1.amazonaws.com"
          VpcId: "vpc-PLACEHOLDER"
          SubnetIds:
            - "subnet-PLACEHOLDER"
            - "subnet-PLACEHOLDER"
          SecurityGroupIds:
            - "sg-PLACEHOLDER"
          PortRanges:
            - "443"
Outputs:
  PrivateConnectionName:
    Value: !Ref PrivateConnection
```

배포되면 AWS가 선택한 Subnet에 ENI를 생성 → Agent가 ENI를 통해 NLB:443에 접근.

### 2.2 Service 등록 시 targetUrl 규칙

> **핵심:** `targetUrl`은 GitLab의 `external_url` 설정과 일치해야 합니다.

Agent Space는 `targetUrl`을 TLS 연결의 **Host header + SNI**로 사용합니다.
VPC Lattice Resource Gateway는 TCP pass-through (L7 미처리)하므로,
GitLab이 받는 Host header가 `external_url`과 다르면 요청을 거부합니다.

| GitLab `external_url` | Service `targetUrl` | 결과 |
|---|---|---|
| `https://gitlab.internal` | `https://gitlab.internal` | ✅ 성공 |
| `https://gitlab.internal` | `https://nlb-hostname.elb.amazonaws.com` | ❌ 실패 |

```bash
# GitLab external_url 확인
kubectl exec -n gitlab deploy/gitlab -- grep "host:" /var/opt/gitlab/gitlab-rails/etc/gitlab.yml
# → host: gitlab.internal

# Service 등록 시 targetUrl
aws devops-agent register-service --service gitlab \
  --service-details '{"gitlab":{"targetUrl":"https://gitlab.internal",...}}'
```

### 2.3 인증서 등록 (필수 — self-signed cert인 경우)

**Private Connection 배포 후 반드시 인증서를 등록해야 합니다.**
DevOps Agent가 Private Connection 통해 GitLab에 접속할 때 TLS 검증을 수행하므로,
self-signed cert는 `update-private-connection-certificate` API로 등록 필수.

> **위자드에서 자동 등록**: Space 위자드에서 Private Connection 등록 시 CA 인증서(PEM) 입력란에
> 인증서를 붙여넣으면, 배포 완료 후 자동으로 `update-private-connection-certificate` API 호출.

#### 핵심 요구사항: 인증서 SAN에 NLB DNS 포함 필수

인증서의 Subject Alternative Name(SAN)에 **NLB DNS 이름**이 반드시 포함되어야 합니다.
CN만 `gitlab.gitlab.svc.cluster.local`인 인증서로는 hostname verification 실패.

#### 인증서 생성 (NLB DNS를 SAN에 포함):

```bash
NLB_DNS="nlb-gitlab-placeholder.elb.us-east-1.amazonaws.com"

openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout gitlab.key -out gitlab.crt \
  -subj "/CN=gitlab.gitlab.svc.cluster.local" \
  -addext "subjectAltName=DNS:gitlab.gitlab.svc.cluster.local,DNS:${NLB_DNS}"
```

#### GitLab pod에 인증서 배포:

```bash
kubectl -n gitlab cp gitlab.crt <POD>:/etc/gitlab/ssl/gitlab.crt -c gitlab
kubectl -n gitlab cp gitlab.key <POD>:/etc/gitlab/ssl/gitlab.key -c gitlab
kubectl -n gitlab exec <POD> -c gitlab -- gitlab-ctl restart nginx
```

#### Private Connection에 인증서 등록:

```bash
aws devops-agent update-private-connection-certificate \
  --name gitlab-pc \
  --certificate "$(cat gitlab.crt)" \
  --profile member2-acc --region us-east-1
```

검증:
```bash
aws devops-agent list-private-connections --profile member2-acc --region us-east-1
# → status: ACTIVE, certificateExpiryTime 확인
```

### 2.3 GitLab 서비스 등록

Private Connection + 인증서 등록 완료 후 GitLab 서비스를 등록합니다:

```bash
aws devops-agent register-service \
  --service gitlab \
  --service-details '{"gitlab": {"targetUrl": "https://<NLB_DNS>", "tokenType": "personal", "tokenValue": "<PAT>"}}' \
  --private-connection-name "gitlab-pc" \
  --profile member2-acc --region us-east-1
```

또는 위자드의 등록 API(`/api/integrations/register`)를 통해 등록:
```json
{
  "provider": "gitlab",
  "host_url": "https://<NLB_DNS>",
  "token": "<PAT>",
  "token_type": "personal",
  "private_connection_name": "gitlab-pc"
}
```

### 2.4 Association (Space에 프로젝트 연결)

서비스 등록 후 Space에 GitLab 프로젝트를 연결:

```bash
aws devops-agent associate-service \
  --agent-space-id <SPACE_ID> \
  --service-id <SERVICE_ID> \
  --configuration '{"gitlab": {"projectId": "1", "projectPath": "root/petshop"}}' \
  --profile member2-acc --region us-east-1
```

**CFn에서 Association 시 스키마** (대소문자 주의):
```yaml
Configuration:
  GitLab:               # CLI: gitlab (소문자), CFn: GitLab (대문자)
    ProjectId: "1"      # 문자열
    ProjectPath: "root/petshop"
```

---

## 3. 연동 흐름 (아키텍처)

```
┌─────────────────────────────────────────────────────────────┐
│  EKS (devops-agent-test-m2-cluster)                         │
│  VPC: vpc-PLACEHOLDER                                  │
│                                                              │
│  ┌──────────────┐     TCP 443     ┌──────────────────────┐  │
│  │ GitLab CE    │◄────────────────│ Internal NLB         │  │
│  │ (HTTPS,      │                 │                      │  │
│  │  self-signed) │                 └──────────┬───────────┘  │
│  └──────────────┘                            │              │
│                                              │              │
│                          ┌───────────────────┘              │
│                          │ Private Connection ENI           │
│                          │ (subnet-09f7ff..., subnet-050f6..)│
│                          │ SG: sg-PLACEHOLDER         │
└──────────────────────────┼──────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  AWS DevOps Agent Service (같은 계정: 222222222222)           │
│                                                              │
│  Agent Space ──▶ Private Connection ("gitlab-pc")            │
│       │              │                                       │
│       │              └── ENI → NLB:443 → GitLab              │
│       │                                                      │
│       └── Association (GitLab service + sslCertificate)      │
└──────────────────────────────────────────────────────────────┘
```

---

## 4. 현재 환경 정보

| 항목 | 값 |
|------|-----|
| 클러스터 | `devops-agent-test-m2-cluster` (us-east-1) |
| 계정 | `222222222222` (member2-acc) |
| GitLab NLB | `nlb-gitlab-placeholder.elb.us-east-1.amazonaws.com` |
| 포트 | 443 (HTTPS, TCP passthrough via NLB) |
| VPC | `vpc-PLACEHOLDER` |
| Private Subnets | `subnet-PLACEHOLDER`, `subnet-PLACEHOLDER` |
| Security Group | `sg-PLACEHOLDER` (devops-agent-test-m2-eks-cluster-sg) |
| Private Connection | `gitlab-pc` (status: ACTIVE) |
| Service ID | `00000000-0000-0000-0000-000000000001` |
| Association ID | `00000000-0000-0000-0000-000000000002` |
| Project | `root/petshop` (id=1) |
| PAT | Secrets Manager `devops-agent/gitlab-pat` (scopes: api, read_repository, write_repository) |
| SSL Cert CN | `gitlab.gitlab.svc.cluster.local` |
| SSL Cert SAN | `DNS:gitlab.gitlab.svc.cluster.local, DNS:<NLB_DNS>` |
| Cert Expiry | 2036-05-23 (10년) |

---

## 5. IaC 파일 목록

| 파일 | 용도 |
|------|------|
| `frontier-agent-test-dockercoins/infrastructure/cloudformation/17-m2-ebs-csi-driver.yml` | EBS CSI Driver (IAM + Addon) |
| `frontier-agent-test-dockercoins/infrastructure/kubernetes/base/gitlab/namespace.yaml` | gitlab namespace |
| `frontier-agent-test-dockercoins/infrastructure/kubernetes/base/gitlab/pvc.yaml` | EBS PVC (data + config) |
| `frontier-agent-test-dockercoins/infrastructure/kubernetes/base/gitlab/deployment.yaml` | GitLab CE HTTPS deployment |
| `frontier-agent-test-dockercoins/infrastructure/kubernetes/base/gitlab/service.yaml` | Internal NLB TCP 443 |
| `frontier-agent-test-dockercoins/infrastructure/kubernetes/base/gitlab/kustomization.yaml` | Kustomize 리소스 목록 |

---

## 6. 트러블슈팅

### Readiness Probe 404
GitLab CE 17.x에서 `/-/readiness` 엔드포인트는 `monitoring_whitelist`에 등록된 IP만 200 반환.
해결: `GITLAB_OMNIBUS_CONFIG`에 `gitlab_rails['monitoring_whitelist'] = ['0.0.0.0/0']` 추가.

### 로컬에서 연결 테스트 실패
Internal NLB는 VPC 내부 전용. 로컬에서 접근 불가 (정상 동작).
로컬 테스트 시: `kubectl port-forward svc/gitlab 8443:443 -n gitlab`

### Association 실패: "project not accessible to this GitLab token"
원인: Private Connection에 인증서가 등록되지 않았거나, 인증서 SAN에 NLB DNS가 누락.

해결 순서:
1. `list-private-connections` → `certificateExpiryTime` 필드 존재 확인
2. 없으면 인증서를 NLB DNS를 SAN에 포함하여 생성
3. GitLab pod에 인증서 교체 + nginx restart
4. `update-private-connection-certificate` 실행
5. association 재시도

### Self-signed cert hostname mismatch
NLB DNS(`*.elb.us-east-1.amazonaws.com`)로 접속하지만 인증서 CN이 `gitlab.gitlab.svc.cluster.local`인 경우 TLS hostname verification 실패.
**반드시 인증서 SAN에 NLB DNS를 추가**해야 합니다.

### 앱 내부 GitLab API 호출 시 SSL 에러
Flask 앱에서 `urllib`로 GitLab 호출 시 self-signed cert 거부됨.
해결: `ssl.create_default_context()` + `check_hostname=False` + `verify_mode=CERT_NONE`
