using System;
using System.IO;
using System.Threading;

namespace SuzuEmojy.Windows;

// Native operations own their storage and cancellation rules.
internal static class WindowsLibraryRuntime
{
    internal readonly record struct Outcome(LibraryResult? Result, bool CanReload = false);

    internal static Outcome TryRun(string data, LibraryCommand command, CancellationToken token, IProgress<LibraryProgress>? progress,
        string? ffmpeg = null)
    {
        if (LibraryEdits.Handles(command)) return new(LibraryEdits.Run(data, command, token), CanReload: true);
        switch (command.Action)
        {
            case "preview_animation":
                var images = RuntimePrimitives.ExistingPath(Path.GetFullPath(Path.Combine(data, "images")));
                var paths = LibraryImagePaths.Validate(images, command.Paths ?? [], token);
                if (paths.Count != 1) throw new ArgumentException("动画预览需要一张图库图片");
                if (NativeWebp.Available)
                {
                    using var original = QqCacheScanner.Open(paths[0]);
                    if (NativeWebp.IsWebp(original))
                    {
                        var frames = NativeWebp.Preview(NativeWebp.Read(original, token), command.Limit, token);
                        return new(new(0, 0, 0, null, null, null, null,
                            Animation: frames is null ? null : new(null, 0, frames)));
                    }
                }
                return ApngDecoder.TryPreview(paths[0], command.Limit, token, out var animation)
                    ? new(new(0, 0, 0, null, null, null, null, Animation: animation))
                    : default;
            case "warm_indexes":
                var warmup = LibraryIndexWarmup.Run(data, token);
                return new(new(warmup.Count, 0, 0, null, null, null, null));
            case "export_zip":
                return new(ExchangeV1Export.TryRun(data, command.Value!, command.Categories, token, progress));
            case "split_zip":
                return new(ExchangeV1Split.TryRun(data, command.Value!, command.Destination!, token, progress));
            case "import_zip":
                return new(ExchangeV1Import.TryRun(data, command.Value!, token, progress), CanReload: true);
            case "import":
                return new(LocalImageImport.TryRun(data, command.Paths ?? [], command.Category, token, progress, ffmpeg), CanReload: true);
            case "delete":
            case "remove_category":
                return new(LibraryDeletion.TryRun(data, command, token, progress), CanReload: true);
            case "similarity":
                return new(LibrarySimilarity.TryRun(data, command, token, progress));
            case "maintain_library":
            case "startup_maintenance":
                var repaired = command.Action == "startup_maintenance" ? NativeLibraryMaintenance.RunOnce(data, token) :
                    NativeLibraryMaintenance.TryRun(data, token, progress);
                return new(repaired, CanReload: repaired is { LibraryChanged: true });
            case "qq_discover":
            case "qq_scan":
                return new(QqCacheScanner.TryRun(command, token, progress));
            case "qq_previews":
            case "qq_detail":
            case "qq_recycle":
            case "qq_export":
            case "qq_import":
                var qq = QqStickerOperations.Run(data, command, token, progress);
                return new(qq, CanReload: command.Action == "qq_import" && qq is { Error: null });
            case "set_wallpaper":
            case "clear_wallpaper":
                var wallpaper = ManagedWallpaper.TryRun(data, command.Value, command.Action == "clear_wallpaper", token);
                return new(wallpaper, CanReload: wallpaper is { Error: null });
            default:
                return default;
        }
    }
}
