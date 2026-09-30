# 开机自启动

在「设置 → 窗口设置 → 开机自启动」开启或关闭，立即生效，无需管理员权限。

- 默认关闭，仅在用户开启后注册当前 Windows 用户的登录启动项。
- 登录 Windows 后启动到托盘，可通过托盘图标或原有全局快捷键打开面板。手动启动仍显示主窗口。
- 无法使用系统托盘时显示主窗口，避免无法找到程序。
- 移动程序或 Python 运行环境后，需要重新开启该选项。删除软件前可先关闭该选项。
- 若在 Windows 任务管理器的「启动应用」中禁用了 SuzuEmojy，需要在那里重新启用。

实现使用 `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run` 下的 `SuzuEmojy` 值，不修改其他启动项。开关读取当前安装路径的注册状态，不随图库配置迁移到另一台电脑。源码运行使用当前 Python 环境（优先 pythonw.exe）；独立打包版使用核心可执行文件。

自动化验证：`python -B -m unittest discover -s tests -p test_autostart.py -v`。测试模拟注册表，不会修改本机启动项。真实登录及重新打包后的启动仍需手动验收。
