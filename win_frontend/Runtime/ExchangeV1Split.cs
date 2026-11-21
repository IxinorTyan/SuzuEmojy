using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;

namespace SuzuEmojy.Windows;

internal static class ExchangeV1Split
{
    internal const int Limit = 100_000_000;
    private sealed record Volume(byte[] Manifest, byte[] Catalog, ExchangeV1Archive.Asset[] Assets)
    {
        internal long Bytes => StoredZipVolume.Footer + StoredZipVolume.EntrySize("manifest.json", Manifest.Length) +
            StoredZipVolume.EntrySize("catalog.json", Catalog.Length) +
            Assets.Sum(asset => StoredZipVolume.EntrySize(asset.Path, asset.Entry.Length));
    }

    internal static LibraryResult? TryRun(string data, string source, string outputDirectory,
        CancellationToken token, IProgress<LibraryProgress>? progress, int maxBytes = Limit)
    {
        if (maxBytes is < 1 or > Limit) throw new ArgumentOutOfRangeException(nameof(maxBytes));
        token.ThrowIfCancellationRequested();
        var output = RuntimePrimitives.ExistingPath(outputDirectory);
        var root = Path.GetFullPath(data);
        if (Directory.Exists(root)) root = RuntimePrimitives.ExistingPath(root);
        if (!Directory.Exists(output) || RuntimePrimitives.IsWithin(root, output) ||
            !File.Exists(source)) throw new ArgumentException("请选择有效的资源包和图库以外的输出文件夹");
        var images = Path.Combine(root, "images");
        if (Directory.Exists(images) && RuntimePrimitives.IsWithin(RuntimePrimitives.ExistingPath(images), output))
            throw new ArgumentException("请选择图库以外的位置");
        progress?.Report(new("exchange", 0, 0, "正在校验资源包"));
        using var input = File.Open(source, FileMode.Open, FileAccess.Read, FileShare.Read);
        using var zip = new ZipArchive(input, ZipArchiveMode.Read, leaveOpen: true);
        if (ExchangeV1Archive.Read(zip, token, progress) is not { } pack) return null;
        progress?.Report(new("exchange", 0, 0, "正在计算分卷大小"));
        var groups = new List<Volume>();
        if (pack.Resources.Length == 0)
        {
            var empty = Build(0, 0);
            if (!Fits(empty)) throw new InvalidDataException("资源清单和图标超过分卷上限");
            groups.Add(empty);
        }
        for (int start = 0; start < pack.Resources.Length;)
        {
            token.ThrowIfCancellationRequested();
            int low = start + 1, high = Math.Min(pack.Resources.Length, start + StoredZipVolume.MaxEntries - 2);
            int end = start;
            Volume? best = null;
            while (low <= high)
            {
                token.ThrowIfCancellationRequested();
                int mid = low + (high - low) / 2;
                var volume = Build(start, mid);
                if (Fits(volume))
                {
                    best = volume;
                    end = mid;
                    low = mid + 1;
                }
                else high = mid - 1;
            }
            if (best is null)
            {
                var resource = pack.Resources[start];
                string image = resource.Spec["display_name"]?.GetValue<string>() ?? resource.Asset.Path;
                throw new InvalidDataException($"图片 {image} 加上资源清单和图标后超过分卷上限，无法在保持图片完整的情况下分卷");
            }
            groups.Add(best);
            start = end;
        }

        string stem = Path.GetFileNameWithoutExtension(source);
        if (stem.Length > 60) stem = stem.Substring(0, 60);
        if (stem.Length != 0 && char.IsHighSurrogate(stem[stem.Length - 1])) stem = stem.Substring(0, stem.Length - 1);
        var leaf = $"{stem}-volumes-{Guid.NewGuid():N}";
        var destination = Directory.CreateDirectory(Path.Combine(output, leaf)).FullName;
        var paths = new List<string>();
        try
        {
            byte[] buffer = new byte[128 * 1024];
            for (int i = 0; i < groups.Count; i++)
            {
                token.ThrowIfCancellationRequested();
                var volume = groups[i];
                progress?.Report(new("exchange", i, groups.Count, $"正在写入分卷 {i + 1}/{groups.Count}"));
                var path = Path.Combine(destination, $"{stem}-part{i + 1}.zip");
                using var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write);
                paths.Add(path);
                var writer = new StoredZipVolume(stream);
                writer.Add("manifest.json", volume.Manifest, buffer, token);
                writer.Add("catalog.json", volume.Catalog, buffer, token);
                foreach (var asset in volume.Assets)
                {
                    using var contents = asset.Entry.Open();
                    writer.Write(asset.Path, contents, asset.Entry.Length, asset.Crc, buffer, token);
                }
                writer.Finish();
                if (stream.Length != volume.Bytes || stream.Length > maxBytes)
                    throw new InvalidDataException("分卷文件超过大小上限");
                stream.Flush(flushToDisk: true);
            }
        }
        catch
        {
            // Only this run's own files. Do not recursively delete a user directory.
            foreach (var path in paths) RuntimePrimitives.DiscardTemporaryFile(path);
            try { Directory.Delete(destination); }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
            throw;
        }
        progress?.Report(new("exchange", groups.Count, groups.Count, "分卷完成"));
        return new(paths.Count, 0, 0, destination, null, null, null);

        bool Fits(Volume group) => group.Assets.Length + 2 <= StoredZipVolume.MaxEntries && group.Bytes <= maxBytes;

        Volume Build(int start, int end)
        {
            var manifest = (JsonObject)pack.Manifest.DeepClone();
            var catalog = new JsonObject();
            foreach (var pair in pack.Catalog)
                if (pair.Key != "resources") catalog[pair.Key] = pair.Value?.DeepClone();
            var items = new JsonArray();
            var assets = pack.Icons.ToDictionary(icon => icon.Path, StringComparer.Ordinal);
            long bytes = 0, relations = 0;
            for (int i = start; i < end; i++)
            {
                var resource = pack.Resources[i];
                items.Add(resource.Spec.DeepClone());
                assets[resource.Asset.Path] = resource.Asset;
                bytes += resource.Asset.Entry.Length;
                relations += resource.Relations;
            }
            catalog["resources"] = items;
            manifest["counts"] = new JsonObject
            {
                ["resources"] = end - start, ["assets"] = end - start, ["categories"] = pack.Categories.Count,
                ["relations"] = relations
            };
            manifest["total_asset_bytes"] = bytes;
            manifest["icon_assets"] = pack.Icons.Length;
            manifest["total_icon_bytes"] = pack.Icons.Sum(icon => icon.Entry.Length);
            return new(JsonSerializer.SerializeToUtf8Bytes(manifest), JsonSerializer.SerializeToUtf8Bytes(catalog), assets.Values.ToArray());
        }
    }
}
