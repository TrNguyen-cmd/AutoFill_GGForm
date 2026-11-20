@echo off
cd /d "%~dp0"
echo Starting AutoGGForm at http://127.0.0.1:8080 ...
start "" /b cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8080"
python "%~dp0app.py"
