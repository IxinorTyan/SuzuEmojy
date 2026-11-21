using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using SuzuEmojy.Windows.Controls;

namespace SuzuEmojy.Windows;

// WIC decodes only the static PNG patches. The canvas and APNG-specific
// sequencing, disposal and alpha composition are independent of WIC.
internal static class ApngDecoder
{
    private const uint Ihdr = 0x49484452, Plte = 0x504c5445, Trns = 0x74524e53, Actl = 0x6163544c,
        Fctl = 0x6663544c, Idat = 0x49444154, Fdat = 0x66644154, Iend = 0x49454e44;
    private const int MaxInput = 48 * 1024 * 1024, MaxFrames = 600, PixelBudget = 4_000_000, SourcePixels = 16_000_000;
    private static readonly byte[] Signature = [137, 80, 78, 71, 13, 10, 26, 10];
    private readonly record struct FrameControl(int Width, int Height, int X, int Y, double Delay, byte Dispose, byte Blend);

    // Same positive test as the legacy startup indexer: acTL must precede
    // IDAT, carry a positive frame count, and have a valid checksum.
    internal static bool HasAnimationControl(Stream source)
    {
        var header = new byte[8];
        if (!Fill(source, header, 8) || !header.AsSpan().SequenceEqual(Signature)) return false;
        while (Fill(source, header, 8))
        {
            uint length = U32(header, 0), kind = U32(header, 4);
            if (kind is Idat or Iend || length > int.MaxValue || length + 4L > source.Length - source.Position) return false;
            if (kind == Actl)
            {
                var checksum = new byte[4];
                return length == 8 && Fill(source, header, 8) && U32(header, 0) > 0 &&
                    Fill(source, checksum, 4) && Crc(Actl, header) == U32(checksum, 0);
            }
            source.Seek(length + 4L, SeekOrigin.Current);
        }
        return false;
    }

    // Non-PNG input is left to the current WebP decoder. A static PNG is a
    // valid no-animation result, so it never starts a process.
    internal static bool TryPreview(string path, int maxSide, CancellationToken token, out PreviewAnimation? animation)
    {
        using var source = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        return TryPreview(source, maxSide, token, out animation);
    }

    internal static bool TryPreview(Stream source, int maxSide, CancellationToken token, out PreviewAnimation? animation,
        bool firstOnly = false)
    {
        token.ThrowIfCancellationRequested();
        animation = null;
        var signature = new byte[8];
        if (!Fill(source, signature, signature.Length) || !signature.AsSpan().SequenceEqual(Signature)) return false;
        if (source.Length > MaxInput) return true;
        var frames = new List<BitmapSource>();
        var delays = new List<int>();
        int animationPlays = 1;
        Decode(source, token, plays => animationPlays = plays, Consume, firstOnly);
        if (frames.Count > 0) animation = new(null, animationPlays, new PreviewFrames(frames.ToArray(), delays.ToArray(), animationPlays));
        return true;

        void Consume(byte[] canvas, int width, int height, int count, double delay)
        {
            int edge = Math.Min(Math.Min(800, Math.Max(1, maxSide)), (int)Math.Sqrt(PixelBudget / count));
            double scale = Math.Min(1, edge / (double)Math.Max(width, height));
            BitmapSource picture = BitmapSource.Create(width, height, 96, 96, PixelFormats.Bgra32, null, canvas, width * 4);
            if (scale < 1)
            {
                var transform = new ScaleTransform(Math.Max(Math.Round(width * scale), 1) / width,
                    Math.Max(Math.Round(height * scale), 1) / height);
                // Scale premultiplied pixels so RGB hidden under alpha cannot
                // bleed into the visible edge, then keep only the small result.
                var premul = new FormatConvertedBitmap(picture, PixelFormats.Pbgra32, null, 0);
                var small = new TransformedBitmap(premul, transform);
                var stride = small.PixelWidth * 4;
                var pixels = new byte[stride * small.PixelHeight];
                small.CopyPixels(pixels, stride, 0);
                picture = BitmapSource.Create(small.PixelWidth, small.PixelHeight, 96, 96, PixelFormats.Pbgra32, null, pixels, stride);
            }
            picture.Freeze();
            frames.Add(picture);
            delays.Add(Math.Max(20, (int)Math.Round(delay == 0 ? 40 : delay)));
        }
    }

    internal static void ReadFullFrames(Stream source, CancellationToken token, Action<int> plays,
        Action<byte[], int, int, int, double> frame, bool firstOnly = false)
    {
        source.Position = 0;
        var signature = new byte[8];
        if (source.Length > MaxInput || !Fill(source, signature, signature.Length) || !signature.AsSpan().SequenceEqual(Signature))
            throw Invalid();
        Decode(source, token, plays, frame, firstOnly);
    }

