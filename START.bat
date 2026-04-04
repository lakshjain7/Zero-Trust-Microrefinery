@echo off
title Zero-Trust Refinery — Backend
cd /d D:\JNTU_hackathon\hail_marry
echo.
echo  Starting Zero-Trust Agentic Micro-Refinery...
echo  Dashboard will be at: http://localhost:8000
echo  Press Ctrl+C to stop.
echo.
python main.py --mock
pause
