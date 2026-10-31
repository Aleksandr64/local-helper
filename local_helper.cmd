@echo off
setlocal
set "LH_PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "LH_PY=%~dp0.venv\Scripts\python.exe"
"%LH_PY%" "%~dp0local_helper.py" %*
