@echo off
setlocal EnableExtensions EnableDelayedExpansion

where python >nul 2>nul
if errorlevel 1 (
  echo python not found
  exit /b 1
)
python -m pip install -U "mineru==3.1.5"
if errorlevel 1 exit /b 1

for /f "delims=" %%i in ('mineru --version') do (
  echo %%i
  goto done_mineru
)
:done_mineru
endlocal
exit /b 0
