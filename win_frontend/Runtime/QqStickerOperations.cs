using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Media.Imaging;

namespace SuzuEmojy.Windows;

internal static class QqStickerOperations
{
    private static readonly LibraryResult Empty = new(0, 0, 0, null, null, null, null);

    internal static LibraryResult? Run(string data, LibraryCommand request, CancellationToken token,
        IProgress<LibraryProgress>? progress, Action<string>? recycle = null)
    {
        try
        {
            var (folder, kind) = QqCacheScanner.ResolveFolder(request, request.Action == "qq_recycle");
            bool market = string.Equals(kind, "marketface", StringComparison.OrdinalIgnoreCase);
            return request.Action switch
            {
                "qq_previews" => Preview(folder, kind, market, request.Items, false, token),
                "qq_detail" => Preview(folder, kind, market, request.Items, true, token),
                "qq_recycle" => Recycle(data, folder, kind, market, request.Items, token, progress, recycle ?? WindowsRecycleBin.Recycle),
                "qq_export" => Export(data, folder, kind, market, request, token, progress),
                "qq_import" => Import(data, folder, kind, market, request, token, progress),
                _ => throw new ArgumentException("未知的 QQ 操作")
            };
        }
        catch (Exception error) when (MediaFailure(error))
        {
            return Empty with { Error = "无法读取 QQ 缓存，请检查目录和账号" };
        }
    }

    private static LibraryResult? Preview(string folder, string kind, bool market, ExternalSticker[]? items, bool detail,
        CancellationToken token)
    {
        var previews = new List<ExternalPreview>();
        foreach (var item in (items ?? []).Take(detail ? 1 : 18))
        {
            token.ThrowIfCancellationRequested();
            try
            {
                var path = QqCacheScanner.ResolveFile(folder, kind, item.Id);
                using var source = QqCacheScanner.Open(path);
                if (market)
                {
                    if (QqCacheScanner.Recover(source, token) is not { } gif) continue;
                    using var content = new MemoryStream(gif.Bytes, writable: false);
                    previews.Add(StickerPreviewMedia.Read(item.Id, content, "gif", detail, token));
                }
                else
                {
                    string? ext = QqCacheScanner.SignatureAtStart(source);
                    previews.Add(StickerPreviewMedia.Read(item.Id, source, ext, detail, token));
                }
            }
            catch (Exception error) when (MediaFailure(error)) { }
        }
        return Empty with { Previews = previews.ToArray() };
    }

    private static LibraryResult? Recycle(string data, string folder, string kind, bool market, ExternalSticker[]? items,
        CancellationToken token, IProgress<LibraryProgress>? progress, Action<string> recycle)
    {
        data = RuntimePrimitives.ExistingPath(data);
        var choices = (items ?? []).GroupBy(item => item.Id, LibraryImagePaths.Comparer).Select(group => group.Last()).ToArray();
        var removed = new List<string>();
        var warnings = new List<string>();
        int completed = 0;
        progress?.Report(new("qq_recycle", 0, choices.Length, null));
        foreach (var item in choices)
        {
            if (token.IsCancellationRequested) break;
            try
            {
                var path = QqCacheScanner.ResolveFile(folder, kind, item.Id, direct: true);
                if (RuntimePrimitives.IsWithin(data, path)) throw new ArgumentException("文件不在安全的 QQ 缓存范围");
                using (var source = QqCacheScanner.Open(path))
                {
                    CheckIdentity(source, item);
                    if (market)
                    {
                        if (QqCacheScanner.Recover(source, token) is null) throw new ArgumentException("不是有效的商店表情");
                    }
                    else VerifyImage(source, token);
                }
                _ = QqCacheScanner.ResolveFile(folder, kind, path, direct: true);
                using (var source = QqCacheScanner.Open(path)) CheckIdentity(source, item);
                token.ThrowIfCancellationRequested();
                recycle(path);
                removed.Add(item.Id);
            }
            catch (OperationCanceledException) when (token.IsCancellationRequested) { break; }
            catch (Exception error) when (MediaFailure(error))
            {
                if (warnings.Count < 5) warnings.Add($"{Path.GetFileName(item.Id)}：{(error is ArgumentException ? error.Message : "无法移到回收站")}");
            }
            completed++;
            progress?.Report(new("qq_recycle", completed, choices.Length, null));
        }
        return Empty with { Count = removed.Count, Failed = completed - removed.Count,
            RemovedIds = removed.ToArray(), Warnings = warnings.ToArray(), Cancelled = completed < choices.Length ? 1 : 0 };
    }

