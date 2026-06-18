---
name: connect-gitlab-private
description: >
  Private GitLab을 DevOps Agent에 연결하는 워크플로우.
  VPC Lattice 기반 Private Connection으로 VPC 내부 GitLab에 접근.
---

# Private GitLab 연결 워크플로우

## 필요 정보

| 항목 | 필수 | 예시 |
|------|------|------|
| GitLab URL | ✓ | https://gitlab.internal:443 |
| Personal Access Token | ✓ | glpat-xxxxx (read_api scope) |
| VPC ID | ✓ | vpc-0abc1234 |
| Subnet IDs (2개+) | ✓ | subnet-a, subnet-b |
| Security Group ID | ✓ | sg-0def5678 |
| GitLab Project ID (숫자) | ✓ | 42 |
| Repo 경로 | ✓ | my-org/my-app |
| Agent Space ID | ✓ | (이전 단계에서 생성) |
| AWS Profile / Region | ✓ | my-profile / us-east-1 |

빠진 항목이 있으면 "[질문필요]"로 한 번에 모두 요청한다.

## 워크플로우

### Step 1: Public/Private 판단

- URL이 `gitlab.com` → Public (Private Connection 불필요)
- URL이 내부 주소 → Private (아래 절차)

### Step 2: 사전 검증

```bash
curl -sk -H "PRIVATE-TOKEN: {TOKEN}" "https://{URL}/api/v4/projects/{PROJECT_ID}" | jq '.name'
```

### Step 3: CFn 템플릿 생성

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: DevOps Agent - GitLab Private Connection

Parameters:
  ProjectName:
    Type: String
  AgentSpaceId:
    Type: String
  GitLabUrl:
    Type: String
  GitLabToken:
    Type: String
    NoEcho: true
  VpcId:
    Type: AWS::EC2::VPC::Id
  SubnetIds:
    Type: List<AWS::EC2::Subnet::Id>
  SecurityGroupId:
    Type: AWS::EC2::SecurityGroup::Id
  GitLabProjectId:
    Type: String
  GitLabProjectPath:
    Type: String

Resources:
  PrivateConnection:
    Type: AWS::DevOpsAgent::PrivateConnection
    Properties:
      Name: !Sub "${ProjectName}-gitlab-pc"
      VpcId: !Ref VpcId
      SubnetIds: !Ref SubnetIds
      SecurityGroupIds:
        - !Ref SecurityGroupId

  ServiceGitLab:
    Type: AWS::DevOpsAgent::Service
    DependsOn: PrivateConnection
    Properties:
      ServiceType: gitlab
      ServiceDetails:
        GitLab:
          Name: !Sub "${ProjectName}-gitlab"
          TargetUrl: !Ref GitLabUrl
          TokenType: personal
          Token: !Ref GitLabToken
      PrivateConnectionName: !GetAtt PrivateConnection.Name

  AssociationGitLab:
    Type: AWS::DevOpsAgent::Association
    DependsOn: ServiceGitLab
    Properties:
      AgentSpaceId: !Ref AgentSpaceId
      ServiceId: !GetAtt ServiceGitLab.ServiceId
      Configuration:
        GitLab:
          ProjectId: !Ref GitLabProjectId
          ProjectPath: !Ref GitLabProjectPath

Outputs:
  PrivateConnectionName:
    Value: !GetAtt PrivateConnection.Name
  ServiceId:
    Value: !GetAtt ServiceGitLab.ServiceId
```

### Step 4: 배포

```bash
aws cloudformation deploy \
  --template-file gitlab-connection.yaml \
  --stack-name {PROJECT_NAME}-gitlab-connection \
  --parameter-overrides \
    GitLabToken="{TOKEN}" \
    VpcId="{VPC}" SubnetIds="{S1},{S2}" SecurityGroupId="{SG}" \
  --profile {PROFILE} --region {REGION} --no-cli-pager
```

### Step 5: 검증

```bash
aws devops-agent list-associations --agent-space-id {ID} \
  --profile {PROFILE} --region {REGION} --no-cli-pager \
  | jq '.associations[] | select(.serviceType=="gitlab")'
```

기대: `status: "ACTIVE"` (Private Connection 프로비저닝 3~5분)

## 핵심 규칙

1. **Private Connection = VPC Lattice 기반** — Agent ENI가 VPC 내부에 직접 접근
2. **Token scope = read_api** — 최소 권한
3. **ProjectId = 숫자** — GitLab UI Settings에서 확인
4. **ProjectPath = namespace/repo** — URL 경로 형식
5. **Service + Association 함께 생성** — 단독 생성 금지
6. **DependsOn 필수** — PC → Service → Association

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| Association FAILED | PC 프로비저닝 중 | 5분 대기 후 재시도 |
| connection timeout | SG 인바운드 미설정 | Agent ENI SG 확인 |
| 401 Unauthorized | Token 만료/scope 부족 | read_api scope로 재생성 |
| Project not found | ProjectId 오류 | API로 확인: /api/v4/projects/{id} |
