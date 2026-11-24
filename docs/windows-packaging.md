# Windows 2.x 封包与更新

封包工具生成本地预览包或正式 ZIP，不推送代码，不创建标签、PR 或 GitHub Release。它与 1.x 的发布脚本、`version.json` 和更新协议分开。

## 使用已经封好的 ZIP

解压到一个新目录，运行外层的 `SuzuEmojy.exe`。外层启动程序相对定位 `bin/`，整套文件移动后仍可双击启动。包内自带 WebP、TGS、动画 GIF 所需的原生解码／编码库和精简 FFmpeg，不含 Python、pip、Pillow 运行时或 Qt，首次运行也不会下载运行库。

同一 Windows 登录会话只运行一个新版实例。即使程序已收进托盘，再次启动也不会开第二份；可通过托盘或快捷键重新呼出。

- `system-runtime`（默认）：使用标准 Windows 10 22H2 x64 / Windows 11 x64 自带的 .NET Framework 4.8 或更高版本，以及系统 SQLite（`winsqlite3.dll`），不需要下载 .NET 8 或安装运行库。不承诺被主动移除了系统组件的定制镜像，也不支持 Windows 8 / 8.1。
- `desktop-runtime`：用于对照，需要预装 .NET 8 Desktop Runtime x64。
- `self-contained`：用于对照，自带 .NET 8 Desktop Runtime，ZIP 更大。

默认图库 `data/` 和外层的 `SuzuEmojy.exe`、`bin/` 同级，压缩包不包含 `data/`。退出旧版后，把完整旧 `data/`（旧独立版一般在 `bin/data`）复制到新版外层，再双击 `SuzuEmojy.exe`。如果新版已经有 `data/`，先把它改名留作备份再放入旧文件夹，不要把两份图库逐文件混合；也可用 `SuzuEmojy.exe --data "图库副本的绝对路径"`。不要让两个版本同时写同一个图库，也不要用 1.x 更新器安装本预览包。

## 2.x 就地更新

在「关于软件」点击「检查更新」，查看更新说明后选择「下载更新…」。下载完成后，只有点击「重启并安装」才会退出并更新。程序启动时不会联网检查，也不会自动安装。未点击安装可返回其他页面；同次运行内再次进「关于软件」可继续安装。

就地安装适用于包含新版更新器的 `system-runtime` 便携包。1.x、早期 2.0 预览包及其他运行库类型，使用上面的新目录方式手动迁移。源码模式只提供下载，不覆盖工作目录。支持重启时保留位于程序目录之外的自定义 `--data` 路径；安装器不迁移图库。

更新包须来自 GitHub 正式发布，文件长度、SHA-256、版本和布局均需匹配。更新器在原进程退出后替换 `bin/` 中的受管文件，保留外层启动器、图库和其他文件；程序启动探测失败会回滚。若安装过程被中断，再次双击外层 `SuzuEmojy.exe` 会先恢复，再启动程序。该探测不会打开或验证真实图库。

## Telegram 默认连接

在 Windows 用户环境变量里设置 `SUZU_TG_BOT_TOKEN`，如需默认 HTTPS API 反代再设置 `SUZU_TG_API_PROXY`，然后重新启动应用。默认值不会回填到「高级配置」，也不会保存到图库或封进 ZIP。手动填写 Bot Token 后，若没同时填写 API 反代，则使用 Telegram 官方地址；隐藏 Token 不会发送给临时填写的反代。只配置由自己管理或明确可信的反代，因为它能看到 Token。

TG 的 APNG 转 GIF 保留原有播放节奏，最高约 60 fps；GIF 按 10 毫秒计时，用相邻帧的时长补偿保持总时长。选择原始格式时不重新编码。

## 从仓库封包

双击仓库根目录的 [`build_windows.bat`](../build_windows.bat)，自动使用项目版本号并开始打包。脚本会查找本仓库 `dist/` 和附近 SuzuEmojy 工作目录里与当前配方匹配的精简 FFmpeg，找不到时自动尝试构建，无需手选目录。成功后自动打开本次目录；同版本重打会使用带时间的独立目录，不覆盖旧包。

在 Windows 上准备 PowerShell 和 .NET SDK（需支持 C# 12）；首次构建 WebP 和调色板 DLL 时需要 MinGW-w64 GCC。默认模式会用 .NET Framework 4.8 参考程序集（开发包或 SDK 从 NuGet 还原，仅构建时需要）。没有匹配的 FFmpeg 缓存时，需要先备好 [精简 FFmpeg 构建工具链](ffmpeg-minimal.md)。也可用 `-FfmpegDirectory` 指定缓存、用 `-Version` 覆盖版本。工具仍会校验配方哈希，不会打入完整版 FFmpeg。

```powershell
# 使用 Windows 自带运行库，整个 ZIP 不超过 20 MB
.\build_windows.ps1
# .NET 8 对照包
.\build_windows.ps1 -Mode desktop-runtime
.\build_windows.ps1 -Mode self-contained
```

构建会下载并校验原生依赖的固定版本；rlottie 只提取 C ABI DLL 和许可证。打包时运行原生离屏冒烟测试，检查编解码器、图库迁移和资源包往返。程序 ZIP、校验文件和独立的 `SuzuEmojy-FFmpeg-sources.tar` 在 `dist/windows-packages` 下。正式分发时应同时提供 FFmpeg 源码包。

版本默认取自 WPF 项目的 `<Version>`；`-Version 2.0.0` 可覆盖这一次构建，同时写入程序版本和封包标记。工具不覆盖已有成果；同版本重打可传 `-OutputDirectory dist/另一个目录`。用 `-DryRun` 可查看命名和路径，不写入、编译或下载。程序 ZIP 不包含个人图库、测试运行器、调试符号或 1.x 更新器。

正式发布时，使用三段数字版本（如 `2.0.0`），创建同版本的稳定版 GitHub Release（如 `v2.0.0`），上传原名 `SuzuEmojy-Windows-2.0.0-win-x64-system-runtime.zip` 和对应 FFmpeg 源码包。确认 GitHub API 中该 ZIP 已有 `sha256:` 摘要，并将稳定版设为 Latest。每次发布递增版本，不复用已发布的版本号。

发布前可以用离屏测试工具演练真实 ZIP 升级和启动探测：

```powershell
dotnet build win_frontend_tests/SuzuEmojy.Windows.Tests.csproj -c Release -p:UseSystemWindowsRuntime=true -o dist/update-check
.\dist\update-check\SuzuEmojy.Windows.Tests.exe --update-tests dist/update-results
.\dist\update-check\SuzuEmojy.Windows.Tests.exe --upgrade-fixtures <旧包.zip> <新包.zip> dist/update-results
.\dist\update-check\SuzuEmojy.Windows.Tests.exe --upgrade-worker <旧包.zip> <新包.zip> dist/update-results
```

进程交接演练需先退出正在运行的 SuzuEmojy；测试只操作隔离目录及模拟进程。

完整离屏回归也能用系统运行库执行：给测试项目的 `dotnet build` 加上 `-p:UseSystemWindowsRuntime=true`。测试程序可用解压后的 `bin/` 作为第一个参数，按 [前端测试说明](windows-native-frontend.md) 传入合成图库和输出目录；不显示窗口，不注册真实热键，也不向其他程序发送粘贴。
