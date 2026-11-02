@echo off
REM Windows: double-click to open the Scalp Scanner control panel in your browser
pip install -r requirements.txt
REM optional system-tray icon support (ignore errors)
pip install -r requirements-tray.txt >nul 2>&1
python -m scanner.panel
pause
