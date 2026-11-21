# Windows 2.0 本地封包

封包工具只生成本地预览 ZIP，不推送代码，不创建标签、PR 或 GitHub Release。它与 1.x 的发布脚本、`version.json` 和更新协议分开。

## 使用已经封好的 ZIP

解压到一个新目录，运行外层的 `SuzuEmojy.exe`。外层启动程序相对定位 `bin/`，整套文件移动后仍可双击启动。包内自带 WebP、TGS、动画 GIF 所需的原生解码／编码库和精简 FFmpeg，不含 Python、pip、Pillow 运行时或 Qt，首次运行也不会下载运行库。

同一 Windows 登录会话只运行一个新版实例。即使程序已收进托盘，再次启动也不会开第二份；可通过托盘或快捷键重新呼出。

- `system-runtime`（默认）：使用标准 Windows 10 22H2 x64 / Windows 11 x64 自带的 .NET Framework 4.8 或更高版本，以及系统 SQLite（`winsqlite3.dll`），不需要下载 .NET 8 或安装运行库。不承诺被主动移除了系统组件的定制镜像，也不支持 Windows 8 / 8.1。
- `desktop-runtime`：用于对照，需要预装 .NET 8 Desktop Runtime x64。
- `self-contained`：用于对照，自带 .NET 8 Desktop Runtime，ZIP 更大。

默认图库 `data/` 和外层的 `SuzuEmojy.exe`、`bin/` 同级，压缩包不包含 `data/`。退出旧版后，把完整旧 `data/`（旧独立版一般在 `bin/data`）复制到新版外层，再双击 `SuzuEmojy.exe`。如果新版已经有 `data/`，先把它改名留作备份再放入旧文件夹，不要把两份图库逐文件混合；也可用 `SuzuEmojy.exe --data "图库副本的绝对路径"`。不要让两个版本同时写同一个图库，也不要用 1.x 更新器安装本预览包。

## Telegram 默认连接

在 Windows 用户环境变量里设置 `SUZU_TG_BOT_TOKEN`，如需默认 HTTPS API 反代再设置 `SUZU_TG_API_PROXY`，然后重新启动应用。默认值不会回填到「高级配置」，也不会保存到图库或封进 ZIP。手动填写 Bot Token 后，若没同时填写 API 反代，则使用 Telegram 官方地址；隐藏 Token 不会发送给临时填写的反代。只配置由自己管理或明确可信的反代，因为它能看到 Token。

TG 的 APNG 转 GIF 保留原有播放节奏，最高约 60 fps；GIF 按 10 毫秒计时，用相邻帧的时长补偿保持总时长。选择原始格式时不重新编码。

## 从仓库封包

在 Windows 上准备 PowerShell 和 .NET SDK（需支持 C# 12）；首次构建 WebP 和调色板 DLL 时需要 MinGW-w64 GCC。默认模式会用 .NET Framework 4.8 参考程序集（开发包或 SDK 从 NuGet 还原，仅构建时需要）。先按 [精简 FFmpeg 构建说明](ffmpeg-minimal.md) 生成 `dist/ffmpeg-minimal`。若在另一份源码中已用同一配方构建，可用 `-FfmpegDirectory` 指向该产物目录。工具会校验配方哈希，不会静默打入完整版 FFmpeg。

```powershell
# 使用 Windows 自带运行库，整个 ZIP 不超过 20 MB
.\build_windows.ps1
# .NET 8 对照包
.\build_windows.ps1 -Mode desktop-runtime
.\build_windows.ps1 -Mode self-contained
```

构建会下载并校验原生依赖的固定版本；rlottie 只提取 C ABI DLL 和许可证。打包时运行原生离屏冒烟测试，检查编解码器、图库迁移和资源包往返。程序 ZIP、校验文件和独立的 `SuzuEmojy-FFmpeg-sources.tar` 在 `dist/windows-packages` 下。正式分发时应同时提供 FFmpeg 源码包。

版本取自 WPF 项目的 `<Version>`。工具不覆盖已有成果；同版本重打可传 `-OutputDirectory dist/另一个目录`。用 `-DryRun` 可查看命名和路径，不写入、编译或下载。程序 ZIP 不包含个人图库、测试运行器、调试符号或 1.x 更新器。

完整离屏回归也能用系统运行库执行：给测试项目的 `dotnet build` 加上 `-p:UseSystemWindowsRuntime=true`。测试程序可用解压后的 `bin/` 作为第一个参数，按 [前端测试说明](windows-native-frontend.md) 传入合成图库和输出目录；不显示窗口，不注册真实热键，也不向其他程序发送粘贴。
