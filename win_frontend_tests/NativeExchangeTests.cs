using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using SuzuEmojy.Windows;

internal static class NativeExchangeTests
{
    internal static async Task Run(string output, string? legacyBackend = null)
    {
        var folder = Directory.CreateDirectory(Path.Combine(output, $"资源包测试-{Guid.NewGuid():N}")).FullName;
        var root = Path.Combine(folder, "data");
        var pictures = Directory.CreateDirectory(Path.Combine(root, "images")).FullName;
        var png = Path.Combine(pictures, "A 透明像素.png");
        File.WriteAllBytes(png, Convert.FromBase64String(NativeIdentityTests.Rgba));
        File.Copy(png, Path.Combine(pictures, "B 重复.png"));
        var gif = Path.Combine(pictures, "C GIF.gif");
        File.WriteAllBytes(gif, Convert.FromBase64String("R0lGODdhAgACAIEAAP8AAAAAAAAAAAAAACwAAAAAAgACAAAIBgABCAQQEAA7"));
        File.WriteAllText(Path.Combine(root, "categories.json"),
            """{"自定义 甲":["A 透明像素.PNG"],"自定义 乙":["B 重复.png","C GIF.gif"],"空分类":[]}""");
        File.WriteAllText(Path.Combine(root, "category_icons.json"), """{"自定义 甲":"C GIF.gif","空分类":"🍵"}""");
        File.WriteAllText(Path.Combine(root, "metadata.json"), """{"B 重复.png":"中文 Café","C GIF.gif":"动图","A 透明像素.png":"你好 中文"}""");
        var commands = new LibraryCommands(Path.Combine(folder, "missing-backend"), root);
        var all = Path.Combine(folder, "全部资源包.zip");
        var selected = Path.Combine(folder, "仅收藏夹.zip");
        var allResult = await commands.Run(new("export_zip", Value: all), reload: false);
        Require(allResult.Result.Destination == all, "Native v1 export did not publish a ZIP.");
        await commands.Run(new("export_zip", Value: selected, Categories: ["自定义 甲", "空分类"]), reload: false);
        Inspect(all, allScope: true);
        Inspect(selected, allScope: false);
        var split = (await commands.Run(new("split_zip", Value: all, Destination: folder), reload: false)).Result;
        var full = Directory.GetFiles(split.Destination!);
        Require(split.Count == 1 && full.Length == 1 && Path.GetFileName(full[0]) == "全部资源包-part1.zip", "Invalid default volume name.");
        Inspect(full[0], allScope: true);
        int tightLimit = checked((int)new FileInfo(full[0]).Length - 1);
        var tight = ExchangeV1Split.TryRun(root, all, folder, CancellationToken.None, null, tightLimit)!;
        var parts = Directory.GetFiles(tight.Destination!).OrderBy(path => path, StringComparer.Ordinal).ToArray();
        Require(tight.Count == 2 && parts.Select(Path.GetFileName).SequenceEqual(new[] { "全部资源包-part1.zip", "全部资源包-part2.zip" }) &&
            parts.All(path => new FileInfo(path).Length <= tightLimit), "Volume limit must account for actual ZIP bytes.");
        foreach (string part in parts)
        {
            using var archive = new ZipArchive(File.OpenRead(part), ZipArchiveMode.Read);
            using var catalog = Read(archive, "catalog.json");
            Require(catalog.RootElement.GetProperty("resources").GetArrayLength() == 1 &&
                catalog.RootElement.GetProperty("categories").GetArrayLength() == 3 &&
                archive.Entries.Any(entry => entry.FullName.StartsWith("icons/", StringComparison.Ordinal)),
                "Split must keep categories and independent icon assets in every volume.");
            Require(ExchangeV1Archive.Read(archive, CancellationToken.None, null)?.Resources.Length == 1, "Split ZIP could not be validated.");
        }
        var nativeRoot = Path.Combine(folder, "原生还原库");
        var importer = new LibraryCommands(Path.Combine(folder, "missing-backend"), nativeRoot);
        var previous = Directory.CreateDirectory(Path.Combine(nativeRoot, "images")).FullName;
        File.Copy(png, Path.Combine(previous, "已有原图.png"));
        File.WriteAllText(Path.Combine(nativeRoot, "categories.json"), """{"先有分类":["已有原图.png"],"空分类":[]}""");
        File.WriteAllText(Path.Combine(nativeRoot, "category_icons.json"), """{"空分类":"⭐"}""");
        File.WriteAllText(Path.Combine(nativeRoot, "metadata.json"), """{"已有原图.png":"本地标签"}""");
        int saves = 0, dedup = 0;
        foreach (string part in parts)
        {
            var imported = await importer.Run(new("import_zip", Value: part), reload: false);
            saves += imported.Result.Count;
            dedup += imported.Result.Skipped;
        }
        var nativeLibrary = await LibrarySnapshot.ReadAsync(nativeRoot);
        Require(saves == 1 && dedup == 1 && nativeLibrary.Images.Length == 2 &&
            nativeLibrary.Images.Single(image => image.Name == "已有原图.png").Keywords == "本地标签 你好 中文 Café" &&
            nativeLibrary.Images.Single(image => image.Name == "已有原图.png").Categories.SequenceEqual(new[] { "先有分类", "自定义 甲", "自定义 乙" }) &&
            nativeLibrary.Categories.Single(category => category.Name == "空分类").Icon == "⭐" &&
            nativeLibrary.Categories.Single(category => category.Name == "自定义 甲").IconPath is { } nativeIcon && File.Exists(nativeIcon),
            "Native ZIP restore changed an original, a legacy category, a valid local icon, or incoming tags.");
        var repeated = await importer.Run(new("import_zip", Value: all), reload: true);
        Require(repeated.Result.Count == 0 && repeated.Result.Skipped == 2 && repeated.Snapshot!.Images.Length == 2,
            "Repeating a complete import did not preserve deduplication.");
        var rolledBack = Path.Combine(folder, "拒绝中的还原库");
        Directory.CreateDirectory(rolledBack);
        // Failure late in publishing the mirrors must not leave duplicate or half-indexed files.
        Directory.CreateDirectory(Path.Combine(rolledBack, "categories.json"));
        try { await new LibraryCommands(Path.Combine(folder, "missing-backend"), rolledBack).Run(new("import_zip", Value: all), false); }
        catch (IOException) { }
        Require(!Directory.EnumerateFiles(Path.Combine(rolledBack, "images")).Any() &&
            !Directory.EnumerateFiles(rolledBack, ".index-*").Any() &&
            (await LibrarySnapshot.ReadAsync(rolledBack)).Categories.Length == 0, "An interrupted ZIP write left partial data.");
        Require(!File.Exists(Path.Combine(root, "features.db")) && !File.Exists(Path.Combine(root, "categories.db")),
            "Export must not mutate the library's indexes.");
        bool rejected = false;
        try { await commands.Run(new("export_zip", Value: Path.Combine(root, "invalid.zip")), false); }
        catch (ArgumentException) { rejected = true; }
        Require(rejected, "Export accepted a destination inside its library.");
        if (legacyBackend is null) return;
        var importedRoot = Path.Combine(folder, "旧版还原库");
        var request = JsonSerializer.Serialize(new { action = "import_zip", value = all });
        using var outcome = JsonDocument.Parse(await PythonBridge.Request(legacyBackend, "services.gallery_commands",
            request, importedRoot, CancellationToken.None, null));
        Require(outcome.RootElement.GetProperty("count").GetInt32() == 2, "Legacy v1 importer rejected the native ZIP.");
        var reopened = await LibrarySnapshot.ReadAsync(importedRoot);
        Require(reopened.Categories.Length == 3 && reopened.Categories[0].IconPath is { } icon && File.Exists(icon) &&
            reopened.Categories[2].Icon == "🍵" && reopened.Images.Any(record => record.Keywords == "你好 中文 Café" &&
                record.Categories.SequenceEqual(new[] { "自定义 甲", "自定义 乙" })),
            "Importing native v1 ZIP did not restore merged membership, tags, empty categories or standalone icon.");
        var fromParts = Path.Combine(folder, "分卷还原库");
        foreach (string part in parts)
        {
            var import = JsonSerializer.Serialize(new { action = "import_zip", value = part });
            using var result = JsonDocument.Parse(await PythonBridge.Request(legacyBackend, "services.gallery_commands",
                import, fromParts, CancellationToken.None, null));
            Require(result.RootElement.GetProperty("count").GetInt32() == 1, "Legacy importer rejected a native stored volume.");
        }
        Require((await LibrarySnapshot.ReadAsync(fromParts)).Images.Length == 2, "Split volumes failed to merge back into one gallery.");
    }

