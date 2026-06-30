import os
import sys
import urllib.request
import zipfile
import subprocess
import threading
import shutil
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox

# 获取当前程序所在的绝对路径，防止工作目录漂移导致找不到文件
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 配置信息
PYTHON_VERSION = "3.11.9"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

RUNTIME_DIR = os.path.join(BASE_DIR, "runtime")
REQUIREMENTS_FILE = os.path.join(BASE_DIR, "requirements.txt")
MAIN_SCRIPT = os.path.join(BASE_DIR, "main.py")

def find_system_python():
    """扫描系统中已安装的 Python"""
    # 1. 检查环境变量 PATH 中的 python
    for cmd in ["python", "python3"]:
        path = shutil.which(cmd)
        if path:
            try:
                result = subprocess.run([path, "-c", "import sys; print(sys.version_info >= (3, 9))"], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
                if result.stdout.strip() == "True":
                    return path
            except:
                pass
    
    # 2. 检查常见安装路径
    local_app_data = os.environ.get('LOCALAPPDATA', '')
    if local_app_data:
        python_dir = os.path.join(local_app_data, 'Programs', 'Python')
        if os.path.exists(python_dir):
            # 倒序遍历，优先找高版本
            for d in sorted(os.listdir(python_dir), reverse=True):
                if d.lower().startswith('python3'):
                    exe_path = os.path.join(python_dir, d, 'python.exe')
                    if os.path.exists(exe_path):
                        try:
                            result = subprocess.run([exe_path, "-c", "import sys; print(sys.version_info >= (3, 9))"], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
                            if result.stdout.strip() == "True":
                                return exe_path
                        except:
                            pass
    return None

def check_dependencies(python_exe):
    """检查依赖是否已安装"""
    try:
        # 简单检查 PySide6 和 qfluentwidgets
        result = subprocess.run(
            [python_exe, "-c", "import PySide6; import qfluentwidgets"], 
            capture_output=True, 
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return result.returncode == 0
    except:
        return False

class LauncherApp:
    def __init__(self, root, python_exe=None):
        self.root = root
        self.system_python = python_exe
        self.root.title("SuzuEmojy - 初始化运行环境")
        self.root.geometry("450x180")
        self.root.resizable(False, False)
        
        # 居中显示
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry('{}x{}+{}+{}'.format(width, height, x, y))

        # 设置图标
        icon_path = os.path.join(BASE_DIR, "hi.ico")
        if os.path.exists(icon_path):
            try:
                self.root.iconbitmap(icon_path)
            except:
                pass

        self.label = tk.Label(root, text="正在准备运行环境，请保持网络畅通...", font=("Microsoft YaHei", 10))
        self.label.pack(pady=20)

        self.progress = ttk.Progressbar(root, orient="horizontal", length=350, mode="determinate")
        self.progress.pack(pady=10)

        self.detail_label = tk.Label(root, text="准备中...", font=("Microsoft YaHei", 8), fg="gray")
        self.detail_label.pack(pady=5)

        self.thread = threading.Thread(target=self.setup_environment)
        self.thread.daemon = True
        self.thread.start()

    def update_status(self, text, detail="", value=None):
        self.label.config(text=text)
        self.detail_label.config(text=detail)
        if value is not None:
            self.progress["value"] = value
        self.root.update()

    def download_file(self, url, dest, desc):
        self.update_status(f"正在下载 {desc}...", "连接服务器中...", 0)
        
        def report(block_num, block_size, total_size):
            if total_size > 0:
                downloaded = block_num * block_size
                percent = int(downloaded * 100 / total_size)
                if percent > 100:
                    percent = 100
                
                mb_downloaded = downloaded / (1024 * 1024)
                mb_total = total_size / (1024 * 1024)
                detail = f"{mb_downloaded:.1f} MB / {mb_total:.1f} MB"
                
                self.progress["value"] = percent
                self.detail_label.config(text=detail)
                self.root.update()

        urllib.request.urlretrieve(url, dest, reporthook=report)

    def setup_environment(self):
        try:
            use_embedded = True
            if self.system_python:
                # 尝试使用系统 Python 安装依赖
                python_exe = self.system_python
                self.update_status("发现系统 Python，正在后台安装图形库依赖...", "这可能需要几分钟，请耐心等待...", 0)
                self.progress.config(mode="indeterminate")
                self.progress.start()
                
                if not os.path.exists(REQUIREMENTS_FILE):
                    raise Exception(f"找不到依赖文件: {REQUIREMENTS_FILE}")
                
                # 默认安装到用户全局环境
                pip_cmd = [
                    python_exe, "-m", "pip", "install", "--user", "-r", REQUIREMENTS_FILE, 
                    "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"
                ]

                result = subprocess.run(pip_cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
                
                # 检查依赖是否真的安装成功了
                if result.returncode == 0 and check_dependencies(python_exe):
                    use_embedded = False
                else:
                    # 记录错误日志
                    error_log = f"System Python pip install failed.\nReturn code: {result.returncode}\nStdout:\n{result.stdout}\nStderr:\n{result.stderr}"
                    try:
                        with open(os.path.join(BASE_DIR, "install_error.log"), "a", encoding="utf-8") as f:
                            f.write(error_log + "\n\n")
                    except:
                        pass
                        
                    # 安装失败，准备回退
                    self.progress.stop()
                    self.progress.config(mode="determinate")
                    self.update_status("系统 Python 配置失败，准备下载独立环境...", "正在回退...", 0)
                    
            if use_embedded:
                # 下载嵌入式环境
                python_exe = os.path.join(RUNTIME_DIR, "python.exe")
                
                if not os.path.exists(python_exe):
                    if not os.path.exists(RUNTIME_DIR):
                        os.makedirs(RUNTIME_DIR)
                    
                    # 1. 下载 Python 嵌入式包
                    zip_path = os.path.join(RUNTIME_DIR, "python.zip")
                    self.download_file(PYTHON_URL, zip_path, "Python 核心组件")
                    
                    # 2. 解压
                    self.update_status("正在解压运行环境...", "请稍候...", 0)
                    self.progress.config(mode="indeterminate")
                    self.progress.start()
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        zip_ref.extractall(RUNTIME_DIR)
                    os.remove(zip_path)
                    self.progress.stop()
                    self.progress.config(mode="determinate")
                    
                    # 3. 修改 _pth 文件以启用 site-packages
                    pth_file = os.path.join(RUNTIME_DIR, "python311._pth")
                    if os.path.exists(pth_file):
                        with open(pth_file, 'r') as f:
                            lines = f.readlines()
                        with open(pth_file, 'w') as f:
                            for line in lines:
                                if line.strip() == "#import site":
                                    f.write("import site\n")
                                else:
                                    f.write(line)
                    
                    # 4. 下载 get-pip.py
                    get_pip_path = os.path.join(RUNTIME_DIR, "get-pip.py")
                    self.download_file(GET_PIP_URL, get_pip_path, "包管理器 (pip)")
                    
                    # 5. 安装 pip
                    self.update_status("正在安装包管理器...", "后台配置 pip，请稍候...", 0)
                    self.progress.config(mode="indeterminate")
                    self.progress.start()
                    
                    result = subprocess.run(
                        [python_exe, get_pip_path], 
                        capture_output=True, text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                    
                    if result.returncode != 0 or not os.path.exists(os.path.join(RUNTIME_DIR, "Scripts", "pip.exe")):
                        error_log = f"Get-pip failed.\nReturn code: {result.returncode}\nStdout:\n{result.stdout}\nStderr:\n{result.stderr}"
                        raise Exception(f"Pip 安装失败。\n{error_log}")
                    
                    try:
                        os.remove(get_pip_path)
                    except:
                        pass
                    self.progress.stop()
                    self.progress.config(mode="determinate")

                # 6. 安装 requirements.txt 中的依赖
                self.update_status("正在后台下载并安装图形库依赖...", "这可能需要几分钟，请耐心等待...", 0)
                self.progress.config(mode="indeterminate")
                self.progress.start()
                
                if not os.path.exists(REQUIREMENTS_FILE):
                    raise Exception(f"找不到依赖文件: {REQUIREMENTS_FILE}")
                
                pip_cmd = [
                    python_exe, "-m", "pip", "install", "-r", REQUIREMENTS_FILE, 
                    "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"
                ]
                result = subprocess.run(pip_cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
                
                if result.returncode != 0 or not check_dependencies(python_exe):
                    error_log = f"Embedded Python pip install failed.\nReturn code: {result.returncode}\nStdout:\n{result.stdout}\nStderr:\n{result.stderr}"
                    raise Exception(f"依赖安装失败。\n{error_log}")

            self.progress.stop()
            self.update_status("环境准备完毕！", "正在启动主程序...", 100)
            
            # 7. 启动主程序
            self.root.destroy()
            subprocess.Popen([python_exe, MAIN_SCRIPT], cwd=BASE_DIR, creationflags=subprocess.CREATE_NO_WINDOW)
            
        except Exception as e:
            self.progress.stop()
            self.progress.config(mode="determinate")
            error_msg = str(e)
            
            # 将完整错误写入日志文件
            log_file = os.path.join(BASE_DIR, "install_error.log")
            try:
                with open(log_file, "w", encoding="utf-8") as f:
                    f.write(error_msg)
            except:
                pass
                
            if len(error_msg) > 200:
                error_msg = error_msg[:200] + "...\n(完整错误已保存至 install_error.log)"
                
            self.update_status("初始化失败", f"错误: {error_msg}", 0)
            messagebox.showerror("初始化失败", f"在配置环境时发生错误：\n\n{error_msg}\n\n请检查网络连接或重试。")

def main():
    # 1. 优先检查本地独立环境
    local_python = os.path.join(RUNTIME_DIR, "python.exe")
    if os.path.exists(local_python) and check_dependencies(local_python):
        subprocess.Popen([local_python, MAIN_SCRIPT], cwd=BASE_DIR, creationflags=subprocess.CREATE_NO_WINDOW)
        return

    # 2. 扫描系统 Python
    system_python = find_system_python()
    if system_python and check_dependencies(system_python):
        subprocess.Popen([system_python, MAIN_SCRIPT], cwd=BASE_DIR, creationflags=subprocess.CREATE_NO_WINDOW)
        return

    # 如果环境未就绪，先弹出确认框
    root = tk.Tk()
    root.withdraw() # 隐藏主窗口
    
    if system_python:
        msg = (
            "检测到您的系统已安装 Python。\n\n"
            "首次运行需要下载并安装图形库依赖（如 PySide6）。\n"
            "依赖将安装到您的当前用户环境中。\n\n"
            "是否立即开始下载？"
        )
    else:
        msg = (
            "检测到您的系统未安装 Python。\n\n"
            "首次运行需要下载独立的运行环境及图形库（约 200MB）。\n"
            "所有文件将仅安装在当前文件夹中，绝对不会污染您的系统环境。\n\n"
            "是否立即开始下载？"
        )
    
    if messagebox.askyesno("SuzuEmojy - 环境初始化", msg):
        root.deiconify() # 恢复显示主窗口
        app = LauncherApp(root, python_exe=system_python)
        root.mainloop()
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()