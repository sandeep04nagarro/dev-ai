#!/bin/bash
set -e
DEPLOY_DIR="/opt/devai-agent/deploy"
IMAGE_URI=$(cat $DEPLOY_DIR/image_uri.txt)
REPOSITORY_URI=$(echo $IMAGE_URI | cut -d: -f1)

echo "Validating DevAI agent service..."
for i in {1..12}; do
  if curl -s http://localhost:8081/health > /dev/null; then
    echo "Service is up and healthy!"

    echo "Cleaning up old Docker images, keeping only $IMAGE_URI..."
    docker images "$REPOSITORY_URI" --format '{{.ID}} {{.Repository}}:{{.Tag}}' \
      | awk -v keep="$IMAGE_URI" '$2 != keep {print $1}' \
      | xargs -r docker rmi -f || true

    exit 0
  fi
  echo "Waiting for service to become healthy... ($i/12)"
  sleep 10
done

echo "Service failed to become healthy in time."
exit 1
