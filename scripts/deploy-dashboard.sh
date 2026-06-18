#!/bin/bash
set -e

ENV_PROFILE="${ENV_PROFILE:-member1-acc}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../config/load-config.sh" "$ENV_PROFILE"
REGISTRY="$ECR_REGISTRY"
REPO="${PROJECT_NAME}/dashboard"
TAG=$(date +%Y%m%d%H%M%S)
IMAGE="$REGISTRY/$REPO:$TAG"
OVERLAY_DIR="${SCRIPT_DIR}/../infrastructure/kubernetes/dashboard"

echo "Building $IMAGE"
docker build --platform linux/amd64 -t $IMAGE services/dashboard/

echo "Pushing $IMAGE"
docker push $IMAGE

echo "Updating kustomize overlay"
cd "$OVERLAY_DIR"
kustomize edit set image "dashboard=${IMAGE}"

echo "Deploying via kustomize"
kubectl apply -k "$OVERLAY_DIR"
kubectl rollout status deployment/dashboard -n dashboard --timeout=90s

echo "Deployed: $TAG"
kubectl get deployment dashboard -n dashboard -o jsonpath='{.spec.template.spec.containers[*].image}'
echo ""
