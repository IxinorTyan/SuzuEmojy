using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using SuzuEmojy.Windows;

internal static class NativeGalleryTests
{
    internal static async Task Run(string? backend, string populatedFixture, string output)
    {
        var root = Path.Combine(output, $"旧库 #中文-{Guid.NewGuid():N}");
        var nonexistent = Path.Combine(root, "未初始化");
        var empty = await LibrarySnapshot.ReadAsync(nonexistent);
        Require(empty.Images.Length == 0 && empty.Categories.Length == 0 && !Directory.Exists(nonexistent),
            "Reading an uninitialized gallery must not create a data folder.");
        var images = Directory.CreateDirectory(Path.Combine(root, "images")).FullName;
        foreach (var name in new[] { "表情.PNG", "动图.gif", "🍵.apng", "\ue000.png", "忽略.bmp" })
            File.WriteAllBytes(Path.Combine(images, name), [42]);
        Directory.CreateDirectory(Path.Combine(images, "目录.png"));
        File.WriteAllText(Path.Combine(root, "config.json"), "{\"appearance_mode\":\"dark\",\"旧设置\":{\"测试\":42}}", new UTF8Encoding(true));
        File.WriteAllText(Path.Combine(root, "order.json"), "[null,\"C:/旧库/表情.png\",9,\"missing.png\",\"表情.PNG\"]");
        File.WriteAllText(Path.Combine(root, "categories.json"), "{\"JSON 收藏\":[\"旧目录/表情.png\",\"表情.PNG\"],\"全部表情\":[\"动图.gif\"],\"空分类\":[]}");
        File.WriteAllText(Path.Combine(root, "category_icons.json"), "{\"JSON 收藏\":\"表情.PNG\",\"空分类\":\"🍵\",\"数据库收藏\":\"图标\"}");
        File.WriteAllText(Path.Combine(root, "metadata.json"), "{\"表情.png\":\"标签;中文\",\"动图.gif\":\"旧标签\"}");
        File.WriteAllText(Path.Combine(root, "recent.json"), "[\"旧目录/动图.gif\",\"表情.PNG\"]");
        var legacy = await CheckParity(backend, root);
        Require(legacy.Images.Select(sticker => sticker.Name).SequenceEqual(new[] { "🍵.apng", "\ue000.png", "动图.gif", "表情.PNG" }) &&
            legacy.Categories is [{ IconPath: not null }, { Icon: "🍵" }], "JSON-only gallery did not preserve Unicode ordering or icons.");

        var iconDir = Directory.CreateDirectory(Path.Combine(root, "category_icons")).FullName;
        File.WriteAllBytes(Path.Combine(iconDir, "分类图标.png"), [42]);
        var wallpaper = Path.Combine(Directory.CreateDirectory(Path.Combine(root, "appearance")).FullName, "window-background");
        File.WriteAllBytes(wallpaper, [42]);
        SeedDatabase(root, "order.db", """
            CREATE TABLE item_orders(image_path TEXT, sort_order INTEGER);
            INSERT INTO item_orders VALUES('动图.GIF', 1), ('表情.png', 2);
            """);
        SeedDatabase(root, "categories.db", """
            CREATE TABLE categories(id INTEGER, name TEXT, icon_path TEXT, sort_order INTEGER);
            CREATE TABLE category_images(category_name TEXT, image_path TEXT);
            INSERT INTO categories VALUES(1,'数据库收藏','category_icons/分类图标.png',2),(2,'最前',NULL,1),(3,'未分类',NULL,0);
            INSERT INTO category_images VALUES('最前','表情.PNG'),('数据库收藏','C:/旧库/动图.GIF'),('未分类','动图.gif');
            """);
        SeedDatabase(root, "metadata.db", """
            CREATE TABLE image_metadata(image_path TEXT, keywords TEXT);
            INSERT INTO image_metadata VALUES('动图.GIF','数据库标签');
            """);
        SeedDatabase(root, "recent.db", """
            CREATE TABLE recent_history(id INTEGER, image_path TEXT, used_at TEXT);
            INSERT INTO recent_history VALUES(1,'动图.GIF','2026-01-01'),(2,'表情.png','2026-11-01'),(3,'missing.png','2026-12-01');
            """);
        var files = Directory.GetFiles(root, "*", SearchOption.AllDirectories);
        var before = files.Select(File.ReadAllBytes).ToArray();
        var mixed = await CheckParity(backend, root);
        Require(mixed.Categories.Select(category => category.Name).SequenceEqual(new[] { "最前", "数据库收藏" }) &&
            mixed.Categories[1].IconPath == Path.Combine(iconDir, "分类图标.png") && mixed.Wallpaper?.Path == wallpaper &&
            mixed.Recent.SequenceEqual(new[] { "表情.PNG", "动图.gif" }) && mixed.Images[^2].Keywords == "数据库标签" &&
            mixed.Images[^1].Keywords == "标签;中文", "SQLite must override/merge JSON exactly as the old reader did.");
        Require(files.Select((path, i) => before[i].SequenceEqual(File.ReadAllBytes(path))).All(equal => equal) &&
            files.Length == Directory.GetFiles(root, "*", SearchOption.AllDirectories).Length,
            "Browsing older databases must not migrate schema, change original bytes or create files.");
        await CheckParity(backend, populatedFixture);
    }

