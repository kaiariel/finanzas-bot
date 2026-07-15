@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo No se encontro el entorno virtual en .venv.
  echo Revisa la instalacion antes de iniciar Finanzas.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -B scripts\start_finance_app.py
if errorlevel 1 (
  echo.
  echo Finanzas se cerro con un error. Revisa data\logs.
  pause
)
