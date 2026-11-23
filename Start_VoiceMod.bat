@echo off
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" /b "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
) else (
    start "" /b pythonw "%~dp0main.py"
)
