using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Threading;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace SuzuEmojy.Windows;

// Direct local PNGs and pass-through animated GIFs use the same transaction
// as v1 ZIP imports; only the byte preparation and UI semantics differ.
internal static class LocalImageImport
{
    internal static LibraryResult? TryRun(string data, string[] paths, string? target,
        CancellationToken token, IProgress<LibraryProgress>? progress, string? ffmpeg = null, Action<string>? rejected = null)
    {
        token.ThrowIfCancellationRequested();
        foreach (var path in paths)
            if (!RuntimePrimitives.IsAbsolutePath(path)) throw new ArgumentException("请选择有效的图片路径。");
        var scan = LocalFolderImport.Run(paths, target, token, progress);
        return TryRunItems(data, scan, token, progress, ffmpeg, rejected);
    }

    internal static LibraryResult? TryRunItems(string data, LocalFolderImport.Scan scan,
        CancellationToken token, IProgress<LibraryProgress>? progress, string? ffmpeg = null, Action<string>? rejected = null)
    {
        var empty = new LibraryResult(0, 0, 0, null, [], null, scan.Stats);
        if (token.IsCancellationRequested) return empty with { Cancelled = 1 };
        if (scan.Items.Length == 0) return empty;
        var categoryIds = new Dictionary<string, int>(StringComparer.Ordinal);
        var groups = new List<IngestCategory>();
        var working = Path.Combine(Path.GetTempPath(), $"suzu-native-import-{Guid.NewGuid():N}");
        var files = new List<string>();
        var items = new List<IngestResource>();
        var warnings = new List<string>();
        int failed = 0;
        bool cancelled = false;
        try
        {
            Directory.CreateDirectory(working);
            progress?.Report(new("import", 0, scan.Items.Length, null));
            foreach (var entry in scan.Items)
            {
                if (token.IsCancellationRequested) { cancelled = true; break; }
                var temp = Path.Combine(working, $"{files.Count}.part");
                files.Add(temp);
                try
                {
                    if (Prepare(entry.Path, temp, [], token, ffmpeg) is not { } item)
                    {
                        failed++;
                        rejected?.Invoke(entry.Path);
                        if (warnings.Count < 5) warnings.Add($"{Path.GetFileName(entry.Path)}：无法清洗此图片");
                        continue;
                    }
                    if (entry.Category is { } name)
                    {
                        if (!categoryIds.TryGetValue(name, out int id))
                        {
                            id = categoryIds.Count + 1;
                            categoryIds.Add(name, id);
                            groups.Add(new(id, name));
                        }
                        item = item with { Categories = [id] };
                    }
                    items.Add(item);
                }
                catch (OperationCanceledException) when (token.IsCancellationRequested)
                { cancelled = true; break; }
            }
            if (items.Count == 0)
            {
                progress?.Report(new("import", failed, scan.Items.Length, null));
                return empty with { Cancelled = cancelled ? 1 : 0, Failed = failed,
                    FolderStats = Stats(), Warnings = warnings.ToArray() };
            }
            var imported = LibraryResourceIngest.TryRun(data, new(items.ToArray(), groups.ToArray(), Promote: true),
                cancelled ? CancellationToken.None : token, new IntakeProgress(progress, failed, scan.Items.Length));
            return imported is null ? null : imported with { FolderStats = Stats(), Cancelled = cancelled ? 1 : imported.Cancelled,
                Failed = imported.Failed + failed, Warnings = [.. imported.Warnings ?? [], .. warnings] };

            FolderImportStats? Stats() => scan.Stats is { } stats ? stats with
            {
                Issues = [.. System.Linq.Enumerable.Take(System.Linq.Enumerable.Concat(stats.Issues, warnings), 10)],
                IssueCount = stats.IssueCount + failed
            } : null;
        }
        finally
        {
            foreach (var file in files) RuntimePrimitives.DiscardTemporaryFile(file);
            try { if (Directory.Exists(working)) Directory.Delete(working); }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
        }
    }

    private sealed class IntakeProgress(IProgress<LibraryProgress>? target, int rejected, int total) : IProgress<LibraryProgress>
    {
        public void Report(LibraryProgress value) => target?.Report(value with { Current = value.Current + rejected, Total = total });
    }

