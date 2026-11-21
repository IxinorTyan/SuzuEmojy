using System;
using System.IO;
using System.Threading;

namespace SuzuEmojy.Windows;

internal static class UniqueStickerFile
{
    // A failed write must not expose a truncated sticker or replace an existing file.
    internal static string Write(string directory, string name, Action<Stream> write, CancellationToken token)
    {
        if (name.Length == 0 || name is "." or ".." || name.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0)
            throw new ArgumentException("无效的文件名");
        var staged = Path.Combine(directory, $".suzu-{Guid.NewGuid():N}.tmp");
        try
        {
            token.ThrowIfCancellationRequested();
            using (var output = File.Open(staged, FileMode.CreateNew, FileAccess.Write, FileShare.None)) write(output);
            for (int i = 0; i < 10_000; i++)
            {
                string candidate = i == 0 ? name : $"{Path.GetFileNameWithoutExtension(name)}_{i}{Path.GetExtension(name)}";
                var path = Path.Combine(directory, candidate);
                if (RuntimePrimitives.CommitFile(staged, path, overwrite: false, token)) return path;
            }
            throw new IOException("文件重名过多");
        }
        finally { RuntimePrimitives.DiscardTemporaryFile(staged); }
    }

    internal static void Copy(Stream source, Stream output, CancellationToken token)
    {
        var bytes = new byte[128 * 1024];
        for (int n; (n = source.Read(bytes, 0, bytes.Length)) > 0;)
        {
            token.ThrowIfCancellationRequested();
            output.Write(bytes, 0, n);
        }
    }
}
