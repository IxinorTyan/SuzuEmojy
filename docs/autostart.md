# 开机自启动

在「设置 → 窗口设置 → 开机自启动」开启或关闭，立即生效，无需管理员权限。

- 默认关闭，仅在用户开启后注册当前 Windows 用户的登录启动项。
- 登录 Windows 后启动到托盘，可通过托盘图标或原有全局快捷键打开面板。手动启动仍显示主窗口。
- 无法使用系统托盘时显示主窗口，避免无法找到程序。
- 移动程序或 Python 运行环境后，需要重新开启该选项。删除软件前可先关闭该选项。
- 旧版 Nuitka 打包版可能错误注册为不存在的 `bin/python.exe`，或直接绕过发布包入口运行核心。新版会把同一安装目录下的旧启动项迁移到根目录 `SuzuEmojy.exe --autostart`，启动器再转发参数给核心。迁移不会启用已关闭的自启动，也不会清除任务管理器中的禁用状态。
- 开关同时检查 Windows 的 `StartupApproved` 禁用记录，系统中已禁用时显示关闭。在软件内主动开启，会清除本软件对应的旧禁用记录并验证结果，无须另去任务管理器操作。

实现使用 `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run` 下的 `SuzuEmojy` 值，只处理本软件的注册和禁用记录，不修改其他启动项。开关读取当前安装路径的实际状态，不随图库配置迁移到另一台电脑。源码运行使用当前 Python 环境（优先 pythonw.exe）；独立打包版通过 Windows `GetModuleFileNameW` 获取实际核心位置，并优先注册其上一层的发布包启动器。核心设置工作目录时也使用真实进程路径，避免依赖 Nuitka 的 `sys.executable` 占位值或 Windows 登录时的工作目录。两种打包启动器都保留 `--autostart` 等传入参数。

自动化验证：`python -B -m unittest discover -s tests -p "test_autostart*.py" -v`。基础测试模拟注册表；Windows 集成测试使用临时非启动注册表键，并实际编译启动器验证中文/空格路径、参数转发和工作目录，不修改用户真实的启动项。另用实际 Nuitka standalone 探测程序验证编译后的命令生成、开关和禁用状态迁移。真实重启登录仍需在交付版本上验收，测试不会重启用户电脑。
