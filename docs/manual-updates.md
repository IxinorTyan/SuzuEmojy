# 手动更新与发布

关于页面显示根目录 `version.json` 中的版本，点击“检查更新”才请求 GitHub Releases API。
程序启动不检查、不下载、不自动安装已下载的更新。启动器只会在检测到上次安装中断时进行本地恢复。

流程：检查稳定版 → 显示更新说明 → 用户下载 → SHA-256 校验 → 暂存 → 用户确认重启安装。
下载完成后选择“稍后”会保留暂存包；再次打开关于页的更新窗口可继续安装。
源码模式允许检查，但不会覆盖工作目录。

## 发布

1. 修改唯一版本来源 `version.json`（本次功能版本设为 `1.13.0`）。使用三段数字，修复也必须增加版本，不要用相同 tag 替换包。
2. 构建环境需安装 `pyinstaller`。运行 `build_nuitka.bat`（完整离线版）或 `build.bat`（轻量版）。
3. 构建脚本生成独立 `SuzuEmojyUpdater.exe`、`release-manifest.json`、`dist/SuzuEmojy_Release.zip` 和 `.zip.sha256`。
4. 创建与内置版本完全一致的 `vX.Y.Z` 稳定版 Release，上传固定名称 `SuzuEmojy_Release.zip`。
5. 确认 GitHub API 的 asset digest 已生成。客户端校验它，不从 zip 文件名推断版本，也不下载 GitHub 自动生成的源码包。

同一个 Release 的固定包名只对应一种构建类型。客户端拒绝在 standalone/lightweight 间切换，因为数据布局不同。
目前稳定版更新协议不支持预发布版、增量更新、跨类型迁移或变更数据库结构后的自动降级。

## 安装与恢复

发布清单记录所有程序文件及 SHA-256；压缩包不包含用户数据。更新器从 `.update` 独立运行，等待主程序进程退出，
完成所有备份后才替换文件。它删除清单中已废弃的旧程序文件，拒绝覆盖不属于旧清单的同名用户文件。
路径中的 `data`、`runtime`、`logs`、`.git`、`.update` 均被保护，根目录和 `bin/data` 一样不会被覆盖。
更新只修改程序文件，不迁移数据库。将来涉及数据库格式变化，需要另行设计迁移备份和兼容策略。

`.update/journal.json` 记录事务阶段。替换失败立即回滚；断电等中断后，从根目录启动器重新打开会先恢复。
恢复失败保留日志和备份，不启动混合版本。`.update/backup` 保留最近一次程序备份；它不代表用户数据库备份。
新版能否正常完成业务初始化不在此事务保证内：本版保证文件替换事务恢复，没有实现新版启动健康检查与自动降级。

异常可查看 `.update/last-error.txt`；网络失败时通过“打开发布页”手动下载。校验摘要验证下载完整性，并不替代代码签名。

## 验证

`python -m unittest discover -s tests -p test_updates.py -v`

`python -m unittest discover -s tests -p test_update_dialog.py -v`

发布前还应在 Windows 的可丢弃目录中完成一次真实的两个版本升级、取消下载、安装时退出及中断恢复演练。

可设置 `SUZU_TEST_UPDATER` 为已编译的更新器绝对路径，再运行 `python -m unittest discover -s tests -p test_update_windows.py -v`。测试使用临时目录和实际 Windows 启动器，验证握手、等待进程退出、安装及重启，不触碰个人数据。
