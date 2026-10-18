import argparse
import ctypes
import os
import subprocess
import sys
import shutil
from pathlib import Path
from time import perf_counter


ROOT = Path(__file__).resolve().parent


def memory_capacity():
    """Physical RAM for a conservative compiler concurrency default."""
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in (
                "total", "available", "page_total", "page_available",
                "virtual_total", "virtual_available", "extended_available",
            )
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return status.total
    return None


def default_jobs():
    # Keep RAM for Windows, Python analysis and the linker. This is a heuristic,
    # not a compiler memory limit; --jobs allows tuning for a particular machine.
    ram = memory_capacity()
    ram_jobs = max(1, (ram - 4 * 1024 ** 3) // (2 * 1024 ** 3)) if ram else 4
    return min(os.cpu_count() or 1, ram_jobs)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Build the standalone SuzuEmojy release without changing size optimizations.")
    parser.add_argument("--jobs", type=int, default=default_jobs(), help="C compiler processes (default: bounded by CPU count and RAM)")
    parser.add_argument("--clean", action="store_true", help="Recreate project intermediates and rebuild the updater; keep global compiler caches")
    parser.add_argument("--dry-run", action="store_true", help="Print the build plan without changing files or compiling")
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    return args


def prepare_build_directory(clean=False):
    # Never mix previous standalone dependencies into a new release. Nuitka
    # itself regenerates C/objects; retaining .build alone is not incremental C.
    paths = [ROOT / "dist" / "main.dist"]
    if clean:
        paths.append(ROOT / "dist" / "main.build")
    for path in paths:
        if path.exists():
            if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(ROOT):
                raise ValueError(f"Refusing to clean linked build directory: {path}")
            print(f"Removing stale build output: {path}")
            shutil.rmtree(path)


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


def nuitka_command(jobs, ffmpeg_exe):
    return [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--windows-disable-console",
        "--enable-plugin=pyside6",
        "--windows-icon-from-ico=ico.ico",
        "--include-data-file=ico.ico=ico.ico",
        "--output-dir=dist",
        "--output-filename=SuzuEmojy.exe",
        "--assume-yes-for-downloads",
        f"--jobs={jobs}",
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
        "--include-data-file=" + ffmpeg_exe + "=ffmpeg/ffmpeg.exe",

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


def build_environment():
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
    return env


def main(argv=None):
    args = parse_args(argv)
    os.chdir(ROOT)
    import imageio_ffmpeg

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = nuitka_command(args.jobs, ffmpeg_exe)
    print(f"Compiler processes: {args.jobs} (override with --jobs N)")
    print("Compiler cache settings: preserved; updater: incremental; ZIP: DEFLATE level 6")
    print("Running command:", subprocess.list2cmdline(cmd))
    if args.dry_run:
        print(f"Dry run only. Clean project intermediates: {args.clean}")
        return

    # Fail before an expensive compile if release prerequisites are missing.
    subprocess.run([sys.executable, "-m", "PyInstaller", "--version"], check=True)
    if not find_csc():
        raise RuntimeError("找不到 csc.exe，无法生成发布包启动器。请安装 .NET Framework 4.x Developer Pack。")
    for required in ("launcher_stub.cs", "ico.ico", ffmpeg_exe):
        if not Path(required).is_file():
            raise FileNotFoundError(required)
    prepare_build_directory(args.clean)
    started = perf_counter()
    print("Building SuzuEmojy with Nuitka...")

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=build_environment())

    for line in process.stdout:
        print(line, end="")

    process.wait()
    print(f"\nNuitka compilation: {perf_counter() - started:.1f}s")

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

        # 创建干净的数据目录结构，不打包本地用户数据（数据库、表情图片、缓存、个人配置等）
        data_dir = os.path.join(bin_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(os.path.join(data_dir, "inbox"), exist_ok=True)

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
        from scripts.package_release import package_release
        package_release(release_dir, "standalone", clean=args.clean)
        print(f"Total build and packaging: {perf_counter() - started:.1f}s")
        print("All Done!")
        print(f"Clean release package is ready at: {os.path.abspath(release_dir)}")
        print("====================================")
    else:
        print("\n====================================")
        print("Build failed with return code", process.returncode)
        print("====================================")
        sys.exit(process.returncode or 1)

if __name__ == "__main__":
    main()
