@echo off
cd /d "%~dp0"
echo Generating TLS certificate if needed...
py -3 scripts\gen_cert.py
echo.
echo Starting UrokLive (HTTPS)
echo Open: https://127.0.0.1:3443
echo HTTP http://127.0.0.1:3000 redirects to HTTPS
echo Accept the self-signed certificate warning in the browser.
echo.
py -3 server\app.py
pause
