using System;
using System.IO;
using System.Threading;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using SuzuEmojy.Windows.Controls;

namespace SuzuEmojy.Windows;

internal static class StickerPreviewMedia
{
    internal const int AnimationLimit = 4 * 1024 * 1024;
    private const int MaxPixels = 32_000_000;

    internal static ExternalPreview Read(string id, Stream source, string? kind, bool detail, CancellationToken token)
    {
        int size = detail ? 380 : 160;
        source.Position = 0;
        PreviewFrames? frames = null;
        BitmapSource? still = null;
        if (kind == "gif" && detail && source.Length <= AnimationLimit)
        {
            using var gif = new MemoryStream();
            source.CopyTo(gif);
            gif.Position = 0;
            try { frames = GifPreview.DecodeFrames(gif, size, token); }
            catch (Exception error) when (error is ArgumentException or System.Runtime.InteropServices.ExternalException) { }
            if (frames is not null) return new(id, "", Native: frames, Still: (BitmapSource)frames.Current);
        }
        if (kind == "webp" && NativeWebp.Available)
        {
            var bytes = NativeWebp.Read(source, token);
            if (detail && bytes.Length <= AnimationLimit) frames = NativeWebp.Preview(bytes, size, token);
            still = (frames?.Current as BitmapSource) ?? NativeWebp.Still(bytes, token);
        }
        if (kind == "png" && ApngDecoder.HasAnimationControl(source))
        {
            source.Position = 0;
            ApngDecoder.TryPreview(source, size, token, out var decoded, firstOnly: !detail || source.Length > AnimationLimit);
            if (decoded?.Native is { } apng)
            {
                still = apng.Current as BitmapSource;
                if (detail && source.Length <= AnimationLimit) frames = apng;
            }
        }
        if (still is null)
        {
            source.Position = 0;
            var decoder = BitmapDecoder.Create(source, BitmapCreateOptions.DelayCreation | BitmapCreateOptions.PreservePixelFormat,
                BitmapCacheOption.None);
            var picture = decoder.Frames[0];
            if ((long)picture.PixelWidth * picture.PixelHeight > MaxPixels) throw new FormatException("Image exceeds the preview limit.");
            still = picture;
        }
        token.ThrowIfCancellationRequested();
        return new(id, "", Native: frames, Still: PreviewBitmap(still, size));
    }

    internal static string Png(BitmapSource image, int side)
    {
        return Convert.ToBase64String(EncodePng(PreviewBitmap(image, side)));
    }

    private static BitmapSource PreviewBitmap(BitmapSource image, int side)
    {
        double ratio = Math.Min(1d, side / (double)Math.Max(image.PixelWidth, image.PixelHeight));
        BitmapSource result = new FormatConvertedBitmap(image, PixelFormats.Pbgra32, null, 0);
        if (ratio < 1d)
            result = new TransformedBitmap(result, new ScaleTransform(
                Math.Max(1, Math.Round(image.PixelWidth * ratio)) / image.PixelWidth,
                Math.Max(1, Math.Round(image.PixelHeight * ratio)) / image.PixelHeight));
        int width = result.PixelWidth, height = result.PixelHeight, stride = width * 4;
        var pixels = new byte[stride * height];
        result.CopyPixels(pixels, stride, 0);
        var frozen = BitmapSource.Create(width, height, 96, 96, PixelFormats.Pbgra32, null, pixels, stride);
        frozen.Freeze();
        return frozen;
    }

    internal static byte[] EncodePng(BitmapSource image)
    {
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(image));
        using var output = new MemoryStream();
        encoder.Save(output);
        return output.ToArray();
    }
}
