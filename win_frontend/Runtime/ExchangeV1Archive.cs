using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;
using System.Windows.Media.Imaging;

namespace SuzuEmojy.Windows;

// Read structured ZIP v1 data without extracting user-supplied paths to disk.
// null means an old codec must verify an otherwise well-formed asset.
internal sealed record ExchangeV1Archive(JsonObject Manifest, JsonObject Catalog,
    JsonArray Categories, ExchangeV1Archive.Resource[] Resources, ExchangeV1Archive.Asset[] Icons)
{
    internal sealed record Asset(string Path, ZipArchiveEntry Entry, uint Crc);
    internal sealed record Resource(JsonObject Spec, Asset Asset, int Relations);
    private const long MaxMetadata = 32 * 1024 * 1024, MaxDecodedInput = 48 * 1024 * 1024;
    private static readonly char[] InvalidNameChars = Path.GetInvalidFileNameChars();

    internal static ExchangeV1Archive? Read(ZipArchive archive, CancellationToken token, IProgress<LibraryProgress>? progress,
        bool streamingSync = false, ISet<string>? skipped = null)
    {
        var entries = new Dictionary<string, ZipArchiveEntry>(StringComparer.OrdinalIgnoreCase);
        foreach (var entry in archive.Entries)
        {
            if ((entries.Count & 1023) == 0) token.ThrowIfCancellationRequested();
            var name = entry.FullName.Replace('\\', '/');
            bool directory = name.EndsWith("/", StringComparison.Ordinal);
            name = SafePath(directory ? name.Substring(0, name.Length - 1) : name);
            if (entries.ContainsKey(name)) throw new InvalidDataException($"ZIP 包含重复路径: {name}");
            entries.Add(name, entry);
        }
        if (!entries.TryGetValue("manifest.json", out var manifestEntry) || !entries.TryGetValue("catalog.json", out var catalogEntry))
            throw new InvalidDataException("ZIP 缺少 manifest.json 或 catalog.json");
        var manifest = ReadObject(manifestEntry);
        var catalog = ReadObject(catalogEntry);
        if (Integer(manifest, "format_version") != 1) throw new InvalidDataException("不支持的交换格式版本");
        if (catalog["categories"] is not JsonArray categories || catalog["resources"] is not JsonArray resources)
            throw new InvalidDataException("catalog.json 缺少 categories 或 resources 列表");

        var refs = new HashSet<int>();
        var icons = new Dictionary<string, Asset>(StringComparer.OrdinalIgnoreCase);
        var checkedAssets = new Dictionary<string, (Asset Asset, string Sha, string Md5, string? Key)>(StringComparer.OrdinalIgnoreCase);
        var buffer = new byte[128 * 1024];
        foreach (var item in categories)
        {
            token.ThrowIfCancellationRequested();
            var category = RequireObject(item);
            int reference = checked((int)Integer(category, "ref"));
            if (!refs.Add(reference) || string.IsNullOrWhiteSpace(String(category, "name"))) throw Invalid();
            if (category["icon"] is not { } iconNode) continue;
            var icon = RequireObject(iconNode);
            if (icon.Count == 0) continue;
            string type = String(icon, "type");
            if (type == "text")
            {
                if (string.IsNullOrWhiteSpace(String(icon, "text"))) throw Invalid();
            }
            else if (type == "image")
            {
                var path = SafePath(String(icon, "asset_path"));
                if (!path.StartsWith("icons/", StringComparison.Ordinal) || !LibrarySnapshotReader.IsIconImage(path)) throw Invalid();
                var info = Verify(path, Integer(icon, "asset_size"), String(icon, "asset_sha256"), icon: true, verifyPixels: false);
                if (info is null) return null;
                icons[path] = info.Value.Asset;
            }
            else if (type != "default") throw Invalid();
        }

        var result = new List<Resource>(resources.Count);
        foreach (var item in resources)
        {
            token.ThrowIfCancellationRequested();
            var resource = RequireObject(item);
            string path = SafePath(String(resource, "asset_path").Replace('\\', '/'));
            progress?.Report(new("exchange", result.Count, resources.Count, $"正在校验资源: {path}"));
            if (path.Equals("manifest.json", StringComparison.OrdinalIgnoreCase) ||
                path.Equals("catalog.json", StringComparison.OrdinalIgnoreCase) || icons.ContainsKey(path))
                throw Invalid();
            int relations = 0;
            if (resource["category_refs"] is { } relationNode)
            {
                if (relationNode is not JsonArray relationArray) throw Invalid();
                foreach (var reference in relationArray)
                {
                    if (reference is not JsonValue id || !id.TryGetValue<int>(out var number) || !refs.Contains(number)) throw Invalid();
                    relations++;
                }
            }
            bool hasKeywords = manifest["includes_keywords"]?.GetValue<bool>() != false &&
                manifest["export_scope"]?.GetValue<string>() != "categories";
            if (hasKeywords && resource["keywords"] is { } keywords &&
                (keywords is not JsonArray words || words.Any(word => word is not JsonValue value || !value.TryGetValue<string>(out _))))
                throw Invalid();
            string? pixel = resource["pixel_md5"]?.GetValue<string>();
            (Asset Asset, string Sha, string Md5, string? Key)? info;
            try
            {
                info = Verify(path, Integer(resource, "asset_size"), String(resource, "asset_sha256"), icon: false,
                    verifyPixels: !string.IsNullOrEmpty(pixel));
            }
            catch (InvalidDataException) when (skipped is not null) { info = null; }
            if (info is null)
            {
                if (skipped is null) return null;
                skipped.Add(String(resource, "sync_key"));
                result.Add(new(resource, new(path, entries[path], 0), relations));
                continue;
            }
            var asset = info.Value;
            var expectedKey = pixel is { Length: > 0 } ? "p:" + pixel.ToLowerInvariant() : "f:" + asset.Md5;
            if (!asset.Md5.Equals(String(resource, "file_md5"), StringComparison.OrdinalIgnoreCase) ||
                expectedKey != asset.Key || String(resource, "sync_key") != expectedKey)
            {
                if (skipped is null) throw new InvalidDataException($"资源 MD5 或 sync_key 校验失败: {path}");
                skipped.Add(String(resource, "sync_key"));
            }
            var normalized = (JsonObject)resource.DeepClone();
            normalized["asset_path"] = path;
            result.Add(new(normalized, asset.Asset, relations));
        }

        if (manifest["counts"] is { } countNode)
        {
            var counts = RequireObject(countNode);
            VerifyCount(counts, "resources", result.Count);
            VerifyCount(counts, "assets", result.Count);
            VerifyCount(counts, "categories", categories.Count);
            VerifyCount(counts, "relations", result.Sum(item => (long)item.Relations));
        }
        VerifyCount(manifest, "total_asset_bytes", result.Sum(item => item.Asset.Entry.Length));
        return new(manifest, catalog, categories, result.ToArray(), icons.Values.ToArray());

        (Asset Asset, string Sha, string Md5, string? Key)? Verify(string path, long length, string sha, bool icon, bool verifyPixels)
        {
            if (checkedAssets.TryGetValue(path, out var previous))
            {
                if (previous.Asset.Entry.Length != length || !previous.Sha.Equals(sha, StringComparison.OrdinalIgnoreCase) ||
                    !StringComparer.Ordinal.Equals(path, previous.Asset.Path))
                    throw Invalid();
                return previous;
            }
            if (!entries.TryGetValue(path, out var entry) || entry.FullName.EndsWith("/", StringComparison.Ordinal) ||
                entry.FullName.EndsWith("\\", StringComparison.Ordinal) || entry.Length != length ||
                entry.FullName.Replace('\\', '/') != path || length <= 0)
                throw new InvalidDataException($"资源大小或路径校验失败: {path}");
            if (!streamingSync && length > MaxDecodedInput) return null;
            bool stage = streamingSync && !icon;
            using var input = entry.Open();
            using var expectedSha = SHA256.Create();
            using var md5 = MD5.Create();
            using Stream? contents = stage
                ? new FileStream(Path.Combine(Path.GetTempPath(), $"suzu-lan-verify-{Guid.NewGuid():N}.part"),
                    FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None, 128 * 1024, FileOptions.DeleteOnClose)
                : new MemoryStream(checked((int)length));
            uint crc = Crc32.Start;
            var header = new byte[6];
            long actual = 0;
            int received;
            while ((received = input.Read(buffer, 0, buffer.Length)) != 0)
            {
                token.ThrowIfCancellationRequested();
                if (actual + received > length) throw Invalid();
                if (actual < header.Length)
                    Buffer.BlockCopy(buffer, 0, header, (int)actual, Math.Min(header.Length - (int)actual, received));
                expectedSha.TransformBlock(buffer, 0, received, null, 0);
                md5.TransformBlock(buffer, 0, received, null, 0);
                crc = Crc32.Update(crc, buffer.AsSpan(0, received));
                contents.Write(buffer, 0, received);
                actual += received;
            }
            expectedSha.TransformFinalBlock([], 0, 0);
            md5.TransformFinalBlock([], 0, 0);
            string computedSha = Hex(expectedSha.Hash!), computedMd5 = Hex(md5.Hash!);
            if (actual != length || !computedSha.Equals(sha, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException($"资源 SHA-256 校验失败: {path}");
            string? key = null;
            if (icon)
            {
                contents.Position = 0;
                try
                {
                    var bitmap = BitmapDecoder.Create(contents, BitmapCreateOptions.DelayCreation, BitmapCacheOption.None).Frames[0];
                    if (bitmap.PixelWidth <= 0 || bitmap.PixelHeight <= 0) return null;
                }
                catch (Exception error) when (error is IOException or FormatException or NotSupportedException or ArgumentException)
                { return null; }
            }
            else if (verifyPixels)
            {
                key = ResourceIdentityV1.TryRead(contents, token);
                if (key is null) return null;
            }
            else
            {
                if (!header.AsSpan().SequenceEqual("GIF87a"u8) && !header.AsSpan().SequenceEqual("GIF89a"u8)) throw Invalid();
                if (streamingSync)
                {
                    if (!GifSyncValidation.Check(contents, token)) return null;
                }
                else try
                {
                    contents.Position = 0;
                    if (new GifBitmapDecoder(contents, BitmapCreateOptions.DelayCreation, BitmapCacheOption.None).Frames.Count == 0)
                        throw Invalid();
                }
                catch (Exception error) when (error is FormatException or NotSupportedException or ArgumentException)
                { throw Invalid(); }
                key = "f:" + computedMd5;
            }
            var inspected = (new Asset(path, entry, Crc32.Finish(crc)), computedSha, computedMd5, key);
            checkedAssets.Add(path, inspected);
            return inspected;
        }
    }

    private static string SafePath(string path)
    {
        if (path.IndexOf('\\') >= 0 || path.Split('/').Any(part => part.Length == 0 || part is "." or ".." ||
            part.EndsWith(" ", StringComparison.Ordinal) || part.EndsWith(".", StringComparison.Ordinal) ||
            part.IndexOfAny(InvalidNameChars) >= 0))
            throw new InvalidDataException("ZIP 包含非法路径");
        return path;
    }

    private static JsonObject ReadObject(ZipArchiveEntry entry)
    {
        if (entry.Length is < 2 or > MaxMetadata) throw new InvalidDataException($"{entry.FullName} 大小无效");
        using var stream = entry.Open();
        var contents = new byte[(int)entry.Length];
        int read = 0;
        while (read < contents.Length)
        {
            int next = stream.Read(contents, read, contents.Length - read);
            if (next == 0) throw Invalid();
            read += next;
        }
        if (stream.ReadByte() != -1) throw Invalid();
        return RequireObject(JsonNode.Parse(contents));
    }

    private static JsonObject RequireObject(JsonNode? node) => node as JsonObject ?? throw Invalid();
    private static string String(JsonObject node, string key) =>
        node[key] is JsonValue value && value.TryGetValue<string>(out var result) && result is not null ? result : throw Invalid();
    private static long Integer(JsonObject node, string key) =>
        node[key] is JsonValue value && value.TryGetValue<long>(out long result) ? result : throw Invalid();
    private static void VerifyCount(JsonObject node, string key, long expected)
    {
        if (node.ContainsKey(key) && Integer(node, key) != expected) throw new InvalidDataException($"manifest {key} 数量校验失败");
    }
    private static string Hex(byte[] bytes) => BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();
    private static InvalidDataException Invalid() => new("资源包声明无效");
}
