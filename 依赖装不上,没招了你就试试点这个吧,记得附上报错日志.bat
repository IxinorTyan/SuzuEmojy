@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

title SuzuEmojy 一键修复

set LOG=%~dp0repair_log.txt
echo ==== Repair Start ==== > "%LOG%"
echo %date% %time% >> "%LOG%"

echo.
echo ========= 依赖(大概是)强力安装 =========
echo.

::----------------------------------------------------------
:: 找 Python
::----------------------------------------------------------

call :FindPython

if defined PYTHON goto INSTALL

echo 未检测到 Python。
echo.

set URL=https://www.python.org/ftp/python/3.13.7/python-3.13.7-amd64.exe
set INSTALLER=%TEMP%\python_installer.exe

echo 正在下载 Python...
echo.

where curl >nul 2>nul

if %errorlevel%==0 (

    curl -L "%URL%" -o "%INSTALLER%" >>"%LOG%" 2>&1

) else (

    powershell -NoProfile -ExecutionPolicy Bypass ^
      -Command "Invoke-WebRequest '%URL%' -OutFile '%INSTALLER%'" >>"%LOG%" 2>&1

)

if not exist "%INSTALLER%" (

    echo 下载失败。
    pause
    exit /b 1

)

echo.
echo 下载完成。
echo.

::----------------------------------------------------------
:: 安装
::----------------------------------------------------------

echo 正在安装 Python...
echo.

powershell -NoProfile -ExecutionPolicy Bypass ^
-Command "Start-Process -FilePath '%INSTALLER%' -ArgumentList '/passive InstallAllUsers=0 Include_pip=1 PrependPath=1 Shortcuts=0' -Wait"

echo.
echo 等待安装完成...
echo.

set /a COUNT=0

:WAIT

call :FindPython

if defined PYTHON goto INSTALL

set /a COUNT+=1

if %COUNT% GEQ 90 (

    echo.
    echo Python 安装失败！
    echo.
    echo 请手动运行：
    echo %INSTALLER%
    echo.
    pause
    exit /b

)

timeout /t 2 >nul

goto WAIT

::----------------------------------------------------------
:: 安装依赖
::----------------------------------------------------------

:INSTALL

echo.
echo 找到 Python：
echo %PYTHON%
echo.

"%PYTHON%" -m ensurepip --upgrade >>"%LOG%" 2>&1

"%PYTHON%" -m pip install --upgrade pip ^
-i https://pypi.tuna.tsinghua.edu.cn/simple >>"%LOG%" 2>&1

if not exist requirements.txt (

    echo requirements.txt 不存在！
    pause
    exit /b

)

echo.
echo 安装依赖...
echo.

"%PYTHON%" -m pip install ^
-r requirements.txt ^
-i https://pypi.tuna.tsinghua.edu.cn/simple ^
--default-timeout=120 ^
--retries=5 ^
--user >>"%LOG%" 2>&1

if errorlevel 1 (

    echo.
    echo 安装失败！
    echo 请把 repair_log.txt 发给开发者。(以及这很可能是因为电脑没有装python?可以装了以后再试试...)
    pause
    exit /b

)

echo.
echo =====================================
echo 修复成功！
echo 请重新启动 SuzuEmojy。
echo =====================================
pause
exit /b

::==========================================================
:: 查找真正 Python
::==========================================================

:FindPython

set PYTHON=

for %%P in (
"%LocalAppData%\Programs\Python\Python313\python.exe"
"%LocalAppData%\Programs\Python\Python312\python.exe"
"%LocalAppData%\Programs\Python\Python311\python.exe"
"%ProgramFiles%\Python313\python.exe"
"%ProgramFiles%\Python312\python.exe"
"%ProgramFiles%\Python311\python.exe"
"%ProgramFiles(x86)%\Python313\python.exe"
"%ProgramFiles(x86)%\Python312\python.exe"
"%ProgramFiles(x86)%\Python311\python.exe"
) do (
    if exist %%~P (
        set PYTHON=%%~P
        exit /b
    )
)

exit /b