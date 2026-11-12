@echo off
rem Double-click to run main.py locally. The first run creates .venv and installs
rem dependencies; later runs reinstall only when a dependencies\requirements*.txt changes.
setlocal
cd /d "%~dp0"
set "py=%~dp0.venv\Scripts\python.exe"
set "stamp=%~dp0.venv\requirements.installed"
if not exist "%py%" call :create || goto :done
copy /y /b dependencies\requirements.txt + dependencies\requirements-local.txt "%stamp%.new" >nul
fc /b "%stamp%.new" "%stamp%" >nul 2>nul && goto :run
echo Installing dependencies into .venv ...
rem The internal index rejects proxied requests; venv config overrides user pip.ini.
> .venv\pip.ini (
    echo [global]
    echo index-url = https://tmg-repo.mfg.intel.com/pypi-s3-prod/simple/
    echo trusted-host = tmg-repo.mfg.intel.com
    echo proxy =
)
"%py%" -m pip install --disable-pip-version-check -r dependencies\requirements-local.txt || goto :done
move /y "%stamp%.new" "%stamp%" >nul
:run
"%py%" main.py
:done
set status=%errorlevel%
pause
exit /b %status%

:create
echo Creating .venv ...
for %%v in (3.13 3.12 3.11 3.14) do (
    py -%%v -c "" >nul 2>nul && (py -%%v -m venv .venv & exit /b)
)
python -m venv .venv && exit /b
echo Python 3.11+ is required: install it from https://www.python.org/downloads/
exit /b 1
