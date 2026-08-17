#!/bin/bash
set -e
echo "Stopping DevAI agent container if it exists..."
if [ "$(docker ps -a -q -f name=devai-agent)" ]; then
    docker stop devai-agent || true
    docker rm devai-agent || true
fi
