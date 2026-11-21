using System;
using System.IO;
using System.Threading;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace SuzuEmojy.Windows.Controls;

internal sealed class PreviewFrames(BitmapSource[] frames, int[] delays, int plays)
{
    private int position, completed;
    internal bool HasMotion => frames.Length > 1;
    internal ImageSource Current => frames[position];
    internal TimeSpan Delay => TimeSpan.FromMilliseconds(Math.Max(10, delays[position]));

    internal bool Advance()
    {
        if (position + 1 < frames.Length) { position++; return true; }
        if (plays != 0 && ++completed >= plays) return false;
        position = 0;
        return true;
    }

    internal static PreviewFrames Decode(PreviewFrame[] frames, int plays, CancellationToken cancellation)
    {
        var images = new BitmapSource[frames.Length];
        for (int i = 0; i < frames.Length; i++)
        {
            cancellation.ThrowIfCancellationRequested();
            using var stream = new MemoryStream(Convert.FromBase64String(frames[i].Png), writable: false);
            var image = BitmapFrame.Create(stream, BitmapCreateOptions.PreservePixelFormat, BitmapCacheOption.OnLoad);
            image.Freeze();
            images[i] = image;
        }
        return new(images, Array.ConvertAll(frames, item => item.Ms), plays);
    }

    internal static bool CanDecode(string path, bool hasStill)
    {
        string ext = Path.GetExtension(path).ToLowerInvariant();
        if (ext is not (".png" or ".apng" or ".webp")) return false;
        if (ext == ".apng" || ext == ".webp" && !hasStill) return true;
        // Avoid launching a decode job for each ordinary PNG/WebP hover.
        try
        {
            using var file = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            if (ext != ".webp") return ApngDecoder.HasAnimationControl(file);
            var header = new byte[21];
            return Read(21) && Tag(0, "RIFF") && Tag(8, "WEBP") && Tag(12, "VP8X") && (header[20] & 2) != 0;
            bool Read(int count)
            {
                for (int at = 0; at < count;)
                {
                    int read = file.Read(header, at, count - at);
                    if (read == 0) return false;
                    at += read;
                }
                return true;
            }
            bool Tag(int index, string tag)
            {
                for (int i = 0; i < tag.Length; i++)
                    if (header[index + i] != tag[i]) return false;
                return true;
            }
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
        return false;
    }
}