    private static void CheckIdentity(FileStream file, ExternalSticker item)
    {
        if (string.IsNullOrEmpty(item.FileIdentity) || !string.Equals(RuntimePrimitives.QqFileIdentity(file),
            item.FileIdentity, StringComparison.Ordinal)) throw new ArgumentException("文件已变化，请重新扫描");
    }

    private static void VerifyImage(Stream source, CancellationToken token)
    {
        var format = QqCacheScanner.SignatureAtStart(source);
        if (format == "webp" && NativeWebp.Available)
        {
            _ = NativeWebp.Still(NativeWebp.Read(source, token), token);
            return;
        }
        if (format is not ("png" or "gif" or "jpg" or "bmp" or "tiff" or "ico"))
            throw new ArgumentException("不是有效的图片");
        var decoder = BitmapDecoder.Create(source, BitmapCreateOptions.PreservePixelFormat, BitmapCacheOption.None);
        foreach (var frame in decoder.Frames)
        {
            token.ThrowIfCancellationRequested();
            long stride = ((long)frame.PixelWidth * frame.Format.BitsPerPixel + 7) / 8;
            if (stride <= 0 || stride * frame.PixelHeight > 128 * 1024 * 1024) throw new ArgumentException("无法验证图片");
            frame.CopyPixels(new byte[stride * frame.PixelHeight], (int)stride, 0);
        }
    }

    private static LibraryResult? Export(string data, string folder, string kind, bool market, LibraryCommand request,
        CancellationToken token, IProgress<LibraryProgress>? progress)
    {
        if (string.IsNullOrEmpty(request.Destination)) return Empty with { Error = "请选择导出文件夹" };
        var target = RuntimePrimitives.ExistingPath(request.Destination!);
        if (!Directory.Exists(target) || RuntimePrimitives.IsWithin(Path.GetFullPath(data), target) ||
            RuntimePrimitives.IsWithin(RuntimePrimitives.ExistingPath(request.Value!), target))
            return Empty with { Error = "请选择图库和 QQ 缓存以外的文件夹" };
        if (Directory.Exists(data) && RuntimePrimitives.IsWithin(RuntimePrimitives.ExistingPath(data), target))
            return Empty with { Error = "请选择图库和 QQ 缓存以外的文件夹" };
        var paths = (request.Items ?? []).Select(item => item.Id).Distinct(LibraryImagePaths.Comparer).ToArray();
        var warnings = new List<string>();
        int saved = 0, completed = 0;
        progress?.Report(new("qq_export", 0, paths.Length, null));
        foreach (var path in paths)
        {
            if (token.IsCancellationRequested) break;
            try
            {
                string actual = QqCacheScanner.ResolveFile(folder, kind, path);
                using var source = QqCacheScanner.Open(actual);
                if (market)
                {
                    var content = QqCacheScanner.Recover(source, token)?.Bytes ?? throw new ArgumentException("商店表情已损坏");
                    UniqueStickerFile.Write(target, Path.GetFileNameWithoutExtension(path) + ".gif",
                        output => output.Write(content, 0, content.Length), token);
                }
                else
                {
                    string ext = QqCacheScanner.SignatureAtStart(source) ?? throw new ArgumentException("无法识别文件格式");
                    var name = Path.GetFileName(path);
                    if (ext == "png" && LegacyAnimatedGif.Available && ApngDecoder.HasAnimationControl(source))
                    {
                        byte[]? gif = null;
                        try { gif = LegacyAnimatedGif.FromApng(source, token); }
                        catch (Exception error) when (MediaFailure(error))
                        {
                            if (warnings.Count < 5) warnings.Add($"{name}：APNG 转换失败，保留 PNG 原格式");
                        }
                        if (gif is not null)
                        {
                            UniqueStickerFile.Write(target, Path.GetFileNameWithoutExtension(name) + ".gif",
                                output => output.Write(gif, 0, gif.Length), token);
                            saved++;
                            completed++;
                            progress?.Report(new("qq_export", completed, paths.Length, null));
                            continue;
                        }
                        name = Path.GetFileNameWithoutExtension(name) + ".png";
                    }
                    source.Position = 0;
                    if (!name.EndsWith($".{ext}", StringComparison.OrdinalIgnoreCase) &&
                        !name.EndsWith($".{ext}.gif", StringComparison.OrdinalIgnoreCase)) name += "." + ext;
                    UniqueStickerFile.Write(target, name, output => UniqueStickerFile.Copy(source, output, token), token);
                }
                saved++;
            }
            catch (OperationCanceledException) when (token.IsCancellationRequested) { break; }
            catch (Exception error) when (MediaFailure(error))
            {
                if (warnings.Count < 5) warnings.Add($"{Path.GetFileName(path)}：{(error is ArgumentException ? error.Message : "无法导出")}");
            }
            completed++;
            progress?.Report(new("qq_export", completed, paths.Length, null));
        }
        return Empty with { Count = saved, Failed = completed - saved, Destination = target, Warnings = warnings.ToArray(),
            Cancelled = completed < paths.Length ? 1 : 0 };
    }

