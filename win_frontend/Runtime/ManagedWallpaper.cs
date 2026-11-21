using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace SuzuEmojy.Windows;

internal static class ManagedWallpaper
{
    internal const string Folder = "appearance", FileName = "window-background";
    private static readonly LibraryResult Empty = new(0, 0, 0, null, null, null, null);
    private const int MaxEdge = 2560, MaxPixels = 4_000_000, MaxSourcePixels = 64_000_000;

    internal static LibraryResult? TryRun(string data, string? path, bool clear, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        var root = Path.GetFullPath(data);
        if (Directory.Exists(root)) root = RuntimePrimitives.ExistingPath(root);
        var folder = Path.Combine(root, Folder);
        var target = Path.Combine(folder, FileName);
        try
        {
            RuntimePrimitives.EnsureNotReparse(folder);
            if (clear)
            {
                if (Directory.Exists(folder)) File.Delete(target);
                return Empty;
            }
            if (path is null || !RuntimePrimitives.IsAbsolutePath(path) || !File.Exists(path))
                return Empty with { Error = "请选择本地图片" };
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException)
        { return Empty with { Error = "无法保存或移除背景图片" }; }

        BitmapSource picture;
        bool png;
        try
        {
            using var stream = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            var header = new byte[8];
            int read = stream.Read(header, 0, header.Length);
            png = read == 8 && header.AsSpan().SequenceEqual(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 });
            bool jpeg = read >= 3 && header[0] == 255 && header[1] == 216 && header[2] == 255;
            bool webp = NativeWebp.Available && NativeWebp.IsWebp(stream);
            bool gif = read >= 6 && (header.AsSpan(0, 6).SequenceEqual("GIF87a"u8) || header.AsSpan(0, 6).SequenceEqual("GIF89a"u8));
            if (!png && !jpeg && !webp && !gif) return null;
            stream.Position = 0;
            BitmapSource image = webp
                ? NativeWebp.Still(NativeWebp.Read(stream, token), token)
                : BitmapDecoder.Create(stream, BitmapCreateOptions.PreservePixelFormat |
                    BitmapCreateOptions.IgnoreColorProfile, BitmapCacheOption.OnDemand).Frames[0];
            long pixelCount = (long)image.PixelWidth * image.PixelHeight;
            if (pixelCount <= 0 || pixelCount > MaxSourcePixels) return Empty with { Error = "图片尺寸过大" };

            // WIC doesn't expose PNG eXIf consistently across Windows versions.
            // Keep the legacy route when a pre-IDAT PNG orientation tag is present.
            if (png)
            {
                stream.Position = 0;
                if (HasPngExif(stream, token)) return null;
            }
            double scale = Math.Min(1, Math.Min(MaxEdge / (double)Math.Max(image.PixelWidth, image.PixelHeight),
                Math.Sqrt(MaxPixels / (double)pixelCount)));
            png = !jpeg;
            var format = png ? PixelFormats.Pbgra32 : PixelFormats.Bgr24;
            BitmapSource pixels = new FormatConvertedBitmap(image, format, null, 0);
            if (scale < 1)
            {
                // Scales premultiplied PNG pixels so transparent borders don't bleed.
                double width = Math.Max(1, Math.Floor(image.PixelWidth * scale));
                double height = Math.Max(1, Math.Floor(image.PixelHeight * scale));
                pixels = new TransformedBitmap(pixels, new ScaleTransform(width / image.PixelWidth, height / image.PixelHeight));
            }
            ushort orientation = jpeg && image is BitmapFrame jpegFrame ? ReadOrientation(jpegFrame) : (ushort)1;
            picture = Orient(pixels, orientation, token);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or FormatException or
            NotSupportedException or COMException or ArgumentException or InvalidOperationException)
        { return null; } // WIC and Pillow don't accept exactly the same files.

        token.ThrowIfCancellationRequested();
        var temp = Path.Combine(folder, $".background-{Guid.NewGuid():N}.tmp");
        try
        {
            Directory.CreateDirectory(folder);
            RuntimePrimitives.EnsureNotReparse(folder);
            BitmapEncoder encoder = png ? new PngBitmapEncoder() : new JpegBitmapEncoder { QualityLevel = 92 };
            encoder.Frames.Add(BitmapFrame.Create(picture));
            using (var output = new FileStream(temp, FileMode.CreateNew, FileAccess.Write))
            {
                encoder.Save(output);
                output.Flush(flushToDisk: true);
            }
            RuntimePrimitives.CommitFile(temp, target, overwrite: true, token);
            return Empty;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException or COMException)
        { return Empty with { Error = "无法保存或移除背景图片" }; }
        finally { RuntimePrimitives.DiscardTemporaryFile(temp); }
    }

    private static ushort ReadOrientation(BitmapFrame image)
    {
        if (image.Metadata is BitmapMetadata metadata && metadata.GetQuery("/app1/ifd/{ushort=274}") is ushort orientation)
            return orientation;
        return 1;
    }

    private static BitmapSource Orient(BitmapSource source, ushort orientation, CancellationToken token)
    {
        int width = source.PixelWidth, height = source.PixelHeight;
        int bytes = source.Format.BitsPerPixel / 8, stride = width * bytes;
        var original = new byte[stride * height];
        source.CopyPixels(original, stride, 0);
        if (orientation is < 2 or > 8)
            return BitmapSource.Create(width, height, 96, 96, source.Format, null, original, stride);
        bool rotated = orientation >= 5;
        int targetWidth = rotated ? height : width, targetHeight = rotated ? width : height;
        var transformed = new byte[original.Length];
        for (int y = 0; y < height; y++)
        {
            token.ThrowIfCancellationRequested();
            for (int x = 0; x < width; x++)
            {
                var (u, v) = orientation switch
                {
                    2 => (width - 1 - x, y), 3 => (width - 1 - x, height - 1 - y), 4 => (x, height - 1 - y),
                    5 => (y, x), 6 => (height - 1 - y, x), 7 => (height - 1 - y, width - 1 - x), _ => (y, width - 1 - x)
                };
                int from = (y * width + x) * bytes, to = (v * targetWidth + u) * bytes;
                for (int c = 0; c < bytes; c++) transformed[to + c] = original[from + c];
            }
        }
        return BitmapSource.Create(targetWidth, targetHeight, 96, 96, source.Format, null, transformed, targetWidth * bytes);
    }

    private static bool HasPngExif(Stream source, CancellationToken token)
    {
        source.Position = 8;
        var header = new byte[8];
        while (source.Read(header, 0, header.Length) == header.Length)
        {
            token.ThrowIfCancellationRequested();
            if (header.AsSpan(4).SequenceEqual("eXIf"u8)) return true;
            if (header.AsSpan(4).SequenceEqual("IDAT"u8) || header.AsSpan(4).SequenceEqual("IEND"u8)) break;
            var length = System.Buffers.Binary.BinaryPrimitives.ReadUInt32BigEndian(header);
            if (length + 4L > source.Length - source.Position) break;
            source.Seek(length + 4L, SeekOrigin.Current);
        }
        return false;
    }
}
