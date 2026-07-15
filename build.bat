@echo off
setlocal enabledelayedexpansion

echo ====================================
echo Building SuzuEmojy Launcher with PyInstaller
echo ====================================

set BUILD_FAILED=0

:: 检查是否安装了 pyinstaller
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo.
        echo ====================================
        echo ERROR: Failed to install PyInstaller.
        echo Check your Python / pip installation and network connection.
        echo ====================================
        goto :fail
    )
)

echo.
echo Cleaning old build folders...
if exist "build" rmdir /s /q "build"
if exist "dist\SuzuEmojy_Release" rmdir /s /q "dist\SuzuEmojy_Release"
if exist "SuzuEmojy.spec" del /q "SuzuEmojy.spec"

echo.
echo Starting PyInstaller for Launcher...
:: 使用 --onefile 打包成单文件，彻底解决 DLL 丢失问题
python -m PyInstaller --noconsole --onefile --icon=hi.ico --add-data "hi.ico;." --name "SuzuEmojy" launcher.py
if errorlevel 1 (
    echo.
    echo ====================================
    echo ERROR: PyInstaller build failed.
    echo Scroll up to see the actual PyInstaller error output above.
    echo ====================================
    goto :fail
)

if not exist "dist\SuzuEmojy.exe" (
    echo.
    echo ====================================
    echo ERROR: PyInstaller reported success but dist\SuzuEmojy.exe was not found.
    echo ====================================
    goto :fail
)

echo.
echo Preparing release folder...
mkdir "dist\SuzuEmojy_Release"
if errorlevel 1 (
    echo ERROR: Failed to create dist\SuzuEmojy_Release folder.
    goto :fail
)

move /Y "dist\SuzuEmojy.exe" "dist\SuzuEmojy_Release\"
if errorlevel 1 (
    echo ERROR: Failed to move SuzuEmojy.exe into release folder.
    goto :fail
)

echo Copying source code and assets to release folder...

call :copy_required_file "main.py" "dist\SuzuEmojy_Release\"
call :copy_required_file "requirements.txt" "dist\SuzuEmojy_Release\"
call :copy_required_file "hi.ico" "dist\SuzuEmojy_Release\"
call :copy_required_file "README.md" "dist\SuzuEmojy_Release\"
call :copy_required_file "说明书.md" "dist\SuzuEmojy_Release\"
call :copy_required_file "依赖装不上,没招了你就试试点这个吧,记得附上报错日志.bat" "dist\SuzuEmojy_Release\"

if !BUILD_FAILED! equ 1 goto :fail

call :copy_required_dir "fluent_ui" "dist\SuzuEmojy_Release\fluent_ui"
call :copy_required_dir "services" "dist\SuzuEmojy_Release\services"

if !BUILD_FAILED! equ 1 goto :fail

if exist "data" (
    xcopy /E /I /Y "data" "dist\SuzuEmojy_Release\data" >nul
    if errorlevel 1 (
        echo ERROR: Failed to copy data folder.
        goto :fail
    )
) else (
    echo WARNING: "data" folder not found, skipping ^(this may be fine if not required^).
)

echo.
echo ====================================
echo Build Complete!
echo You can find the lightweight executable and source files in the 'dist\SuzuEmojy_Release' folder.
echo The total size should be very small (around 10-15MB).
echo ====================================
pause
exit /b 0

:copy_required_file
:: %~1 = 源文件, %~2 = 目标目录
if not exist "%~1" (
    echo ERROR: Required file not found: "%~1"
    set BUILD_FAILED=1
    exit /b 1
)
xcopy /Y "%~1" "%~2" >nul
if errorlevel 1 (
    echo ERROR: Failed to copy "%~1"
    set BUILD_FAILED=1
    exit /b 1
)
exit /b 0

:copy_required_dir
:: %~1 = 源目录, %~2 = 目标目录
if not exist "%~1" (
    echo ERROR: Required folder not found: "%~1"
    set BUILD_FAILED=1
    exit /b 1
)
xcopy /E /I /Y "%~1" "%~2" >nul
if errorlevel 1 (
    echo ERROR: Failed to copy folder "%~1"
    set BUILD_FAILED=1
    exit /b 1
)
exit /b 0

:fail
echo.
echo ====================================
echo BUILD FAILED - see error messages above.
echo ====================================
if exist "dist\SuzuEmojy_Release" (
    ren "dist\SuzuEmojy_Release" "SuzuEmojy_Release_FAILED" >nul 2>&1
    echo Release folder renamed to SuzuEmojy_Release_FAILED - do NOT publish it.
)
pause
exit /b 1