    private static IngestResource? Prepare(string original, string staged, int[] categories, CancellationToken token,
        string? ffmpeg = null, bool keepGif = false)
    {
        try
        {
            using var input = File.Open(original, FileMode.Open, FileAccess.Read, FileShare.Read);
            if (ffmpeg is not null && StickerVideoConverter.IsWebm(input))
                return StickerVideoConverter.Clean(ffmpeg, original, (output, animated) =>
                    Prepare(output, staged, categories, token, keepGif: animated), token);
            if (LegacyAnimatedGif.Available)
            {
                if (NativeLottie.Available && NativeLottie.LooksCompressed(input))
                    return PrepareGif(NativeLottie.Gif(NativeWebp.Read(input, token), token), keep: false);
                if (ApngDecoder.HasAnimationControl(input))
                    return PrepareGif(LegacyAnimatedGif.FromApng(input, token), keep: true);
                input.Position = 0;
            }
            BitmapSource? image = null;
            if (NativeWebp.Available && NativeWebp.IsWebp(input))
            {
                var bytes = NativeWebp.Read(input, token);
                if (NativeWebp.Dimensions(input, token) is { Animated: true })
                    return PrepareGif(NativeWebp.Gif(bytes, token), keep: true);
                image = NativeWebp.Still(bytes, token);
            }
            var key = image is null ? ResourceIdentityV1.TryRead(input, token) : ResourceIdentityV1.HashPixels(image, token);
            if (key is null)
            {
                image = ResourceIdentityV1.DecodeOtherStill(input, token);
                if (image is null) return null;
                key = ResourceIdentityV1.HashPixels(image, token);
            }
            bool animated = key.StartsWith("f:", StringComparison.Ordinal);
            input.Position = 0;
            if (animated)
            {
                var gif = new GifBitmapDecoder(input, BitmapCreateOptions.PreservePixelFormat, BitmapCacheOption.OnDemand);
                if (gif.Frames.Count == 1 && !keepGif)
                {
                    image = SingleFrameGif.Decode(input, gif.Frames[0], token);
                    if (image is null) return null;
                    key = ResourceIdentityV1.HashPixels(image, token);
                    animated = false;
                }
                else if (gif.Frames.Count == 0) return null;
            }
            using var output = new FileStream(staged, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None);
            if (animated)
            {
                input.Position = 0;
                var bytes = new byte[128 * 1024];
                int count;
                while ((count = input.Read(bytes, 0, bytes.Length)) != 0)
                {
                    token.ThrowIfCancellationRequested();
                    output.Write(bytes, 0, count);
                }
            }
            else
            {
                image ??= ResourceIdentityV1.DecodePng(input, token);
                if (image is null) return null;
                int stride = image.PixelWidth * 4;
                var pixels = new byte[stride * image.PixelHeight];
                image.CopyPixels(pixels, stride, 0);
                token.ThrowIfCancellationRequested();
                // Create a new frame, not an encoder copy of the source.
                // EXIF, text chunks, and profiles stay outside the clean image.
                var clean = BitmapSource.Create(image.PixelWidth, image.PixelHeight, 96, 96, PixelFormats.Bgra32, null, pixels, stride);
                var encoder = new PngBitmapEncoder();
                encoder.Frames.Add(BitmapFrame.Create(clean));
                encoder.Save(output);
                output.Flush();
                // The final encoding must not change the actual v1 identity.
                if (ResourceIdentityV1.TryRead(output, token) != key) return null;
            }
            output.Flush();
            output.Position = 0;
            using var md5 = MD5.Create();
            var buffer = new byte[128 * 1024];
            int read;
            uint crc = Crc32.Start;
            while ((read = output.Read(buffer, 0, buffer.Length)) != 0)
            {
                token.ThrowIfCancellationRequested();
                crc = Crc32.Update(crc, buffer.AsSpan(0, read));
                md5.TransformBlock(buffer, 0, read, null, 0);
            }
            md5.TransformFinalBlock([], 0, 0);
            string fileHash = BitConverter.ToString(md5.Hash!).Replace("-", "").ToLowerInvariant();
            if (animated && key != "f:" + fileHash) return null;
            string name = DateTime.Now.ToString("yyyyMMddHHmmssffffff", CultureInfo.InvariantCulture) + (animated ? ".gif" : ".png");
            return new(new(output.Length, Crc32.Finish(crc), () => File.OpenRead(staged)), name, key, fileHash, 0, [], categories);

            IngestResource? PrepareGif(byte[] bytes, bool keep)
            {
                var temporary = staged + ".gif";
                try
                {
                    using (var converted = File.Open(temporary, FileMode.CreateNew, FileAccess.Write))
                        converted.Write(bytes, 0, bytes.Length);
                    return Prepare(temporary, staged, categories, token, keepGif: keep);
                }
                finally { RuntimePrimitives.DiscardTemporaryFile(temporary); }
            }
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or FormatException or
            NotSupportedException or ArgumentException or InvalidOperationException or System.ComponentModel.Win32Exception
            or System.Runtime.InteropServices.COMException)
        { return null; }
    }
}
