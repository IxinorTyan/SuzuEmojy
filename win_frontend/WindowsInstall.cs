using System;
using System.IO;
using System.Linq;
using System.Text.Json;

namespace SuzuEmojy.Windows;

internal sealed record WindowsInstall(string Root, string Data, string? Launcher = null)
{
    internal const string PackageMarker = PortableLayout.Marker;
    private const string ProgramPrefix = PortableLayout.ProgramDirectory + "/";
    internal static readonly string[] ProgramFiles = [PortableLayout.Application, PackageMarker,
        "suzu-webp.dll", "suzu-legacy-palette.dll", "rlottie.dll", "ffmpeg/ffmpeg.exe"];
    internal static readonly string[] PackageFiles = [PortableLayout.Launcher,
        ProgramPrefix + PortableLayout.Application + ".config", .. ProgramFiles.Select(name => ProgramPrefix + name)];

    internal static bool IsUnexpectedPackageEntry(string entry) =>
        !entry.Equals(PortableLayout.Launcher, StringComparison.Ordinal) && !entry.StartsWith(ProgramPrefix, StringComparison.Ordinal) ||
        entry.TrimEnd('/').Split('/').Any(part =>
        part is "" or "." or ".." || part.Equals("backend", StringComparison.OrdinalIgnoreCase) ||
        part.Equals("data", StringComparison.OrdinalIgnoreCase) || part.IndexOf(':') >= 0 ||
        part.StartsWith("python", StringComparison.OrdinalIgnoreCase) ||
        part.StartsWith("qt6", StringComparison.OrdinalIgnoreCase) ||
        part.StartsWith("pyside", StringComparison.OrdinalIgnoreCase) ||
        part.StartsWith("pyqt", StringComparison.OrdinalIgnoreCase) ||
        Path.GetExtension(part).ToLowerInvariant() is ".py" or ".pyw" or ".pyc" or ".pyd" or ".whl");

    internal static WindowsInstall Locate(string applicationDirectory)
    {
        var directory = Path.GetFullPath(applicationDirectory);
        var marker = Path.Combine(directory, PackageMarker);
        if (File.Exists(marker))
        {
            if (ProgramFiles.Any(name => !File.Exists(Path.Combine(directory, name))))
                throw new InvalidOperationException("Windows 封包不完整，请重新解压完整 ZIP。");
            using var package = JsonDocument.Parse(File.ReadAllText(marker));
            if (package.RootElement.TryGetProperty("layout", out var layout))
            {
                var bin = new DirectoryInfo(directory);
                var outer = bin.Parent;
                if (layout.GetString() != PortableLayout.LayoutId || outer is null ||
                    !bin.Name.Equals(PortableLayout.ProgramDirectory, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Windows 封包目录不正确，请重新解压完整 ZIP。");
                var launcher = Path.Combine(outer.FullName, PortableLayout.Launcher);
                if (!File.Exists(launcher)) throw new InvalidOperationException("Windows 封包缺少启动程序，请重新解压完整 ZIP。");
                return new(directory, Path.Combine(outer.FullName, PortableLayout.DataDirectory), launcher);
            }
            // Earlier 2.0 previews were flat; keep opening their existing libraries where they are.
            return new(directory, Path.Combine(directory, PortableLayout.DataDirectory));
        }
        for (var parent = new DirectoryInfo(directory); parent is not null; parent = parent.Parent)
            if (File.Exists(Path.Combine(parent.FullName, "win_frontend", "SuzuEmojy.Windows.csproj")))
                return new(parent.FullName, Path.Combine(parent.FullName, "data"));
        throw new InvalidOperationException("找不到 SuzuEmojy 安装目录。请从完整 ZIP 解压后运行，或在源码目录中运行开发版。");
    }
}
