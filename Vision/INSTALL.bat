@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Installation failed. Check Python and the error above.
) else (
  echo Complete. Double-click START_APP.vbs.
)
pause
