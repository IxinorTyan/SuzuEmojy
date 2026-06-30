@echo off
echo ====================================
echo Building SuzuEmojy Launcher with PyInstaller
echo ====================================

:: 检查是否安装了 pyinstaller
python -m PyInstaller --version >nul 2>&1
if %errorlevel% neq 0 (
    echo PyInstaller not found. Installing...
    python -m pip install pyinstaller
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

echo.
echo Preparing release folder...
mkdir "dist\SuzuEmojy_Release"
move /Y "dist\SuzuEmojy.exe" "dist\SuzuEmojy_Release\" >nul

echo Copying source code and assets to release folder...
xcopy /Y "main.py" "dist\SuzuEmojy_Release\" >nul
xcopy /Y "requirements.txt" "dist\SuzuEmojy_Release\" >nul
xcopy /Y "hi.ico" "dist\SuzuEmojy_Release\" >nul
xcopy /E /I /Y "fluent_ui" "dist\SuzuEmojy_Release\fluent_ui" >nul
xcopy /E /I /Y "services" "dist\SuzuEmojy_Release\services" >nul

if exist "data" xcopy /E /I /Y "data" "dist\SuzuEmojy_Release\data" >nul

echo.
echo ====================================
echo Build Complete!
echo You can find the lightweight executable and source files in the 'dist\SuzuEmojy_Release' folder.
echo The total size should be very small (around 10-15MB).
echo ====================================
pause
