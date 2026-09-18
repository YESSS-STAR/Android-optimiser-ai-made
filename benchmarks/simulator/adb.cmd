@echo off
rem Simulated adb shim for Windows. Put this directory on PATH.
setlocal
set "PY=%FAKE_ADB_PYTHON%"
if "%PY%"=="" set "PY=python"
"%PY%" -S "%~dp0adb_sim.py" %*
exit /b %ERRORLEVEL%
