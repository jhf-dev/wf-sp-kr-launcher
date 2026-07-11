@echo off
setlocal
cd /d "%~dp0"

if exist "WFTSP_KR_Steam_Patch_GUI.exe" (
  start "" "%~dp0WFTSP_KR_Steam_Patch_GUI.exe" %*
  exit /b 0
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 tooling\wftsp_steam_kr_patch_gui.py
) else (
  where python >nul 2>nul
  if %ERRORLEVEL%==0 (
    python tooling\wftsp_steam_kr_patch_gui.py
  ) else (
    echo WFTSP_KR_Steam_Patch_GUI.exe not found.
    echo Use the packaged release that includes the local Python runtime, or rebuild it with:
    echo python tooling\build_ddraw_proxy.py
    echo python -m PyInstaller WFTSP_KR_Steam_Patch_GUI.spec
    pause
    exit /b 1
  )
)

if errorlevel 1 pause
