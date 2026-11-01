@echo off
REM Windows: double-click to start the scanner on your PC (scans every 60 seconds)
pip install -r requirements.txt
python -m scanner.main --loop 60
pause
