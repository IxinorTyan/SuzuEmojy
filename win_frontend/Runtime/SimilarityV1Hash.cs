using System;
using System.Threading;
using System.Windows.Media.Imaging;

namespace SuzuEmojy.Windows;

// Existing similarity-v1 records can be reused only when both the resize and
// hash agree with the legacy Windows implementation. This is not a sync key.
internal static class SimilarityV1Hash
{
    private const int Edge = 32, Precision = 22, Round = 1 << (Precision - 1);
    private static readonly double[,] Basis = MakeBasis();
    private sealed record Kernel(int Start, int[] Weights);

    internal static long Compute(BitmapSource image, CancellationToken token)
    {
        int width = image.PixelWidth, height = image.PixelHeight;
        var pixels = new byte[checked(width * height * 4)];
        image.CopyPixels(pixels, width * 4, 0);
        if (width != Edge || height != Edge)
        {
            for (int i = 0; i < pixels.Length; i += 4)
            {
                if ((i & 65535) == 0) token.ThrowIfCancellationRequested();
                int alpha = pixels[i + 3];
                for (int channel = 0; channel < 3; channel++)
                {
                    int value = pixels[i + channel] * alpha + 128;
                    pixels[i + channel] = (byte)(((value >> 8) + value) >> 8);
                }
            }
            if (width != Edge)
            {
                var horizontal = new byte[height * Edge * 4];
                var columns = Coefficients(width);
                for (int y = 0; y < height; y++)
                {
                    token.ThrowIfCancellationRequested();
                    for (int x = 0; x < Edge; x++)
                        Sample(pixels, (y * width + columns[x].Start) * 4, 4, columns[x].Weights, horizontal, (y * Edge + x) * 4);
                }
                pixels = horizontal;
            }
            if (height != Edge)
            {
                var vertical = new byte[Edge * Edge * 4];
                var rows = Coefficients(height);
                for (int y = 0; y < Edge; y++)
                {
                    token.ThrowIfCancellationRequested();
                    for (int x = 0; x < Edge; x++)
                        Sample(pixels, (rows[y].Start * Edge + x) * 4, Edge * 4, rows[y].Weights, vertical, (y * Edge + x) * 4);
                }
                pixels = vertical;
            }
            for (int i = 0; i < pixels.Length; i += 4)
            {
                int alpha = pixels[i + 3];
                if (alpha is 0 or 255) continue;
                for (int channel = 0; channel < 3; channel++)
                    pixels[i + channel] = (byte)Math.Min(255, 255 * pixels[i + channel] / alpha);
            }
        }
        var luminance = new double[Edge * Edge];
        for (int i = 0; i < luminance.Length; i++)
        {
            int at = i * 4;
            double alpha = pixels[at + 3] / 255d;
            luminance[i] = (0.299 * pixels[at + 2] + 0.587 * pixels[at + 1] + 0.114 * pixels[at]) * alpha + 255 * (1 - alpha);
        }
        return Dct(luminance);
    }

    private static void Sample(byte[] pixels, int offset, int stride, int[] weights, byte[] destination, int position)
    {
        int b = Round, g = Round, r = Round, a = Round;
        foreach (int weight in weights)
        {
            b += pixels[offset] * weight;
            g += pixels[offset + 1] * weight;
            r += pixels[offset + 2] * weight;
            a += pixels[offset + 3] * weight;
            offset += stride;
        }
        destination[position] = Clip(b);
        destination[position + 1] = Clip(g);
        destination[position + 2] = Clip(r);
        destination[position + 3] = Clip(a);
    }

    private static byte Clip(int sample) => (byte)RuntimePrimitives.Clamp(sample >> Precision, 0, 255);

    private static Kernel[] Coefficients(int input)
    {
        var result = new Kernel[Edge];
        double scale = input / (double)Edge, support = Math.Max(scale, 1), inverse = 1 / support;
        for (int i = 0; i < Edge; i++)
        {
            double center = (i + 0.5) * scale;
            int start = Math.Max(0, (int)(center - support + 0.5));
            int end = Math.Min(input, (int)(center + support + 0.5));
            var weights = new double[end - start];
            double sum = 0;
            for (int j = 0; j < weights.Length; j++)
            {
                double distance = Math.Abs((j + start - center + 0.5) * inverse);
                weights[j] = distance < 1 ? 1 - distance : 0;
                sum += weights[j];
            }
            var quantized = new int[weights.Length];
            for (int j = 0; j < quantized.Length; j++)
                quantized[j] = (int)(0.5 + weights[j] / sum * (1 << Precision));
            result[i] = new(start, quantized);
        }
        return result;
    }

    private static long Dct(double[] pixels)
    {
        var horizontal = new double[Edge, 8];
        for (int y = 0; y < Edge; y++)
            for (int u = 0; u < 8; u++)
                for (int x = 0; x < Edge; x++)
                    horizontal[y, u] += pixels[y * Edge + x] * Basis[u, x];
        var ac = new double[63];
        int k = 0;
        for (int v = 0; v < 8; v++)
            for (int u = 0; u < 8; u++)
            {
                if (u == 0 && v == 0) continue;
                double term = 0;
                for (int y = 0; y < Edge; y++) term += horizontal[y, u] * Basis[v, y];
                ac[k++] = term;
            }
        var rank = (double[])ac.Clone();
        Array.Sort(rank);
        long bits = 0;
        for (int i = 0; i < ac.Length; i++)
            if (ac[i] > rank[31] + 1e-7) bits |= 1L << i;
        return bits;
    }

    private static double[,] MakeBasis()
    {
        var basis = new double[8, Edge];
        for (int u = 0; u < 8; u++)
            for (int x = 0; x < Edge; x++)
                basis[u, x] = Math.Cos((2 * x + 1) * u * Math.PI / 64);
        return basis;
    }
}
