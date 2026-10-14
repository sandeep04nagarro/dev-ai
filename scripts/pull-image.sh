#!/usr/bin/env bash
set -euo pipefail
REGION="${AWS_REGION:-ap-south-1}"
SERVICE_HOME="${SERVICE_HOME:-/opt/openswe-worker}"
IMAGE=$(cat "$SERVICE_HOME/image_uri.txt")
LOCAL_NAME="${LOCAL_NAME:-openswe}"
ECR_REGISTRY=$(cut -d'/' -f1 <<< "$IMAGE")
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY"
docker pull "$IMAGE"
docker tag "$IMAGE" "$LOCAL_NAME:latest"
echo "worker image updated: $IMAGE -> $LOCAL_NAME:latest"
