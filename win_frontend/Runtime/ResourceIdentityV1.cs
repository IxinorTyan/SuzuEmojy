using System;
using System.Buffers.Binary;
using System.IO;
using System.Security.Cryptography;
using System.Threading;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace SuzuEmojy.Windows;

// null means not supported by this implementation; it does not mean an invalid resource.
internal static class ResourceIdentityV1
{
    private sealed record Layout(int Width, int Height, byte Depth, byte Color, byte[]? Palette, byte[]? Transparency, long AlphaOffset);
    internal static string? TryRead(string path, CancellationToken cancellation)
    {
        cancellation.ThrowIfCancellationRequested();
        try
        {
            using var source = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            return TryRead(source, cancellation);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or FormatException or NotSupportedException)
        { return null; }
    }

    internal static string? TryRead(Stream source, CancellationToken cancellation)
    {
        cancellation.ThrowIfCancellationRequested();
        try
        {
            source.Position = 0;
            var header = new byte[8];
            if (Fill(source, header) < 6) return null;
            using var digest = MD5.Create();
            if (Tag(header, 0, "GIF87a") || Tag(header, 0, "GIF89a"))
            {
                // v1 identifies GIFs by file bytes, including metadata, not by visual appearance.
                source.Position = 0;
                var bytes = new byte[64 * 1024];
                int used;
                while ((used = source.Read(bytes, 0, bytes.Length)) > 0)
                {
                    cancellation.ThrowIfCancellationRequested();
                    digest.TransformBlock(bytes, 0, used, null, 0);
                }
                return Finish(digest, "f:");
            }

            if (BinaryPrimitives.ReadUInt64BigEndian(header) != 0x89504e470d0a1a0a ||
                DecodePngBody(source, cancellation) is not { } image) return null;
            return HashPixels(image, cancellation);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or FormatException or NotSupportedException)
        { return null; }
    }

    internal static string HashPixels(BitmapSource image, CancellationToken cancellation)
    {
        using var digest = MD5.Create();
        int width = image.PixelWidth, height = image.PixelHeight;
        int stride = checked(width * 4);
        int rows = Math.Max(1, Math.Min(height, (64 * 1024) / stride));
        var pixels = new byte[stride * rows];
        for (int y = 0; y < height; y += rows)
        {
            cancellation.ThrowIfCancellationRequested();
            int heightInBuffer = Math.Min(rows, height - y);
            image.CopyPixels(new Int32Rect(0, y, width, heightInBuffer), pixels, stride, 0);
            int bytesInBuffer = heightInBuffer * stride;
            for (int i = 0; i < bytesInBuffer; i += 4)
                (pixels[i], pixels[i + 2]) = (pixels[i + 2], pixels[i]);
            digest.TransformBlock(pixels, 0, bytesInBuffer, null, 0);
        }
        return Finish(digest, "p:");
    }

    internal static BitmapSource? DecodePng(Stream source, CancellationToken token)
    {
        source.Position = 0;
        var header = new byte[8];
        return Fill(source, header) == 8 && BinaryPrimitives.ReadUInt64BigEndian(header) == 0x89504e470d0a1a0a
            ? DecodePngBody(source, token) : null;
    }

    internal static BitmapSource? DecodeOtherStill(Stream source, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        if (QqCacheScanner.SignatureAtStart(source) is not ("jpg" or "bmp" or "tiff")) return null;
        source.Position = 0;
        var decoder = BitmapDecoder.Create(source, BitmapCreateOptions.PreservePixelFormat |
            BitmapCreateOptions.IgnoreColorProfile, BitmapCacheOption.OnDemand);
        if (decoder.Frames.Count != 1) return null;
        var frame = decoder.Frames[0];
        if ((long)frame.PixelWidth * frame.PixelHeight > 16_000_000) return null;
        return new FormatConvertedBitmap(frame, PixelFormats.Bgra32, null, 0);
    }

