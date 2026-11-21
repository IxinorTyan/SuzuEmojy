using System;
using System.Buffers.Binary;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace SuzuEmojy.Windows.Desktop;

internal sealed record ClipboardMedia(string Path, byte[]? DibV5);

internal sealed class StickerSendMedia(string cache, bool convertStatic)
{
    internal async Task<ClipboardMedia> Prepare(string original, CancellationToken cancellation)
    {
        string path = original;
        var extension = Path.GetExtension(path).ToLowerInvariant();
        if (convertStatic && extension is ".png" or ".jpg" or ".jpeg" or ".webp")
        {
            try
            {
                var native = await Task.Run(() => StaticGifCache.TryPrepare(original, cache, cancellation), cancellation).ConfigureAwait(false);
                if (native is not null) path = native;
            }
            catch (Exception error) when (error is Win32Exception or IOException or TimeoutException or
                InvalidOperationException)
            {
                Trace.TraceWarning("Static GIF preparation failed: {0}", error.Message);
            }
        }
        cancellation.ThrowIfCancellationRequested();
        byte[]? bitmap = await Task.Run(() =>
        {
            try
            {
                var decoded = MediaThumbnail.Load(path, 2048);
                return decoded is null ? null : DibPayload(decoded);
            }
            catch (Exception error) when (error is ArgumentException or InvalidOperationException or
                NotSupportedException or COMException)
            {
                return null;
            }
        }, cancellation).ConfigureAwait(false);
        return new ClipboardMedia(File.Exists(path) ? path : original, bitmap);
    }

    internal static byte[] DibPayload(BitmapSource bitmap)
    {
        const int header = 124;
        var pixels = new FormatConvertedBitmap(bitmap, PixelFormats.Bgra32, null, 0);
        int stride = checked(bitmap.PixelWidth * 4);
        int size = checked(stride * bitmap.PixelHeight);
        var payload = new byte[header + size];
        void Write(int offset, uint value) => BinaryPrimitives.WriteUInt32LittleEndian(payload.AsSpan(offset, 4), value);
        Write(0, header);
        Write(4, (uint)bitmap.PixelWidth);
        Write(8, unchecked((uint)-bitmap.PixelHeight)); // top-down rows
        Write(12, 1u | (32u << 16)); // one plane, 32 bits per pixel
        Write(16, 3); // BI_BITFIELDS
        Write(20, (uint)size);
        Write(40, 0x00FF0000); // R, G, B and straight alpha masks
        Write(44, 0x0000FF00);
        Write(48, 0x000000FF);
        Write(52, 0xFF000000);
        Write(56, 0x73524742); // LCS_sRGB
        Write(108, 4); // LCS_GM_IMAGES
        pixels.CopyPixels(payload, stride, header);
        return payload;
    }
}
