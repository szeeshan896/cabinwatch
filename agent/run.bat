@echo off
cd /d "%~dp0\.."
echo Installing / updating the agent dependencies...
py -m pip install -r agent\requirements.txt || python -m pip install -r agent\requirements.txt
echo.
echo Starting the CabinWatch agent...
:loop
py agent\agent.py || python agent\agent.py
echo.
echo Agent stopped. Restarting in 5 seconds - close this window to stop.
timeout /t 5 >nul
goto loop
