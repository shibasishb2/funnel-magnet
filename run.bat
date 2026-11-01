@echo off
rem Starts the app from the virtual environment, then opens it in the browser.
cd /d "%~dp0"
start "" /b cmd /c "ping -n 4 127.0.0.1 >nul & start http://localhost:8501"
"%USERPROFILE%\.venvs\funnel-magnet\Scripts\streamlit.exe" run app.py
