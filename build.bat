@echo off
setlocal
cd /d "%~dp0"

echo === GURU Mobile Discovery — production build ===
echo.

python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
  echo Installing PyInstaller...
  python -m pip install -r requirements-dev.txt
  if errorlevel 1 exit /b 1
)

if not exist "assets\app.ico" (
  echo Generating assets\app.ico ...
  python scripts\make_icon.py
  if errorlevel 1 exit /b 1
)

echo Running PyInstaller...
python -m PyInstaller guru_mobile_discovery.spec --clean --noconfirm
if errorlevel 1 (
  echo PyInstaller failed.
  exit /b 1
)

where iscc >nul 2>&1
if errorlevel 1 (
  echo.
  echo Inno Setup compiler ^(iscc^) not found on PATH.
  echo Install Inno Setup 6 from https://jrsoftware.org/isinfo.php
  echo Then add e.g. "C:\Program Files ^(x86^)\Inno Setup 6" to your PATH, or run iscc manually:
  echo   iscc installer.iss
  echo.
  echo PyInstaller output is ready: dist\GuruMobileDiscovery\
  exit /b 0
)

echo Running Inno Setup...
iscc installer.iss
if errorlevel 1 (
  echo Inno Setup failed.
  exit /b 1
)

echo.
echo Done. Installer: Output\GuruMobileDiscovery_Setup.exe
exit /b 0
