@echo off
title BOLI AI Batch Image Platform

echo ========================================
echo   BOLI AI Batch Image Platform - Starting...
echo ========================================
echo.

:: Start backend API (port 8000)
echo [1/2] Starting backend API...
start "BOLI API" cmd /c "cd /d %~dp0backend && .\venv\Scripts\python.exe -m app.main --port 8000"

:: Wait for backend
timeout /t 3 /nobreak >nul

:: Start frontend dev server (port 8001)
echo [2/2] Starting frontend dev server...
start "BOLI Frontend" cmd /c "cd /d %~dp0frontend && ..\backend\venv\Scripts\python.exe dev_server.py 8001"

timeout /t 2 /nobreak >nul

echo.
echo ========================================
echo   Started!
echo.
echo   Frontend: http://localhost:8001
echo   Backend API: http://localhost:8000
echo.
echo   Close the windows to stop services.
echo ========================================

:: Open browser
start http://localhost:8001
