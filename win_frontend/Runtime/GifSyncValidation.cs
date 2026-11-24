using System;
using System.IO;
using System.Threading;

namespace SuzuEmojy.Windows;

// Walk the GIF block structure without decoding every frame or allocating the file.
internal static class GifSyncValidation
{
    internal static bool Check(Stream input, CancellationToken token, int maximumFrames = int.MaxValue)
    {
        try
        {
            input.Position = 0;
            var header = LanSyncHost.ReadExactly(input, 13);
            if ((!header.AsSpan(0, 6).SequenceEqual("GIF87a"u8) && !header.AsSpan(0, 6).SequenceEqual("GIF89a"u8)) ||
                (header[6] | header[7]) == 0 || (header[8] | header[9]) == 0 || !Table(header[10])) return false;
            int frames = 0;
            while (true)
            {
                token.ThrowIfCancellationRequested();
                switch (input.ReadByte())
                {
                    case 0x3b: return frames > 0;
                    case 0x21:
                        if (input.ReadByte() < 0 || !Blocks()) return false;
                        break;
                    case 0x2c:
                        if (++frames > maximumFrames) return false;
                        var image = LanSyncHost.ReadExactly(input, 9);
                        if ((image[4] | image[5]) == 0 || (image[6] | image[7]) == 0 || !Table(image[8]) ||
                            input.ReadByte() is < 2 or > 8 || !Blocks(requireData: true)) return false;
                        break;
                    default: return false;
                }
            }
        }
        catch (EndOfStreamException) { return false; }
        finally { input.Position = 0; }

        bool Table(byte flags) => (flags & 128) == 0 || Skip(3L << ((flags & 7) + 1));
        bool Skip(long count)
        {
            if (input.Length - input.Position < count) return false;
            input.Seek(count, SeekOrigin.Current);
            return true;
        }
        bool Blocks(bool requireData = false)
        {
            for (int i = 0; ; i++)
            {
                if ((i & 1023) == 0) token.ThrowIfCancellationRequested();
                int size = input.ReadByte();
                if (size == 0) return !requireData || i > 0;
                if (size < 0 || !Skip(size)) return false;
            }
        }
    }
}
