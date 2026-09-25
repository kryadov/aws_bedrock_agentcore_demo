@echo off
rem Create or update the demo environment in AWS (see README.md -> Run). Pass -y to skip the prompt.
cd /d "%~dp0"
set VIRTUAL_ENV=
uv run stack up %*
