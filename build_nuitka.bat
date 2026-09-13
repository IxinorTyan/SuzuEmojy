@echo off
setlocal
cd /d "%~dp0"

echo ====================================
echo Building SuzuEmojy with Nuitka
echo ====================================
echo.

:: Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not found in PATH.
    echo Please ensure Python is installed and added to your system PATH.
    echo.
    pause
    exit /b 1
)

:: Check if Nuitka is installed
python -m nuitka --version >nul 2>&1
if errorlevel 1 (
    echo Nuitka is not installed. Installing Nuitka and zstandard...
    python -m pip install nuitka zstandard
    if errorlevel 1 (
        echo ERROR: Failed to install Nuitka via pip.
        echo.
        pause
        exit /b 1
    )
)

:: Run build script
echo Starting Nuitka build process...
python build_nuitka.py %*
if errorlevel 1 (
    echo.
    echo ====================================
    echo BUILD FAILED - see error messages above.
    echo ====================================
    pause
    exit /b 1
)

echo.
echo ====================================
echo BUILD SUCCESSFUL!
echo Output directory: dist\SuzuEmojy_Release
echo ====================================
pause
exit /b 0
