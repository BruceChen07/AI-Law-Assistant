@echo off
setlocal

REM ---- 1) Install system dependencies (tesseract + poppler)
call "%~dp0install_ocr_windows.bat"
if errorlevel 1 (
  echo install_ocr_windows.bat failed
  exit /b 1
)

REM ---- 2) Check python
where python >nul 2>nul
if errorlevel 1 (
  echo python not found
  exit /b 1
)

REM ---- 3) Optional mirror
if "%OCR_PIP_INDEX%"=="" (
  set "PIP_INDEX="
) else (
  set "PIP_INDEX=-i %OCR_PIP_INDEX%"
)

REM ---- 4) Prefer uv if available
where uv >nul 2>nul
if errorlevel 1 (
  set "INSTALLER=python -m pip"
) else (
  set "INSTALLER=uv pip"
)

REM ---- 5) Upgrade build tools
%INSTALLER% install %PIP_INDEX% -U pip setuptools wheel
if errorlevel 1 exit /b 1

REM ---- 6) Install OCR Python deps
%INSTALLER% install %PIP_INDEX% pytesseract==0.3.10 pdf2image==1.17.0 pillow>=11.0.0 pypdf>=5.6.0 "mineru[all]==2.7.6"
if errorlevel 1 exit /b 1

echo OCR dependencies installed successfully.
endlocal