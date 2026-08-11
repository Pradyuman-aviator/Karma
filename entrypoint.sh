#!/usr/bin/env bash
set -euo pipefail

REPO="${GITHUB_WORKSPACE:-.}"
BASE_BRANCH="${INPUT_BASE_BRANCH:-main}"

exec python /action/cli.py run \
  --ci \
  --base "${BASE_BRANCH}" \
  --repo "${REPO}"
