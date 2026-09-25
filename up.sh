#!/usr/bin/env sh
# Create or update the demo environment in AWS (see README.md -> Run). Pass -y to skip the prompt.
set -e
cd "$(dirname "$0")"
unset VIRTUAL_ENV
exec uv run stack up "$@"
