using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text.RegularExpressions;

namespace SuzuEmojy.Windows;

// There is no path from a release ZIP to the gallery, settings or arbitrary
// user files. Only program-owned files in this layout can be replaced.
internal static class PackageLayout
{
    private const string Bin = PortableLayout.ProgramDirectory + "/";
    internal static readonly string[] Required = [PortableLayout.Launcher, .. new[] {
        PortableLayout.Application, PortableLayout.Application + ".config", PortableLayout.Marker, PortableLayout.Updater,
        "suzu-webp.dll", "suzu-legacy-palette.dll", "rlottie.dll", "ffmpeg/ffmpeg.exe"
    }.Select(name => Bin + name)];
    private static readonly HashSet<string> Files = new(Required.Concat(new[] {
        "LICENSE", "Windows-README.md", "rlottie-LICENSE.txt", "libwebp-COPYING.txt", "libwebp-PATENTS.txt",
        "Pillow-LICENSE.txt", "ffmpeg/THIRD_PARTY.txt", "QRCoder-LICENSE.txt", "QRCoder.dll",
        "Microsoft.Bcl.AsyncInterfaces.dll", "Microsoft.Bcl.TimeProvider.dll", "System.Buffers.dll",
        "System.Memory.dll", "System.Numerics.Vectors.dll", "System.Runtime.CompilerServices.Unsafe.dll",
        "System.Text.Encodings.Web.dll", "System.Text.Json.dll", "System.Threading.Tasks.Extensions.dll",
        "System.ValueTuple.dll"
    }.Select(name => Bin + name)), StringComparer.OrdinalIgnoreCase);
    private static readonly Regex Notice = new(@"\Abin/licenses/[A-Za-z0-9]+(?:[.\-_][A-Za-z0-9]+)*/(?:LICENSE|THIRD-PARTY-NOTICES)\.TXT\z",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
    private static readonly Regex VersionPattern = new(@"\A(?:2)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-preview(?:\.[0-9]+)?)?\z",
        RegexOptions.CultureInvariant);

    [DataContract]
    internal sealed class Marker
    {
        [DataMember(Name = "version")] public string? Version { get; set; }
        [DataMember(Name = "layout")] public string? Layout { get; set; }
        [DataMember(Name = "runtime")] public string? Runtime { get; set; }
        [DataMember(Name = "architecture")] public string? Architecture { get; set; }
        [DataMember(Name = "updateProtocol")] public int UpdateProtocol { get; set; }
    }

    internal static bool NewerStable(string incoming, string installed)
    {
        if (!VersionPattern.IsMatch(incoming) || incoming.Contains("-") || !VersionPattern.IsMatch(installed) ||
            !Version.TryParse(incoming, out var next) || !Version.TryParse(installed.Split('-')[0], out var current))
            return false;
        return next > current || next == current && installed.Contains("-");
    }

    internal static Marker ReadMarker(Stream stream)
    {
        using var bytes = new MemoryStream();
        var buffer = new byte[4097];
        while (bytes.Length <= 4096)
        {
            int count = stream.Read(buffer, 0, buffer.Length);
            if (count == 0) break;
            bytes.Write(buffer, 0, count);
        }
        if (bytes.Length > 4096) throw new InvalidDataException("更新包标记过大。");
        // Windows PowerShell writes a BOM; PowerShell 7 does not.
        bytes.Position = 0;
        int offset = bytes.Length >= 3 && bytes.ReadByte() == 0xef && bytes.ReadByte() == 0xbb && bytes.ReadByte() == 0xbf ? 3 : 0;
        using var json = new MemoryStream(bytes.GetBuffer(), offset, (int)bytes.Length - offset, writable: false);
        try { return (Marker?)new DataContractJsonSerializer(typeof(Marker)).ReadObject(json)
            ?? throw new InvalidDataException("缺少更新包标记。"); }
        catch (SerializationException error) { throw new InvalidDataException("更新包标记无效。", error); }
    }

    internal static bool SupportsInstall(Marker marker) =>
        marker.UpdateProtocol == 1 && marker.Layout == PortableLayout.LayoutId &&
        marker.Runtime == PortableLayout.SystemRuntime && marker.Architecture == "win-x64" &&
        marker.Version is { } version && VersionPattern.IsMatch(version);

    internal static bool Allowed(string path) => Files.Contains(path) || Notice.IsMatch(path);
    internal static bool AllowedDirectory(string path) =>
        Allowed(path + "/LICENSE.TXT") || Files.Any(name => name.StartsWith(path + "/", StringComparison.OrdinalIgnoreCase)) ||
        path.Equals("bin/licenses", StringComparison.OrdinalIgnoreCase);

    internal static ZipArchiveEntry[] Validate(ZipArchive archive, string version, bool stableOnly = true)
    {
        var result = new Dictionary<string, ZipArchiveEntry>(StringComparer.OrdinalIgnoreCase);
        long total = 0;
        foreach (var entry in archive.Entries)
        {
            if (result.Count >= 300 || !Allowed(entry.FullName) || result.ContainsKey(entry.FullName) ||
                (entry.ExternalAttributes & 0xf0000000) == 0xa0000000 || (entry.ExternalAttributes & 0x400) != 0 ||
                entry.Length is <= 0 or > 32_000_000 || (total += entry.Length) > 64_000_000)
                throw new InvalidDataException("更新包包含意外或重复文件。");
            result.Add(entry.FullName, entry);
        }
        if (!Required.All(result.ContainsKey)) throw new InvalidDataException("更新包缺少程序文件。");
        using var stream = result[Bin + PortableLayout.Marker].Open();
        var marker = ReadMarker(stream);
        if (!SupportsInstall(marker) || marker.Version != version || stableOnly && version.Contains("-"))
            throw new InvalidDataException("更新包版本或运行库类型不匹配。");
        return result.Values.ToArray();
    }
}
