using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;

namespace SuzuEmojy.Windows;

internal static class LocalFolderImport
{
    internal sealed record Item(string Path, string? Category);
    internal sealed record Scan(Item[] Items, FolderImportStats? Stats);
    private static readonly Regex Digits = new(@"(\d+)", RegexOptions.CultureInvariant);
    private static readonly HashSet<string> Formats = new(StringComparer.OrdinalIgnoreCase)
        { ".png", ".apng", ".jpg", ".jpeg", ".gif", ".webp", ".webm", ".tgs" };
    private static readonly StringComparer FileNames = LibraryImagePaths.Comparer;

    internal static bool Supported(string path) => Formats.Contains(Path.GetExtension(path));

    internal static string? Category(string? name, string? fallback = null)
    {
        var value = name?.Trim();
        return string.IsNullOrEmpty(value) || LibrarySnapshotReader.Reserved(value!) || value is "全部" or "最近" ||
            string.Equals(value, "all", StringComparison.OrdinalIgnoreCase) ? fallback : value;
    }

    internal static Scan Run(string[] paths, string? target, CancellationToken token, IProgress<LibraryProgress>? progress)
    {
        var folders = paths.Where(Directory.Exists).Select(path => Path.GetFullPath(path).TrimEnd('\\')).ToArray();
        if (folders.Length == 0) return new(paths.Select(path => new Item(path, Category(target))).ToArray(), null);
        var local = paths.Where(path => !Directory.Exists(path)).ToArray();
        target = Category(target);
        var result = new List<Item>();
        var seen = new Dictionary<string, HashSet<string?>>(FileNames);
        var issues = new List<string>();
        int total = local.Length, nonImages = 0, subdirs = 0, scanned = 0, issueCount = 0;
        foreach (var file in local) Add(file, target);
        progress?.Report(new("folder_scan", 0, 0, $"已扫描 0 个目录，发现 {result.Count} 张图片"));
        foreach (var root in folders)
        {
            var pending = new Stack<(string Path, string? Category, bool Root)>();
            var visited = new HashSet<string>(FileNames);
            pending.Push((root, Category(Path.GetFileName(root), target), true));
            while (pending.Count > 0 && !token.IsCancellationRequested)
            {
                var (path, category, isRoot) = pending.Pop();
                var children = new List<(string, string?, bool)>();
                try
                {
                    if (!visited.Add(RuntimePrimitives.ExistingPath(path))) continue;
                    if (!isRoot) RuntimePrimitives.EnsureNotReparse(path);
                    var entries = new DirectoryInfo(path).EnumerateFileSystemInfos()
                        .OrderBy(entry => entry.Name, Comparer<string>.Create(NaturalCompare)).ToArray();
                    foreach (var entry in entries)
                    {
                        if (token.IsCancellationRequested) break;
                        try
                        {
                            if ((entry.Attributes & FileAttributes.ReparsePoint) != 0) continue;
                            if (entry is DirectoryInfo)
                            {
                                subdirs++;
                                children.Add((entry.FullName, isRoot ? Category(entry.Name, category) : category, false));
                            }
                            else
                            {
                                total++;
                                if (Supported(entry.Name)) Add(entry.FullName, category);
                                else nonImages++;
                            }
                        }
                        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
                        { Issue(entry.FullName, error); }
                    }
                }
                catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException)
                { Issue(path, error); }
                for (int i = children.Count - 1; i >= 0; i--) pending.Push(children[i]);
                scanned++;
                progress?.Report(new("folder_scan", scanned, 0, $"已扫描 {scanned} 个目录，发现 {result.Count} 张图片"));
            }
        }
        return new(result.ToArray(), new(string.Join("、", folders.Select(Path.GetFileName)), total, result.Count,
            nonImages, subdirs, issues.ToArray(), issueCount));

        void Add(string path, string? name)
        {
            string key = Path.GetFullPath(path);
            if (!seen.TryGetValue(key, out var names)) seen.Add(key, names = new(StringComparer.Ordinal));
            if (names.Add(name)) result.Add(new(path, name));
        }

        void Issue(string path, Exception error)
        {
            issueCount++;
            if (issues.Count < 10) issues.Add($"{path}: {error.Message}");
        }
    }

    internal static int NaturalCompare(string left, string right)
    {
        var a = Digits.Split(left);
        var b = Digits.Split(right);
        for (int i = 0; i < Math.Min(a.Length, b.Length); i++)
        {
            int difference;
            if (i % 2 == 1)
            {
                var x = a[i].TrimStart('0');
                var y = b[i].TrimStart('0');
                difference = x.Length.CompareTo(y.Length);
                if (difference == 0) difference = string.CompareOrdinal(x, y);
            }
            else difference = LibrarySnapshotReader.CompareNames(a[i].ToLowerInvariant(), b[i].ToLowerInvariant());
            if (difference != 0) return difference;
        }
        int lengths = a.Length.CompareTo(b.Length);
        return lengths != 0 ? lengths : LibrarySnapshotReader.CompareNames(left, right);
    }
}
