import os
import subprocess
import sys
import shutil


def find_csc():
    """查找 Windows 自带的 C# 编译器。"""
    candidates = [
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Microsoft.NET", "Framework64", "v4.0.30319", "csc.exe"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Microsoft.NET", "Framework", "v4.0.30319", "csc.exe"),
    ]

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    return shutil.which("csc.exe")


def build_launcher_stub(release_dir):
    """编译带项目图标的极轻量 Windows GUI 启动器。"""
    csc_path = find_csc()
    if not csc_path:
        raise RuntimeError(
            "找不到 csc.exe，无法生成带图标的轻量启动器。"
            "请安装 .NET Framework 4.x Developer Pack 或 Visual Studio Build Tools。"
        )

    source_path = os.path.abspath("launcher_stub.cs")
    icon_path = os.path.abspath("ico.ico")
    output_path = os.path.abspath(os.path.join(release_dir, "SuzuEmojy.exe"))

    if not os.path.isfile(source_path):
        raise FileNotFoundError(f"找不到启动器源码: {source_path}")
    if not os.path.isfile(icon_path):
        raise FileNotFoundError(f"找不到图标文件: {icon_path}")

    compile_cmd = [
        csc_path,
        "/nologo",
        "/target:winexe",
        "/platform:x64",
        f"/out:{output_path}",
        f"/win32icon:{icon_path}",
        source_path,
        "/reference:System.dll",
        "/reference:System.Windows.Forms.dll",
    ]
    print("Compiling lightweight launcher:", " ".join(compile_cmd))
    result = subprocess.run(compile_cmd, capture_output=True, text=True)

    if result.returncode != 0:
        details = (result.stdout + "\n" + result.stderr).strip()
        raise RuntimeError(f"轻量启动器编译失败:\n{details}")

    if not os.path.isfile(output_path):
        raise RuntimeError(f"启动器编译完成但未找到输出文件: {output_path}")

    return output_path


