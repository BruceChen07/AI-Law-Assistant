@echo off
setlocal EnableExtensions EnableDelayedExpansion

call :ensure_tesseract
if errorlevel 1 exit /b 1

call :ensure_poppler
if errorlevel 1 exit /b 1

where python >nul 2>nul
if errorlevel 1 (
  echo python not found
  exit /b 1
)
python -m pip install -U "mineru[all]==2.7.6"

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
exit /b 0

:ensure_tesseract
where tesseract >nul 2>nul
if not errorlevel 1 exit /b 0
where winget >nul 2>nul
if errorlevel 1 (
  echo winget not found, cannot auto install tesseract
  exit /b 1
)
winget install -e --id Tesseract-OCR.Tesseract-OCR
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" call :add_user_path "C:\Program Files\Tesseract-OCR"
where tesseract >nul 2>nul
if not errorlevel 1 exit /b 0
echo tesseract not found in PATH
exit /b 1

:ensure_poppler
where pdftoppm >nul 2>nul
if not errorlevel 1 exit /b 0

where winget >nul 2>nul
if not errorlevel 1 winget install -e --id oschwartz10612.Poppler

call :resolve_poppler_bin
where pdftoppm >nul 2>nul
if not errorlevel 1 exit /b 0

where choco >nul 2>nul
if not errorlevel 1 (
  choco install poppler -y
  call :resolve_poppler_bin
)

where pdftoppm >nul 2>nul
if not errorlevel 1 exit /b 0
echo poppler not found in PATH
echo Try restarting shell, or run: choco install poppler -y
exit /b 1

:resolve_poppler_bin
if exist "C:\Program Files\poppler\Library\bin\pdftoppm.exe" (
  call :add_user_path "C:\Program Files\poppler\Library\bin"
  exit /b 0
)
for /r "%LOCALAPPDATA%\Microsoft\WinGet\Packages" %%F in (pdftoppm.exe) do (
  call :add_user_path "%%~dpF"
  exit /b 0
)
exit /b 0

:add_user_path
set "P=%~1"
if "%P%"=="" exit /b 0
if not exist "%P%" exit /b 0
set "PATH=%P%;%PATH%"
for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul ^| find /I "Path"') do set "UPATH=%%B"
if not defined UPATH set "UPATH="
echo !UPATH!| find /I "%P%" >nul
if errorlevel 1 setx PATH "!UPATH!;%P%" >nul
exit /b 0
