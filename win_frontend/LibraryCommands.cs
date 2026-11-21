using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace SuzuEmojy.Windows;

internal sealed record LibraryCommand(string Action, string[]? Paths = null, string? Category = null,
    string? Value = null, string? Destination = null, string[]? Categories = null,
    Dictionary<string, object>? Settings = null, bool DeleteImages = false,
    int Limit = 30, string Kind = "all", int Threshold = 6, bool ReportProgress = false,
    string? Account = null, ExternalSticker[]? Items = null, string? BotToken = null, string? ApiProxy = null,
    string? OutputFormat = null, bool ZipOutput = false, string[]? Links = null, string? CancelFile = null,
    InboxFile[]? InboxFiles = null, string? PackageId = null, string? SourceUrl = null, bool OverwritePackage = false)
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
    int Cancelled = 0, BatchPackageResult[]? BatchRows = null, string? SourceUrl = null, bool LibraryChanged = false);
internal sealed record BatchPackageResult(int Index, string Status, string Message, string? Path = null);

internal sealed record ExternalSticker(string Id, int Index, string Label, string? PreviewId = null,
    bool Animated = false, int Width = 0, int Height = 0, string? FileIdentity = null);
internal sealed record ExternalPreview(string Id, string Data, string? Animation = null);
internal sealed record ExternalOption(string Key, string Label);
internal sealed record ExternalAccount(string Key, ExternalOption[] Options);

internal sealed class LibraryCommands(string repository, string data)
{
    private readonly SemaphoreSlim serial = new(1);
    private readonly SemaphoreSlim thumbnails = new(1);
    private readonly SemaphoreSlim inspector = new(1);
    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull
    };

    internal async Task<(LibraryResult Result, LibrarySnapshot? Snapshot)> Run(LibraryCommand command, bool reload,
        CancellationToken cancellation = default, IProgress<LibraryProgress>? progress = null)
    {
        var gate = command.Action switch
        {
            "qq_previews" or "tg_previews" => thumbnails,
            "qq_detail" or "tg_detail" => inspector,
            _ => serial
        };
        await gate.WaitAsync(cancellation);
        string? marker = null;
        try
        {
            // Signal writable jobs to stop between items. Never kill a running write.
            if (command.Action is "tg_batch_package" or "import" or "import_link" or "qq_export" && cancellation.CanBeCanceled)
            {
                marker = Path.GetTempFileName();
                command = command with { CancelFile = marker };
            }
            using var stop = marker is { } cancelPath
                ? cancellation.Register(() =>
                {
                    try { File.WriteAllText(cancelPath, "1"); }
                    catch (IOException) { }
                    catch (UnauthorizedAccessException) { }
                })
                : default;
            var response = await PythonBridge.Request(repository, "services.gallery_commands",
                JsonSerializer.Serialize(command with { ReportProgress = progress is not null }, Json),
                data, marker is null ? cancellation : CancellationToken.None,
                progress is null ? null : line =>
                {
                    try
                    {
                        if (JsonSerializer.Deserialize<LibraryProgress>(line, Json) is { } update) progress.Report(update);
                    }
                    catch (JsonException) { System.Diagnostics.Trace.TraceWarning("Invalid library progress update."); }
                });
            var result = JsonSerializer.Deserialize<LibraryResult>(response, Json) ??
                throw new InvalidOperationException("无法读取操作结果。");
            bool changed = command.Action != "import_inbox" || result.Count > 0 || result.Skipped > 0;
            return (result, reload && (result.LibraryChanged || changed && result.Error is null)
                ? await LibrarySnapshot.ReadAsync(repository, data) : null);
        }
        finally
        {
            if (marker is not null)
            {
                try { File.Delete(marker); }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
            }
            gate.Release();
        }
    }
}
