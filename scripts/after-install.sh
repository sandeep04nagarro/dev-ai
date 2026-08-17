#!/bin/bash
set -e
DEPLOY_DIR="/opt/devai-agent/deploy"
IMAGE_URI=$(cat $DEPLOY_DIR/image_uri.txt)

echo "Pulling Docker image: $IMAGE_URI"
docker pull $IMAGE_URI
