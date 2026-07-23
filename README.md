🥟 SuzuEmojy

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

<img src="https://github.com/IxinorTyan/SuzuEmojy/blob/main/assets/%E5%BF%AB%E6%8D%B7%E6%A1%86.gif" width="80%" alt="快捷框">

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

---

## 🤝 贡献与反馈

非常欢迎提交 Pull Request 或者在 Issues 中反馈你遇到的问题和想要的特性！

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源，请随意使用和修改。


