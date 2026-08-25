#!/bin/bash
set -e
DEPLOY_DIR="/opt/devai-agent/deploy"
IMAGE_URI=$(cat $DEPLOY_DIR/image_uri.txt)

echo "Starting DevAI agent container..."

# Using --env-file if you have a non-secret env file
ENV_ARGS=""
if [ -f "/opt/devai-agent/.env" ]; then
    ENV_ARGS="--env-file /opt/devai-agent/.env"
fi

# The app itself fetches secrets via boto3 at startup.
docker run -d \
  --name devai-agent \
  --restart unless-stopped \
  -p 8081:2024 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  $ENV_ARGS \
  $IMAGE_URI
