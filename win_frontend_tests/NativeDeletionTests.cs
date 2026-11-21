using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using SuzuEmojy.Windows;

internal static class NativeDeletionTests
{
    internal static async Task Run(string output)
    {
        var root = Path.Combine(output, $"删除事务-{Guid.NewGuid():N}");
        var images = Directory.CreateDirectory(Path.Combine(root, "images")).FullName;
        var only = Path.Combine(images, "仅属于甲.png");
        var shared = Path.Combine(images, "共享.png");
        var outside = Path.Combine(root, "库外.png");
        byte[] payload = Convert.FromBase64String(NativeIdentityTests.Rgba);
        foreach (var path in new[] { only, shared, outside }) File.WriteAllBytes(path, payload);
        File.WriteAllText(Path.Combine(root, "categories.json"), """{"甲":["仅属于甲.png","共享.PNG"],"乙":["共享.png"]}""");
        File.WriteAllText(Path.Combine(root, "category_icons.json"), """{"甲":"仅属于甲.png","乙":"共享.png"}""");
        File.WriteAllText(Path.Combine(root, "hashes.json"), """{"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa":"仅属于甲.png","共享.png":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}""");
        var commands = new LibraryCommands(Path.Combine(root, "missing"), root);
        await commands.Run(new("tags", [only, shared], Value: "已标记"), false);
        await commands.Run(new("recent", [only]), false);
        await commands.Run(new("recent", [shared]), false);
        await commands.Run(new("order", [only, shared]), false);
        try
        {
            await commands.Run(new("delete", [outside]), false);
            throw new InvalidOperationException("Deletion accepted a path outside images.");
        }
        catch (ArgumentException) { }
        Require(File.Exists(outside), "An outside original changed.");

        using (File.Open(Path.Combine(root, "categories.json"), FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            try
            {
                await commands.Run(new("delete", [only]), false);
                throw new InvalidOperationException("An expected mirror write failure was ignored.");
            }
            catch (IOException) { }
        }
        var restored = await LibrarySnapshot.ReadAsync(root);
        Require(File.Exists(only) && restored.Images.Any(item => item.Path == only && item.Keywords == "已标记") &&
            !Directory.EnumerateDirectories(images, ".suzu-delete-*").Any(),
            "Failed deletion did not restore the file and its indexes.");

        var deletion = await commands.Run(new("remove_category", Category: "甲", DeleteImages: true), reload: true);
        var remaining = deletion.Snapshot!;
        Require(deletion.Result.Count == 1 && !File.Exists(only) && File.Exists(shared) &&
            remaining.Categories.Length == 1 && remaining.Categories[0].IconPath == shared &&
            remaining.Images[0].Categories.SequenceEqual(new[] { "乙" }) && remaining.Recent.SequenceEqual(new[] { "共享.png" }) &&
            !File.ReadAllText(Path.Combine(root, "hashes.json")).Contains("仅属于甲.png"), "Deletion removed a shared original or left stale data.");
        using (var metadata = WindowsSqlite.OpenIfPresent(Path.Combine(root, "metadata.db"))!)
            Require(metadata.Read("SELECT image_path FROM image_metadata", 1, CancellationToken.None).Single()[0] == "共享.png",
                "Unselected metadata was removed.");
        var last = await commands.Run(new("delete", [shared]), reload: true);
        Require(last.Result.Count == 1 && last.Snapshot!.Images.Length == 0 && last.Snapshot.Categories[0].IconPath is null &&
            last.Snapshot.Recent.Length == 0 && File.Exists(outside), "Deleting a final image changed its empty category or library boundary.");
    }

    private static void Require(bool valid, string message)
    {
        if (!valid) throw new InvalidOperationException(message);
    }
}
