using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text.Json.Nodes;
using System.Threading;
using static SuzuEmojy.Windows.LibrarySnapshotReader;

namespace SuzuEmojy.Windows;

internal static class ExchangeV1Import
{
    internal static LibraryResult? TryRun(string data, string archive, CancellationToken token, IProgress<LibraryProgress>? progress)
    {
        token.ThrowIfCancellationRequested();
        using var input = File.Open(archive, FileMode.Open, FileAccess.Read, FileShare.Read);
        using var zip = new ZipArchive(input, ZipArchiveMode.Read, leaveOpen: true);
        if (ExchangeV1Archive.Read(zip, token, progress) is not { } package) return null;
        bool scoped = package.Manifest["export_scope"]?.GetValue<string>() == "categories" ||
            package.Manifest["includes_keywords"]?.GetValue<bool>() == false;
        var items = package.Resources.Select(resource =>
        {
            var spec = resource.Spec;
            var key = spec["sync_key"]!.GetValue<string>();
            var name = Name(spec["display_name"]?.GetValue<string>());
            if (!LibraryResourceIngest.ValidName(name)) name = Path.GetFileName(resource.Asset.Path);
            if (!LibraryResourceIngest.ValidName(name) || !IsImageName(name))
                name = key.Replace(':', '-') + (key.StartsWith("p:", StringComparison.Ordinal) ? ".png" : ".gif");
            double quality = spec["quality_score"]?.GetValue<double>() ?? 0;
            if (!RuntimePrimitives.IsFinite(quality)) throw new InvalidDataException("无效的资源评分");
            return new IngestResource(FromArchive(resource.Asset), name, key, spec["file_md5"]!.GetValue<string>().ToLowerInvariant(), quality,
                !scoped && spec["keywords"] is JsonArray keywords ? keywords.SelectMany(value => LibraryResourceIngest.Words(value!.GetValue<string>()))
                    .Distinct(StringComparer.Ordinal).ToArray() : [],
                spec["category_refs"] is JsonArray refs ? refs.Select(value => value!.GetValue<int>()).ToArray() : []);
        }).ToArray();
        var attachments = package.Icons.ToDictionary(icon => icon.Path, StringComparer.Ordinal);
        var categories = package.Categories.Select(entry =>
        {
            var category = (JsonObject)entry!;
            IngestIcon? icon = null;
            if (category["icon"] is JsonObject spec)
            {
                switch (spec["type"]?.GetValue<string>())
                {
                    case "text":
                        icon = new(Text: spec["text"]!.GetValue<string>());
                        break;
                    case "image":
                        var asset = attachments[spec["asset_path"]!.GetValue<string>()];
                        var digest = spec["asset_sha256"]!.GetValue<string>().ToLowerInvariant();
                        icon = new(AttachmentName: digest + Path.GetExtension(asset.Path).ToLowerInvariant(),
                            Attachment: FromArchive(asset));
                        break;
                }
            }
            return new IngestCategory(category["ref"]!.GetValue<int>(), category["name"]!.GetValue<string>().Trim(), icon);
        }).ToArray();
        return LibraryResourceIngest.TryRun(data, new(items, categories), token, progress);
    }

    private static IngestAsset FromArchive(ExchangeV1Archive.Asset asset) => new(asset.Entry.Length, asset.Crc, asset.Entry.Open);
}
