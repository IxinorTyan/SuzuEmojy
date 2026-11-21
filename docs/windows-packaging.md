# Windows 2.0 本地封包

封包工具只生成本地预览 ZIP，不推送代码，不创建标签、PR 或 GitHub Release。它与 1.x 的发布脚本、`version.json` 和更新协议分开。

## 使用已经封好的 ZIP

解压到一个新目录，运行 `SuzuEmojy.Windows.exe`。不要只拷贝 EXE，目录里的 `backend` 必须完整。本包仅面向 Windows x64。轻量包（文件名含 `desktop-runtime`）需要 .NET 8 Desktop Runtime x64；`self-contained` 包把该运行库也带上。两种包都已内置 Python、Pillow、TG 下载／TGS 转换所需依赖和精简 FFmpeg，不需要 Qt。

默认图库在 EXE 旁边的 `data`，不在程序后端目录中。试用旧图库时先退出旧版、复制整个 `data`（旧独立版一般在 `bin/data`），再用 `SuzuEmojy.Windows.exe --data "图库副本的绝对路径"` 启动。不要让两个版本同时写同一个图库，也不要用 1.x 更新器安装本预览包。

## 从仓库封包

在 Windows 上准备 .NET SDK（需能构建 `net8.0-windows`）、Python 3.11+ 和 pip。先按 [精简 FFmpeg 构建说明](ffmpeg-minimal.md) 生成 `dist/ffmpeg-minimal`。若在另一份源码中已用同一配方构建，可用 `--ffmpeg-dir` 指向该产物目录。工具会校验配方哈希，不会静默打入完整版 FFmpeg。

```powershell
python -B build_windows.py
# 也打入 .NET 运行库
python -B build_windows.py --self-contained
```

首次需要联网获取锁定的 CPython 和依赖；Python ZIP 和各 wheel 校验 SHA-256。打包时会用内置 Python 离线验证 APNG 导入、资源包往返、TGS 库和 FFmpeg。程序 ZIP、校验文件和独立的 `SuzuEmojy-FFmpeg-sources.tar` 在 `dist/windows-packages` 下。正式分发时应同时提供 FFmpeg 源码包。

版本取自 WPF 项目的 `<Version>`。工具不覆盖已有成果；同版本重打可传 `--output-dir dist/另一个目录`。用 `--dry-run` 可查看命名和路径，不写入、编译或下载。程序 ZIP 不包含个人图库、Qt、调试符号或 1.x 更新器。
