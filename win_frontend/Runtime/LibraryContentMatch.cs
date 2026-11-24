using System;
using System.IO;
using System.Threading;
using System.Windows;

namespace SuzuEmojy.Windows;

internal static class LibraryContentMatch
{
    // The v1 digest is a lookup hint. In particular, its PNG digest omits dimensions.
    internal static bool Exact(Stream first, Stream second, string key, CancellationToken token)
    {
        first.Position = second.Position = 0;
        if (key.StartsWith("f:", StringComparison.Ordinal)) return SameBytes();

        bool animated = ApngDecoder.HasAnimationControl(first);
        if (ApngDecoder.HasAnimationControl(second) != animated) return false;
        if (animated) return SameBytes();
        var left = ResourceIdentityV1.DecodePng(first, token);
        var right = ResourceIdentityV1.DecodePng(second, token);
        if (left is null || right is null || left.PixelWidth != right.PixelWidth || left.PixelHeight != right.PixelHeight) return false;
        int stride = left.PixelWidth * 4;
        int batch = Math.Max(1, 65536 / stride);
        var a = new byte[batch * stride];
        var b = new byte[a.Length];
        for (int y = 0; y < left.PixelHeight; y += batch)
        {
            token.ThrowIfCancellationRequested();
            int rows = Math.Min(batch, left.PixelHeight - y);
            var region = new Int32Rect(0, y, left.PixelWidth, rows);
            left.CopyPixels(region, a, stride, 0);
            right.CopyPixels(region, b, stride, 0);
            if (!a.AsSpan(0, rows * stride).SequenceEqual(b.AsSpan(0, rows * stride))) return false;
        }
        return true;

        bool SameBytes()
        {
            if (first.Length != second.Length) return false;
            first.Position = second.Position = 0;
            var x = new byte[65536];
            var z = new byte[x.Length];
            for (long left = first.Length; left > 0;)
            {
                token.ThrowIfCancellationRequested();
                int size = (int)Math.Min(left, x.Length);
                ReadBlock(first, x, size);
                ReadBlock(second, z, size);
                if (!x.AsSpan(0, size).SequenceEqual(z.AsSpan(0, size))) return false;
                left -= size;
            }
            return true;
        }
    }

    private static void ReadBlock(Stream source, byte[] bytes, int size)
    {
        for (int offset = 0; offset < size;)
        {
            int count = source.Read(bytes, offset, size - offset);
            if (count == 0) throw new EndOfStreamException();
            offset += count;
        }
    }
}
