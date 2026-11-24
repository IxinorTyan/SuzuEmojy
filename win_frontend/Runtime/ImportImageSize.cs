using System;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace SuzuEmojy.Windows;

internal static class ImportImageSize
{
    internal const int MaxSide = 720;

    internal static BitmapSource Fit(BitmapSource source)
    {
        int width = source.PixelWidth, height = source.PixelHeight;
        int longest = Math.Max(width, height);
        if (longest <= MaxSide) return source;
        double scale = (double)MaxSide / longest;
        int w = Math.Max(1, (int)Math.Round(width * scale));
        int h = Math.Max(1, (int)Math.Round(height * scale));
        // Scale premultiplied pixels so invisible RGB does not bleed at the edges.
        var premultiplied = new FormatConvertedBitmap(source, PixelFormats.Pbgra32, null, 0);
        var scaled = new TransformedBitmap(premultiplied, new ScaleTransform((double)w / width, (double)h / height));
        return new FormatConvertedBitmap(scaled, PixelFormats.Bgra32, null, 0);
    }
}
