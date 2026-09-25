@echo off
rem Delete the demo environment from AWS (see README.md -> Cleanup). Pass -y to skip the prompt.
cd /d "%~dp0"
set VIRTUAL_ENV=
uv run stack down %*
