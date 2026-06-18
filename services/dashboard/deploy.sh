#!/bin/bash
set -e

ENV_PROFILE="${ENV_PROFILE:-member1-acc}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../../config/load-config.sh" "$ENV_PROFILE"
REGISTRY="$ECR_REGISTRY"
REPO="${PROJECT_NAME}/dashboard"
REGION="$AWS_REGION"
PROFILE="$AWS_PROFILE"
KUSTOMIZE_DIR="infrastructure/kubernetes/dashboard"
NAMESPACE="dashboard"

# 1. ECR login
echo "🔑 ECR 로그인"
aws ecr get-login-password --region $REGION --profile $PROFILE | \
  docker login --username AWS --password-stdin $REGISTRY 2>/dev/null

# 2. Build with unique tag (초 + 랜덤 suffix로 충돌 방지)
TAG=$(date +%Y%m%d%H%M%S)-$(openssl rand -hex 2)
echo "🏗️  빌드: $TAG"
docker build -t $REGISTRY/$REPO:$TAG services/dashboard/

# 3. Push
echo "📤 푸시: $TAG"
docker push $REGISTRY/$REPO:$TAG

# 4. Verify image exists in ECR before deploy
echo "🔍 ECR 이미지 확인: $TAG"
if ! aws ecr describe-images --repository-name $REPO --image-ids imageTag=$TAG \
    --profile $PROFILE --region $REGION --no-cli-pager >/dev/null 2>&1; then
  echo "❌ ECR에 $TAG 이미지가 없습니다. 배포 중단."
  exit 1
fi
echo "✅ ECR 이미지 확인 완료"

# 5. Deploy with kustomize
echo "🚀 배포: $TAG"
sed -i '' "s|newTag:.*|newTag: \"$TAG\"|" $KUSTOMIZE_DIR/kustomization.yaml
kubectl apply -k $KUSTOMIZE_DIR

# 6. Kill old pod → 새 pod 즉시 생성 (rolling update 대기보다 빠름)
kubectl delete pod -n $NAMESPACE -l app=dashboard --wait=false 2>/dev/null || true
kubectl wait --for=condition=ready pod -n $NAMESPACE -l app=dashboard --timeout=60s

# 7. Verify
DEPLOYED=$(kubectl get deployment dashboard -n $NAMESPACE -o jsonpath='{.spec.template.spec.containers[0].image}')
echo "✅ 배포 완료: $DEPLOYED"
