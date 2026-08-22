@echo off
setlocal
if /I "%~1"=="-?" goto :help
if /I "%~1"=="-h" goto :help
if /I "%~1"=="--help" goto :help
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0bilibili-subtitles.ps1" %*
set "BILI_SUBTITLE_EXIT=%ERRORLEVEL%"
endlocal & exit /b %BILI_SUBTITLE_EXIT%

:help
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0bilibili-subtitles.ps1" -ShowHelp
set "BILI_SUBTITLE_EXIT=%ERRORLEVEL%"
endlocal & exit /b %BILI_SUBTITLE_EXIT%
