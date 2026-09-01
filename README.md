# 🥟 SuzuEmojy

[简体中文](#-suzuemojy-cn) | [English](#-suzuemojy-en)

---

<span id="-suzuemojy-cn"></span>

## 🥟 SuzuEmojy (简体中文)

一个专注于 Windows 的本地表情包管理工具。

快速搜索、分类整理、一键发送，让收藏多年的表情包真正用起来。

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E5%A4%9C%E9%97%B4ui.png" width="50%" alt="日间ui">
<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E6%97%A5%E9%97%B4ui.png" width="50%" alt="夜间ui">

为什么会有 SuzuEmojy？

(首先是因为开发者常年使用多个账号,但是账号之间的表情包是不互通的，用起来非常不顺手．于是就会经常的在电脑里存一堆表情包)
聊天软件里的表情包越来越多。
几百张、几千张图片散落在各个文件夹里。
每次想找一张图，都要翻半天。
浏览器里的图片不好保存。
下载下来的图片又经常不能直接发送。
于是就有了这个软件。

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/QQ%E6%8B%96%E6%8B%BD.gif" width="80%" alt="快速添加">

SuzuEmojy 希望把这些麻烦全部解决。

功能

\\全局快捷键呼出\\

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E5%BF%AB%E6%8D%B7%E9%94%AE.gif" width="80%" alt="快捷键">

无论当前正在使用什么软件。
按下快捷键即(默认ctrl+shift+e,可更改)可立即打开表情面板。
选择图片后自动粘贴到原来的聊天窗口(没粘上的话可能是光标失焦,不过这个时候表情包依旧复制在剪切板了)。

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E5%BF%AB%E6%8D%B7%E7%AA%97%E5%8F%A3.gif" width="80%" alt="快捷框">

快捷窗口.按下快捷键(默认ctrl+shift+d,可更改)快速唤出
在快捷窗口可以搜索到(仅)自己打过关键词的表情包
按顺序排列最近使用的表情包(默认30,可自定义1-999)

\\快捷导入\\

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E5%AF%BC%E5%85%A5.gif" width="80%" alt="导入">

即使导入上千张图片。界面依然保持流畅。无需等待全部加载完成。

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E5%A4%B9.gif" width="80%" alt="文件夹">

支持直接导入分类好的文件夹,会自动创建同名分类夹

\\搜索比翻文件夹快得多\\

(虽然要提前打好tag)

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E6%90%9C%E7%B4%A2.gif" width="80%" alt="搜索">

支持自定义关键词,实时过滤

真正做到想找什么立刻找到。

\\自由整理\\

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E6%8B%96%E5%8A%A8%E6%95%B4%E7%90%86.gif" width="80%" alt="拖拽整理">

支持：

拖拽排序

跨分类移动

批量操作

\\图片一键收藏\\

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E6%89%92%E5%9B%BE.gif" width="80%" alt="扒图">

复制粘贴导入(这里因为b站评论区动图只有点开才会加载,所以必须点开才能保存)

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E5%A4%8D%E5%88%B6url.gif" width="80%" alt="url">

通过复制url尝试下载图片(不过不是所有url都能下载)

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/telegram.gif" width="80%" alt="T">

另存为到(\bin)\data\inbox文件夹实现转换webm(导入,所有另存为都可以这样用)

很多网页图片(webp,webm)直接拖到聊天软件会变成文件。

SuzuEmojy 可以自动重新编码。使其真正作为图片保存。

\\更多细节\\

支持：

Fluent Design 风格界面

Ctrl + 鼠标滚轮缩放

高清悬停预览

网格 / 列表双布局

JSON 数据存储

数据迁移

\\下载\\

前往 Release(https://github.com/IxinorTyan/SuzuEmojy/releases) 下载最新版。

解压即可运行。

运行软件会自动检测依赖安装(安装不上可以执行同文件夹旁边的小脚本)

所有数据都保存在程序目录。

换电脑时复制 data 文件夹即可。

\\常用快捷键:\\

Ctrl + Shift + E(可更改)-----呼出主页面

Ctrl + 滚轮-----缩放缩略图

双击 "全部表情"-----在侧边栏的“列表模式”与“网格模式”之间切换

等等。

\\关于默认表情包\\

软件默认内置了一套 Suzu 表情包。

只是为了让第一次打开软件时就可以直接体验。

如果你更喜欢自己的表情包。

完全可以删除它们(真的要吗QAQ)。

希望有一天，你也会喜欢她。

------------------------------------------------------

💻 开发者指南

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

---

## 📁 目录结构

```text
SuzuEmojy/
├── launcher.py            # 轻量级环境初始化启动器
├── main.py                # 主程序入口点
├── build.bat              # 启动器打包脚本 (PyInstaller)
├── build_nuitka.py        # 完整程序编译脚本 (Nuitka)
├── requirements.txt       # 依赖列表
├── ico.ico                # 程序图标
├── fluent_ui/             # 现代化界面核心代码
│   ├── main_window.py     # 主窗口容器
│   ├── components/        # UI 组件 (表情卡片、悬停预览等)
│   └── views/             # 主要视图 (图库页、设置页)
└── services/              # 核心业务逻辑
    ├── clipboard.py       # 剪贴板监听与操作系统API
    ├── config.py          # 设置项存储
    └── storage.py         # 图片物理存储与JSON元数据管理
```

---

## 🤝 贡献与反馈

非常欢迎提交 Pull Request 或者在 Issues 中反馈你遇到的问题和想要的特性！

## 🙏 致谢与鸣谢

本项目在开发过程中，参考与借鉴了以下优秀开源项目的思路与技术实现，在此向原作者们致以衷心的感谢：

- **[QQFavoriteExtract](https://github.com/VanillaNahida/QQFavoriteExtract)** (by [VanillaNahida](https://github.com/VanillaNahida))：为本项目的 QQNT 本地表情扫描、数据解析与提取导出功能提供了重要的实现思路与参考。
- **[tg_sticker_downloader](https://github.com/Kiowx/tg_sticker_downloader)** (by [Kiowx](https://github.com/Kiowx))：为本项目的 Telegram 贴纸包解析、下载与转码处理功能提供了宝贵的技术借鉴。

### ⚠️ 合规与正确使用声明
1. **仅供个人学习与备份**：本项目提供的 QQ 表情扫描与 Telegram 贴纸下载等功能，仅用于用户对自己合法拥有或授权的表情数据进行本地备份、整理与学习交流。
2. **遵守相关法规与平台规范**：请在遵守相关法律法规及对应平台服务条款的前提下使用本软件。
3. **尊重原创版权**：表情包与贴纸资源的著作权及知识产权归原作者所有，请勿将获取的资源用于任何未经授权的商业用途或侵权传播。用户需自行承担因不当或违规使用而产生的法律责任。

## 📄 许可证

本项目基于 [GNU General Public License v3.0 (GPL-3.0)](LICENSE) 协议开源。

---

<span id="-suzuemojy-en"></span>

## 🥟 SuzuEmojy (English)

[Back to Chinese version (返回中文版)](#-suzuemojy-cn)

A local meme and emoji sticker manager focused on Windows.

Fast search, categorized organization, and one-click sending—making your meme collection of years truly useful.

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E5%A4%9C%E9%97%B4ui.png" width="50%" alt="Day UI">
<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E6%97%A5%E9%97%B4ui.png" width="50%" alt="Night UI">

Why SuzuEmojy?

(First of all, because the developer uses multiple accounts all year round, but stickers cannot be shared across accounts, which is very inconvenient. As a result, a large number of meme pictures are often saved on the PC.)
Stickers in chat applications are increasing.
Hundreds or thousands of pictures are scattered across various folders.
Every time you want to find a picture, you have to spend a long time searching.
Images on browsers are difficult to save directly.
Downloaded images often cannot be sent as stickers directly.
So this software was created.

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/QQ%E6%8B%96%E6%8B%BD.gif" width="80%" alt="Quick Add">

SuzuEmojy aims to solve all these troubles.

Features

\\Global Hotkey Callout\\

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E5%BF%AB%E6%8D%B7%E9%94%AE.gif" width="80%" alt="Hotkey">

No matter what software you are currently using.
Press the hotkey (default: Ctrl+Shift+E, customizable) to immediately open the emoji panel.
After selecting an image, it is automatically pasted into your previous chat window (if it fails to paste, it may be due to losing focus, but the sticker is still copied to your clipboard).

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E5%BF%AB%E6%8D%B7%E7%AA%97%E5%8F%A3.gif" width="80%" alt="Quick Box">

Quick Panel: quickly summon with a hotkey (default: Alt+2 / customizable).
In the quick window, you can search for emojis that you have added keywords to (only).
Arranges recently used emojis in order (default: 30, customizable: 1-999).

\\Fast Import\\

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E5%AF%BC%E5%85%A5.gif" width="80%" alt="Import">

Even when importing thousands of images, the interface remains smooth without waiting for all images to finish loading.

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E5%A4%B9.gif" width="80%" alt="Folder">

Supports directly importing categorized folders, automatically creating category folders with the same names.

\\Search is Much Faster Than Browsing Folders\\

(Although tags need to be set in advance)

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E6%90%9C%E7%B4%A2.gif" width="80%" alt="Search">

Supports custom keywords and real-time filtering.

Truly find whatever you want right away.

\\Free Organization\\

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E6%8B%96%E5%8A%A8%E6%95%B4%E7%90%86.gif" width="80%" alt="Drag & Drop Organization">

Supports:

Drag-and-drop reordering

Cross-category moving

Batch operations

\\One-Click Image Capture / Import\\

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E6%89%92%E5%9B%BE.gif" width="80%" alt="Image Scraper">

Import via copy & paste (Note: animated images in Bilibili comments only load when clicked, so they must be expanded before saving).

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E5%A4%8D%E5%88%B6url.gif" width="80%" alt="URL">

Try downloading images by copying the URL (note: not all URLs support direct download).

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/telegram.gif" width="80%" alt="Telegram">

Save directly to the `(\bin)\data\inbox` folder to convert WebM / import (all "Save As" can be used this way).

Many web images (WebP, WebM) will turn into files if dragged directly into chat apps.

SuzuEmojy can automatically re-encode them so they are properly saved as stickers/images.

\\More Details\\

Supports:

Fluent Design UI

Ctrl + Mouse Wheel Zoom

HD Hover Preview

Grid / List Dual Layout

SQLite + JSON Data Storage

Data Migration

\\Download\\

Go to [Releases](https://github.com/IxinorTyan/SuzuEmojy/releases) to download the latest version.

Extract and run.

Running the software will automatically detect and install dependencies (if installation fails, you can run the helper script in the folder).

All data is stored in the application directory.

When switching PCs, simply copy the `data` folder.

\\Common Shortcuts:\\

Ctrl + Shift + E (customizable) ----- Summon main window

Ctrl + Wheel ----- Zoom thumbnails

Double click "All Emojis" ----- Switch between "List Mode" and "Grid Mode" in the sidebar

And more.

\\About Default Emoji Pack\\

The software comes with a built-in set of Suzu emojis by default.

Just so you can experience it right away the first time you open the app.

If you prefer your own emoji collection,

you can delete them completely (do you really want to QAQ).

Hope that one day, you will like her too.

------------------------------------------------------

💻 Developer Guide

If you want to run from source code or do secondary development:

### Requirements
- Python 3.9+
- Windows 10/11 (Windows 11 recommended for the best Mica effect experience)

### Install Dependencies
```bash
git clone https://github.com/IxinorTyan/SuzuEmojy.git
cd SuzuEmojy
pip install -r requirements.txt
```

### Run Application
```bash
python main.py
```

### Build as EXE
This project provides two packaging methods:

**Method 1: Lightweight Launcher (Recommended)**
Double-click and run `build.bat`. This uses PyInstaller to package `launcher.py` into a tiny single-file EXE (~10MB).
When users launch this EXE for the first time, it automatically detects the system environment and downloads the required Python runtime and GUI libraries (such as PySide6).

**Method 2: Fully Standalone Packaging**
If you want to package a complete offline standalone bundle with all dependencies (larger file size), run:
```bash
python build_nuitka.py
```
This uses Nuitka to compile the entire program into standalone binary files under the `dist/main.dist` directory.

---

## 📁 Project Structure

```text
SuzuEmojy/
├── launcher.py            # Lightweight environment initialization launcher
├── main.py                # Main application entry point
├── build.bat              # Launcher build script (PyInstaller)
├── build_nuitka.py        # Full standalone build script (Nuitka)
├── requirements.txt       # Dependencies list
├── ico.ico                # Application icon
├── fluent_ui/             # Modern UI core code
│   ├── main_window.py     # Main window container
│   ├── components/        # UI components (emoji cards, hover preview, etc.)
│   └── views/             # Main views (gallery view, settings view)
└── services/              # Core business logic
    ├── clipboard.py       # Clipboard listener and OS APIs
    ├── config.py          # Configuration storage
    └── storage.py         # Image storage and metadata management
```

---

## 🤝 Contribution & Feedback

Pull Requests and Issues reporting bugs or requesting new features are very welcome!

## 🙏 Acknowledgments

During the development of this project, we referenced and learned from the ideas and implementations of the following excellent open-source projects. We express our sincere gratitude to the original authors:

- **[QQFavoriteExtract](https://github.com/VanillaNahida/QQFavoriteExtract)** (by [VanillaNahida](https://github.com/VanillaNahida)): Provided great ideas and reference for local QQNT emoji scanning, parsing, and extraction features.
- **[tg_sticker_downloader](https://github.com/Kiowx/tg_sticker_downloader)** (by [Kiowx](https://github.com/Kiowx)): Provided valuable technical inspiration and references for Telegram sticker pack downloading and conversion workflows.

### ⚠️ Fair Use & Compliance Statement
1. **For Personal Backup & Study Only**: The emoji scanning and sticker downloading features in this project are strictly intended for users to backup, organize, and manage their own legitimately accessed emoji/sticker assets locally for personal use and learning.
2. **Platform & Legal Compliance**: Please use this software in full compliance with applicable laws, regulations, and platform Terms of Service.
3. **Respect Intellectual Property**: The copyright and intellectual property rights of all stickers, memes, and artwork belong to their original creators. Do not use acquired assets for unauthorized commercial purposes or infringing redistribution. Users are solely responsible for any misuse.

## 📄 License

This project is licensed under the [GNU General Public License v3.0 (GPL-3.0)](LICENSE).