    internal static byte[] FirstFramePng(Stream source, CancellationToken token)
    {
        byte[]? result = null;
        ReadFullFrames(source, token, _ => { }, (canvas, w, h, _, _) =>
            result = StickerPreviewMedia.EncodePng(BitmapSource.Create(w, h, 96, 96, PixelFormats.Bgra32, null, canvas, w * 4)), true);
        return result ?? throw Invalid();
    }

    private static void Decode(Stream source, CancellationToken token, Action<int> metadata,
        Action<byte[], int, int, int, double> onFrame, bool firstOnly)
    {
        var header = new byte[8];
        var crc = new byte[4];
        var pngHeader = new byte[13];
        var palette = new List<(uint Type, byte[] Data)>();
        var scratch = Array.Empty<byte>();
        byte[]? canvas = null;
        FrameControl? current = null;
        using var patchPng = new MemoryStream();
        int width = 0, height = 0, declared = 0, decoded = 0;
        bool seenData = false, usesIdat = false, hasPayload = false;
        uint sequence = 0;

        if (!Fill(source, header, 8) || U32(header, 0) != 13 || U32(header, 4) != Ihdr ||
            !Fill(source, pngHeader, 13) || !Fill(source, crc, 4) || U32(crc, 0) != Crc(Ihdr, pngHeader))
            throw Invalid();
        uint w = U32(pngHeader, 0), h = U32(pngHeader, 4);
        if (w == 0 || h == 0 || w > SourcePixels || h > SourcePixels || (long)w * h > SourcePixels) return;
        width = (int)w;
        height = (int)h;

        while (Fill(source, header, 8))
        {
            token.ThrowIfCancellationRequested();
            uint length = U32(header, 0), kind = U32(header, 4);
            if (length + 4L > source.Length - source.Position) throw Invalid();
            if (declared == 0 && kind is Idat or Iend) return;
            if (kind is not (Ihdr or Plte or Trns or Actl or Fctl or Idat or Fdat or Iend))
            {
                source.Seek(length + 4L, SeekOrigin.Current);
                continue;
            }
            if (kind == Ihdr || kind == Actl && length != 8 || kind == Fctl && length != 26 ||
                kind == Fdat && length < 4 || kind == Iend && length != 0 ||
                (kind is Plte or Trns) && (seenData || length > 768))
                throw Invalid();
            if (scratch.Length < length) scratch = new byte[length];
            if (!Fill(source, scratch, (int)length) || !Fill(source, crc, 4)) throw Invalid();
            var data = scratch.AsSpan(0, (int)length);
            if (Crc(kind, data) != U32(crc, 0)) throw Invalid();
            switch (kind)
            {
                case Plte: case Trns:
                    if (palette.Exists(item => item.Type == kind)) throw Invalid();
                    palette.Add((kind, data.ToArray()));
                    break;
                case Actl:
                    if (declared != 0 || seenData) throw Invalid();
                    uint count = U32(data, 0);
                    if (count == 0) throw Invalid();
                    if (count > MaxFrames) return;
                    declared = (int)count;
                    canvas = new byte[width * height * 4];
                    metadata((int)Math.Min(U32(data, 4), int.MaxValue));
                    break;
                case Fctl:
                    if (declared == 0 || U32(data, 0) != sequence++) throw Invalid();
                    FinishFrame();
                    if (firstOnly && decoded > 0) return;
                    if (decoded == declared) throw Invalid();
                    uint fw = U32(data, 4), fh = U32(data, 8), x = U32(data, 12), y = U32(data, 16);
                    if (fw == 0 || fh == 0 || x + (long)fw > width || y + (long)fh > height || data[24] > 2 || data[25] > 1 ||
                        !seenData && (fw != width || fh != height || x != 0 || y != 0))
                        throw Invalid();
                    int denominator = BinaryPrimitives.ReadUInt16BigEndian(data.Slice(22));
                    double delay = BinaryPrimitives.ReadUInt16BigEndian(data.Slice(20)) * 1000d / (denominator == 0 ? 100 : denominator);
                    current = new((int)fw, (int)fh, (int)x, (int)y, delay, data[24], data[25]);
                    usesIdat = !seenData;
                    hasPayload = false;
                    patchPng.SetLength(0);
                    patchPng.Write(Signature, 0, Signature.Length);
                    BinaryPrimitives.WriteUInt32BigEndian(pngHeader.AsSpan(0, 4), fw);
                    BinaryPrimitives.WriteUInt32BigEndian(pngHeader.AsSpan(4, 4), fh);
                    WriteChunk(patchPng, Ihdr, pngHeader);
                    foreach (var item in palette) WriteChunk(patchPng, item.Type, item.Data);
                    break;
                case Idat:
                    // An IDAT before the first fcTL is only a cover (not a frame).
                    seenData = true;
                    if (current is null) break;
                    if (!usesIdat) throw Invalid();
                    hasPayload |= data.Length > 0;
                    WriteChunk(patchPng, Idat, data);
                    break;
                case Fdat:
                    if (current is null || !seenData || usesIdat || U32(data, 0) != sequence++) throw Invalid();
                    hasPayload |= data.Length > 4;
                    WriteChunk(patchPng, Idat, data.Slice(4));
                    break;
                case Iend:
                    FinishFrame();
                    if (firstOnly && decoded > 0) return;
                    if (!seenData || decoded != declared) throw Invalid();
                    return;
            }
        }
        throw Invalid();

        void FinishFrame()
        {
            if (current is not { } frame) return;
            if (!hasPayload || canvas is null) throw Invalid();
            WriteChunk(patchPng, Iend, ReadOnlySpan<byte>.Empty);
            patchPng.Position = 0;
            var decoder = new PngBitmapDecoder(patchPng, BitmapCreateOptions.PreservePixelFormat |
                BitmapCreateOptions.IgnoreColorProfile, BitmapCacheOption.OnDemand);
            var image = decoder.Frames[0];
            BitmapSource bgra = image.Format == PixelFormats.Bgra32 ? image : new FormatConvertedBitmap(image, PixelFormats.Bgra32, null, 0);
            int stride = frame.Width * 4;
            var pixels = new byte[stride * frame.Height];
            bgra.CopyPixels(pixels, stride, 0);
            var previous = frame.Dispose == 2 ? new byte[pixels.Length] : null;
            for (int y = 0; y < frame.Height; y++)
            {
                token.ThrowIfCancellationRequested();
                int from = y * stride, to = ((frame.Y + y) * width + frame.X) * 4;
                if (previous is not null) Buffer.BlockCopy(canvas, to, previous, from, stride);
                if (frame.Blend == 0) Buffer.BlockCopy(pixels, from, canvas, to, stride);
                else
                    for (int i = 0; i < stride; i += 4) Over(pixels, from + i, canvas, to + i);
            }
            onFrame(canvas, width, height, declared, frame.Delay);
            decoded++;
            for (int y = 0; y < frame.Height; y++)
            {
                int to = ((frame.Y + y) * width + frame.X) * 4;
                if (frame.Dispose == 1) Array.Clear(canvas, to, stride);
                else if (previous is not null) Buffer.BlockCopy(previous, y * stride, canvas, to, stride);
            }
        }
    }

