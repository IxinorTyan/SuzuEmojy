using System;
using System.IO;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using Drawing = System.Drawing;
using DrawingImaging = System.Drawing.Imaging;

namespace SuzuEmojy.Windows;

/// <summary>Decode one animated preview at a time without a PNG encode/decode per frame.</summary>
internal sealed class GifPreview : IDisposable
{
    private const DrawingImaging.PixelFormat PixelFormat = DrawingImaging.PixelFormat.Format32bppPArgb;
    private readonly Stream source;
    private readonly Drawing.Image gif;
    private readonly Drawing.Bitmap pixels;
    private readonly byte[]? delays;
    private int remaining, frame;
    internal int FrameCount { get; }
    internal WriteableBitmap Bitmap { get; }

    private GifPreview(Stream source, Drawing.Image gif, int frameCount, int maxSide)
    {
        this.source = source;
        this.gif = gif;
        FrameCount = frameCount;
        delays = Property(gif, 0x5100);
        var loop = Property(gif, 0x5101);
        remaining = loop is { Length: >= 2 } ? BitConverter.ToUInt16(loop) : -1;
        if (remaining == 0) remaining = int.MaxValue;

        double scale = Math.Min(1.0, (double)maxSide / Math.Max(gif.Width, gif.Height));
        int width = Math.Max(1, (int)(gif.Width * scale));
        int height = Math.Max(1, (int)(gif.Height * scale));
        Bitmap = new WriteableBitmap(width, height, 96, 96, PixelFormats.Pbgra32, null);
        pixels = new Drawing.Bitmap(width, height, PixelFormat);
        try { UpdatePixels(); }
        catch { pixels.Dispose(); throw; }
    }

    internal static GifPreview? Open(string path, int maxSide) =>
        Open(File.Open(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete), maxSide);

    // Owns the stream, including the no-animation and invalid-data paths.
    internal static GifPreview? Open(Stream source, int maxSide)
    {
        Drawing.Image? gif = null;
        var keep = false;
        try
        {
            gif = Drawing.Image.FromStream(source);
            if (Array.IndexOf(gif.FrameDimensionsList, DrawingImaging.FrameDimension.Time.Guid) < 0)
                return null;
            var frames = gif.GetFrameCount(DrawingImaging.FrameDimension.Time);
            if (frames < 2) return null;
            var preview = new GifPreview(source, gif, frames, maxSide);
            keep = true;
            return preview;
        }
        finally
        {
            if (!keep) { gif?.Dispose(); source.Dispose(); }
        }
    }

    private static byte[]? Property(Drawing.Image image, int id) =>
        Array.IndexOf(image.PropertyIdList, id) < 0 ? null : image.GetPropertyItem(id)?.Value;

    internal TimeSpan Delay => TimeSpan.FromMilliseconds(delays is { } samples && frame < samples.Length / 4
        ? Math.Max(20, 10.0 * BitConverter.ToUInt32(samples, frame * 4)) : 100);

    internal bool Advance()
    {
        var next = frame + 1;
        if (next == FrameCount)
        {
            if (remaining <= 0) return false;
            if (remaining < int.MaxValue) remaining--;
            next = 0;
        }
        frame = next;
        gif.SelectActiveFrame(DrawingImaging.FrameDimension.Time, frame);
        UpdatePixels();
        return true;
    }

    private void UpdatePixels()
    {
        using (var graphics = Drawing.Graphics.FromImage(pixels))
        {
            graphics.CompositingMode = Drawing.Drawing2D.CompositingMode.SourceCopy;
            graphics.InterpolationMode = Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
            graphics.Clear(Drawing.Color.Transparent);
            graphics.DrawImage(gif, 0, 0, pixels.Width, pixels.Height);
        }
        var data = pixels.LockBits(new Drawing.Rectangle(0, 0, pixels.Width, pixels.Height),
            DrawingImaging.ImageLockMode.ReadOnly, PixelFormat);
        try
        {
            Bitmap.WritePixels(new Int32Rect(0, 0, pixels.Width, pixels.Height),
                data.Scan0, data.Stride * data.Height, data.Stride);
        }
        finally { pixels.UnlockBits(data); }
    }

    public void Dispose()
    {
        pixels.Dispose();
        gif.Dispose();
        source.Dispose();
    }
}
