using System;
using System.IO;
using System.IO.Compression;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace SuzuEmojy.Windows;

internal static class NativeLottie
{
    private const string Dll = "rlottie.dll";
    private const DllImportSearchPath SafeSearch = DllImportSearchPath.AssemblyDirectory | DllImportSearchPath.System32;
    private const int JsonLimit = 8 * 1024 * 1024;
    private static readonly Lazy<bool> Present = new(() => File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, Dll)));
    internal static bool Available => Present.Value;

    internal static bool LooksCompressed(Stream source)
    {
        source.Position = 0;
        try { return source.ReadByte() == 0x1f && source.ReadByte() == 0x8b; }
        finally { source.Position = 0; }
    }

    internal static BitmapSource FirstFrame(byte[] tgs, CancellationToken token) => Read(tgs, token,
        (animation, width, height, _, _, _) =>
        {
            var bitmap = BitmapSource.Create(width, height, 96, 96, PixelFormats.Bgra32, null,
                Render(animation, 0, width, height, token), width * 4);
            bitmap.Freeze();
            return bitmap;
        });

    internal static byte[] Gif(byte[] tgs, CancellationToken token) => Read(tgs, token, (animation, width, height, total, duration, rate) =>
        LegacyAnimatedGif.Build((add, loop) =>
        {
            loop(0);
            int count = Math.Max(1, (int)Math.Ceiling(duration * Math.Min(rate, 50)));
            for (int i = 0; i < count; i++)
            {
                int at = Math.Min(total - 1, (int)((long)i * total / count));
                double ticks = Math.Round((i + 1) * duration * 100 / count) - Math.Round(i * duration * 100 / count);
                add(Render(animation, at, width, height, token), width, height, Math.Max(10, ticks * 10));
            }
        }, removeHiddenRgb: false, token));

    private static T Read<T>(byte[] tgs, CancellationToken token, Func<nint, int, int, int, double, double, T> render)
    {
        using var input = new MemoryStream(tgs, writable: false);
        using var gzip = new GZipStream(input, CompressionMode.Decompress);
        using var json = new MemoryStream();
        var buffer = new byte[32 * 1024];
        for (int read; (read = gzip.Read(buffer, 0, buffer.Length)) > 0;)
        {
            token.ThrowIfCancellationRequested();
            if (json.Length + read > JsonLimit) throw new FormatException("TGS 解压后内容过大");
            json.Write(buffer, 0, read);
        }
        var payload = json.ToArray();
        using var document = JsonDocument.Parse(payload);
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("v", out var version) || version.ValueKind != JsonValueKind.String ||
            !root.TryGetProperty("layers", out var layers) || layers.ValueKind != JsonValueKind.Array) throw Invalid();
        double width = Number("w"), height = Number("h"), rate = Number("fr"), span = Number("op") - Number("ip");
        if (width <= 0 || width > 4096 || height <= 0 || height > 4096 || rate <= 0 || rate > 120 || span <= 0 || span > 1200 ||
            span / rate > 10) throw Invalid();
        if (root.TryGetProperty("assets", out var assets) && assets.ValueKind == JsonValueKind.Array)
            foreach (var asset in assets.EnumerateArray())
                if (asset.ValueKind == JsonValueKind.Object && asset.TryGetProperty("p", out var path) &&
                    path.ValueKind == JsonValueKind.String && path.GetString() is { Length: > 0 } value &&
                    !value.StartsWith("data:", StringComparison.OrdinalIgnoreCase)) throw Invalid();

        var text = new byte[payload.Length + 1]; // const char*, UTF-8.
        Buffer.BlockCopy(payload, 0, text, 0, payload.Length);
        var tag = Encoding.ASCII.GetBytes(Guid.NewGuid().ToString("N") + "\0");
        nint animation = lottie_animation_from_data(text, tag, [0]);
        if (animation == 0) throw Invalid();
        try
        {
            ulong total = lottie_animation_get_totalframe(animation).ToUInt64();
            if (total is 0 or > 1201) throw Invalid();
            var factor = Math.Min(1, 512 / Math.Max(width, height));
            int w = Math.Max(1, (int)Math.Round(width * factor)), h = Math.Max(1, (int)Math.Round(height * factor));
            return render(animation, w, h, (int)total, span / rate, rate);
        }
        finally { lottie_animation_destroy(animation); }

        double Number(string key) => root.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.Number &&
            value.TryGetDouble(out double result) && RuntimePrimitives.IsFinite(result) ? result : throw Invalid();
    }

    private static byte[] Render(nint animation, int frame, int width, int height, CancellationToken token)
    {
        var pixels = new byte[width * height * 4];
        token.ThrowIfCancellationRequested();
        lottie_animation_render(animation, (UIntPtr)frame, pixels, (UIntPtr)width, (UIntPtr)height, (UIntPtr)(width * 4));
        // Pillow's RGBa unpremultiplication uses integer division.
        for (int i = 0; i < pixels.Length; i += 4)
        {
            int alpha = pixels[i + 3];
            if (alpha is > 0 and < 255)
                for (int color = 0; color < 3; color++)
                    pixels[i + color] = (byte)Math.Min(255, 255 * pixels[i + color] / alpha);
        }
        return pixels;
    }

    private static FormatException Invalid() => new("无效的 TGS 动画");
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl), DefaultDllImportSearchPaths(SafeSearch)]
    private static extern nint lottie_animation_from_data(byte[] json, byte[] key, byte[] assetPath);
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl), DefaultDllImportSearchPaths(SafeSearch)]
    private static extern UIntPtr lottie_animation_get_totalframe(nint animation);
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl), DefaultDllImportSearchPaths(SafeSearch)]
    private static extern void lottie_animation_render(nint animation, UIntPtr frame, [Out] byte[] pixels,
        UIntPtr width, UIntPtr height, UIntPtr stride);
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl), DefaultDllImportSearchPaths(SafeSearch)]
    private static extern void lottie_animation_destroy(nint animation);
}
