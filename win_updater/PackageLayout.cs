using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text.RegularExpressions;

namespace SuzuEmojy.Windows;

// There is no path from a release ZIP to the gallery, settings or arbitrary
// user files. Only program-owned files in this layout can be replaced.
internal static class PackageLayout
{
    internal const string Marker = "windows-preview.json";
    internal const string App = "SuzuEmojy.Windows.exe";
    internal const string Updater = "SuzuEmojy.Updater.exe";
    internal const string JournalDirectory = ".suzu-update";
    internal static readonly string[] Required = [App, App + ".config", Marker, Updater,
        "suzu-webp.dll", "suzu-legacy-palette.dll", "rlottie.dll", "ffmpeg/ffmpeg.exe"];
    private static readonly HashSet<string> Files = new(Required.Concat([
        "LICENSE", "Windows-README.md", "rlottie-LICENSE.txt", "libwebp-COPYING.txt", "libwebp-PATENTS.txt",
        "Pillow-LICENSE.txt", "ffmpeg/THIRD_PARTY.txt",
        "Microsoft.Bcl.AsyncInterfaces.dll", "Microsoft.Bcl.TimeProvider.dll", "System.Buffers.dll",
        "System.Memory.dll", "System.Numerics.Vectors.dll", "System.Runtime.CompilerServices.Unsafe.dll",
        "System.Text.Encodings.Web.dll", "System.Text.Json.dll", "System.Threading.Tasks.Extensions.dll",
        "System.ValueTuple.dll"
    ]), StringComparer.OrdinalIgnoreCase);
    private static readonly Regex Notice = new(@"Alicenses/(?:[A-Za-z0-9]+[.\-_])*[A-Za-z0-9]+/(?:LICENSE|THIRD-PARTY-NOTICES)\.TXT\z",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    internal static bool Allowed(string path) => Files.Contains(path) || Notice.IsMatch(path);

    internal static ZipArchiveEntry[] Validate(ZipArchive archive)
    {
        var result = new Dictionary<string, ZipArchiveEntry>(StringComparer.OrdinalIgnoreCase);
        long total = 0;
        foreach (var entry in archive.Entries)
        {
            if (!Allowed(entry.FullName) || result.ContainsKey(entry.FullName) ||
                (entry.ExternalAttributes & 0xf0000000) == 0xa0000000 || (entry.ExternalAttributes & 0x400) != 0 ||
                entry.Length is <= 0 or > 32_000_000 || (total += entry.Length) > 64_000_000)
                throw new InvalidDataException("更新包包含意外或重复文件。");
            result.Add(entry.FullName, entry);
        }
        if (!Required.All(result.ContainsKey)) throw new InvalidDataException("更新包缺少程序文件。");
        return result.Values.ToArray();
    }
}
