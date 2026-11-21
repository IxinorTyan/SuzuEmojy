using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;

namespace SuzuEmojy.Windows;

internal static class LibraryFileExport
{
    internal static async Task<int> Copy(string data, string destination, string[] paths, CancellationToken cancellation)
    {
        cancellation.ThrowIfCancellationRequested();
        var root = RuntimePrimitives.ExistingPath(data);
        var folder = RuntimePrimitives.ExistingPath(destination);
        if (!Directory.Exists(folder) || RuntimePrimitives.IsWithin(root, folder))
            throw new ArgumentException("请选择图库以外的文件夹");
        var images = RuntimePrimitives.ExistingPath(Path.Combine(root, "images"));
        var sources = LibraryImagePaths.Validate(images, paths, cancellation);
        // Do not export into a separately redirected images folder either.
        if (RuntimePrimitives.IsWithin(images, folder)) throw new ArgumentException("请选择图库以外的文件夹");

        foreach (var source in sources)
        {
            var temporary = Path.Combine(folder, $".suzu-export-{Guid.NewGuid():N}.part");
            try
            {
                using (var input = new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.Read, 81920, true))
                using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 81920, true))
                    await input.CopyToAsync(output, 81920, cancellation).ConfigureAwait(false);

                for (var index = 0; ; index++)
                {
                    var name = index == 0 ? Path.GetFileName(source)
                        : $"{Path.GetFileNameWithoutExtension(source)}_{index}{Path.GetExtension(source)}";
                    if (RuntimePrimitives.CommitFile(temporary, Path.Combine(folder, name), overwrite: false, cancellation)) break;
                }
            }
            finally { RuntimePrimitives.DiscardTemporaryFile(temporary); }
        }
        return sources.Count;
    }
}
