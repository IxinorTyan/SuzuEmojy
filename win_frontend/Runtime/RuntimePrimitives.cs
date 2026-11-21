using System;
using System.ComponentModel;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace SuzuEmojy.Windows;

internal static class RuntimePrimitives
{
    internal static readonly DateTime UnixEpoch = new(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc);

    internal static int Clamp(int value, int min, int max) =>
        min > max ? throw new ArgumentException("Invalid bounds.") : value < min ? min : value > max ? max : value;

    internal static double Clamp(double value, double min, double max) =>
        min > max ? throw new ArgumentException("Invalid bounds.") : value < min ? min : value > max ? max : value;

    internal static bool IsFinite(double value) => !double.IsNaN(value) && !double.IsInfinity(value);

    internal static long MonotonicMilliseconds => (long)GetTickCount64();

    internal static DateTime LastWriteTimeUtc(SafeFileHandle handle)
    {
        if (!GetFileTime(handle, 0, 0, out var modified))
            throw new IOException("Could not read the file timestamp.", new Win32Exception(Marshal.GetLastWin32Error()));
        return DateTime.FromFileTimeUtc(modified);
    }

    internal static string Revision(SafeFileHandle handle)
    {
        if (!GetFileInformationByHandle(handle, out var file))
            throw new IOException("无法读取文件状态。", new Win32Exception(Marshal.GetLastWin32Error()));
        return $"{file.LastWriteHigh:x8}{file.LastWriteLow:x8}:{file.SizeHigh:x8}{file.SizeLow:x8}:" +
            $"{file.Volume:x8}{file.IndexHigh:x8}{file.IndexLow:x8}";
    }

    // Compatible with the existing QQ recycle guard's Windows stat receipt.
    internal static string? QqFileIdentity(FileStream stream)
    {
        if (!GetFileInformationByHandle(stream.SafeFileHandle, out var file) ||
            (file.Attributes & ((uint)FileAttributes.Directory | (uint)FileAttributes.ReparsePoint)) != 0 || file.Links != 1)
            return null;
        static long Nanoseconds(uint high, uint low) => checked((DateTime.FromFileTimeUtc(((long)high << 32) | low).Ticks - UnixEpoch.Ticks) * 100);
        ulong volume = file.Volume, fileId = ((ulong)file.IndexHigh << 32) | file.IndexLow;
        string identity = fileId.ToString(CultureInfo.InvariantCulture);
        if (GetFileInformationByHandleEx(stream.SafeFileHandle, 18, out var info, 24)) // FileIdInfo
        {
            volume = info.Volume;
            identity = ((System.Numerics.BigInteger)info.High << 64 | info.Low).ToString(CultureInfo.InvariantCulture);
        }
        return string.Format(CultureInfo.InvariantCulture, "{0}:{1}:{2}:{3}:{4}", volume, identity, stream.Length,
            Nanoseconds(file.LastWriteHigh, file.LastWriteLow), Nanoseconds(file.CreatedHigh, file.CreatedLow));
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct FileIdInformation { internal ulong Volume, Low, High; }

    [StructLayout(LayoutKind.Sequential)]
    private struct FileInformation
    {
        internal uint Attributes, CreatedLow, CreatedHigh, LastAccessLow, LastAccessHigh, LastWriteLow, LastWriteHigh,
            Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
    }

    internal static void ReplaceFile(string source, string target)
    {
        if (!MoveFileExW(source, target, 1)) // MOVEFILE_REPLACE_EXISTING; both paths are in the same directory.
            throw new IOException("Could not replace the file.", new Win32Exception(Marshal.GetLastWin32Error()));
    }

    internal static bool CommitFile(string source, string target, bool overwrite, CancellationToken cancellation)
    {
        for (int attempt = 0; ; attempt++)
        {
            cancellation.ThrowIfCancellationRequested();
            if (MoveFileExW(source, target, overwrite ? 1u : 0u)) return true;
            int error = Marshal.GetLastWin32Error();
            if (!overwrite && error is 80 or 183) return false;
            if (!overwrite || attempt == 4 || error is not (5 or 32 or 33))
                throw new IOException(new Win32Exception(error).Message, new Win32Exception(error));
            cancellation.WaitHandle.WaitOne(50 << attempt);
        }
    }

    internal static void DiscardTemporaryFile(string path)
    {
        try { File.Delete(path); }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        { System.Diagnostics.Trace.TraceWarning("Could not remove an incomplete temporary file: {0}", error.Message); }
    }

    internal static bool IsAbsolutePath(string path) => path.IndexOf('\0') < 0 &&
        (path.Length >= 3 && char.IsLetter(path[0]) && path[1] == ':' && (path[2] is '\\' or '/') ||
         path.StartsWith("\\\\", StringComparison.Ordinal) || path.StartsWith("//", StringComparison.Ordinal));

    internal static void EnsureNotReparse(string path)
    {
        try
        {
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                throw new ArgumentException($"不支持链接类型的图库路径：{Path.GetFileName(path)}");
        }
        catch (FileNotFoundException) { }
        catch (DirectoryNotFoundException) { }
    }

    // Resolve junctions and aliases before deciding whether a path is in the library.
    internal static string ExistingPath(string path)
    {
        if (!IsAbsolutePath(path)) throw new ArgumentException("路径必须是绝对路径。");
        var full = Path.GetFullPath(path);
        using var file = CreateFileW(full, 0, 7, 0, 3, 0x02000000, 0); // Open existing, including a directory.
        if (file.IsInvalid) throw new IOException(new Win32Exception(Marshal.GetLastWin32Error()).Message);
        var buffer = new StringBuilder(32768);
        uint length = GetFinalPathNameByHandleW(file, buffer, (uint)buffer.Capacity, 0);
        if (length == 0 || length >= buffer.Capacity) throw new IOException("无法解析路径。");
        var resolved = buffer.ToString();
        if (resolved.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) return @"\\" + resolved.Substring(8);
        return resolved.StartsWith(@"\\?\", StringComparison.Ordinal) ? resolved.Substring(4) : resolved;
    }

    internal static bool IsWithin(string root, string path)
    {
        root = root.TrimEnd('\\', '/');
        return string.Equals(root, path.TrimEnd('\\', '/'), StringComparison.OrdinalIgnoreCase) ||
            path.StartsWith(root + "\\", StringComparison.OrdinalIgnoreCase);
    }

    [DllImport("kernel32.dll")] private static extern ulong GetTickCount64();
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileTime(SafeFileHandle file, nint creation, nint access, out long modified);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(SafeFileHandle file, out FileInformation information);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandleEx(SafeFileHandle file, int kind, out FileIdInformation information, uint size);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    private static extern bool MoveFileExW(string source, string target, uint flags);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(string path, uint access, uint sharing, nint security,
        uint creation, uint flags, nint template);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandleW(SafeFileHandle file, StringBuilder path, uint capacity, uint flags);
}
