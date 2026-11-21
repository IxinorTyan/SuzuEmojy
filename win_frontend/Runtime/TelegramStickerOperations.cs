using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;

namespace SuzuEmojy.Windows;

internal static class TelegramStickerOperations
{
    private static readonly LibraryResult Empty = new(0, 0, 0, null, null, null, null);
    private static readonly HashSet<string> Formats = new(StringComparer.Ordinal)
        { "png", "apng", "gif", "webp", "webm", "tgs", "jpg", "jpeg" };

    internal static async Task<LibraryResult?> Run(string repository, string data, LibraryCommand request, CancellationToken token,
        IProgress<LibraryProgress>? progress, HttpMessageHandler? transport = null)
    {
        using var client = TelegramStickerClient.Create(request, out var validation, transport);
        if (client is null) return validation is null ? null : Empty with { Error = validation };
        try
        {
            switch (request.Action)
            {
                case "tg_test_connection":
                    await client.Check(token).ConfigureAwait(false);
                    return Empty;
                case "tg_parse":
                    if (TelegramStickerClient.ParseName(request.Value) is not { } input)
                        return Empty with { Error = "请输入 Telegram 贴纸包链接或包名" };
                    var pack = await client.ReadPack(input, token).ConfigureAwait(false);
                    return Empty with { SourceName = pack.Name, SourceTitle = pack.Title, SourceUrl = pack.SourceUrl, Items = pack.Items };
                case "tg_previews":
                case "tg_detail":
                    bool detail = request.Action == "tg_detail";
                    var preview = await Map((request.Items ?? []).Take(detail ? 1 : 18).ToArray(), detail ? 1 : 4, "preview",
                        (item, cancel) => Preview(repository, client, item, detail, cancel), token, null).ConfigureAwait(false);
                    return Empty with { Previews = preview.OfType<ExternalPreview>().ToArray() };
                case "tg_download":
                    return await Download(repository, data, client, request, token, progress).ConfigureAwait(false);
                case "tg_single_package":
                    return await Single(repository, data, client, request, token, progress).ConfigureAwait(false);
                case "tg_batch_package":
                    return await Batch(repository, data, client, request, token, progress).ConfigureAwait(false);
                default:
                    return null;
            }
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested) { return Empty with { Cancelled = 1 }; }
        catch (ArgumentException error) when (error.Message == "Telegram 返回了无效的贴纸包名")
        { return Empty with { Error = error.Message }; }
        catch (Exception)
        {
            // Exceptions from a transport, URL, proxy or server may carry credentials.
            return Empty with { Error = TelegramStickerClient.Failure };
        }
    }

    private static async Task<ExternalPreview?> Preview(string repository, TelegramStickerClient client, ExternalSticker item,
        bool detail, CancellationToken token)
    {
        var asset = await client.Download(detail ? item.Id : item.PreviewId ?? item.Id, token).ConfigureAwait(false);
        if (asset.Format is "tgs" or "webm")
            asset = await ConvertAsset(repository, asset, detail && item.Animated ? "gif" : "png", token).ConfigureAwait(false);
        if (asset.Format == "webp" && !NativeWebp.Available) throw new NotSupportedException();
        using var source = new MemoryStream(asset.Bytes, writable: false);
        return StickerPreviewMedia.Read(item.Id, source, asset.Format == "apng" ? "png" : asset.Format, detail, token);
    }

