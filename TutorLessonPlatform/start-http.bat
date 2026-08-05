@echo off
cd /d "%~dp0"
set USE_TLS=0
set PORT=3000
echo Starting UrokLive without HTTPS
echo Open: http://127.0.0.1:3000
py -3 server\app.py
pause
