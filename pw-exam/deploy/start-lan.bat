@echo off
REM Offline LAN exam lab on Windows. Requires Python 3.9+ (python.org). Allow port 8080 in Windows Firewall.
cd /d "%~dp0.."
python -m pwexam --host 0.0.0.0 --port 8080 --data data\pwexam.db %*
pause
