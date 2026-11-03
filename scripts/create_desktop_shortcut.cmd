@echo off
setlocal

set "APP_EXE=%~dp0HLS Key Control Utility.exe"
set "SHORTCUT_NAME=HLS Key Control Utility.lnk"

if not exist "%APP_EXE%" (
    echo Application file was not found:
    echo %APP_EXE%
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$desktop = [Environment]::GetFolderPath('Desktop');" ^
    "$shortcutPath = Join-Path $desktop $env:SHORTCUT_NAME;" ^
    "$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath);" ^
    "$shortcut.TargetPath = $env:APP_EXE;" ^
    "$shortcut.WorkingDirectory = Split-Path $env:APP_EXE;" ^
    "$shortcut.IconLocation = $env:APP_EXE + ',0';" ^
    "$shortcut.Description = 'HLS Key Control Utility';" ^
    "$shortcut.Save()"

if errorlevel 1 (
    echo Failed to create the shortcut.
    pause
    exit /b 1
)

echo Shortcut created on the desktop.
pause