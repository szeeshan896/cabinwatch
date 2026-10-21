@echo off
cd /d "%~dp0"
echo Installing / updating dependencies...
py -m pip install -r requirements.txt || python -m pip install -r requirements.txt
echo.
echo Starting CabinWatch...
py cctv.py || python cctv.py
pause
