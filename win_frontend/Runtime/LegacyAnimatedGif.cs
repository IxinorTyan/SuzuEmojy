using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace SuzuEmojy.Windows;

// For imported animations the file bytes are the v1 identity. In particular,
// replacing the historical palette layout with WIC's would make an old source
// re-import as a different sticker even when the pictures look the same.
internal static class LegacyAnimatedGif
{
    private const string Dll = "suzu-legacy-palette.dll";
    private static readonly Lazy<bool> Present = new(() => File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, Dll)));
    internal static bool Available => Present.Value;
    internal delegate void AddFrame(byte[] bgra, int width, int height, double delay);

    internal static byte[] FromApng(Stream source, CancellationToken token) => Build((add, setLoop) =>
    {
        ApngDecoder.ReadFullFrames(source, token, plays => setLoop(plays == 1 ? null : plays == 0 ? 0 : plays - 1),
            (pixels, width, height, _, duration) => add(pixels, width, height, duration > 0 ? duration : 40));
    }, removeHiddenRgb: true, token);

    // Export path only: retain source cadence up to 60 FPS and round cumulative
    // timestamps to GIF's 10 ms units (not each frame independently).
    internal static byte[] FromTelegramApng(Stream source, CancellationToken token) => Build((add, loop) =>
    {
        const double sample = 1000.0 / 60;
        const double epsilon = 0.00001;
        double end = 0, nextSample = 0;
        int startTick = 0, width = 0, height = 0;
        byte[]? pending = null;
        ApngDecoder.ReadFullFrames(source, token, plays => loop(plays == 1 ? null : plays == 0 ? 0 : plays - 1),
            (pixels, w, h, _, duration) =>
            {
                double begin = end;
                end += duration > 0 ? duration : 40;
                int tick = (int)Math.Round(begin / 10, MidpointRounding.AwayFromZero);
                if (pending is not null && (end <= nextSample + epsilon || tick <= startTick)) return;
                if (pending is not null) add(pending, width, height, (tick - startTick) * 10.0);
                pending ??= new byte[pixels.Length];
                Buffer.BlockCopy(pixels, 0, pending, 0, pixels.Length);
                (width, height, startTick) = (w, h, tick);
                nextSample = Math.Ceiling((end - epsilon) / sample) * sample;
            });
        if (pending is not null)
            add(pending, width, height, Math.Max(1, Math.Round(end / 10, MidpointRounding.AwayFromZero) - startTick) * 10);
    }, removeHiddenRgb: true, token, fullFrames: true);

    internal static byte[] Build(Action<AddFrame, Action<int?>> render, bool removeHiddenRgb, CancellationToken token,
        bool webp = false, uint webpBackground = 0, bool fullFrames = false)
    {
        var frames = new List<Frame>();
        int? loop = null;
        int width = 0, height = 0;
        long storedPixels = 0;
        render(Add, value => loop = value);
        if (frames.Count == 0 || loop is < 0 or > ushort.MaxValue) throw new FormatException("无效的 GIF 动画");
        using var destination = new MemoryStream();
        using var output = new BinaryWriter(destination, Encoding.ASCII, leaveOpen: true);
        bool interlace = frames.Count == 1 && Math.Min(width, height) >= 16;
        for (int index = 0; index < frames.Count; index++)
        {
            token.ThrowIfCancellationRequested();
            var frame = frames[index];
            var crop = frame.Crop ?? new Rectangle(0, 0, width, height);
            byte[] table = frame.Palette;
            byte background = 255;
            if (webp)
            {
                if (!frame.Crop.HasValue) (table, background) = PutBackground(frame, webpBackground);
                var rgb = new byte[table.Length / 4 * 3];
                for (int i = 0; i < rgb.Length / 3; i++) Buffer.BlockCopy(table, i * 4, rgb, i * 3, 3);
                table = rgb;
            }
            int sizeFlag = PaletteSize(table.Length / 3);
            if (frame.Crop is null)
            {
                output.Write("GIF89a"u8.ToArray());
                output.Write((ushort)width);
                output.Write((ushort)height);
                output.Write(new byte[] { (byte)(0x80 | sizeFlag), background, 0 });
                WritePalette();
                if (loop is { } repeats)
                {
                    output.Write(new byte[] { 0x21, 0xff, 11 });
                    output.Write("NETSCAPE2.0"u8.ToArray());
                    output.Write(new byte[] { 3, 1 });
                    output.Write((ushort)repeats);
                    output.Write((byte)0);
                }
            }
            double ticks = frame.Delay / 10;
            if (!RuntimePrimitives.IsFinite(ticks) || ticks < 0 || ticks >= 65536) throw new FormatException("GIF 帧时长无效");
            output.Write(new byte[] { 0x21, 0xf9, 4, frame.Transparency >= 0 ? (byte)0x09 : (byte)0x08 });
            output.Write((ushort)ticks);
            output.Write(new byte[] { (byte)Math.Max(0, frame.Transparency), 0, 0x2c });
            output.Write((ushort)crop.X);
            output.Write((ushort)crop.Y);
            output.Write((ushort)crop.Width);
            output.Write((ushort)crop.Height);
            output.Write(frame.Crop.HasValue ? (byte)(0x80 | sizeFlag) : interlace ? (byte)0x40 : (byte)0);
            if (frame.Crop.HasValue) WritePalette();
            byte[] indices = frame.Indices;
            if (interlace || crop.Width != width || crop.Height != height)
            {
                indices = new byte[crop.Width * crop.Height];
                int offset = 0;
                if (interlace)
                {
                    CopyRows(0, 8);
                    CopyRows(4, 8);
                    CopyRows(2, 4);
                    CopyRows(1, 2);
                }
                else CopyRows(0, 1);

                void CopyRows(int start, int step)
                {
                    for (int y = start; y < crop.Height; y += step)
                    {
                        Buffer.BlockCopy(frame.Indices, (crop.Y + y) * width + crop.X, indices, offset, crop.Width);
                        offset += crop.Width;
                    }
                }
            }
            StaticGifCache.WriteImageData(output, indices, token);

            void WritePalette()
            {
                output.Write(table);
                int pad = (2 << sizeFlag) * 3 - table.Length;
                if (pad > 0) output.Write(new byte[pad]);
            }
        }
        output.Write((byte)0x3b);
        output.Flush();
        return destination.ToArray();

        void Add(byte[] bgra, int w, int h, double delay)
        {
            token.ThrowIfCancellationRequested();
            if (w <= 0 || h <= 0 || w > 16383 || h > 16383 || (long)w * h > 16_000_000 ||
                bgra.Length != w * h * 4 || frames.Count > 1200) throw new FormatException("GIF 动画过大");
            if (frames.Count == 0) (width, height) = (w, h);
            else if (w != width || h != height) throw new FormatException("GIF 帧尺寸不一致");
            var indices = new byte[w * h];
            var rgbaPalette = new byte[1024];
            int count = suzu_quantize(bgra, indices.Length, webp ? 1 : 0, webp ? 256 : 255,
                removeHiddenRgb ? 1 : 0, indices, rgbaPalette);
            if (count is < 1 or > 256 || !webp && count >= 256) throw new FormatException("无法生成 GIF 调色板");
            byte[] palette;
            int transparency;
            if (webp) (palette, transparency) = Optimize(rgbaPalette, indices);
            else
            {
                palette = new byte[removeHiddenRgb ? 768 : (count + 1) * 3];
                for (int i = 0; i < count; i++) Buffer.BlockCopy(rgbaPalette, i * 4, palette, i * 3, 3);
                for (int i = 0; i < indices.Length; i++)
                {
                    if ((i & 65535) == 0) token.ThrowIfCancellationRequested();
                    if (bgra[i * 4 + 3] < 128) indices[i] = 255;
                }
                transparency = 255;
            }
            var frame = new Frame(indices, palette, delay, transparency, null);
            if (frames.Count > 0)
            {
                var previous = frames[frames.Count - 1];
                if (Difference(previous, frame, width, height, blank: false, webp, token) is null)
                {
                    frames[frames.Count - 1] = previous with { Delay = previous.Delay + frame.Delay };
                    return;
                }
                frame = frame with { Crop = webp || fullFrames ? new Rectangle(0, 0, width, height) :
                    Difference(frames[0], frame, width, height, blank: true, webp, token) };
            }
            storedPixels += indices.Length;
            if (storedPixels > 96 * 1024 * 1024) throw new FormatException("GIF 动画过大");
            frames.Add(frame);
        }
    }

    private static (byte[] Palette, int Transparency) Optimize(byte[] palette, byte[] indices)
    {
        int transparent = -1;
        for (int i = 0; i < 256; i++)
            if (palette[i * 4 + 3] == 0) { transparent = i; break; }
        if (indices.Length >= 512 * 512) return (palette, transparent);
        var used = new bool[256];
        foreach (byte index in indices) used[index] = true;
        int[] ordered = Enumerable.Range(0, 256).Where(index => used[index]).ToArray();
        if (ordered.Length > 128 && ordered[ordered.Length - 1] < ordered.Length) return (palette, transparent);
        var optimized = new byte[ordered.Length * 4];
        var map = new byte[256];
        for (int i = 0; i < ordered.Length; i++)
        {
            int index = ordered[i];
            Buffer.BlockCopy(palette, index * 4, optimized, i * 4, 4);
            map[index] = (byte)i;
        }
        for (int i = 0; i < indices.Length; i++) indices[i] = map[indices[i]];
        return (optimized, transparent >= 0 && used[transparent] ? map[transparent] : -1);
    }

    private static (byte[] Palette, byte Background) PutBackground(Frame frame, uint argb)
    {
        var palette = frame.Palette;
        var color = new byte[] { (byte)(argb >> 16), (byte)(argb >> 8), (byte)argb, (byte)(argb >> 24) };
        int length = palette.Length / 4;
        for (int i = 0; i < length; i++)
            if (palette.AsSpan(i * 4, 4).SequenceEqual(color)) return (palette, (byte)i);
        int next = length == frame.Transparency ? length + 1 : length;
        if (next > 255)
        {
            var taken = new bool[256];
            foreach (byte index in frame.Indices) taken[index] = true;
            for (next = 255; next >= 0 && (taken[next] || next == frame.Transparency); next--) { }
            if (next < 0) return (palette, 0);
        }
        var patched = new byte[Math.Max(length, next + 1) * 4];
        Buffer.BlockCopy(palette, 0, patched, 0, palette.Length);
        Buffer.BlockCopy(color, 0, patched, next * 4, 4);
        return (patched, (byte)next);
    }

    private static Rectangle? Difference(Frame previous, Frame frame, int width, int height, bool blank, bool rgba, CancellationToken token)
    {
        bool paletteEqual = previous.Palette.SequenceEqual(frame.Palette);
        int minX = width, minY = height, maxX = -1, maxY = -1;
        for (int y = 0; y < height; y++)
        {
            token.ThrowIfCancellationRequested();
            for (int x = 0; x < width; x++)
            {
                int i = y * width + x;
                int from = blank ? 255 : previous.Indices[i], to = frame.Indices[i];
                bool same = paletteEqual ? from == to : Color(previous, from, rgba, blank) == Color(frame, to, rgba, false);
                if (same) continue;
                minX = Math.Min(minX, x);
                minY = Math.Min(minY, y);
                maxX = Math.Max(maxX, x);
                maxY = Math.Max(maxY, y);
            }
        }
        return maxX < 0 ? null : new(minX, minY, maxX - minX + 1, maxY - minY + 1);
    }

    private static int Color(Frame frame, int index, bool rgba, bool blank)
    {
        var palette = frame.Palette;
        int i = index * (rgba ? 4 : 3);
        int alpha = !blank && index == frame.Transparency ? 0 : rgba && i + 3 < palette.Length ? palette[i + 3] : 255;
        return (alpha << 24) | (i + 2 >= palette.Length ? 0 : palette[i] | palette[i + 1] << 8 | palette[i + 2] << 16);
    }

    private static int PaletteSize(int colors)
    {
        int flag = 1;
        while (colors > (2 << flag)) flag++;
        return flag;
    }

    private readonly record struct Rectangle(int X, int Y, int Width, int Height);
    private sealed record Frame(byte[] Indices, byte[] Palette, double Delay, int Transparency, Rectangle? Crop);

    [DefaultDllImportSearchPaths(DllImportSearchPath.AssemblyDirectory | DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern int suzu_quantize(byte[] bgra, int pixels, int alpha, int colors, int blackHidden,
        [Out] byte[] indices, [Out] byte[] palette);
}