def main():
    print("====================================")
    print("Building SuzuEmojy with Nuitka")
    print("====================================")

    # 清理 Nuitka 上一次的 standalone 产物，避免旧依赖残留到新发布包。
    for path in ("dist/main.dist", "dist/main.build"):
        if os.path.exists(path):
            print(f"Removing stale build output: {path}")
            shutil.rmtree(path)

    # Nuitka command
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--windows-disable-console",
        "--enable-plugin=pyside6",
        "--windows-icon-from-ico=ico.ico",
        "--include-data-file=ico.ico=ico.ico",
        "--output-dir=dist",
        "--output-filename=SuzuEmojy.exe",
        "--assume-yes-for-downloads",
        "--include-package=qfluentwidgets",
        # services/fluent_ui 均通过入口和页面的显式导入自动跟踪，
        # 不再强制递归包含，避免把未使用模块带入发布包。
        "--include-package=certifi",
        "--include-package-data=certifi",
        "--nofollow-import-to=imageio_ffmpeg",
        "--nofollow-import-to=cryptography",
        "--nofollow-import-to=OpenSSL",
        "--nofollow-import-to=PySide6.QtMultimedia",
        "--nofollow-import-to=PySide6.QtMultimediaWidgets",
        "--nofollow-import-to=PySide6.QtPdf",
        "--nofollow-import-to=PySide6.QtPdfWidgets",
        
        # imageio-ffmpeg 只用于定位 FFmpeg；发布包仅携带实际需要的单个可执行文件。
        "--include-data-file=" + os.path.join(
            os.path.dirname(__import__("imageio_ffmpeg").get_ffmpeg_exe()),
            "ffmpeg-win-x86_64-v7.1.exe",
        ) + "=ffmpeg/ffmpeg.exe",

        # ---- 体积优化：禁止无用的 OpenCV/NumPy 整套依赖进入发布包 ----
        "--nofollow-import-to=cv2",
        "--nofollow-import-to=numpy",
        "--nofollow-import-to=scipy",
        "--nofollow-import-to=matplotlib",
        "--nofollow-import-to=pandas",
        "--nofollow-import-to=sympy",
        "--nofollow-import-to=numpy.testing",
        "--nofollow-import-to=numpy.f2py",
        "--nofollow-import-to=numpy.distutils",
        "--nofollow-import-to=numpy.array_api",

        # ---- 体积优化：去掉用不到的测试/构建相关模块 ----
        "--noinclude-pytest-mode=nofollow",
        "--noinclude-setuptools-mode=nofollow",
        "--noinclude-unittest-mode=nofollow",

        # ---- 体积优化：PySide6 翻译文件通常几十MB用不上，先去掉 ----
        "--noinclude-qt-translations",

        # ---- 体积优化：去掉断言和docstring（编译期生效，收益不大但无副作用）----
        "--python-flag=no_asserts",
        "--python-flag=no_docstrings",

        # ---- 编译报告：用来核对每个模块实际占用的体积，别再靠猜 ----
        "--report=dist/compilation-report.xml",

        "main.py"
    ]

    print("Running command:", " ".join(cmd))
    print("This may take a while, please wait...")
    
    # Run the build process
    env = os.environ.copy()
    # Clear existing CFLAGS/CCFLAGS to avoid conflicts
    for key in ["CFLAGS", "CCFLAGS", "CXXFLAGS", "LDFLAGS", "CPPFLAGS"]:
        if key in env:
            del env[key]
            
    # Force x86_64_v3 architecture to enable AVX2 but avoid AVX-512 instructions
    # x86_64_v3 includes AVX, AVX2, BMI1, BMI2, F16C, FMA, LZCNT, MOVBE, XSAVE
    # Note: Zig compiler requires underscores (x86_64_v3) instead of hyphens
    env["CFLAGS"] = "-march=x86_64_v3"
    env["CCFLAGS"] = "-march=x86_64_v3"

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    
    for line in process.stdout:
        print(line, end="")
        
    process.wait()
    
    if process.returncode == 0:
        print("\n====================================")
        print("Nuitka Build Complete! Now preparing clean release folder...")
        
        release_dir = "dist/SuzuEmojy_Release"
        bin_dir = os.path.join(release_dir, "bin")
        
        # 1. 清理旧的 release 文件夹
        if os.path.exists(release_dir):
            shutil.rmtree(release_dir)
            
        os.makedirs(release_dir)
        
        # 2. 将 Nuitka 生成的 dist 移动为 bin 目录
        shutil.move("dist/main.dist", bin_dir)

        # 项目不使用 PDF：qpdf 图片插件是 qt6pdf.dll 的唯一引入源。
        # 仅移除 PDF 相关文件，保留其它 Qt 图片格式插件。
        for relative_path in (
            os.path.join("PySide6", "qt-plugins", "imageformats", "qpdf.dll"),
            "qt6pdf.dll",
        ):
            optional_file = os.path.join(bin_dir, relative_path)
            if os.path.isfile(optional_file):
                print(f"Removing unused Qt PDF component: {optional_file}")
                os.remove(optional_file)
        
        # 3. 复制文档、图标和数据
        files_to_copy = ["README.md", "说明书.md", "ico.ico"]
        for f in files_to_copy:
            if os.path.exists(f):
                shutil.copy2(f, release_dir)

        if os.path.exists("data"):
            shutil.copytree("data", os.path.join(bin_dir, "data"))

        if os.path.exists("translations"):
            shutil.copytree("translations", os.path.join(bin_dir, "translations"))

        # 4. 生成几 KB 的 Windows GUI 启动器。
        # 启动器只负责调用 bin 内的 Nuitka 核心程序，不携带 Python runtime。
        launcher_path = build_launcher_stub(release_dir)
        print(f"Created lightweight launcher: {launcher_path}")

        # 清理可能遗留的旧版 PyInstaller 外壳及临时文件。
        for stale_path in (
            "dist/SuzuEmojy.exe",
            "mini_launcher.py",
            "SuzuEmojy.spec",
        ):
            if os.path.isfile(stale_path):
                os.remove(stale_path)
        if os.path.isdir("build"):
            shutil.rmtree("build")
        
        print("\n====================================")
        print("Running AVX-512 verification...")
        print("====================================")
        
        # 运行 AVX-512 检测脚本
        check_script = "check_avx512.py"
        if os.path.exists(check_script):
            check_cmd = [sys.executable, check_script, os.path.join(bin_dir, "SuzuEmojy.exe")]
            check_process = subprocess.run(check_cmd)
            if check_process.returncode != 0:
                print("\n====================================")
                print("BUILD FAILED: AVX-512 instructions detected in the binary!")
                print("====================================")
                sys.exit(1)
        
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
