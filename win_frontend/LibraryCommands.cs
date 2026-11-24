using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace SuzuEmojy.Windows;

internal sealed record LibraryCommand(string Action, string[]? Paths = null, string? Category = null,
    string? Value = null, string? Destination = null, string[]? Categories = null,
    bool DeleteImages = false,
    int Limit = 30, string Kind = "all", int Threshold = 6, bool ReportProgress = false,
    string? Account = null, ExternalSticker[]? Items = null, string? BotToken = null, string? ApiProxy = null,
    string? OutputFormat = null, bool ZipOutput = false, string[]? Links = null, string? CancelFile = null,
    InboxFile[]? InboxFiles = null, string? PackageId = null, string? SourceUrl = null, bool OverwritePackage = false,
    string? NetworkProxy = null)
{
    public override string ToString() => Action;
}

internal sealed record InboxFile(string Name, long Length, long Modified, bool Staged = false);

internal sealed record SimilarityImage(string Path, int Width, int Height, long Size, int Duration, bool Animated);
internal sealed record LibraryProgress(string Phase, int Current, int Total, string? Detail, int? Row = null, string? Name = null);
internal sealed record FolderImportStats(string FolderName, int TotalFiles, int ImageCount, int NonImages,
    int Subdirs, string[] Issues, int IssueCount);

internal sealed record LibraryResult(int Count, int Skipped, int Failed, string? Destination,
    string[]? Warnings, SimilarityImage[][]? SimilarityGroups, FolderImportStats? FolderStats,
    string? Error = null, ExternalSticker[]? Items = null, ExternalPreview[]? Previews = null,
    ExternalAccount[]? Sources = null, string? SourceName = null, string? SourceTitle = null, string[]? RemovedIds = null,
    int Cancelled = 0, BatchPackageResult[]? BatchRows = null, string? SourceUrl = null, bool LibraryChanged = false,
    PreviewAnimation? Animation = null, string[]? Recent = null);
internal sealed record BatchPackageResult(int Index, string Status, string Message, string? Path = null);

internal sealed record ExternalSticker(string Id, int Index, string Label, string? PreviewId = null,
    bool Animated = false, int Width = 0, int Height = 0, string? FileIdentity = null);
internal sealed record ExternalPreview(string Id, string Data, string? Animation = null,
    [property: System.Text.Json.Serialization.JsonIgnore] Controls.PreviewFrames? Native = null,
    [property: System.Text.Json.Serialization.JsonIgnore] System.Windows.Media.Imaging.BitmapSource? Still = null);
internal sealed record ExternalOption(string Key, string Label);
internal sealed record ExternalAccount(string Key, ExternalOption[] Options);
internal sealed record PreviewAnimation(PreviewFrame[]? Frames, int Plays,
    [property: System.Text.Json.Serialization.JsonIgnore] Controls.PreviewFrames? Native = null);
internal sealed record PreviewFrame(string Png, int Ms);

internal sealed class LibraryCommands(string repository, string data)
{
    private readonly SemaphoreSlim serial = new(1);
    private readonly SemaphoreSlim thumbnails = new(1);
    private readonly SemaphoreSlim inspector = new(1);
    private readonly SemaphoreSlim preview = new(1);
    private readonly object imageCutover = new();
    private string? preRefurbishImages;
    private Dictionary<string, string>? refurbishedNames;
    internal WindowsReleaseService Updates { get; } = new(data);

    internal Task<LibrarySnapshot> ReadForLanSync() => LibrarySnapshot.ReadAsync(data);

    internal async Task<LibrarySnapshot?> RefurbishOnStartup(CancellationToken token)
    {
        await serial.WaitAsync(token);
        try
        {
            return await Task.Run(() => StartupLibraryRefurbish.RunOnce(data, token, publish: Publish)
                ? LibrarySnapshotReader.Read(data, CancellationToken.None) : null, token);
        }
        finally { serial.Release(); }

        void Publish(string originalImages, LibraryRefurbish.Report report, Action install)
        {
            lock (imageCutover)
            {
                install();
                preRefurbishImages = originalImages;
                refurbishedNames = report.Renames;
            }
        }
    }

