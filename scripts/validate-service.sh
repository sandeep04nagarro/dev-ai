#!/bin/bash
set -e

echo "Validating DevAI agent service..."
for i in {1..12}; do
  if curl -s http://localhost:2024/health > /dev/null; then
    echo "Service is up and healthy!"
    exit 0
  fi
  echo "Waiting for service to become healthy... ($i/12)"
  sleep 10
done

echo "Service failed to become healthy in time."
exit 1
