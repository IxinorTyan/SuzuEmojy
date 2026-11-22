using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Windows.Media.Imaging;
using static SuzuEmojy.Windows.LibrarySnapshotReader;

namespace SuzuEmojy.Windows;

// ZIP v1 is shared with the phone app. It does not use the similarity cache
// or decode GIFs to pixels to determine identity. Export streams originals,
// and publishes only after both the payload and manifest have been written.
internal static class ExchangeV1Export
{
    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    };
    private static readonly IComparer<string> Unicode = Comparer<string>.Create(CompareNames);
    private sealed record Asset(string Source, string AssetPath, long AssetSize, string AssetSha256);
    private sealed record Icon(string Type, string? Text = null, string? AssetPath = null,
        long? AssetSize = null, string? AssetSha256 = null);
    private sealed record ExportCategory(int Ref, string Name, Icon? Icon);
    private sealed record Skipped(string Name, string Reason);
    private sealed record Resource(string SyncKey, string AssetPath, long AssetSize, string AssetSha256,
        string? PixelMd5, string FileMd5, string Format, bool IsAnimated, int Width, int Height,
        double QualityScore, string[]? Keywords, long CreatedAt, string DisplayName, int[] CategoryRefs);

    internal static LibraryResult? TryRun(string data, string destination, string[]? selection,
        CancellationToken token, IProgress<LibraryProgress>? progress, string? packageId = null, string? telegramSource = null,
        SyncAssets? syncAssets = null, IReadOnlyDictionary<string, List<string>>? syncPaths = null)
    {
        if (selection is { Length: 0 }) throw new ArgumentException("请至少选择一个收藏夹");
        var root = Path.GetFullPath(data);
        if (Directory.Exists(root)) root = RuntimePrimitives.ExistingPath(root);
        var parent = Path.GetDirectoryName(Path.GetFullPath(destination))!;
        var ancestor = parent;
        while (!Directory.Exists(ancestor))
            ancestor = Path.GetDirectoryName(ancestor) ?? throw new DirectoryNotFoundException();
        parent = Path.Combine(RuntimePrimitives.ExistingPath(ancestor), parent.Substring(ancestor.Length).TrimStart('\\'));
        var target = Path.Combine(parent, Path.GetFileName(destination));
        if (RuntimePrimitives.IsWithin(root, target) ||
            (File.Exists(target) && RuntimePrimitives.IsWithin(root, RuntimePrimitives.ExistingPath(target))))
            throw new ArgumentException("请选择图库以外的位置");
        var images = Path.Combine(root, "images");
        if (Directory.Exists(images) && RuntimePrimitives.IsWithin(RuntimePrimitives.ExistingPath(images), target))
            throw new ArgumentException("请选择图库以外的位置");
        Directory.CreateDirectory(parent);

        var groupRows = new List<(string Name, string? Icon, List<string> Members)>();
        ReadCategories(root, ReadObject(root, "category_icons.json"), groupRows, token);
        var warnings = new List<string>();
        var chosen = selection?.Select(Normalize).Where(name => name.Length != 0).ToHashSet(StringComparer.Ordinal);
        bool withKeywords = selection is null;
        var categories = new List<ExportCategory>();
        var wantedKeys = syncAssets?.Keys.ToHashSet(StringComparer.Ordinal);
        var wantedIcons = syncAssets?.Icons.ToHashSet(StringComparer.Ordinal);
        var memberships = new Dictionary<string, List<int>>(LibraryImagePaths.Comparer);
        var assets = new Dictionary<string, Asset>(StringComparer.Ordinal);
        foreach (var rows in groupRows.GroupBy(row => Normalize(row.Name), StringComparer.Ordinal))
        {
            token.ThrowIfCancellationRequested();
            string name = rows.Key;
            if (chosen is { Count: > 0 } && !chosen.Contains(name)) continue;
            if (rows.Count() > 1) warnings.Add($"{name}：已合并同名收藏夹");
            Icon? icon = null;
            if ((wantedIcons is null || wantedIcons.Count > 0) &&
                rows.Select(row => row.Icon).FirstOrDefault(value => !string.IsNullOrEmpty(value)) is { } value)
            {
                if (!IsIconImage(value)) icon = new("text", value);
                else
                {
                    try
                    {
                        if (IconPath(root, value) is not { } source || MediaThumbnail.Load(source, 64) is null)
                            throw new FormatException();
                        using var stream = OpenOriginal(source);
                        var hashes = Hashes(stream, token, includeMd5: false);
                        string iconPath = $"icons/{hashes.Sha256}{Path.GetExtension(source).ToLowerInvariant()}";
                        if (wantedIcons is null || wantedIcons.Contains(iconPath))
                        {
                            assets[iconPath] = new(source, iconPath, stream.Length, hashes.Sha256);
                            icon = new("image", AssetPath: iconPath, AssetSize: stream.Length, AssetSha256: hashes.Sha256);
                        }
                    }
                    catch (Exception error) when (error is IOException or UnauthorizedAccessException or FormatException)
                    { warnings.Add($"{name}：跳过无效图标"); }
                }
            }
            int reference = categories.Count + 1;
            categories.Add(new(reference, name, icon));
            foreach (var member in rows.SelectMany(group => group.Members).Distinct(LibraryImagePaths.Comparer))
            {
                if (!memberships.TryGetValue(member, out var references)) memberships.Add(member, references = []);
                references.Add(reference);
            }
        }
        var labels = withKeywords ? Keywords(root, token) : new Dictionary<string, string>();
        var scores = QualityScores(root, token);
        var skipped = new List<Skipped>();
        var resources = new Dictionary<string, Resource>(StringComparer.Ordinal);
        var files = syncPaths is not null && syncAssets is not null
            ? syncAssets.Keys.Select(key => new FileInfo(Path.Combine(images, syncPaths[key][0]))).ToArray()
            : wantedKeys is { Count: 0 } ? [] : Directory.Exists(images)
            ? Directory.EnumerateFiles(images).Select(path => new FileInfo(path))
                .Where(file => (withKeywords || memberships.ContainsKey(file.Name)) &&
                    (wantedKeys is null || file.Extension.Equals(".png", StringComparison.OrdinalIgnoreCase) ||
                     file.Extension.Equals(".gif", StringComparison.OrdinalIgnoreCase))).OrderBy(file => file.Name, Unicode).ToArray()
            : [];
        int steps = Math.Max(files.Length, 1);
        for (int i = 0; i < files.Length; i++)
        {
            token.ThrowIfCancellationRequested();
            var file = files[i];
            progress?.Report(new("exchange", i, steps * 2, $"正在解析表情: {file.Name}"));
            var references = memberships.TryGetValue(file.Name, out var refs) ? refs.ToArray() : [];
            if (!withKeywords && references.Length == 0) continue;
            using var stream = OpenOriginal(file.FullName);
            var bytes = new byte[12];
            int size = 0;
            while (size < bytes.Length)
            {
                int more = stream.Read(bytes, size, bytes.Length - size);
                if (more == 0) break;
                size += more;
            }
            stream.Position = 0;
            bool png = size >= 8 && bytes.AsSpan(0, 8).SequenceEqual(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 });
            bool gif = size >= 6 && bytes.AsSpan(0, 6).SequenceEqual("GIF87a"u8) ||
                size >= 6 && bytes.AsSpan(0, 6).SequenceEqual("GIF89a"u8);
            if (!png && !gif)
            {
                skipped.Add(new(file.Name, $"unsupported type: UNKNOWN({Hex(bytes.Take(size).ToArray())})"));
                continue;
            }
            string? pixelMd5 = null;
            if (png)
            {
                if (ApngDecoder.HasAnimationControl(stream))
                {
                    skipped.Add(new(file.Name, "animated png (apng) not supported in v0"));
                    continue;
                }
                if (ResourceIdentityV1.TryRead(stream, token) is not { } key || !key.StartsWith("p:", StringComparison.Ordinal))
                {
                    skipped.Add(new(file.Name, "invalid or unsupported PNG"));
                    continue;
                }
                pixelMd5 = key.Substring(2);
            }
            stream.Position = 0;
            var checksums = Hashes(stream, token, includeMd5: true);
            string md5 = checksums.Md5!;
            string syncKey = pixelMd5 is null ? $"f:{md5}" : $"p:{pixelMd5}";
            if (wantedKeys is not null && !wantedKeys.Contains(syncKey)) continue;
            var keywords = withKeywords
                ? (labels.GetValueOrDefault(file.Name) ?? "").Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries)
                    .Select(Normalize).Distinct(StringComparer.Ordinal).ToArray()
                : null;
            double score = scores.GetValueOrDefault(file.Name);
            long created = (long)(RuntimePrimitives.LastWriteTimeUtc(stream.SafeFileHandle) - RuntimePrimitives.UnixEpoch).TotalMilliseconds;

            if (resources.TryGetValue(syncKey, out var previous))
            {
                resources[syncKey] = previous with
                {
                    CreatedAt = Math.Min(created, previous.CreatedAt), QualityScore = Math.Max(score, previous.QualityScore),
                    Keywords = previous.Keywords?.Union(keywords!, StringComparer.Ordinal).ToArray(),
                    CategoryRefs = previous.CategoryRefs.Union(references).OrderBy(x => x).ToArray()
                };
                continue;
            }
            int width, height;
            if (gif)
            {
                // GIF's dimensions in the catalog are the logical screen,
                // not the possibly cropped rectangle of the first frame.
                if (size < 12) { skipped.Add(new(file.Name, "invalid GIF")); continue; }
                width = bytes[6] | bytes[7] << 8;
                height = bytes[8] | bytes[9] << 8;
            }
            else
            {
                stream.Position = 0;
                var frame = new PngBitmapDecoder(stream, BitmapCreateOptions.PreservePixelFormat |
                    BitmapCreateOptions.IgnoreColorProfile, BitmapCacheOption.OnDemand).Frames[0];
                width = frame.PixelWidth;
                height = frame.PixelHeight;
            }
            string assetPath = pixelMd5 is null ? $"assets/f-{md5}.gif" : $"assets/p-{pixelMd5}.png";
            assets.Add(assetPath, new(file.FullName, assetPath, stream.Length, checksums.Sha256));
            resources.Add(syncKey, new(syncKey, assetPath, stream.Length, checksums.Sha256, pixelMd5, md5,
                png ? "PNG" : "GIF", gif, width, height, score, keywords, created, file.Name, references));
        }

        var ordered = resources.Values.OrderBy(record => record.CreatedAt).ThenBy(record => record.DisplayName, Unicode)
            .ThenBy(record => record.SyncKey, StringComparer.Ordinal).ToArray();
        var icons = assets.Values.Where(asset => asset.AssetPath.StartsWith("icons/", StringComparison.Ordinal)).ToArray();
        if (syncAssets is not null && (!wantedKeys!.SetEquals(resources.Keys) ||
            !wantedIcons!.SetEquals(icons.Select(icon => icon.AssetPath))))
            throw new IOException("图库资源或图标已变化，请重新同步。");
        var manifest = new
        {
            format_version = 1, export_scope = withKeywords ? "all" : "categories", includes_keywords = withKeywords,
            icon_assets = icons.Length, total_icon_bytes = icons.Sum(asset => asset.AssetSize),
            package_id = packageId ?? Guid.NewGuid().ToString(), exporter = "pc", app_version = AboutPage.InstalledVersion,
            exported_at = (long)(DateTime.UtcNow - RuntimePrimitives.UnixEpoch).TotalMilliseconds,
            hash_rules = new { @static = "p:md5(RGBA bytes)", animated = "f:md5(file bytes)" },
            counts = new { resources = ordered.Length, categories = categories.Count, relations = ordered.Sum(r => r.CategoryRefs.Length), assets = ordered.Length },
            total_asset_bytes = ordered.Sum(record => record.AssetSize), skipped
        };

        var temporary = Path.Combine(parent, $".suzu-exchange-{Guid.NewGuid():N}.part");
        try
        {
            using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write))
            {
                using (var zip = new ZipArchive(output, ZipArchiveMode.Create, leaveOpen: true))
                {
                    using (var entry = zip.CreateEntry("manifest.json").Open()) JsonSerializer.Serialize(entry, manifest, Json);
                    using (var entry = zip.CreateEntry("catalog.json").Open()) JsonSerializer.Serialize(entry, new { categories, resources = ordered }, Json);
                    if (telegramSource is not null)
                    {
                        using var source = new StreamWriter(zip.CreateEntry("TG.txt").Open(), new System.Text.UTF8Encoding(false));
                        source.Write(telegramSource + "\n");
                    }
                    foreach (var icon in icons) CopyAsset(zip, icon, token);
                    for (int i = 0; i < ordered.Length; i++)
                    {
                        var resource = ordered[i];
                        progress?.Report(new("exchange", steps + (int)((long)i * steps / ordered.Length),
                            steps * 2, $"正在打包表情: {resource.DisplayName}"));
                        CopyAsset(zip, assets[resource.AssetPath], token);
                    }
                }
                output.Flush(flushToDisk: true);
            }
            RuntimePrimitives.CommitFile(temporary, target, overwrite: true, token);
        }
        finally { RuntimePrimitives.DiscardTemporaryFile(temporary); }
        progress?.Report(new("exchange", steps * 2, steps * 2, "打包完成"));
        return new(ordered.Length, skipped.Count, 0, target, warnings.ToArray(), null, null);
    }

    private static Dictionary<string, string> Keywords(string root, CancellationToken token)
    {
        var result = new Dictionary<string, string>(LibraryImagePaths.Comparer);
        foreach (var pair in ReadObject(root, "metadata.json"))
        {
            var value = pair.Value;
            result[Name(pair.Key)] = Text(value) ?? (value.ValueKind switch
            {
                JsonValueKind.Null => "None",
                JsonValueKind.True => "True",
                JsonValueKind.False => "False",
                _ => value.GetRawText()
            });
        }
        try
        {
            using var db = WindowsSqlite.OpenIfPresent(Path.Combine(root, "metadata.db"));
            if (db is not null)
                foreach (var row in db.Read("SELECT image_path, keywords FROM image_metadata", 2, token))
                    if (row[0] is { } path) result[Name(path)] = row[1] ?? "";
        }
        catch (LibrarySqliteException) { }
        return result;
    }

    private static Dictionary<string, double> QualityScores(string root, CancellationToken token)
    {
        var result = new Dictionary<string, double>(LibraryImagePaths.Comparer);
        try
        {
            using var db = WindowsSqlite.OpenIfPresent(Path.Combine(root, "features.db"));
            if (db is not null)
                foreach (var row in db.Read("SELECT image_path, quality_score FROM image_features", 2, token))
                    if (row[0] is { } path && double.TryParse(row[1], NumberStyles.Float, CultureInfo.InvariantCulture, out double score)
                        && RuntimePrimitives.IsFinite(score))
                        result[Name(path)] = score;
        }
        catch (LibrarySqliteException) { }
        return result;
    }

    private static void CopyAsset(ZipArchive zip, Asset asset, CancellationToken token)
    {
        using var source = OpenOriginal(asset.Source);
        using var destination = zip.CreateEntry(asset.AssetPath, CompressionLevel.NoCompression).Open();
        if (source.Length != asset.AssetSize || Hashes(source, token, includeMd5: false, destination).Sha256 != asset.AssetSha256)
            throw new IOException($"打包时图片已变化: {Path.GetFileName(asset.Source)}");
    }

    private static FileStream OpenOriginal(string path) => File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
    private static string Normalize(string value) => value.Normalize().Trim();
    private static string Hex(byte[] bytes) => BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();

    private static (string Sha256, string? Md5) Hashes(Stream stream, CancellationToken token, bool includeMd5, Stream? output = null)
    {
        using var sha256 = SHA256.Create();
        using var md5 = includeMd5 ? MD5.Create() : null;
        var buffer = new byte[64 * 1024];
        int count;
        while ((count = stream.Read(buffer, 0, buffer.Length)) > 0)
        {
            token.ThrowIfCancellationRequested();
            sha256.TransformBlock(buffer, 0, count, null, 0);
            md5?.TransformBlock(buffer, 0, count, null, 0);
            output?.Write(buffer, 0, count);
        }
        sha256.TransformFinalBlock([], 0, 0);
        md5?.TransformFinalBlock([], 0, 0);
        return (Hex(sha256.Hash!), md5?.Hash is { } digest ? Hex(digest) : null);
    }
}