    private static void Over(byte[] source, int s, byte[] target, int d)
    {
        int alpha = source[s + 3];
        if (alpha == 0) return;
        // Integer, straight-alpha Porter-Duff source-over, with the same
        // rounding as the legacy compositor (7 bits of coefficient precision).
        int a = alpha * 255 + target[d + 3] * (255 - alpha);
        int weight = alpha * 255 * 255 * 128 / a;
        int residue = 255 * 128 - weight;
        for (int i = 0; i < 3; i++)
            target[d + i] = (byte)(Div255(source[s + i] * weight + target[d + i] * residue + 16384) >> 7);
        target[d + 3] = (byte)Div255(a + 128);
    }

    private static int Div255(int value) => ((value >> 8) + value) >> 8;
    private static InvalidDataException Invalid() => new("无法读取 APNG 帧。");
    private static uint U32(ReadOnlySpan<byte> data, int offset) => BinaryPrimitives.ReadUInt32BigEndian(data.Slice(offset));

    private static bool Fill(Stream stream, byte[] buffer, int size)
    {
        for (int at = 0; at < size;)
        {
            int read = stream.Read(buffer, at, size - at);
            if (read == 0) return false;
            at += read;
        }
        return true;
    }

    private static void WriteChunk(Stream stream, uint kind, ReadOnlySpan<byte> data)
    {
        var header = new byte[8];
        BinaryPrimitives.WriteUInt32BigEndian(header.AsSpan(0, 4), (uint)data.Length);
        BinaryPrimitives.WriteUInt32BigEndian(header.AsSpan(4, 4), kind);
        stream.Write(header, 0, header.Length);
        // Stream.Write(Span) isn't available on the inbox .NET Framework.
        var bytes = data.ToArray();
        stream.Write(bytes, 0, bytes.Length);
        BinaryPrimitives.WriteUInt32BigEndian(header, Crc(kind, data));
        stream.Write(header, 0, 4);
    }

    private static uint Crc(uint kind, ReadOnlySpan<byte> data)
    {
        Span<byte> type = stackalloc byte[4];
        BinaryPrimitives.WriteUInt32BigEndian(type, kind);
        return Crc32.Finish(Crc32.Update(Crc32.Update(Crc32.Start, type), data));
    }
}
