#!/usr/bin/env sh
# Delete the demo environment from AWS (see README.md -> Cleanup). Pass -y to skip the prompt.
set -e
cd "$(dirname "$0")"
unset VIRTUAL_ENV
exec uv run stack down "$@"
