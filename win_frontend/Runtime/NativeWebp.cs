using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using SuzuEmojy.Windows.Controls;

namespace SuzuEmojy.Windows;

internal static class NativeWebp
{
    private const int DecodeAbi = 0x0210, DemuxAbi = 0x0107, Bgra = 3;
    private const string Dll = "suzu-webp.dll";
    private const DllImportSearchPath SafeSearch = DllImportSearchPath.AssemblyDirectory | DllImportSearchPath.System32;
    private const int MaxPixels = 16_000_000, PreviewBudget = 4_000_000, MaxBytes = 64 * 1024 * 1024;
    private static readonly Lazy<bool> Present = new(() => File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, Dll)));
    internal static bool Available => Present.Value;
    internal static bool IsWebp(Stream file)
    {
        file.Position = 0;
        try
        {
            var header = new byte[12];
            return file.Read(header, 0, 12) == 12 && header.AsSpan(0, 4).SequenceEqual("RIFF"u8) &&
                header.AsSpan(8, 4).SequenceEqual("WEBP"u8);
        }
        finally { file.Position = 0; }
    }

    internal static (int Width, int Height, bool Animated)? Dimensions(Stream source, CancellationToken token)
    {
        if (!Available) return null;
        var bytes = Read(source, token);
        var features = Inspect(bytes);
        if (features.Animated == 0) return (features.Width, features.Height, false);
        using var decoder = new Animation(bytes);
        return (features.Width, features.Height, decoder.Count > 1);
    }

    internal static BitmapSource Still(byte[] source, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        var features = Inspect(source);
        if (features.Animated != 0)
        {
            using var decoder = new Animation(source);
            return decoder.Next(token).Frame;
        }
        int stride = features.Width * 4;
        var pixels = new byte[stride * features.Height];
        if (WebPDecodeBGRAInto(source, (UIntPtr)source.Length, pixels, (UIntPtr)pixels.Length, stride) == 0)
            throw new FormatException("无法解码 WebP");
        return Image(features.Width, features.Height, pixels);
    }

    internal static PreviewFrames? Preview(byte[] source, int side, CancellationToken token)
    {
        var features = Inspect(source);
        if (features.Animated == 0) return null;
        using var decoder = new Animation(source);
        if (decoder.Count is <= 1 or > 600) return null;
        side = Math.Min(Math.Max(1, side), (int)Math.Sqrt(PreviewBudget / decoder.Count));
        var images = new List<BitmapSource>(decoder.Count);
        var delays = new List<int>(decoder.Count);
        while (decoder.HasMore)
        {
            var (full, duration) = decoder.Next(token);
            images.Add(ResizeStill(full, side));
            delays.Add(Math.Max(20, duration));
        }
        return images.Count == decoder.Count ? new(images.ToArray(), delays.ToArray(), decoder.Plays) : null;
    }

    internal static byte[] Gif(byte[] source, CancellationToken token)
    {
        using var animation = new Animation(source);
        if (animation.Count is <= 1 or > 600) throw new FormatException("无效的 WebP 动画帧数");
        return LegacyAnimatedGif.Build((add, loop) =>
        {
            loop(animation.Plays);
            while (animation.HasMore)
            {
                var (pixels, delay) = animation.NextPixels(token);
                add(pixels, animation.Width, animation.Height, delay);
            }
        }, removeHiddenRgb: false, token, webp: true, animation.Background);
    }

    internal static void ReadFullFrames(byte[] source, CancellationToken token, Action<BitmapSource, int> add)
    {
        var dimensions = Inspect(source);
        if (dimensions.Animated == 0) { add(Still(source, token), 0); return; }
        using var animation = new Animation(source);
        if (animation.Count is < 1 or > 600) throw new FormatException("无效的 WebP 动画帧数");
        while (animation.HasMore)
        {
            var (frame, delay) = animation.Next(token);
            add(frame, delay);
        }
    }

    internal static BitmapSource ResizeStill(BitmapSource full, int side)
    {
        if (Math.Max(full.PixelWidth, full.PixelHeight) <= side) return full;
        double scale = side / (double)Math.Max(full.PixelWidth, full.PixelHeight);
        var premul = new FormatConvertedBitmap(full, PixelFormats.Pbgra32, null, 0);
        var scaled = new TransformedBitmap(premul, new ScaleTransform(
            Math.Max(1, Math.Round(full.PixelWidth * scale)) / full.PixelWidth,
            Math.Max(1, Math.Round(full.PixelHeight * scale)) / full.PixelHeight));
        int stride = scaled.PixelWidth * 4;
        var pixels = new byte[stride * scaled.PixelHeight];
        scaled.CopyPixels(pixels, stride, 0);
        var result = BitmapSource.Create(scaled.PixelWidth, scaled.PixelHeight, 96, 96, PixelFormats.Pbgra32, null, pixels, stride);
        result.Freeze();
        return result;
    }

    internal static byte[] Read(Stream source, CancellationToken token)
    {
        source.Position = 0;
        if (source.Length <= 0 || source.Length > MaxBytes) throw new FormatException("WebP 文件过大或为空");
        var bytes = new byte[source.Length];
        for (int at = 0; at < bytes.Length;)
        {
            token.ThrowIfCancellationRequested();
            int read = source.Read(bytes, at, Math.Min(128 * 1024, bytes.Length - at));
            if (read == 0) throw new EndOfStreamException();
            at += read;
        }
        return bytes;
    }

    private static Features Inspect(byte[] source)
    {
        if (source.Length == 0 || source.Length > MaxBytes ||
            WebPGetFeaturesInternal(source, (UIntPtr)source.Length, out var features, DecodeAbi) != 0)
            throw new FormatException("无效的 WebP 图片");
        CheckSize(features.Width, features.Height);
        return features;
    }

    private static void CheckSize(long width, long height)
    {
        if (width <= 0 || height <= 0 || width > 16384 || height > 16384 || width * height > MaxPixels)
            throw new FormatException("WebP 图片尺寸过大");
    }

    private static BitmapSource Image(int width, int height, byte[] pixels)
    {
        var frame = BitmapSource.Create(width, height, 96, 96, PixelFormats.Bgra32, null, pixels, width * 4);
        frame.Freeze();
        return frame;
    }

    private sealed class Animation : IDisposable
    {
        private GCHandle pin;
        private nint handle;
        private int timestamp;
        private readonly AnimationInfo info;
        internal int Count => (int)info.FrameCount;
        internal int Plays => (int)info.LoopCount;
        internal uint Background => info.Background;
        internal int Width => (int)info.Width;
        internal int Height => (int)info.Height;
        internal bool HasMore => WebPAnimDecoderHasMoreFrames(handle) != 0;

        internal Animation(byte[] source)
        {
            // Inspect before initialization: the native decoder allocates its full canvas at New.
            _ = Inspect(source);
            pin = GCHandle.Alloc(source, GCHandleType.Pinned);
            try
            {
                var data = new Input { Bytes = pin.AddrOfPinnedObject(), Length = (UIntPtr)source.Length };
                if (WebPAnimDecoderOptionsInitInternal(out var options, DemuxAbi) == 0) throw new FormatException("WebP ABI");
                options.ColorMode = Bgra;
                handle = WebPAnimDecoderNewInternal(ref data, ref options, DemuxAbi);
                if (handle == 0 || WebPAnimDecoderGetInfo(handle, out info) == 0) throw new FormatException("无效的 WebP 动图");
                CheckSize(info.Width, info.Height);
                if (info.FrameCount == 0 || info.FrameCount > int.MaxValue || info.LoopCount > int.MaxValue)
                    throw new FormatException("无效的 WebP 帧数");
            }
            catch { Dispose(); throw; }
        }

        internal (BitmapSource Frame, int Delay) Next(CancellationToken token)
        {
            var (pixels, duration) = NextPixels(token);
            return (Image(Width, Height, pixels), duration);
        }

        internal (byte[] Bytes, int Delay) NextPixels(CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            if (WebPAnimDecoderGetNext(handle, out var pointer, out int end) == 0 || end < timestamp)
                throw new FormatException("损坏的 WebP 动画帧");
            int duration = end - timestamp;
            timestamp = end;
            var bytes = new byte[Width * Height * 4];
            Marshal.Copy(pointer, bytes, 0, bytes.Length);
            return (bytes, duration);
        }

        public void Dispose()
        {
            if (handle != 0) { WebPAnimDecoderDelete(handle); handle = 0; }
            if (pin.IsAllocated) pin.Free();
        }
    }

    [StructLayout(LayoutKind.Sequential, Size = 40)]
    private struct Features { internal int Width, Height, Alpha, Animated, Format; }
    [StructLayout(LayoutKind.Sequential)]
    private struct Input { internal nint Bytes; internal UIntPtr Length; }
    [StructLayout(LayoutKind.Sequential, Size = 36)]
    private struct Options { internal int ColorMode, UseThreads; }
    [StructLayout(LayoutKind.Sequential, Size = 36)]
    private struct AnimationInfo { internal uint Width, Height, LoopCount, Background, FrameCount; }

    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl), DefaultDllImportSearchPaths(SafeSearch)]
    private static extern int WebPGetFeaturesInternal(byte[] source, UIntPtr length, out Features features, int abi);
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl), DefaultDllImportSearchPaths(SafeSearch)]
    private static extern nint WebPDecodeBGRAInto(byte[] source, UIntPtr length, [Out] byte[] pixels, UIntPtr size, int stride);
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl), DefaultDllImportSearchPaths(SafeSearch)]
    private static extern int WebPAnimDecoderOptionsInitInternal(out Options options, int abi);
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl), DefaultDllImportSearchPaths(SafeSearch)]
    private static extern nint WebPAnimDecoderNewInternal(ref Input data, ref Options options, int abi);
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl), DefaultDllImportSearchPaths(SafeSearch)]
    private static extern int WebPAnimDecoderGetInfo(nint decoder, out AnimationInfo info);
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl), DefaultDllImportSearchPaths(SafeSearch)]
    private static extern int WebPAnimDecoderGetNext(nint decoder, out nint buffer, out int timestamp);
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl), DefaultDllImportSearchPaths(SafeSearch)]
    private static extern int WebPAnimDecoderHasMoreFrames(nint decoder);
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl), DefaultDllImportSearchPaths(SafeSearch)]
    private static extern void WebPAnimDecoderDelete(nint decoder);
}
