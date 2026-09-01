@echo off
setlocal
cd /d "%~dp0"

if not exist "..\env314\Scripts\activate.bat" (
  echo Virtual environment was not found at ..\env314
  pause
  exit /b 1
)

call "..\env314\Scripts\activate.bat"
python app.py %*
