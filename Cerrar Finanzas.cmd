@echo off
setlocal
cd /d "%~dp0"
title Cerrar Finanzas

if not exist ".venv\Scripts\python.exe" (
  echo No se encontro el entorno virtual en .venv.
  echo Revisa la instalacion antes de cerrar Finanzas.
  echo.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -B "scripts\start_finance_app.py" --stop
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
