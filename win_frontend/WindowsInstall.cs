using System;
using System.IO;

namespace SuzuEmojy.Windows;

internal sealed record WindowsInstall(string Backend, string Data)
{
    private const string PackageMarker = "windows-preview.json";

    internal static WindowsInstall Locate(string applicationDirectory)
    {
        var directory = Path.GetFullPath(applicationDirectory);
        var packagedBackend = Path.Combine(directory, "backend");
        if (IsPackaged(packagedBackend))
        {
            if (!HasBackend(packagedBackend))
                throw new InvalidOperationException("Windows 封包不完整，请重新解压完整 ZIP。");
            PythonExecutable(packagedBackend);
            return new(packagedBackend, Path.Combine(directory, "data"));
        }
        for (var parent = new DirectoryInfo(directory); parent is not null; parent = parent.Parent)
            if (HasBackend(parent.FullName))
                return new(parent.FullName, Path.Combine(parent.FullName, "data"));
        throw new InvalidOperationException("找不到 SuzuEmojy 后端。请从完整 ZIP 解压后运行，或在源码目录中运行开发版。");
    }

    internal static string PythonExecutable(string backend)
    {
        if (!IsPackaged(backend))
            return Environment.GetEnvironmentVariable("SUZU_PYTHON") ?? "python.exe";
        var python = Path.Combine(backend, "python", "python.exe");
        if (!File.Exists(python))
            throw new InvalidOperationException("Windows 封包缺少 Python 运行库，请重新解压完整 ZIP。");
        return python;
    }

    private static bool IsPackaged(string backend) => File.Exists(Path.Combine(backend, PackageMarker));
    private static bool HasBackend(string backend) => File.Exists(Path.Combine(backend, "services", "gallery_snapshot.py"));
}