    private static async Task<LibrarySnapshot> CheckParity(string? backend, string data)
    {
        // The executable already uses the native reader. Python is only a regression oracle here.
        var native = await LibrarySnapshot.ReadAsync(data);
        if (backend is null) return native;
        var reference = JsonSerializer.Deserialize<LibrarySnapshot>(await PythonBridge.Run(backend, "services.gallery_snapshot",
            TimeSpan.FromSeconds(20), CancellationToken.None, data), new JsonSerializerOptions { PropertyNameCaseInsensitive = true })!;
        var firstImage = native.Images.Zip(reference.Images, (left, right) => (left, right)).FirstOrDefault(pair =>
            pair.left != (pair.right with { Categories = pair.left.Categories }) || !pair.left.Categories.SequenceEqual(pair.right.Categories));
        Require(native.Settings.Keys.SequenceEqual(reference.Settings.Keys) && native.Settings.All(pair =>
                    JsonNode.DeepEquals(JsonNode.Parse(pair.Value.GetRawText()), JsonNode.Parse(reference.Settings[pair.Key].GetRawText()))) &&
                native.Themes.SequenceEqual(reference.Themes) && native.Categories.SequenceEqual(reference.Categories) &&
                native.Recent.SequenceEqual(reference.Recent) && native.Wallpaper?.Path == reference.Wallpaper?.Path &&
                native.Images.Length == reference.Images.Length &&
                native.Images.Zip(reference.Images, (left, right) => left == (right with { Categories = left.Categories }) &&
                    left.Categories.SequenceEqual(right.Categories)).All(equal => equal),
            $"C# and legacy gallery readers differ for {Path.GetFileName(data)}: " +
            $"images {native.Images.Length}/{reference.Images.Length}, categories {native.Categories.Length}/{reference.Categories.Length}, " +
            $"recent {native.Recent.Length}/{reference.Recent.Length}, settings {native.Settings.Count}/{reference.Settings.Count}; " +
            $"settingsOrder={native.Settings.Keys.SequenceEqual(reference.Settings.Keys)}, themes={native.Themes.SequenceEqual(reference.Themes)}, " +
            $"categoryRows={native.Categories.SequenceEqual(reference.Categories)}, recentRows={native.Recent.SequenceEqual(reference.Recent)}, " +
            $"first image mismatch={firstImage.left?.Name}/{firstImage.right?.Name}.");
        return native;
    }

    private static void SeedDatabase(string data, string name, string sql)
    {
        using var db = WindowsSqlite.OpenForWrite(Path.Combine(data, name));
        foreach (var statement in sql.Split(';').Where(statement => !string.IsNullOrWhiteSpace(statement)))
            db.Execute(statement, CancellationToken.None);
    }

    private static void Require(bool valid, string message)
    {
        if (!valid) throw new InvalidOperationException(message);
    }
}