    private static LibraryResult? Import(string data, string folder, string kind, bool market, LibraryCommand request,
        CancellationToken token, IProgress<LibraryProgress>? progress)
    {
        var working = Path.Combine(Path.GetTempPath(), $"suzu-qq-{Guid.NewGuid():N}");
        var staged = new List<string>();
        var warnings = new List<string>();
        int failed = 0;
        try
        {
            Directory.CreateDirectory(working);
            foreach (var path in (request.Paths ?? []).Distinct(LibraryImagePaths.Comparer))
            {
                token.ThrowIfCancellationRequested();
                try
                {
                    var actual = QqCacheScanner.ResolveFile(folder, kind, path);
                    using var source = QqCacheScanner.Open(actual);
                    var ext = market ? "gif" : QqCacheScanner.SignatureAtStart(source);
                    if (ext is not ("png" or "gif" or "webp" or "jpg" or "bmp" or "tiff"))
                        throw new FormatException("无法识别文件格式");
                    var output = Path.Combine(working, $"{staged.Count}.{ext}");
                    staged.Add(output);
                    if (market)
                    {
                        var recovered = QqCacheScanner.Recover(source, token);
                        if (recovered is null) throw new FormatException("商店表情已损坏");
                        File.WriteAllBytes(output, recovered.Bytes);
                    }
                    else
                    {
                        using var stagedFile = File.Open(output, FileMode.CreateNew, FileAccess.Write, FileShare.None);
                        UniqueStickerFile.Copy(source, stagedFile, token);
                    }
                }
                catch (Exception error) when (MediaFailure(error))
                {
                    failed++;
                    if (warnings.Count < 5) warnings.Add($"{Path.GetFileName(path)}：无法读取 QQ 缓存图片");
                }
            }
            var result = LocalImageImport.TryRun(data, staged.Where(File.Exists).ToArray(), request.Category, token, progress);
            return result is null ? null : result with { Failed = result.Failed + failed,
                Warnings = [.. result.Warnings ?? [], .. warnings] };
        }
        finally
        {
            foreach (var path in staged) RuntimePrimitives.DiscardTemporaryFile(path);
            try { Directory.Delete(working); }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
        }
    }

    private static bool MediaFailure(Exception error) => error is IOException or UnauthorizedAccessException or ArgumentException or
        FormatException or NotSupportedException or COMException or InvalidOperationException;
}
