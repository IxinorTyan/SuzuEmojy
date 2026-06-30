# SuzuEmojy - 本地表情包管理器

一个独立、轻量、高颜值的本地表情包管理系统。采用了现代化的 Fluent Design（Windows 11 风格）设计，让你能够跨软件快速管理、搜索和发送你的专属表情包。

## ✨ 核心特性
(具体操作看说明书)
- 🎨 **现代化 UI**：采用 Fluent Design，支持无边框窗口、云母（Mica）特效、跟随系统深浅色主题自动切换。支持ctrl+鼠标滚轮调节缩略图大小
- ⚡ **闪电呼出**：支持全局快捷键（默认 `Ctrl+Shift+E`）一键唤醒，用完即走，常驻系统托盘。
- 🖱️ **快速发送**：点击表情包即可复制到剪贴板，并**自动粘贴**到你刚才激活的聊天窗口。
- 📂 **便捷管理**：
  - 支持直接将图片拖拽进窗口进行批量导入。
  - 支持在网格内自由拖拽排序。
  - 支持无限创建分类，并可以为分类设置专属 Emoji 图标。
  - 支持对表情包设置自定义"搜索关键词"。
- 🔍 **智能搜索**：通过上方搜索框瞬间找到你做过关键词标记的表情。
- 🖼️ **悬停预览**：鼠标悬停在表情包上，可自动弹出大图预览。
- 📦 **绿色便携**：打包成 EXE 后支持纯绿色运行！所有数据均自动保存在 EXE 同级的 `data/` 目录下。拷贝整个文件夹即可将你的专属表情包带到任何电脑上。

## 🚀 下载与使用

**普通用户：**
1. 请前往 [Releases](https://github.com/IxinorTyan/SuzuEmojy/releases) 页面。
2. 下载最新版本的 `SuzuEmojy.zip` 压缩包并解压到一个你喜欢的文件夹中。
3. 双击运行 `SuzuEmojy.exe` 即可使用。
4. **数据备份**：当你需要重装系统或换电脑时，只需将 exe 旁边的 `data/` 文件夹拷走即可完美保留所有表情包与设置！

*注：首次运行可能需要加载启动，之后它将常驻系统托盘。*(画饼:暂时没有做开机自启动,或许以后会做)

## 💻 开发者指南

如果你想从源码运行或二次开发：

### 环境要求
- Python 3.9+
- Windows 10/11 (推荐 Windows 11 以获得最佳的云母特效体验)

### 安装依赖
```bash
git clone https://github.com/IxinorTyan/SuzuEmojy.git
cd SuzuEmojy
pip install -r requirements.txt
```

### 运行程序
```bash
python main.py
```

### 打包为 EXE
本项目提供了两种打包方式：

**方式一：轻量级启动器 (推荐)**
双击运行 `build.bat`。这会使用 PyInstaller 将 `launcher.py` 打包为一个极小的单文件 EXE（约 10MB）。
用户首次运行该 EXE 时，它会自动检测系统环境，并下载所需的 Python 运行环境和图形库依赖（如 PySide6）。

**方式二：完全独立打包**
如果你希望打包出一个包含所有依赖的完整离线版（体积较大），请运行：
```bash
python build_nuitka.py
```
这会使用 Nuitka 将整个程序编译为独立的二进制文件，产物在 `dist/main.dist` 目录下。

## 📁 目录结构

```text
SuzuEmojy/
├── launcher.py            # 轻量级环境初始化启动器
├── main.py                # 主程序入口点
├── build.bat              # 启动器打包脚本 (PyInstaller)
├── build_nuitka.py        # 完整程序编译脚本 (Nuitka)
├── requirements.txt       # 依赖列表
├── hi.ico                 # 程序图标
├── fluent_ui/             # 现代化界面核心代码
│   ├── main_window.py     # 主窗口容器
│   ├── components/        # UI 组件 (表情卡片、悬停预览等)
│   └── views/             # 主要视图 (图库页、设置页)
└── services/              # 核心业务逻辑
    ├── clipboard.py       # 剪贴板监听与操作系统API
    ├── config.py          # 设置项存储
    └── storage.py         # 图片物理存储与JSON元数据管理
```

## 🤝 贡献与反馈

非常欢迎提交 Pull Request 或者在 Issues 中反馈你遇到的问题和想要的特性！

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源，请随意使用和修改。






在最后

其实 `Suzu` 是我的好孩子,也是这次的"饺子醋"

这个小软件，反而是"饺子"

如果你不喜欢这套内置表情包,想要删掉的话……就随你便吧吧（ 哭哭 ）。

最后在此感谢vscode,让我也能弄出这么个小软件