    private static async Task<LibraryResult?> Download(string repository, string data, TelegramStickerClient client, LibraryCommand request,
        CancellationToken token, IProgress<LibraryProgress>? progress)
    {
        string? name = TelegramStickerClient.ParseName(request.Value);
        var format = request.OutputFormat ?? "auto";
        if (name is null || name != request.Value || format is not ("auto" or "original" or "png" or "gif"))
            return Empty with { Error = "贴纸包或格式无效" };
        bool library = request.Kind == "library";
        string? target = library ? null : OutputDirectory(data, request.Destination);
        if (!library && (request.Kind != "files" || target is null))
            return Empty with { Error = request.Kind == "files" ? "请选择图库以外的文件夹" : "下载方式无效" };
        var items = Choices(request.Items);
        using var stage = new StickerStaging();
        var files = await Fetch(repository, client, items, format, stage, token, progress).ConfigureAwait(false);
        int failed = files.Count(file => file is null);
        if (library)
        {
            var result = await Task.Run(() => LocalImageImport.TryRun(data, files.OfType<string>().ToArray(), $"TG_{name}", token, progress,
                StickerVideoConverter.Find(repository)), token)
                .ConfigureAwait(false);
            return result is null ? null : result with { Failed = result.Failed + failed, Warnings = null,
                LibraryChanged = result.Count + result.Skipped > 0 };
        }
        var exported = new List<string>();
        for (int i = 0; i < files.Length; i++)
        {
            token.ThrowIfCancellationRequested();
            if (files[i] is not { } source) continue;
            try
            {
                var label = $"{name}_{items[i].Index + 1:000}{Path.GetExtension(source)}";
                using var input = File.OpenRead(source);
                exported.Add(UniqueStickerFile.Write(target!, label, output => UniqueStickerFile.Copy(input, output, token), token));
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException) { failed++; }
        }
        string destination = target!;
        if (request.ZipOutput && exported.Count > 0)
        {
            try
            {
                destination = UniqueStickerFile.Write(target!, name + ".zip", output =>
                {
                    using var zip = new ZipArchive(output, ZipArchiveMode.Create, leaveOpen: true);
                    foreach (var path in exported)
                    {
                        using var input = File.OpenRead(path);
                        using var entry = zip.CreateEntry(Path.GetFileName(path), CompressionLevel.NoCompression).Open();
                        UniqueStickerFile.Copy(input, entry, token);
                    }
                }, token);
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException)
            { return Empty with { Count = exported.Count, Failed = failed, Destination = target, Warnings = ["ZIP 打包失败"] }; }
        }
        return Empty with { Count = exported.Count, Failed = failed, Destination = destination };
    }