    // Paste and drag carry a path which the receiving app may open after the
    // migration switches the live directory. Give them stable original bytes.
    internal string CaptureRefurbishingImage(string path, CancellationToken token)
    {
        lock (imageCutover)
        {
            token.ThrowIfCancellationRequested();
            var source = preRefurbishImages is null ? path : Path.Combine(preRefurbishImages, Path.GetFileName(path));
            using var input = File.Open(source, FileMode.Open, FileAccess.Read, FileShare.Read);
            var revision = Path.GetFullPath(path) + ":" + RuntimePrimitives.Revision(input.SafeFileHandle);
            using var sha = SHA256.Create();
            var key = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(revision))).Replace("-", "");
            var folder = Directory.CreateDirectory(Path.Combine(data, "cache", "refurbish-send", key)).FullName;
            var target = Path.Combine(folder, Path.GetFileName(path));
            if (File.Exists(target)) return target;
            var staging = Path.Combine(folder, "." + Guid.NewGuid().ToString("N") + ".part");
            try
            {
                using (var output = File.Open(staging, FileMode.CreateNew, FileAccess.Write)) input.CopyTo(output);
                RuntimePrimitives.CommitFile(staging, target, overwrite: false, token);
                return target;
            }
            finally { RuntimePrimitives.DiscardTemporaryFile(staging); }
        }
    }

    internal async Task Synchronize(System.IO.Stream stream, Action<string> status, CancellationToken token, bool streaming = false)
    {
        await serial.WaitAsync(token).ConfigureAwait(false);
        try { await Task.Run(() => LanReconciliation.Run(new(data), stream, status, token, streaming), token).ConfigureAwait(false); }
        finally { serial.Release(); }
    }

    internal async Task<Dictionary<string, JsonElement>> SaveSettings(IReadOnlyDictionary<string, object> changes,
        CancellationToken cancellation = default)
    {
        await serial.WaitAsync(cancellation);
        try { return await Task.Run(() => LibraryPreferences.Save(data, changes, cancellation), cancellation); }
        finally { serial.Release(); }
    }

    internal async Task<(LibraryResult Result, LibrarySnapshot? Snapshot)> Run(LibraryCommand command, bool reload,
        CancellationToken cancellation = default, IProgress<LibraryProgress>? progress = null)
    {
        var gate = command.Action switch
        {
            "qq_previews" or "tg_previews" => thumbnails,
            "qq_detail" or "tg_detail" => inspector,
            "preview_animation" => preview,
            _ => serial
        };
        await gate.WaitAsync(cancellation);
        try
        {
            if (command.Action == "recent" && command.Paths is { Length: 1 } paths && refurbishedNames is { } names &&
                !File.Exists(paths[0]) && names.TryGetValue(Path.GetFileName(paths[0]), out var renamed))
                command = command with { Paths = [Path.Combine(Path.GetDirectoryName(paths[0])!, renamed)] };
            if (command.Action == "export_files")
            {
                var count = await Task.Run(() => LibraryFileExport.Copy(data, command.Value!, command.Paths ?? [], cancellation), cancellation);
                return (new(count, 0, 0, null, null, null, null), null);
            }
            var ffmpeg = StickerVideoConverter.Find(repository);
            if (command.Action == "import_link")
            {
                var linked = await LinkedImageImport.TryRun(data, command, cancellation, progress, ffmpeg: ffmpeg) ?? Unavailable;
                return (linked, reload && linked.Error is null ? await LibrarySnapshot.ReadAsync(data) : null);
            }
            if (command.Action == "import_inbox")
            {
                var inbox = await Task.Run(() => InboxLibraryImport.Run(data, command.InboxFiles ?? [], cancellation, progress,
                    ffmpeg), cancellation);
                var claimed = inbox ?? Unavailable;
                return (claimed, reload && (claimed.Count > 0 || claimed.Skipped > 0) ? await LibrarySnapshot.ReadAsync(data) : null);
            }
            if (command.Action.StartsWith("tg_", StringComparison.Ordinal))
            {
                var telegram = await TelegramStickerOperations.Run(repository, data, command, cancellation, progress) ?? Unavailable;
                return (telegram, reload && telegram.LibraryChanged ? await LibrarySnapshot.ReadAsync(data) : null);
            }
            var outcome = await Task.Run(() => WindowsLibraryRuntime.TryRun(data, command, cancellation, progress, ffmpeg), cancellation);
            var result = outcome.Result ?? Unavailable;
            return (result, reload && outcome.CanReload && result.Error is null ? await LibrarySnapshot.ReadAsync(data) : null);
        }
        finally { gate.Release(); }
    }

    private static LibraryResult Unavailable => new(0, 0, 0, null, null, null, null,
        Error: "无法完成操作，请检查文件格式、图库状态和访问权限。");
}
