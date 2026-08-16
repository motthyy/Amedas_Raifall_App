@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo Amedas Long-term Rainfall / Railway Disaster Prevention App
echo Setup
echo ============================================================
echo.
echo Please choose a setup option:
echo   [1] Install Python 3.11 automatically, then install required libraries
echo       (choose this if Python 3.11 is not installed on this PC yet)
echo   [2] Python 3.11 is already installed - install required libraries only
echo   [3] Check current setup status (Python / venv / libraries / Playwright)
echo.
choice /c 123 /n /m "Enter 1, 2, or 3: "
if errorlevel 3 goto :check_status
if errorlevel 2 goto :find_existing_python
goto :install_python_311

:check_status
echo.
echo ============================================================
echo Current Setup Status
echo ============================================================
echo.

set "STATUS_PY_LAUNCHER=py -3.11"
where py >nul 2>nul
if errorlevel 1 set "STATUS_PY_LAUNCHER=python"
%STATUS_PY_LAUNCHER% -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) and sys.version_info.releaselevel == 'final' else 1)" >nul 2>nul
if errorlevel 1 (
    echo [NG] Python 3.11            : not found
) else (
    for /f "delims=" %%V in ('%STATUS_PY_LAUNCHER% --version 2^>^&1') do set "STATUS_PY_VER=%%V"
    echo [OK] Python 3.11            : !STATUS_PY_VER!
)

if exist ".venv\Scripts\python.exe" (
    echo [OK] Virtual environment    : .venv found
) else (
    echo [NG] Virtual environment    : .venv not found
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import streamlit, pandas, numpy, scipy, plotly, playwright, requests, httpx, bs4, openpyxl, xlsxwriter, pyarrow, yaml, tenacity, pytz" >nul 2>nul
    if errorlevel 1 (
        echo [NG] Required libraries     : missing or incomplete
    ) else (
        echo [OK] Required libraries     : installed
    )
) else (
    echo [--] Required libraries     : skipped, no virtual environment
)

set "STATUS_PLAYWRIGHT_FOUND=0"
if exist "%LOCALAPPDATA%\ms-playwright" (
    for /d %%D in ("%LOCALAPPDATA%\ms-playwright\chromium-*") do set "STATUS_PLAYWRIGHT_FOUND=1"
)
if "!STATUS_PLAYWRIGHT_FOUND!"=="1" (
    echo [OK] Playwright Chromium    : installed
) else (
    echo [NG] Playwright Chromium    : not installed
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import amedas_rainfall" >nul 2>nul
    if errorlevel 1 (
        echo [NG] Project package        : amedas_rainfall not installed
    ) else (
        echo [OK] Project package        : amedas_rainfall installed
    )
) else (
    echo [--] Project package        : skipped, no virtual environment
)

echo.
echo ============================================================
echo If any item above shows [NG], run install.bat again and
echo choose option 1 or 2 to set it up.
echo ============================================================
pause
exit /b 0

:install_python_311
set "PYTHON_VERSION=3.11.9"
set "PYTHON_INSTALLER_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/python-%PYTHON_VERSION%-amd64.exe"
set "PYTHON_INSTALLER=%TEMP%\python-%PYTHON_VERSION%-installer.exe"

echo.
echo Downloading the official Python %PYTHON_VERSION% installer ...
echo   %PYTHON_INSTALLER_URL%
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%PYTHON_INSTALLER_URL%' -OutFile '%PYTHON_INSTALLER%' -UseBasicParsing } catch { exit 1 }"
if errorlevel 1 (
    echo [ERROR] Failed to download the Python installer.
    echo Please check your internet connection, or install Python 3.11
    echo manually from https://www.python.org/downloads/ and re-run this
    echo script choosing option 2.
    pause
    exit /b 1
)

echo Installing Python %PYTHON_VERSION% for the current user only
echo (no administrator rights required) ...
echo A short progress window will appear. Please wait for it to finish.
"%PYTHON_INSTALLER%" /passive InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
if errorlevel 1 (
    echo [ERROR] The Python installer reported an error.
    pause
    exit /b 1
)
del "%PYTHON_INSTALLER%" >nul 2>nul

set "PY311_DIR=%LOCALAPPDATA%\Programs\Python\Python311"
if not exist "%PY311_DIR%\python.exe" (
    echo [ERROR] Python was not found at the expected location after installation:
    echo   %PY311_DIR%
    echo Please close this window, open a new one, and re-run install.bat
    echo choosing option 2, or install Python 3.11 manually from
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)
set "PATH=%PY311_DIR%;%PY311_DIR%\Scripts;%PATH%"
set "PY_LAUNCHER=%PY311_DIR%\python.exe"
echo Python %PYTHON_VERSION% installed successfully.
goto :check_version

