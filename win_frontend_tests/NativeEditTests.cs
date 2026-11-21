using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using SuzuEmojy.Windows;

internal static class NativeEditTests
{
    internal static async Task Run(string output)
    {
        var root = Path.Combine(output, $"原生图库写入-{Guid.NewGuid():N}");
        var images = Directory.CreateDirectory(Path.Combine(root, "images")).FullName;
        var first = Path.Combine(images, "🫖甲.png");
        var second = Path.Combine(images, "乙.gif");
        var third = Path.Combine(images, "丙.png");
        var outsider = Path.Combine(root, "outside.png");
        foreach (var file in new[] { first, second, third, outsider }) File.WriteAllBytes(file, [1, 2, 3]);

        // Both an invalid backend and a non-ASCII data path exercise the real dispatch.
        var runtime = new LibraryCommands(Path.Combine(root, "does-not-exist"), root);
        File.WriteAllText(Path.Combine(root, "categories.json"),
            """{"旧分类":["C:/旧库/🫖甲.PNG","乙.gif"],"全部表情":["丙.png"],"另一个":["乙.gif"]}""");
        File.WriteAllText(Path.Combine(root, "category_icons.json"), """{"旧分类":"🫖甲.png","另一个":"🧁"}""");
        File.WriteAllText(Path.Combine(root, "metadata.json"), """{"🫖甲.PNG":"旧词  保留","乙.gif":{"unknown":42}}""");
        File.WriteAllText(Path.Combine(root, "order.json"), """["丙.png","🫖甲.png","乙.gif"]""");
        File.WriteAllText(Path.Combine(root, "recent.json"), """["乙.gif","丙.png","不存在.gif"]""");
        var before = await LibrarySnapshot.ReadAsync(root);
        await Fails(() => runtime.Run(new("create_category", Value: "all"), true));
        Require((await LibrarySnapshot.ReadAsync(root)).Categories.SequenceEqual(before.Categories),
            "A rejected first operation made the old JSON index unreadable.");
        const string renamed = " 选 ' ? ";
        var afterRename = (await runtime.Run(new("rename_category", Category: "旧分类", Value: renamed), true)).Snapshot!;
        Require(afterRename.Categories[0].Name == renamed.Trim() && afterRename.Categories[0].IconPath == first &&
            afterRename.Images.Single(x => x.Path == first).Categories.SequenceEqual(new[] { renamed.Trim() }),
            "Renaming a JSON-only category lost its icon or member.");
        var created = (await runtime.Run(new("create_category", Value: " 新收藏 "), true)).Snapshot!;
        Require(created.Categories.Select(x => x.Name).SequenceEqual(new[] { renamed.Trim(), "另一个", "新收藏" }),
            "New categories must append without replacing older categories.");
        await Fails(() => runtime.Run(new("add", [first, outsider], Value: "新收藏"), true));
        await runtime.Run(new("add", [first, first], Value: "新收藏"), false);
        await runtime.Run(new("move", [second], Category: renamed.Trim(), Value: "新收藏"), false);
        await runtime.Run(new("remove", [first], Category: "新收藏"), false);
        await runtime.Run(new("category_icon", [second], "新收藏"), false);
        var afterOrder = (await runtime.Run(new("category_order", Categories: ["新收藏", "另一个", renamed.Trim()]), true)).Snapshot!;
        Require(afterOrder.Categories[0].IconPath == second &&
            afterOrder.Images.Single(x => x.Path == second).Categories.SequenceEqual(new[] { "新收藏", "另一个" }) &&
            afterOrder.Images.Single(x => x.Path == first).Categories.SequenceEqual(new[] { renamed.Trim() }),
            "Category membership, icon, or ordering changed across a move.");
        await runtime.Run(new("category_icon", Category: "新收藏"), false);

        await runtime.Run(new("add_tags", [first], Value: "新词 保留 新词"), false);
        using (var metadata = WindowsSqlite.OpenForWrite(Path.Combine(root, "metadata.db")))
            metadata.Execute("UPDATE image_metadata SET tags = ?, created_at = ?", CancellationToken.None, "legacy-raw", "2017-02-03");
        var afterTags = (await runtime.Run(new("remove_tags", [first], Value: "旧词"), true)).Snapshot!;
        Require(afterTags.Images.Single(x => x.Path == first).Keywords == "保留 新词",
            "The legacy tag separator, stable deduplication or Unicode was lost.");
        using (var metadata = WindowsSqlite.OpenIfPresent(Path.Combine(root, "metadata.db"))!)
            Require(metadata.Read("SELECT tags, created_at FROM image_metadata", 2, CancellationToken.None).Single()
                .SequenceEqual(new[] { "legacy-raw", "2017-02-03" }), "Updating keywords must not rewrite unrelated DB columns.");
        using (var mirror = JsonDocument.Parse(File.ReadAllText(Path.Combine(root, "metadata.json"))))
            Require(mirror.RootElement.GetProperty("乙.gif").GetProperty("unknown").GetInt32() == 42, "Unrelated legacy JSON fields were lost.");
        var afterStickerOrder = (await runtime.Run(new("order", [second, first]), true)).Snapshot!;
        Require(afterStickerOrder.Images.Select(x => x.Path).SequenceEqual(new[] { third, second, first }),
            "Reordering a subset must keep unselected positions unchanged.");

        var recent = (await runtime.Run(new("recent", [first], Limit: 3), false)).Result.Recent!;
        Require(recent.SequenceEqual(new[] { Path.GetFileName(first), Path.GetFileName(second), Path.GetFileName(third) }),
            "Promoting a JSON-only recent history dropped its existing order.");
        var smaller = (await runtime.Run(new("recent", [third], Limit: 2), true));
        Require(smaller.Result.Recent!.SequenceEqual(smaller.Snapshot!.Recent) &&
            smaller.Result.Recent!.SequenceEqual(new[] { Path.GetFileName(third), Path.GetFileName(first) }),
            "DB and recently used result disagree after promoting an entry.");
        var legacy = Path.Combine(root, "旧版最近使用");
        var fixtures = Directory.CreateDirectory(Path.Combine(legacy, "images"));
        File.Copy(first, Path.Combine(fixtures.FullName, Path.GetFileName(first)));
        File.Copy(second, Path.Combine(fixtures.FullName, Path.GetFileName(second)));
        using (var db = WindowsSqlite.OpenForWrite(Path.Combine(legacy, "recent.db")))
        {
            db.Execute("CREATE TABLE recent_history(id INTEGER PRIMARY KEY, image_path TEXT, used_at TEXT)", CancellationToken.None);
            db.Execute("INSERT INTO recent_history VALUES (1, ?, '2020-01-01')", CancellationToken.None, Path.GetFileName(second));
        }
        var reopened = await new LibraryCommands(Path.Combine(root, "missing"), legacy)
            .Run(new("recent", [Path.Combine(fixtures.FullName, Path.GetFileName(first))], Limit: 2), true);
        Require(reopened.Snapshot!.Recent.SequenceEqual(new[] { Path.GetFileName(first), Path.GetFileName(second) }),
            "The old used_at table was not migrated in place.");

        var iconMirror = Path.Combine(root, "category_icons.json");
        var originalBytes = File.ReadAllBytes(iconMirror);
        // Deny replacement of the second mirror; the first publication and DB changes must roll back.
        using (File.Open(Path.Combine(root, "categories.json"), FileMode.Open, FileAccess.Read, FileShare.Read))
            await Fails(() => runtime.Run(new("rename_category", Category: "另一个", Value: "不应保存"), true), io: true);
        Require(originalBytes.SequenceEqual(File.ReadAllBytes(iconMirror)) &&
            (await LibrarySnapshot.ReadAsync(root)).Categories.Any(x => x.Name == "另一个") &&
            !Directory.EnumerateFiles(root, ".index-*").Any(), "A mirror failure left a partial edit.");

        foreach (var category in afterOrder.Categories)
            await runtime.Run(new("remove_category", Category: category.Name), false);
        var final = await LibrarySnapshot.ReadAsync(root);
        Require(final.Categories.Length == 0 && new[] { first, second, third }.All(file => File.ReadAllBytes(file).SequenceEqual(new byte[] { 1, 2, 3 })) &&
            !LibraryEdits.Handles(new("remove_category", DeleteImages: true)),
            "Removing a folder must not remove image originals or fall back to a stale JSON list.");
    }

    private static async Task Fails(Func<Task> run, bool io = false)
    {
        try { await run(); }
        catch (Exception e) when (io ? e is IOException : e is ArgumentException) { return; }
        throw new InvalidOperationException("An invalid edit unexpectedly succeeded.");
    }

    private static void Require(bool valid, string message)
    {
        if (!valid) throw new InvalidOperationException(message);
    }
}
