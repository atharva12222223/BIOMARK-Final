@echo off
title BIOMARK - Face Attendance Marker
echo.
echo  ============================================
echo   BIOMARK Local Face Attendance Marker
echo   Syncing to: https://www.biomark.co.in
echo  ============================================
echo.

cd /d "%~dp0backend"

:: Install local-only packages if missing
echo [*] Checking local dependencies (OpenCV, NumPy, OpenPyXL)...
pip install opencv-python numpy openpyxl python-dotenv requests -q

echo.
echo  [*] Starting face recognition camera feed...
echo  [*] Press 'Q' or 'ESC' to exit the camera window.
echo  [*] Press 'R' to reload face data from the database.
echo.

python face.py

pause
