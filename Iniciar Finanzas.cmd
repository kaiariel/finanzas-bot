@echo off
setlocal
cd /d "%~dp0"
title Finanzas

set "PYTHON_EXE=.venv-working\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  echo No se encontro un Python valido en .venv o .venv-working.
  echo Revisa la instalacion antes de iniciar Finanzas.
  echo.
  pause
  exit /b 1
)

"%PYTHON_EXE%" -B "scripts\start_finance_app.py" --detached
set "CODIGO=%ERRORLEVEL%"
if not "%CODIGO%"=="0" (
  echo.
  echo Finanzas no se pudo iniciar ^(codigo %CODIGO%^).
  echo Revisa "data\logs\launcher.log".
  echo.
  pause
  exit /b %CODIGO%
)

exit /b 0
