@echo off
setlocal

REM ---- 1) Check python
where python >nul 2>nul
if errorlevel 1 (
  echo python not found
  exit /b 1
)

REM ---- 2) Optional mirror
if "%OCR_PIP_INDEX%"=="" (
  set "PIP_INDEX="
) else (
  set "PIP_INDEX=-i %OCR_PIP_INDEX%"
)

REM ---- 3) Prefer uv if available
where uv >nul 2>nul
if errorlevel 1 (
  set "INSTALLER=python -m pip"
) else (
  set "INSTALLER=uv pip"
)

REM ---- 4) Upgrade build tools
%INSTALLER% install %PIP_INDEX% -U pip setuptools wheel
if errorlevel 1 exit /b 1

REM ---- 5) Install OCR Python deps
%INSTALLER% install %PIP_INDEX% pillow>=11.0.0 pypdf>=5.6.0 "mineru==3.1.5"
if errorlevel 1 exit /b 1

echo MinerU OCR dependencies installed successfully.
endlocal