    private static BitmapSource? DecodePngBody(Stream source, CancellationToken token)
    {
        if (InspectPng(source, token) is not { } layout) return null;
        var (width, height, depth, color, palette, transparency, alphaOffset) = layout;
        source.Position = 0;
        // A few WIC decoders expose a premultiplied frame for PNG tRNS. Give
        // WIC the opaque samples and apply the file's transparency ourselves.
        using var opaque = transparency is not null ? WithoutTransparency(source, alphaOffset, transparency.Length, token) : null;
        var decoder = new PngBitmapDecoder(opaque ?? source,
            BitmapCreateOptions.PreservePixelFormat | BitmapCreateOptions.IgnoreColorProfile,
            opaque is null ? BitmapCacheOption.OnDemand : BitmapCacheOption.OnLoad);
        // A Pbgra32 result has already lost hidden RGB and rounding precision.
        if (decoder.Frames.Count != 1 || decoder.Frames[0] is not { } frame ||
            frame.PixelWidth != width || frame.PixelHeight != height) return null;
        if (color == 3)
        {
            if (frame.Format == PixelFormats.Indexed1 || frame.Format == PixelFormats.Indexed2 ||
                frame.Format == PixelFormats.Indexed4 || frame.Format == PixelFormats.Indexed8)
                return ExpandPalette(frame, palette!, transparency, token);
            // Some WIC versions expand tRNS internally to straight BGRA.
            // Until the hidden RGB can be verified, keep that driver on the legacy route.
            return transparency is null && frame.Format == PixelFormats.Bgra32 ? frame : null;
        }
        if (color is 4 or 6) return frame.Format == PixelFormats.Bgra32 ? frame : null;
        if (frame.Format == PixelFormats.Pbgra32) return null;
        if (frame.Format != PixelFormats.Bgra32 && frame.Format != PixelFormats.Bgr24 && frame.Format != PixelFormats.Rgb24 &&
            frame.Format != PixelFormats.Bgr32 && frame.Format != PixelFormats.BlackWhite &&
            frame.Format != PixelFormats.Gray2 && frame.Format != PixelFormats.Gray4 && frame.Format != PixelFormats.Gray8)
            return null;
        BitmapSource converted = frame.Format == PixelFormats.Bgra32 ? frame : new FormatConvertedBitmap(frame, PixelFormats.Bgra32, null, 0);
        if (transparency is null) return converted;
        if (depth != 8 || transparency.Length != (color == 2 ? 6 : 2)) return null;
        int r = BinaryPrimitives.ReadUInt16BigEndian(transparency);
        int g = color == 2 ? BinaryPrimitives.ReadUInt16BigEndian(transparency.AsSpan(2)) : r;
        int b = color == 2 ? BinaryPrimitives.ReadUInt16BigEndian(transparency.AsSpan(4)) : r;
        var pixels = new byte[width * height * 4];
        converted.CopyPixels(pixels, width * 4, 0);
        for (int i = 0; i < pixels.Length; i += 4)
        {
            if ((i & 65535) == 0) token.ThrowIfCancellationRequested();
            if (pixels[i] != b || pixels[i + 1] != g || pixels[i + 2] != r) continue;
            if (r > 255 || g > 255 || b > 255) return null;
            pixels[i] = (byte)b;
            pixels[i + 1] = (byte)g;
            pixels[i + 2] = (byte)r;
            pixels[i + 3] = 0;
        }
        return BitmapSource.Create(width, height, 96, 96, PixelFormats.Bgra32, null, pixels, width * 4);
    }

    private static Stream WithoutTransparency(Stream original, long offset, int length, CancellationToken token)
    {
        // Preserve sample bytes without holding a large compressed PNG in memory.
        Stream result = original.Length < (8L << 20)
            ? new MemoryStream((int)original.Length - length - 12)
            : new FileStream(Path.Combine(Path.GetTempPath(), $"suzu-png-{Guid.NewGuid():N}.part"),
                FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None, 64 * 1024, FileOptions.DeleteOnClose);
        try
        {
            var buffer = new byte[64 * 1024];
            Copy(offset);
            original.Seek(length + 12, SeekOrigin.Current);
            Copy(original.Length - original.Position);
            result.Position = 0;
            return result;

            void Copy(long count)
            {
                while (count > 0)
                {
                    token.ThrowIfCancellationRequested();
                    int next = original.Read(buffer, 0, (int)Math.Min(buffer.Length, count));
                    if (next == 0) throw new EndOfStreamException();
                    result.Write(buffer, 0, next);
                    count -= next;
                }
            }
        }
        catch { result.Dispose(); throw; }
    }

