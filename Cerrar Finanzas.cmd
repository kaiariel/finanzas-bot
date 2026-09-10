@echo off
setlocal
cd /d "%~dp0"
title Cerrar Finanzas

set "PYTHON_EXE=.venv-working\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  echo No se encontro un Python valido en .venv o .venv-working.
  echo Revisa la instalacion antes de cerrar Finanzas.
  echo.
  pause
  exit /b 1
)

"%PYTHON_EXE%" -B "scripts\start_finance_app.py" --stop
set "CODIGO=%ERRORLEVEL%"
if not "%CODIGO%"=="0" (
  echo.
  echo No se pudo cerrar Finanzas del todo ^(codigo %CODIGO%^).
  echo Revisa el Administrador de tareas.
  echo.
  pause
  exit /b %CODIGO%
)

ping -n 4 127.0.0.1 >nul
exit /b 0
