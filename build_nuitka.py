import os
import subprocess
import sys

def main():
    print("====================================")
    print("Building SuzuEmojy with Nuitka")
    print("====================================")

    # Nuitka command
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--windows-disable-console",
        "--enable-plugin=pyside6",
        "--windows-icon-from-ico=hi.ico",
        "--include-data-file=hi.ico=hi.ico",
        "--output-dir=dist",
        "--assume-yes-for-downloads",
        "--include-package=qfluentwidgets",
        "--include-package=services",
        "--include-package=fluent_ui",
        "main.py"
    ]

    print("Running command:", " ".join(cmd))
    print("This may take a while, please wait...")
    
    # Run the build process
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    for line in process.stdout:
        print(line, end="")
        
    process.wait()
    
    if process.returncode == 0:
        print("\n====================================")
        print("Nuitka Build Complete! Now preparing clean release folder...")
        
        import shutil
        
        release_dir = "dist/SuzuEmojy_Release"
        bin_dir = os.path.join(release_dir, "bin")
        
        # 1. 清理旧的 release 文件夹
        if os.path.exists(release_dir):
            shutil.rmtree(release_dir)
            
        os.makedirs(release_dir)
        
        # 2. 将 Nuitka 生成的 dist 移动为 bin 目录
        shutil.move("dist/main.dist", bin_dir)
        
        # 3. 复制文档和数据
        files_to_copy = ["README.md", "说明书.md", "依赖装不上,没招了你就试试点这个吧,记得附上报错日志.bat"]
        for f in files_to_copy:
            if os.path.exists(f):
                shutil.copy2(f, release_dir)
                
        if os.path.exists("data"):
            shutil.copytree("data", os.path.join(release_dir, "data"))
            
        # 4. 创建一个极小的 VBS 启动脚本并转换为 EXE (或者直接提供一个启动脚本)
        # 为了不引入额外的 C++ 编译器依赖，我们写一个极小的 Python 脚本，用 PyInstaller 打包成单文件作为外壳
        launcher_code = """import os
import sys
import subprocess

def main():
    base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    bin_exe = os.path.join(base_dir, "bin", "main.exe")
    
    if os.path.exists(bin_exe):
        subprocess.Popen([bin_exe], cwd=os.path.join(base_dir, "bin"), creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("错误", f"找不到核心程序文件：\\n{bin_exe}\\n\\n请确保 bin 文件夹完整。")

if __name__ == "__main__":
    main()
"""
        with open("mini_launcher.py", "w", encoding="utf-8") as f:
            f.write(launcher_code)
            
        print("Building mini launcher wrapper...")
        subprocess.run([
            sys.executable, "-m", "PyInstaller", 
            "--noconsole", "--onefile", 
            "--icon=hi.ico", 
            "--name=SuzuEmojy", 
            "mini_launcher.py"
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # 移动生成的启动器到 release 目录
        if os.path.exists("dist/SuzuEmojy.exe"):
            shutil.move("dist/SuzuEmojy.exe", os.path.join(release_dir, "SuzuEmojy.exe"))
            
        # 清理临时文件
        if os.path.exists("mini_launcher.py"): os.remove("mini_launcher.py")
        if os.path.exists("SuzuEmojy.spec"): os.remove("SuzuEmojy.spec")
        if os.path.exists("build"): shutil.rmtree("build")
        
        print("\n====================================")
        print("All Done!")
        print(f"Clean release package is ready at: {os.path.abspath(release_dir)}")
        print("====================================")
    else:
        print("\n====================================")
        print("Build failed with return code", process.returncode)
        print("====================================")

if __name__ == "__main__":
    main()