:find_existing_python
set PY_LAUNCHER=py -3.11
where py >nul 2>nul
if errorlevel 1 goto :use_python_command
%PY_LAUNCHER% -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) and sys.version_info.releaselevel == 'final' else 1)" >nul 2>nul
if errorlevel 1 goto :use_python_command
goto :check_version

:use_python_command
echo [WARNING] "py -3.11" launcher not found. Trying "python" command instead.
set PY_LAUNCHER=python

:check_version
echo Using Python command: %PY_LAUNCHER%
%PY_LAUNCHER% --version
if errorlevel 1 (
    echo [ERROR] Python 3.11 was not found.
    echo Re-run install.bat and choose option 1 to install it automatically,
    echo or install it manually from https://www.python.org/downloads/
    pause
    exit /b 1
)
%PY_LAUNCHER% -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) and sys.version_info.releaselevel == 'final' else 1)" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] This app requires a stable Python 3.11 release.
    echo The selected command points to another version or a prerelease.
    echo Re-run install.bat and choose option 1, or install Python 3.11 manually.
    pause
    exit /b 1
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) and sys.version_info.releaselevel == 'final' else 1)" >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Existing .venv was created with an incompatible Python version.
        echo Rename or remove the project-local .venv folder, then run install.bat again.
        pause
        exit /b 1
    )
)
if exist ".venv" goto :venv_exists
echo Creating virtual environment .venv ...
%PY_LAUNCHER% -m venv .venv
if errorlevel 1 (
    echo [ERROR] Failed to create the virtual environment.
    pause
    exit /b 1
)
goto :install_deps

:venv_exists
echo Virtual environment .venv already exists.

:install_deps
echo Installing dependencies (this may take a few minutes) ...
call ".venv\Scripts\python.exe" -m pip install --upgrade pip
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :install_deps_via_corp_proxy
goto :install_playwright

:install_deps_via_corp_proxy
echo.
echo [INFO] Direct access to PyPI failed. This is common on corporate
echo networks that inspect SSL/TLS traffic through a proxy (e.g. Zscaler).
echo Retrying using certificates from the Windows certificate store ...
set "CORP_CA_BUNDLE="
set "CA_BUNDLE_TMP=%TEMP%\amedas_rainfall_ca_bundle_path.txt"
del "%CA_BUNDLE_TMP%" >nul 2>nul
".venv\Scripts\python.exe" "%~dp0scripts\build_ca_bundle.py" > "%CA_BUNDLE_TMP%"
if exist "%CA_BUNDLE_TMP%" set /p CORP_CA_BUNDLE=<"%CA_BUNDLE_TMP%"
del "%CA_BUNDLE_TMP%" >nul 2>nul
if not defined CORP_CA_BUNDLE (
    echo [ERROR] Failed to install dependencies, and no corporate proxy
    echo certificate could be found in the Windows certificate store.
    echo Please check your internet connection and proxy settings.
    pause
    exit /b 1
)
set "PIP_CERT=%CORP_CA_BUNDLE%"
set "SSL_CERT_FILE=%CORP_CA_BUNDLE%"
set "NODE_EXTRA_CA_CERTS=%CORP_CA_BUNDLE%"
call ".venv\Scripts\python.exe" -m pip install --upgrade pip
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies even with the Windows
    echo certificate store trusted. Please check your internet connection
    echo and proxy settings.
    pause
    exit /b 1
)

:install_playwright
echo Installing Playwright browser (Chromium) for the fallback download mode ...
call ".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 (
    echo [WARNING] Failed to install the Playwright browser.
    echo The Playwright fallback mode will not be available.
    echo This does not affect the normal direct-download mode.
)

echo Installing this project in editable mode ...
call ".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 (
    echo [ERROR] Failed to install the project package.
    pause
    exit /b 1
)

echo Verifying installed packages and application imports ...
call ".venv\Scripts\python.exe" -m pip check
if errorlevel 1 (
    echo [ERROR] Installed packages have incompatible dependencies.
    pause
    exit /b 1
)
call ".venv\Scripts\python.exe" -c "from amedas_rainfall.config import get_default_config; get_default_config(); import amedas_rainfall.ui.station_page, amedas_rainfall.ui.probability_page"
if errorlevel 1 (
    echo [ERROR] Application smoke test failed. Review the error shown above.
    pause
    exit /b 1
)

echo ============================================================
echo Setup complete.
echo Run run.bat to start the app.
echo ============================================================
pause
