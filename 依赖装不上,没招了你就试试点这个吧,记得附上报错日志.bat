@echo off
:: 强制使用 UTF-8 编码，防止中文乱码
chcp 65001 >nul

:: 强制切换到当前文件夹，防止路径识别出错
cd /d "%~dp0"

echo ==================================================
echo       SuzuEmojy 依赖全局强制安装工具
echo ==================================================
echo.
echo 正在尝试直接往你电脑的全局 Python 环境里灌入依赖...
echo 请确保当前目录下有 requirements.txt 文件。
echo --------------------------------------------------

:: 1. 强行升级全局 pip（使用清华源）
python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple

echo.
echo --------------------------------------------------
echo 开始强行安装图形库组件...
echo --------------------------------------------------

:: 2. 无脑强制全量安装依赖（带上 --user 确保不会因为权限不足而失败）
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --force-reinstall --user

echo.
echo ==================================================
echo 运行结束！请看上方是否显示了下载进度条或 Successfully installed。
echo 如果提示“'python' 不是内部或外部命令”，说明你电脑的环境变量没配好。(如果真报错我也没招了,问问ai吧)
echo ==================================================
pause