    private static BitmapSource? ExpandPalette(BitmapSource indexed, byte[] rgb, byte[]? alpha, CancellationToken token)
    {
        int width = indexed.PixelWidth, height = indexed.PixelHeight, bits = indexed.Format.BitsPerPixel;
        int stride = (width * bits + 7) / 8;
        var source = new byte[stride * height];
        var pixels = new byte[width * height * 4];
        indexed.CopyPixels(source, stride, 0);
        int mask = (1 << bits) - 1;
        for (int y = 0; y < height; y++)
        {
            token.ThrowIfCancellationRequested();
            for (int x = 0; x < width; x++)
            {
                int bit = x * bits;
                int value = (source[y * stride + bit / 8] >> (8 - bits - bit % 8)) & mask;
                int into = (y * width + x) * 4, from = value * 3;
                if (from + 2 >= rgb.Length) return null;
                pixels[into] = rgb[from + 2];
                pixels[into + 1] = rgb[from + 1];
                pixels[into + 2] = rgb[from];
                pixels[into + 3] = alpha is { } alphas && value < alphas.Length ? alphas[value] : (byte)255;
            }
        }
        return BitmapSource.Create(width, height, 96, 96, PixelFormats.Bgra32, null, pixels, width * 4);
    }

    private static Layout? InspectPng(Stream file, CancellationToken cancellation)
    {
        var chunk = new byte[8];
        var ihdr = new byte[17]; // 13 data + 4 CRC
        if (Fill(file, chunk) != chunk.Length || !Tag(chunk, 4, "IHDR") || BinaryPrimitives.ReadUInt32BigEndian(chunk) != 13 ||
            Fill(file, ihdr) != ihdr.Length) return null;
        byte depth = ihdr[8], color = ihdr[9];
        if ((color is 0 or 3 ? depth is not (1 or 2 or 4 or 8) : depth != 8) ||
            color is not (0 or 2 or 3 or 4 or 6)) return null;
        int width = BinaryPrimitives.ReadInt32BigEndian(ihdr);
        int height = BinaryPrimitives.ReadInt32BigEndian(ihdr.AsSpan(4));
        if (width <= 0 || height <= 0 || (long)width * height > 16_000_000) return null;
        bool data = false;
        byte[]? palette = null, transparency = null;
        long alphaOffset = 0;
        for (int i = 0; i < 4096 && Fill(file, chunk) == chunk.Length; i++)
        {
            cancellation.ThrowIfCancellationRequested();
            long length = BinaryPrimitives.ReadUInt32BigEndian(chunk);
            if (length + 4 > file.Length - file.Position || Tag(chunk, 4, "acTL") || Tag(chunk, 4, "fcTL") ||
                Tag(chunk, 4, "fdAT"))
                return null;
            if (Tag(chunk, 4, "IEND")) return length == 0 && data &&
                (color != 3 || palette is not null) ? new(width, height, depth, color, palette, transparency, alphaOffset) : null;
            if (Tag(chunk, 4, "PLTE") && color == 3 || Tag(chunk, 4, "tRNS"))
            {
                bool alpha = Tag(chunk, 4, "tRNS");
                if (data || length < 1 || length > (alpha ? 256 : 768) ||
                    (alpha ? transparency is not null || color is 4 or 6 : palette is not null || length % 3 != 0))
                    return null;
                var contents = new byte[length];
                long start = file.Position - chunk.Length;
                if (Fill(file, contents) != contents.Length) return null;
                if (alpha) { transparency = contents; alphaOffset = start; }
                else palette = contents;
                file.Seek(4, SeekOrigin.Current);
                continue;
            }
            if (Tag(chunk, 4, "IDAT")) data = true;
            file.Seek(length + 4, SeekOrigin.Current);
        }
        return null;
    }

    private static int Fill(Stream file, byte[] buffer)
    {
        int at = 0;
        while (at < buffer.Length)
        {
            int read = file.Read(buffer, at, buffer.Length - at);
            if (read == 0) break;
            at += read;
        }
        return at;
    }

    private static bool Tag(byte[] bytes, int offset, string tag)
    {
        for (int i = 0; i < tag.Length; i++)
            if (bytes[offset + i] != tag[i]) return false;
        return true;
    }

    private static string Finish(HashAlgorithm digest, string prefix)
    {
        digest.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
        return prefix + BitConverter.ToString(digest.Hash!).Replace("-", "").ToLowerInvariant();
    }
}
