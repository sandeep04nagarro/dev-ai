#!/usr/bin/env bash

set -e

docker run \
  -p 2024:2024 \
  -v "$(pwd)/.env.dev:/workspace/.env" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e GITHUB_APP_PRIVATE_KEY="$(cat ./keys/test.pem)" \
  openswe