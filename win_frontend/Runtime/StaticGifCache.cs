using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace SuzuEmojy.Windows;

internal static class StaticGifCache
{
    internal static string? TryPrepare(string source, string cache, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        try
        {
            using var original = File.Open(source, FileMode.Open, FileAccess.Read, FileShare.Read);
            var signature = new byte[12];
            int read = original.Read(signature, 0, signature.Length);
            original.Position = 0;
            if (read >= 6 && (signature.AsSpan(0, 6).SequenceEqual("GIF87a"u8) || signature.AsSpan(0, 6).SequenceEqual("GIF89a"u8)))
                return default;
            bool png = read >= 8 && signature.AsSpan(0, 8).SequenceEqual(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 });
            bool jpeg = read >= 3 && signature[0] == 255 && signature[1] == 216 && signature[2] == 255;
            bool webp = NativeWebp.Available && NativeWebp.IsWebp(original);
            if (!png && !jpeg && !webp) return null;
            if (png && ApngDecoder.HasAnimationControl(original)) return default;
            if (webp && NativeWebp.Dimensions(original, token) is not { Animated: false }) return default;
            original.Position = 0;

            string key = $"native-send-v1:{Path.GetFullPath(source)}:{RuntimePrimitives.Revision(original.SafeFileHandle)}";
            using var sha = SHA256.Create();
            var digest = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(key))).Replace("-", "").ToLowerInvariant();
            string target = Path.Combine(cache, digest + ".gif");
            if (File.Exists(target) && new FileInfo(target).Length > 0) return target;
            var frame = webp ? NativeWebp.Still(NativeWebp.Read(original, token), token)
                : BitmapDecoder.Create(original, BitmapCreateOptions.PreservePixelFormat |
                    BitmapCreateOptions.IgnoreColorProfile, BitmapCacheOption.OnDemand).Frames[0];
            int width = frame.PixelWidth, height = frame.PixelHeight;
            if (width < 1 || height < 1 || width > ushort.MaxValue || height > ushort.MaxValue || (long)width * height > 16_000_000)
                return null;
            var bgra = new FormatConvertedBitmap(frame, PixelFormats.Bgra32, null, 0);
            var pixels = new byte[width * height * 4];
            bgra.CopyPixels(pixels, width * 4, 0);
            var transparent = new System.Collections.BitArray(width * height);
            var indices = new byte[width * height];
            var exact = new Dictionary<int, byte>();
            var exactColors = new List<Color>();
            bool overflow = false;
            for (int i = 0; i < transparent.Length; i++)
            {
                if ((i & 65535) == 0) token.ThrowIfCancellationRequested();
                int offset = i * 4;
                if (pixels[offset + 3] < 128)
                {
                    transparent[i] = true;
                    indices[i] = 255;
                    pixels[offset] = pixels[offset + 1] = pixels[offset + 2] = 0;
                }
                else if (!overflow)
                {
                    byte b = pixels[offset], g = pixels[offset + 1], r = pixels[offset + 2];
                    int packed = (r << 16) | (g << 8) | b;
                    if (exact.TryGetValue(packed, out byte index)) indices[i] = index;
                    else if (exact.Count == 255) overflow = true;
                    else
                    {
                        indices[i] = (byte)exact.Count;
                        exact.Add(packed, indices[i]);
                        exactColors.Add(Color.FromRgb(r, g, b));
                    }
                }
                pixels[offset + 3] = 255;
            }
            // Ignore hidden RGB when making the visible palette. Index 255 is
            // reserved solely for binary transparency and never quantized.
            IList<Color> colors = exactColors;
            if (overflow)
            {
                var opaque = BitmapSource.Create(width, height, 96, 96, PixelFormats.Bgr32, null, pixels, width * 4);
                var palette = new BitmapPalette(opaque, 255);
                var indexed = new FormatConvertedBitmap(opaque, PixelFormats.Indexed8, palette, 0);
                indexed.CopyPixels(indices, width, 0);
                for (int i = 0; i < indices.Length; i++)
                    if (transparent[i]) indices[i] = 255;
                colors = palette.Colors;
            }
            token.ThrowIfCancellationRequested();

            Directory.CreateDirectory(cache);
            var temporary = Path.Combine(cache, $".gif-{Guid.NewGuid():N}.tmp");
            try
            {
                using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write))
                {
                    WriteGif(output, (ushort)width, (ushort)height, colors, indices, token);
                    output.Flush(flushToDisk: true);
                }
                if (!RuntimePrimitives.CommitFile(temporary, target, overwrite: false, token) && new FileInfo(target).Length == 0)
                    return default;
                return target;
            }
            finally { RuntimePrimitives.DiscardTemporaryFile(temporary); }
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or NotSupportedException or FormatException or
            ArgumentException or InvalidOperationException or COMException)
        { return null; }
    }

    private static void WriteGif(Stream output, ushort width, ushort height, IList<Color> colors, byte[] pixels, CancellationToken token)
    {
        using var writer = new BinaryWriter(output, Encoding.ASCII, leaveOpen: true);
        writer.Write("GIF89a"u8.ToArray());
        writer.Write(width);
        writer.Write(height);
        writer.Write(new byte[] { 0xf7, 255, 0 }); // 256-entry global palette.
        for (int i = 0; i < 256; i++)
        {
            var color = i < colors.Count ? colors[i] : Color.FromRgb(0, 0, 0);
            writer.Write(color.R); writer.Write(color.G); writer.Write(color.B);
        }
        writer.Write(new byte[] { 0x21, 0xf9, 4, 0x09, 0, 0, 255, 0 }); // Disposal to background; transparent index 255.
        writer.Write((byte)0x2c);
        writer.Write(0u); // Left/top.
        writer.Write(width);
        writer.Write(height);
        writer.Write((byte)0);
        WriteImageData(writer, pixels, token);
        writer.Write((byte)0x3b);
    }

    internal static void WriteImageData(BinaryWriter writer, byte[] pixels, CancellationToken token)
    {
        writer.Write((byte)8); // LZW minimum code size.

        const int clear = 256, end = 257, max = 4096;
        int next = 258, bits = 9, pending = 0, pendingBits = 0, position = 0;
        var block = new byte[255];
        var table = new Dictionary<int, int>(4096);
        Emit(clear);
        int prefix = pixels[0];
        for (int i = 1; i < pixels.Length; i++)
        {
            if ((i & 65535) == 0) token.ThrowIfCancellationRequested();
            int symbol = pixels[i], pair = (prefix << 8) | symbol;
            if (table.TryGetValue(pair, out int code)) { prefix = code; continue; }
            Emit(prefix);
            if (next < max) table.Add(pair, next++);
            else
            {
                Emit(clear);
                next = 258;
                bits = 9;
                table.Clear();
            }
            prefix = symbol;
        }
        Emit(prefix);
        Emit(end);
        if (pendingBits > 0) Byte((byte)pending);
        FlushBlock();
        writer.Write((byte)0); // End of image data.

        void Emit(int code)
        {
            pending |= code << pendingBits;
            pendingBits += bits;
            while (pendingBits >= 8)
            {
                Byte((byte)pending);
                pending >>= 8;
                pendingBits -= 8;
            }
            // The decoder adds the previous pair after reading this code.
            if (bits < 12 && next == (1 << bits)) bits++;
        }

        void Byte(byte item)
        {
            block[position++] = item;
            if (position == block.Length) FlushBlock();
        }

        void FlushBlock()
        {
            if (position == 0) return;
            writer.Write((byte)position);
            writer.Write(block, 0, position);
            position = 0;
        }
    }
}
