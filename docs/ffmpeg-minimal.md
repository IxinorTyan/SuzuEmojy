# Windows 离线版：精简 FFmpeg

发行包在 `bin/ffmpeg/ffmpeg.exe` 内置转码器，终端用户无需联网、装工具链或改 PATH。当前配方保留本地 WebM 的 VP9（含 alpha）解码、PNG 抽帧、GIF 调色板及动画输出；它不是通用版 FFmpeg。

## 构建

1. 使用 Git Bash，准备 Windows x64 MinGW-w64 GCC（含 binutils）、GNU Make（MSYS 版）和 NASM，并确保在 Bash 的 PATH 上。也可使用 MSYS2 的 MINGW64 shell：`pacman -S --needed make mingw-w64-x86_64-gcc mingw-w64-x86_64-nasm`。
2. 在仓库根目录运行 `bash scripts/build_ffmpeg_minimal.sh`。可传入并行数，例如 `bash scripts/build_ffmpeg_minimal.sh 6`。上游源码按 SHA-256 验证并缓存于 `dist/ffmpeg-minimal-downloads/`，工作目录路径会打印在构建日志中。
3. 回到 PowerShell，运行 `.\build_windows.ps1`。它会复用 `dist/ffmpeg-minimal/` 的结果，构建配方更新后需要重跑第 2 步。`-DryRun` 只输出计划，不下载或编译。

发布时请把输出目录中和应用 ZIP 同级的 `SuzuEmojy-FFmpeg-sources.tar` 放在同一个 Release 中。源码 tar 内包含这次使用的原版上游源码、构建脚本和 FFmpeg 配置，不放入应用 ZIP；应用自身会附带 `bin/ffmpeg/THIRD_PARTY.txt`。

## 验证

Windows 封包会运行原生冒烟测试，检查内置 FFmpeg 能启动和转换贴纸。
