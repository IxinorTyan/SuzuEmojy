using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;

namespace SuzuEmojy.Windows;

internal static class LibraryImagePaths
{
    internal static readonly StringComparer Comparer = StringComparer.OrdinalIgnoreCase;

    internal static List<string> Validate(string images, IEnumerable<string> paths, CancellationToken cancellation)
    {
        var sources = new List<string>();
        var seen = new HashSet<string>(Comparer);
        foreach (var path in paths)
        {
            cancellation.ThrowIfCancellationRequested();
            var resolved = RuntimePrimitives.ExistingPath(path);
            if (!Comparer.Equals(Path.GetDirectoryName(resolved), images) ||
                (File.GetAttributes(path) & (FileAttributes.ReparsePoint | FileAttributes.Directory)) != 0 ||
                !Comparer.Equals(RuntimePrimitives.ExistingPath(Path.GetDirectoryName(path)!), images))
                throw new ArgumentException("图片不存在或不在当前图库");
            if (seen.Add(resolved)) sources.Add(resolved);
        }
        return sources;
    }
}
