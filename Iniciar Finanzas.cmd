@echo off
setlocal
cd /d "%~dp0"
title Finanzas

if not exist ".venv\Scripts\python.exe" (
  echo No se encontro el entorno virtual en .venv.
  echo Revisa la instalacion antes de iniciar Finanzas.
  echo.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -B "scripts\start_finance_app.py" --detached
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