    private static void Inspect(string path, bool allScope)
    {
        using var stream = File.OpenRead(path);
        using var zip = new ZipArchive(stream, ZipArchiveMode.Read);
        using var manifest = Read(zip, "manifest.json");
        using var catalog = Read(zip, "catalog.json");
        var header = manifest.RootElement;
        var resources = catalog.RootElement.GetProperty("resources").EnumerateArray().ToArray();
        var categories = catalog.RootElement.GetProperty("categories").EnumerateArray().ToArray();
        Require(header.GetProperty("format_version").GetInt32() == 1 &&
            header.GetProperty("includes_keywords").GetBoolean() == allScope &&
            header.GetProperty("export_scope").GetString() == (allScope ? "all" : "categories") &&
            header.GetProperty("icon_assets").GetInt32() == 1 &&
            categories.Length == (allScope ? 3 : 2) && resources.Length == (allScope ? 2 : 1),
            "Native output changed the ZIP v1 export scope, resource deduplication or icon counts.");
        var png = resources.Single(resource => resource.GetProperty("format").GetString() == "PNG");
        Require(png.GetProperty("sync_key").GetString() == "p:6fd2f58323d227445b3d1b0af54f47a0" &&
            png.GetProperty("category_refs").GetArrayLength() == (allScope ? 2 : 1) &&
            png.TryGetProperty("keywords", out var keywords) == allScope &&
            (!allScope || keywords.EnumerateArray().Select(x => x.GetString()).SequenceEqual(new[] { "你好", "中文", "Café" })),
            "Native ZIP changed transparent PNG identity, membership or normalized keywords.");
        foreach (var info in resources.Append(categories[0].GetProperty("icon")))
        {
            var entry = zip.GetEntry(info.GetProperty("asset_path").GetString()!)!;
            using var asset = entry.Open();
            using var sha = SHA256.Create();
            string actual = BitConverter.ToString(sha.ComputeHash(asset)).Replace("-", "").ToLowerInvariant();
            Require(entry.Length == info.GetProperty("asset_size").GetInt64() && actual == info.GetProperty("asset_sha256").GetString(),
                "A v1 payload failed its SHA-256 verification.");
        }
    }

    private static JsonDocument Read(ZipArchive archive, string entry)
    {
        using var stream = archive.GetEntry(entry)!.Open();
        return JsonDocument.Parse(stream);
    }

    private static void Require(bool valid, string message)
    {
        if (!valid) throw new InvalidOperationException(message);
    }
}
