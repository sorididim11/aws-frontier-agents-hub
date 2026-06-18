---
inclusion: always
---

# AWS CLI 사용 가이드라인

## 필수 옵션

### 모든 AWS CLI 명령에 필수 옵션
```bash
--profile member1-acc    # AWS 프로파일 지정
--region us-east-1       # 리전 지정
--no-cli-pager          # 페이저 비활성화 (필수! 없으면 터미널 블로킹)
```

**예시:**
```bash
# ✅ 올바른 사용
aws cloudformation describe-stacks --profile member1-acc --region us-east-1 --no-cli-pager

# ❌ 잘못된 사용 (페이저로 인해 블로킹됨)
aws cloudformation describe-stacks
```

## CloudFormation 배포

### 스택 생성
```bash
aws cloudformation create-stack \
    --stack-name <stack-name> \
    --template-body file://<template-path> \
    --capabilities CAPABILITY_NAMED_IAM \
    --profile member1-acc \
    --region us-east-1 \
    --no-cli-pager
```

### 스택 상태 확인
```bash
aws cloudformation describe-stacks \
    --stack-name <stack-name> \
    --query 'Stacks[0].StackStatus' \
    --output text \
    --profile member1-acc \
    --region us-east-1 \
    --no-cli-pager
```

### 스택 이벤트 확인 (에러 디버깅)
```bash
aws cloudformation describe-stack-events \
    --stack-name <stack-name> \
    --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`].[LogicalResourceId,ResourceStatusReason]' \
    --output table \
    --profile member1-acc \
    --region us-east-1 \
    --no-cli-pager
```

## ECR 작업

### ECR 로그인
```bash
aws ecr get-login-password --region us-east-1 --profile member1-acc | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com
```

### 이미지 푸시
```bash
docker tag <local-image> <account-id>.dkr.ecr.us-east-1.amazonaws.com/<repo>:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/<repo>:latest
```

## EKS 작업

### kubeconfig 설정
```bash
aws eks update-kubeconfig \
    --name <cluster-name> \
    --region us-east-1 \
    --profile member1-acc \
    --no-cli-pager
```

### 노드 그룹 스케일링
```bash
aws eks update-nodegroup-config \
    --cluster-name <cluster-name> \
    --nodegroup-name <nodegroup-name> \
    --scaling-config minSize=2,maxSize=6,desiredSize=2 \
    --profile member1-acc \
    --region us-east-1 \
    --no-cli-pager
```

## 리소스 버전 조회

### RDS PostgreSQL 버전
```bash
aws rds describe-db-engine-versions \
    --engine postgres \
    --query 'DBEngineVersions[].EngineVersion' \
    --output text \
    --profile member1-acc \
    --region us-east-1 \
    --no-cli-pager | tr '\t' '\n' | sort -V | tail -5
```

### EKS 버전
```bash
aws eks describe-addon-versions \
    --query 'addons[0].addonVersions[].compatibilities[].clusterVersion' \
    --output text \
    --profile member1-acc \
    --region us-east-1 \
    --no-cli-pager | sort -u
```

## IAM 정책 연결

```bash
aws iam attach-role-policy \
    --role-name <role-name> \
    --policy-arn <policy-arn> \
    --profile member1-acc \
    --no-cli-pager
```

## 자주 사용하는 정책 ARN

| 서비스 | 정책 ARN |
|--------|----------|
| Secrets Manager | `arn:aws:iam::aws:policy/SecretsManagerReadWrite` |
| CloudWatch | `arn:aws:iam::aws:policy/CloudWatchFullAccess` |
| X-Ray | `arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess` |
| ECR | `arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly` |
