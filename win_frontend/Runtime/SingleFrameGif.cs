using System;
using System.IO;
using System.Threading;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace SuzuEmojy.Windows;

internal static class SingleFrameGif
{
    // WIC supplies the original LZW indices, not premultiplied image pixels.
    // Preserve the GIF table's RGB even for the fully transparent index, since
    // v1 static identities hash the bytes hidden behind transparency too.
    internal static BitmapSource? Decode(Stream source, BitmapFrame frame, CancellationToken token)
    {
        source.Position = 6;
        using var reader = new BinaryReader(source, System.Text.Encoding.UTF8, leaveOpen: true);
        int width = reader.ReadUInt16(), height = reader.ReadUInt16();
        int flags = reader.ReadByte();
        int background = reader.ReadByte();
        reader.ReadByte(); // Pixel aspect.
        if (width == 0 || height == 0 || (long)width * height > 16_000_000) return null;
        var palette = Palette(flags);
        int transparent = -1;
        while (source.Position < source.Length)
        {
            token.ThrowIfCancellationRequested();
            int type = reader.ReadByte();
            if (type == 0x21)
            {
                int extension = reader.ReadByte();
                if (extension == 0xf9)
                {
                    if (reader.ReadByte() != 4) return null;
                    int control = reader.ReadByte();
                    reader.ReadUInt16();
                    int index = reader.ReadByte();
                    transparent = (control & 1) != 0 ? index : -1;
                    if (reader.ReadByte() != 0) return null;
                }
                else
                    for (int block; (block = reader.ReadByte()) > 0;)
                    {
                        token.ThrowIfCancellationRequested();
                        if (block > source.Length - source.Position) return null;
                        source.Seek(block, SeekOrigin.Current);
                    }
                continue;
            }
            if (type != 0x2c) return null;
            int left = reader.ReadUInt16(), top = reader.ReadUInt16(), w = reader.ReadUInt16(), h = reader.ReadUInt16();
            var global = palette;
            palette = Palette(reader.ReadByte()) ?? global;
            if (left + w > width || top + h > height || palette is null ||
                frame.PixelWidth != w || frame.PixelHeight != h) return null;
            if (frame.Format != PixelFormats.Indexed1 && frame.Format != PixelFormats.Indexed2 &&
                frame.Format != PixelFormats.Indexed4 && frame.Format != PixelFormats.Indexed8) return null;
            int bits = frame.Format.BitsPerPixel;
            int stride = (w * bits + 7) / 8;
            var indices = new byte[stride * h];
            frame.CopyPixels(indices, stride, 0);
            int mask = (1 << bits) - 1;
            var pixels = new byte[width * height * 4];
            // WIC exposes only the image rectangle for an offset GIF frame.
            // Fill the logical canvas before placing that rectangle.
            if (w != width || h != height)
            {
                var fill = transparent >= 0 ? palette : global ?? palette;
                int color = (transparent >= 0 ? transparent : global is null ? 0 : background) * 3;
                byte b = 0, g = 0, r = 0, a = transparent >= 0 ? (byte)0 : (byte)255;
                if (color + 2 < fill.Length) (b, g, r) = (fill[color + 2], fill[color + 1], fill[color]);
                for (int k = 0; k < pixels.Length; k += 4)
                {
                    pixels[k] = b;
                    pixels[k + 1] = g;
                    pixels[k + 2] = r;
                    pixels[k + 3] = a;
                }
            }
            for (int y = 0; y < h; y++)
            {
                token.ThrowIfCancellationRequested();
                for (int x = 0; x < w; x++)
                {
                    int bit = x * bits;
                    int index = (indices[y * stride + bit / 8] >> (8 - bits - bit % 8)) & mask;
                    int dest = ((top + y) * width + left + x) * 4, color = index * 3;
                    if (color + 2 >= palette.Length)
                    {
                        if (index != transparent) return null;
                    }
                    else
                    {
                        pixels[dest] = palette[color + 2];
                        pixels[dest + 1] = palette[color + 1];
                        pixels[dest + 2] = palette[color];
                    }
                    pixels[dest + 3] = index == transparent ? (byte)0 : (byte)255;
                }
            }
            return BitmapSource.Create(width, height, 96, 96, PixelFormats.Bgra32, null, pixels, width * 4);
        }
        return null;

        byte[]? Palette(int packed)
        {
            if ((packed & 0x80) == 0) return null;
            int length = 3 * (2 << (packed & 7));
            var table = reader.ReadBytes(length);
            if (table.Length != length) throw new EndOfStreamException();
            return table;
        }
    }
}
