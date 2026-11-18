@echo off
chcp 65001 >nul
setlocal
title agproxy - Antigravity proxy launcher
cd /d "%~dp0"

rem Locate a Python 3 interpreter. Prefer the launcher, then PATH.
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
  where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python 3 was not found.
  echo         Install Python 3.8+ from https://www.python.org/downloads/
  echo         and make sure "Add python.exe to PATH" is checked.
  echo.
  pause
  exit /b 1
)

echo Running: %PY% agproxy.py %*
echo.
%PY% agproxy.py %*
set "RC=%ERRORLEVEL%"
echo.
echo ----------------------------------------------------------
echo Exit code: %RC%
echo ----------------------------------------------------------
pause
exit /b %RC%
