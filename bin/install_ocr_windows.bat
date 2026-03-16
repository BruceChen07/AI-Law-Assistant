@echo off
setlocal
where winget >nul 2>nul
if errorlevel 1 (
  echo winget not found
  exit /b 1
)
where tesseract >nul 2>nul
if errorlevel 1 (
  winget install -e --id Tesseract-OCR.Tesseract-OCR
)
where pdftoppm >nul 2>nul
if errorlevel 1 (
  winget install -e --id oschwartz10612.Poppler
)
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
  setx PATH "%PATH%;C:\Program Files\Tesseract-OCR"
)
if exist "C:\Program Files\poppler\Library\bin\pdftoppm.exe" (
  setx PATH "%PATH%;C:\Program Files\poppler\Library\bin"
)
where tesseract >nul 2>nul
if errorlevel 1 (
  echo tesseract not found in PATH
  exit /b 1
)
where pdftoppm >nul 2>nul
if errorlevel 1 (
  echo poppler not found in PATH
  exit /b 1
)
for /f "delims=" %%i in ('tesseract --version') do (
  echo %%i
  goto done_tess
)
:done_tess
for /f "delims=" %%i in ('pdftoppm -v 2^>^&1') do (
  echo %%i
  goto done_pop
)
:done_pop
endlocal
