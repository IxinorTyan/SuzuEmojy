using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;

namespace SuzuEmojy.Windows;

internal static class InboxLibraryImport
{
    private static readonly Regex StagedName = new(@"\A[a-f0-9]{32}_.+\z", RegexOptions.CultureInvariant);
    private static readonly LibraryResult Empty = new(0, 0, 0, null, null, null, null);

    internal static LibraryResult? Run(string data, InboxFile[] candidates, CancellationToken token, IProgress<LibraryProgress>? progress,
        string? ffmpeg = null)
    {
        var inbox = Path.Combine(data, "inbox");
        var processing = Path.Combine(inbox, ".processing");
        var failed = Path.Combine(inbox, "failed");
        if (!PlainDirectory(inbox)) return Empty;
        foreach (var directory in new[] { processing, failed })
        {
            Directory.CreateDirectory(directory);
            if (!PlainDirectory(directory)) return Empty;
        }
        var claimed = new List<(InboxFile File, string Path, string Revision)>();
        foreach (var candidate in candidates.Take(64))
        {
            token.ThrowIfCancellationRequested();
            if (!LibraryResourceIngest.ValidName(candidate.Name) || !LocalFolderImport.Supported(candidate.Name) ||
                candidate.Length <= 0 || candidate.Staged && !StagedName.IsMatch(candidate.Name)) continue;
            try
            {
                var path = Path.Combine(candidate.Staged ? processing : inbox, candidate.Name);
                if (Receipt(path, candidate) is not { } revision) continue;
                var name = candidate.Name;
                if (!candidate.Staged)
                {
                    name = $"{Guid.NewGuid():N}_{name.Substring(Math.Max(0, name.Length - 160))}";
                    var staged = Path.Combine(processing, name);
                    if (!RuntimePrimitives.CommitFile(path, staged, overwrite: false, token)) continue;
                    path = staged;
                }
                if (Receipt(path, candidate) == revision) claimed.Add((candidate with { Staged = true, Name = name }, path, revision));
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException) { }
        }
        if (claimed.Count == 0) return Empty;
        var rejected = new HashSet<string>(LibraryImagePaths.Comparer);
        var result = LocalImageImport.TryRun(data, claimed.Select(item => item.Path).ToArray(), null, token, progress,
            ffmpeg, path => rejected.Add(path));
        if (result is not { Cancelled: 0, Error: null } || result.Failed != rejected.Count ||
            result.Count + result.Skipped + rejected.Count != claimed.Count) return result;
        // Don't consume a changed or replaced claim, even if the library already
        // committed the previous revision of that name.
        var warnings = new List<string>(result.Warnings ?? []);
        foreach (var item in claimed)
        {
            try
            {
                if (!PlainDirectory(processing) || Receipt(item.Path, item.File) != item.Revision) continue;
                if (rejected.Contains(item.Path))
                {
                    if (PlainDirectory(failed)) RuntimePrimitives.CommitFile(item.Path,
                        Path.Combine(failed, item.File.Name), overwrite: false, token);
                }
                else File.Delete(item.Path);
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException)
            {
                if (warnings.Count < 5) warnings.Add($"清理 {item.File.Name}: {error.Message}");
            }
        }
        return result with { Warnings = warnings.ToArray() };
    }

    internal static void EnsureFolders(string data)
    {
        var inbox = Path.Combine(data, "inbox");
        Directory.CreateDirectory(inbox);
        if (PlainDirectory(inbox)) Directory.CreateDirectory(Path.Combine(inbox, "failed"));
    }

    private static bool PlainDirectory(string path) => Directory.Exists(path) &&
        (File.GetAttributes(path) & FileAttributes.ReparsePoint) == 0;

    private static string? Receipt(string path, InboxFile expected)
    {
        if ((File.GetAttributes(path) & (FileAttributes.Directory | FileAttributes.ReparsePoint)) != 0) return null;
        using var file = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        return file.Length == expected.Length &&
            (RuntimePrimitives.LastWriteTimeUtc(file.SafeFileHandle) - RuntimePrimitives.UnixEpoch).Ticks == expected.Modified
            ? RuntimePrimitives.Revision(file.SafeFileHandle) : null;
    }
}
