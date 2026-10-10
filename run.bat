@echo off
cd /d "%~dp0"
if not exist .venv (
    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt
)
set PYTHONPATH=%~dp0src
.venv\Scripts\python -m windtunnel %*