    private static async Task<LibraryResult?> Single(string repository, string data, TelegramStickerClient client, LibraryCommand request,
        CancellationToken token, IProgress<LibraryProgress>? progress, string? batchFileName = null)
    {
        string? category = request.PackageId, name = TelegramStickerClient.ParseName(request.Value);
        if (category is null || LocalFolderImport.Category(category) != category ||
            batchFileName is null && (category.Length > 251 || PackageStem(category) != category))
            return Empty with { Error = "资源包 ID 不能作为收藏夹名称或 ZIP 文件名。" };
        if (name is null || name != request.Value || request.SourceUrl != $"https://t.me/addstickers/{name}" &&
            request.SourceUrl != $"https://t.me/addemoji/{name}")
            return Empty with { Error = "贴纸包无效，请重新解析。" };
        var items = Choices(request.Items);
        if (items.Length == 0 || (request.Items ?? []).Any(item => item.Index < 0 || item.Index >= 10_000))
            return Empty with { Error = "没有有效的贴纸，请重新解析。" };
        var target = OutputDirectory(data, request.Destination);
        if (target is null) return Empty with { Error = "请选择图库以外的文件夹。" };
        string output = Path.Combine(target, batchFileName ?? category + ".zip");
        string? existing = ExistingOutput(output);
        if (existing is not null && !request.OverwritePackage) return Empty with { Error = "资源包已存在，未覆盖。" };
        using var stage = new StickerStaging();
        string?[] files = await Fetch(repository, client, items, "original", stage, token, progress).ConfigureAwait(false);
        var warnings = items.Where((_, index) => files[index] is null).Select(item => $"贴纸 #{item.Index + 1} 获取失败").ToArray();
        if (files.Length == warnings.Length) return Empty with { Error = "没有可导出的贴纸，所有贴纸均获取失败。", Warnings = warnings };
        var imported = await Task.Run(() => LocalImageImport.TryRun(data, files.OfType<string>().ToArray(), category, token, progress,
            StickerVideoConverter.Find(repository)), token)
            .ConfigureAwait(false);
        if (imported is null) return null;
        bool changed = imported.Count + imported.Skipped > 0;
        if (imported.Cancelled > 0 || token.IsCancellationRequested) return imported with { LibraryChanged = changed, Cancelled = 1 };
        if (imported.Failed != 0) return imported with { Error = $"{imported.Failed} 张贴纸入库失败，未导出资源包。", LibraryChanged = changed };
        var temp = Path.Combine(target, $".suzu-tg-{Guid.NewGuid():N}.zip");
        try
        {
            var exported = await Task.Run(() => ExchangeV1Export.TryRun(data, temp, [category], token,
                new PackageProgress(progress), category, request.SourceUrl), token).ConfigureAwait(false);
            if (exported is null) return imported with { Error = "无法导出资源包，请检查所选目录后重试。", LibraryChanged = changed };
            if (exported.Count == 0) return imported with { Error = "收藏夹中没有可导出的资源。", LibraryChanged = changed };
            if (ExistingOutput(output) != existing || !RuntimePrimitives.CommitFile(temp, output, overwrite: existing is not null, token))
                return imported with { Error = "同名资源包已变化，未覆盖。", LibraryChanged = changed };
            return imported with { Destination = output, Warnings = warnings, Failed = warnings.Length, LibraryChanged = changed };
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested)
        { return imported with { Cancelled = 1, LibraryChanged = changed }; }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException or InvalidOperationException)
        { return imported with { Error = "无法导出资源包，请检查所选目录后重试。", LibraryChanged = changed }; }
        finally { RuntimePrimitives.DiscardTemporaryFile(temp); }
    }

    private static async Task<LibraryResult> Batch(string repository, string data, TelegramStickerClient client, LibraryCommand request,
        CancellationToken token, IProgress<LibraryProgress>? progress)
    {
        var links = (request.Links ?? []).Select(link => link.Trim()).Where(link => link.Length > 0).ToArray();
        if (links.Length == 0 || string.IsNullOrEmpty(request.Destination))
            return Empty with { Error = "请输入 TG 链接并选择保存目录。" };
        var target = OutputDirectory(data, request.Destination, create: true);
        if (target is null) return Empty with { Error = "请选择图库以外的文件夹" };
        var results = new List<BatchPackageResult>(links.Length);
        var names = (await LibrarySnapshot.ReadAsync(data).ConfigureAwait(false)).Categories.Select(category => Normalize(category.Name))
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        bool changed = false;
        Task<TelegramStickerClient.Pack>? incoming = null;
        for (int i = 0; i < links.Length; i++)
        {
            var reporter = new RowProgress(i, progress);
            if (token.IsCancellationRequested)
            {
                if (incoming is not null)
                {
                    try { await incoming.ConfigureAwait(false); }
                    catch (Exception) { }
                    incoming = null;
                }
                Finish("cancelled", "");
                continue;
            }
            var parsing = incoming ?? Read(i);
            incoming = i + 1 < links.Length ? Read(i + 1) : null;
            string? staging = null, file = null;
            try
            {
                var pack = await parsing.ConfigureAwait(false);
                reporter.Report(new("parsed", 0, 0, null, Name: pack.Title));
                var title = new string(pack.Title.Select(c => char.IsControl(c) ? ' ' : c).ToArray());
                title = LocalFolderImport.Category(title, pack.Name)!;
                string categoryBase = title;
                string category = categoryBase;
                for (int suffix = 2; !names.Add(Normalize(category)); suffix++) category = $"{categoryBase} ({suffix})";
                staging = Directory.CreateDirectory(Path.Combine(target, $".suzu-tg-row-{Guid.NewGuid():N}")).FullName;
                bool safeCategory = category.Length <= 251 && PackageStem(category) == category;
                string stagedName = safeCategory ? category + ".zip" : "package.zip";
                file = Path.Combine(staging, stagedName);
                var single = request with { Action = "tg_single_package", Value = pack.Name, SourceUrl = pack.SourceUrl,
                    Items = pack.Items, Destination = staging, PackageId = category, OverwritePackage = false };
                var packaged = await Single(repository, data, client, single, token, reporter, stagedName).ConfigureAwait(false);
                changed |= packaged is { LibraryChanged: true };
                if (packaged is { Cancelled: > 0 }) { Finish("cancelled", ""); continue; }
                if (packaged is not { Error: null, Destination: not null } || !File.Exists(file))
                {
                    Finish("failed", packaged?.Error ?? "无法解析、下载或写入该贴纸包。");
                    continue;
                }
                var stem = PackageStem(title.Substring(0, Math.Min(title.Length, 96)));
                string? published = null;
                for (int suffix = 1; suffix <= 10_000; suffix++)
                {
                    var path = Path.Combine(target, (suffix == 1 ? stem : $"{stem} ({suffix})") + ".zip");
                    if (Directory.Exists(path)) continue;
                    if (RuntimePrimitives.CommitFile(file, path, overwrite: false, token)) { published = path; break; }
                }
                if (published is null) { Finish("failed", "资源包文件重名过多。"); continue; }
                var message = $"已导出：处理成功 {packaged.Count + packaged.Skipped} 张，获取失败跳过 {packaged.Failed} 张。\n{published}";
                if (packaged.Warnings is { Length: > 0 } warnings) message += "\n" + string.Join("\n", warnings);
                Finish("done", message, published);
            }
            catch (OperationCanceledException) when (token.IsCancellationRequested) { Finish("cancelled", ""); }
            catch (Exception) { Finish("failed", "无法解析、下载或写入该贴纸包。"); }
            finally
            {
                if (file is not null) RuntimePrimitives.DiscardTemporaryFile(file);
                if (staging is not null)
                    try { Directory.Delete(staging); } catch (IOException) { }
            }

            void Finish(string status, string message, string? path = null)
            {
                message = client.Redact(message);
                results.Add(new(i, status, message, path));
                reporter.Report(new(status, 0, 0, message));
            }
        }
        return Empty with { Count = results.Count(row => row.Status == "done"), Failed = results.Count(row => row.Status == "failed"),
            Cancelled = results.Count(row => row.Status == "cancelled"), BatchRows = results.ToArray(), LibraryChanged = changed };

        Task<TelegramStickerClient.Pack> Read(int row)
        {
            progress?.Report(new("parse", 0, 0, null, Row: row));
            return TelegramStickerClient.ParseName(links[row]) is { } name ? client.ReadPack(name, token) :
                Task.FromException<TelegramStickerClient.Pack>(new ArgumentException("请输入 Telegram 贴纸包链接或包名"));
        }
    }

    private static Task<string?[]> Fetch(string repository, TelegramStickerClient client, ExternalSticker[] items,
        string format, StickerStaging stage, CancellationToken token, IProgress<LibraryProgress>? progress)
    {
        return Map(items, 3, "download", Work, token, progress);

        async Task<string?> Work(ExternalSticker item, CancellationToken cancel)
        {
            var asset = await client.Download(item.Id, cancel).ConfigureAwait(false);
            if (!Formats.Contains(asset.Format)) return null;
            var selectedFormat = format == "auto" ? item.Animated || IsApng(asset) ? "gif" : "png" : format;
            asset = await ConvertAsset(repository, asset, selectedFormat, cancel).ConfigureAwait(false);
            cancel.ThrowIfCancellationRequested();
            return stage.Write(item.Index, asset);
        }
    }

    private static async Task<T?[]> Map<T>(ExternalSticker[] items, int width, string phase, Func<ExternalSticker, CancellationToken, Task<T?>> work,
        CancellationToken token, IProgress<LibraryProgress>? progress) where T : class
    {
        var results = new T?[items.Length];
        int next = -1, completed = 0;
        var report = new object();
        progress?.Report(new(phase, 0, items.Length, null));
        await Task.WhenAll(Enumerable.Range(0, Math.Min(width, items.Length)).Select(_ => Worker())).ConfigureAwait(false);
        token.ThrowIfCancellationRequested();
        return results;

        async Task Worker()
        {
            while (!token.IsCancellationRequested)
            {
                int index = Interlocked.Increment(ref next);
                if (index >= items.Length) break;
                try { results[index] = await work(items[index], token).ConfigureAwait(false); }
                catch (Exception error) when (error is IOException or HttpRequestException or ArgumentException or NotSupportedException or
                    FormatException or InvalidOperationException or OperationCanceledException or TelegramStickerClient.MissingSticker or
                    System.Runtime.InteropServices.COMException or System.ComponentModel.Win32Exception) { }
                lock (report)
                {
                    completed++;
                    progress?.Report(new(phase, completed, items.Length, null));
                }
            }
        }
    }

    internal static async Task<TelegramStickerClient.Asset> ConvertAsset(string repository, TelegramStickerClient.Asset asset, string format,
        CancellationToken token)
    {
        if (format == "original") return asset;
        if (IsApng(asset))
        {
            using var apng = new MemoryStream(asset.Bytes, writable: false);
            if (format == "gif")
            {
                if (!LegacyAnimatedGif.Available) throw new NotSupportedException();
                return new(LegacyAnimatedGif.FromTelegramApng(apng, token), "gif");
            }
            return new(ApngDecoder.FirstFramePng(apng, token), "png");
        }
        if (asset.Format == "webm")
        {
            if (StickerVideoConverter.Find(repository) is not { } ffmpeg) throw new NotSupportedException();
            return await StickerVideoConverter.Webm(ffmpeg, asset.Bytes, format, token).ConfigureAwait(false);
        }
        if (asset.Format == "tgs")
        {
            if (!NativeLottie.Available || format == "gif" && !LegacyAnimatedGif.Available) throw new NotSupportedException();
            return format == "gif" ? new(NativeLottie.Gif(asset.Bytes, token), "gif")
                : new(StickerPreviewMedia.EncodePng(NativeLottie.FirstFrame(asset.Bytes, token)), "png");
        }
        if (asset.Format == "webp")
        {
            if (!NativeWebp.Available) throw new NotSupportedException();
            using var webp = new MemoryStream(asset.Bytes, writable: false);
            return format == "gif" && NativeWebp.Dimensions(webp, token) is { Animated: true }
                ? new(NativeWebp.Gif(asset.Bytes, token), "gif")
                : new(StickerPreviewMedia.EncodePng(NativeWebp.Still(asset.Bytes, token)), "png");
        }
        return asset;
    }

    private static bool IsApng(TelegramStickerClient.Asset asset)
    {
        if (asset.Format is not ("apng" or "png")) return false;
        using var source = new MemoryStream(asset.Bytes, writable: false);
        return ApngDecoder.HasAnimationControl(source);
    }

    private static ExternalSticker[] Choices(ExternalSticker[]? items) => (items ?? []).Where(item => item.Index is >= 0 and < 10_000)
        .GroupBy(item => item.Index).Select(group => group.Last()).OrderBy(item => item.Index).ToArray();

    private static string? OutputDirectory(string data, string? destination, bool create = false)
    {
        if (destination is null || !RuntimePrimitives.IsAbsolutePath(destination)) return null;
        string root = Directory.Exists(data) ? RuntimePrimitives.ExistingPath(data) : Path.GetFullPath(data);
        if (!Directory.Exists(destination))
        {
            if (!create) return null;
            var desired = Path.GetFullPath(destination);
            string? parent = Path.GetDirectoryName(desired);
            while (parent is not null && !Directory.Exists(parent)) parent = Path.GetDirectoryName(parent);
            if (parent is null) return null;
            var resolved = RuntimePrimitives.ExistingPath(parent);
            var prospective = Path.Combine(resolved, desired.Substring(parent.Length).TrimStart('\\'));
            if (RuntimePrimitives.IsWithin(root, prospective)) return null;
            Directory.CreateDirectory(prospective);
        }
        string target = RuntimePrimitives.ExistingPath(destination);
        return RuntimePrimitives.IsWithin(root, target) ? null : target;
    }

    private static string Normalize(string value) => value.Trim().Normalize(System.Text.NormalizationForm.FormC);

    private static string PackageStem(string value)
    {
        var name = new string(value.Select(c => char.IsControl(c) || Path.GetInvalidFileNameChars().Contains(c) ? '_' : c).ToArray()).TrimEnd(' ', '.');
        if (name.Length == 0) return "package";
        if (System.Text.RegularExpressions.Regex.IsMatch(name.Split('.')[0].TrimEnd(' '),
            @"\A(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])\z",
            System.Text.RegularExpressions.RegexOptions.IgnoreCase | System.Text.RegularExpressions.RegexOptions.CultureInvariant))
            return "_" + name;
        return name;
    }

    private static string? ExistingOutput(string path)
    {
        if (Directory.Exists(path)) throw new IOException("同名路径不是普通 ZIP 文件");
        if (!File.Exists(path)) return null;
        RuntimePrimitives.EnsureNotReparse(path);
        using var file = File.OpenRead(path);
        return RuntimePrimitives.Revision(file.SafeFileHandle);
    }

    private sealed class StickerStaging : IDisposable
    {
        private readonly string folder = Directory.CreateDirectory(Path.Combine(Path.GetTempPath(), $"suzu-tg-{Guid.NewGuid():N}")).FullName;
        private readonly System.Collections.Concurrent.ConcurrentBag<string> files = new();
        internal string Write(int index, TelegramStickerClient.Asset asset)
        {
            var path = Path.Combine(folder, $"{index}.{asset.Format}");
            files.Add(path);
            using var output = File.Open(path, FileMode.CreateNew, FileAccess.Write);
            output.Write(asset.Bytes, 0, asset.Bytes.Length);
            return path;
        }
        public void Dispose()
        {
            foreach (var path in files) RuntimePrimitives.DiscardTemporaryFile(path);
            try { Directory.Delete(folder); } catch (IOException) { }
        }
    }

    private sealed class PackageProgress(IProgress<LibraryProgress>? progress) : IProgress<LibraryProgress>
    {
        public void Report(LibraryProgress value) => progress?.Report(value with { Phase = "export" });
    }

    private sealed class RowProgress(int index, IProgress<LibraryProgress>? progress) : IProgress<LibraryProgress>
    {
        public void Report(LibraryProgress value)
        {
            progress?.Report(value with { Row = index });
        }
    }
}
