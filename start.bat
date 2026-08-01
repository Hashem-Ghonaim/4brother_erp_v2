@echo off
title MyERP System - Running
color 0A

echo ======================================================
echo      Starting MyERP Fashion System...
echo ======================================================

:: 1. الذهاب لمجلد المشروع
cd /d "%~dp0"

:: ================================================
:: 2. لو venv موجود اصلاً استخدمه مباشرة
:: ================================================
if exist "%~dp0venv\Scripts\python.exe" (
    echo [OK] Virtual environment found.
    set VENV_PYTHON=%~dp0venv\Scripts\python.exe
    goto :run_server
)

:: ================================================
:: 3. venv مش موجود - ابحث عن Python لإنشائه
:: ================================================
echo [INFO] No venv found. Searching for Python...
set PYTHON_EXE=

for /d %%i in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%i\python.exe" ( set "PYTHON_EXE=%%i\python.exe" & goto :create_venv )
)
for /d %%i in ("C:\Python3*") do (
    if exist "%%i\python.exe" ( set "PYTHON_EXE=%%i\python.exe" & goto :create_venv )
)
for /d %%i in ("C:\Program Files\Python3*") do (
    if exist "%%i\python.exe" ( set "PYTHON_EXE=%%i\python.exe" & goto :create_venv )
)

where python >nul 2>nul
if %errorlevel%==0 ( set "PYTHON_EXE=python" & goto :create_venv )

where py >nul 2>nul
if %errorlevel%==0 ( set "PYTHON_EXE=py" & goto :create_venv )

echo.
echo [ERROR] Python not found on this machine!
echo Please install Python 3 from: https://www.python.org/downloads/
pause
exit /b 1

:create_venv
echo [OK] Python found: %PYTHON_EXE%
echo [INFO] Creating virtual environment...
"%PYTHON_EXE%" -m venv "%~dp0venv"
if %errorlevel% neq 0 ( echo [ERROR] Failed. & pause & exit /b 1 )
set VENV_PYTHON=%~dp0venv\Scripts\python.exe

:run_server
:: ================================================
:: 4. تثبيت/تحديث المكتبات
:: ================================================
echo Checking requirements...
"%VENV_PYTHON%" -m pip install -r requirements.txt --quiet 2>nul

:: ================================================
:: 5. فتح Firewall (مرة واحدة فقط)
:: ================================================
netsh advfirewall firewall show rule name="MyERP Port 5000" >nul 2>nul
if %errorlevel% neq 0 (
    netsh advfirewall firewall add rule name="MyERP Port 5000" dir=in action=allow protocol=TCP localport=5000 >nul 2>nul
)

:: ================================================
:: 6. جيب الـ IP المحلي
:: ================================================
set LOCAL_IP=unknown
for /f "tokens=2 delims=:" %%a in ('ipconfig 2^>nul ^| findstr /c:"IPv4 Address"') do (
    if "!LOCAL_IP!"=="unknown" set LOCAL_IP=%%a
)
set LOCAL_IP=%LOCAL_IP: =%

:: ================================================
:: 7. فتح المتصفح وتشغيل السيرفر
:: ================================================
timeout /t 2 >nul
start http://127.0.0.1:5000

echo.
echo ======================================================
echo  [READY] MyERP is running!
echo.
echo  This device  ^>  http://127.0.0.1:5000
echo  Network      ^>  http://%LOCAL_IP%:5000
echo.
echo  Press Ctrl+C to stop
echo ======================================================
echo.
"%VENV_PYTHON%" app.py

echo.
echo [SERVER STOPPED]
pause