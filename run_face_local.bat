@echo off
title BIOMARK - Local Face Registration
echo.
echo  ============================================
echo   BIOMARK Local Face Registration Server
echo   Syncing to: https://www.biomark.co.in
echo  ============================================
echo.

cd /d "%~dp0backend"

:: Install required python packages
echo [*] Installing dependencies from requirements.txt...
pip install -r requirements.txt -q

echo.
echo  [*] Starting face registration server on http://localhost:8001
echo  [*] Open your browser to: http://localhost:8001
echo  [*] Student registrations will auto-sync to Render cloud DB.
echo.

uvicorn face_app:app --host 0.0.0.0 --port 8001 --reload

pause
