using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using SuzuEmojy.Windows;

internal static class NativeIdentityTests
{
    internal static void Run(string output)
    {
        var folder = Directory.CreateDirectory(Path.Combine(output, $"哈希夹具-{Guid.NewGuid():N}")).FullName;
        // Golden PIL result; the transparent pixel has nonzero RGB bytes that must not be stripped.
        Check("透明 RGBA.png", "iVBORw0KGgoAAAANSUhEUgAAAAMAAAABCAYAAAAb4BS0AAAAFUlEQVR4nGNgZGJmkDL61XDdQfQ/ABAcA/iN7WTQAAAAAElFTkSuQmCC",
            "p:6fd2f58323d227445b3d1b0af54f47a0");
        Check("不透明 RGB.png", "iVBORw0KGgoAAAANSUhEUgAAAAMAAAABCAIAAACUgoPjAAAAEklEQVR4nGNkZGJmZmY+8lUQAAS2AdvEmqzfAAAAAElFTkSuQmCC",
            "p:0cebff3d5494d0ff051bb3a37fd79d50");
        Check("1 bit.png", "iVBORw0KGgoAAAANSUhEUgAAAAMAAAABAQAAAAAzmykZAAAACklEQVR4nGNwAAAAQgBBKTf07wAAAABJRU5ErkJggg==",
            "p:84a7d8d2fdc31354cca43aaaa2a6fbe1");
        Check("L tRNS.png", "iVBORw0KGgoAAAANSUhEUgAAAAMAAAABCAAAAAA+i0toAAAAAnRSTlMADH8lgRMAAAAMSURBVHicY+RiygEAAJYAep5M9cQAAAAASUVORK5CYII=",
            "p:1116d1662f2bc314bcce82cc549db339");
        Check("LA.png", "iVBORw0KGgoAAAANSUhEUgAAAAMAAAABCAQAAACx6dw/AAAAD0lEQVR4nGPgYuCpr/gPAAPfAg25szLwAAAAAElFTkSuQmCC",
            "p:946a00de6ed6dffdf835468d44236d7b");
        Check("RGB tRNS.png", "iVBORw0KGgoAAAANSUhEUgAAAAMAAAABCAIAAACUgoPjAAAABnRSTlMAKAAyADyXly4tAAAAEklEQVR4nGPgEpHTMLL5zyMPAAfkAf2P8k+2AAAAAElFTkSuQmCC",
            "p:4c794b18e6d7ac16f329f69ff716d78d");
        Check("Palette tRNS.png", "iVBORw0KGgoAAAANSUhEUgAAAAMAAAABAgMAAABmjvwnAAAADFBMVEUJCAdkKFoXGBkAAAA/tB78AAAAA3RSTlP/gAB/bWh4AAAACklEQVR4nGOYAAAAkgCREiL7ewAAAABJRU5ErkJggg==",
            "p:d6fd9be8928c07096a8140591860eb56");
        Check("GIF 图片.gif", "R0lGODdhAgACAIEAAP8AAAAAAAAAAAAAACwAAAAAAgACAAAIBgABCAQQEAA7", "f:3725217a39672c11ad139a90877090d1");

        void Check(string name, string payload, string expected)
        {
            var path = Path.Combine(folder, name);
            File.WriteAllBytes(path, Convert.FromBase64String(payload));
            Expect(path, expected);
        }
    }

    internal static void CheckAndroid(string directory)
    {
        // Expected keys from the Android repository's CleanerParityTest.kt.
        Expect(Path.Combine(directory, "20260702121038188211.png"), "p:92b93896345dc57b4b461983afe3ac48");
        Expect(Path.Combine(directory, "20260702121038215660.png"), "p:d5499ed1398cc8cc2f346db3cce16da6");
        Expect(Path.Combine(directory, "20260702121038248736.png"), "p:426cac601783a517be01ee830c5e940c");
        Console.WriteLine("Three Android static PNG v1 hash fixtures matched the C# candidate.");
    }

    internal static async Task CheckWarmup(string output, string? legacyBackend = null)
    {
        const string rgba = Rgba;
        var root = Path.Combine(output, $"增量索引-{Guid.NewGuid():N}");
        var images = Directory.CreateDirectory(Path.Combine(root, "images")).FullName;
        var still = Path.Combine(images, "原图.png");
        var apng = Path.Combine(images, "动图.png");
        var gif = Path.Combine(images, "动图.gif");
        File.WriteAllBytes(still, Convert.FromBase64String(rgba));
        File.WriteAllBytes(apng, Convert.FromBase64String(NativeApngTests.Fixture));
        File.WriteAllBytes(gif, Convert.FromBase64String("R0lGODdhAgACAIEAAP8AAAAAAAAAAAAAACwAAAAAAgACAAAIBgABCAQQEAA7"));
        var dbPath = Path.Combine(root, "features.db");
        var command = new LibraryCommands(Path.Combine(root, "missing"), root);
        var token = CancellationToken.None;
        // Start with a pre-v1 schema, including custom feature data.
        using (var db = WindowsSqlite.OpenForWrite(dbPath))
        {
            db.Execute("CREATE TABLE image_features(image_path TEXT PRIMARY KEY, md5 TEXT, phash TEXT)", token);
            db.Execute("INSERT INTO image_features VALUES (?, ?, ?)", token, Path.GetFileName(still), "legacy-byte-hash", "legacy-phash");
        }
        Require((await command.Run(new("warm_indexes"), false)).Result.Count == 2, "Startup did not warm native v1 identities.");
        Require((await command.Run(new("warm_indexes"), false)).Result.Count == 0, "Unchanged original was decoded again.");
        if (legacyBackend is not null)
        {
            var legacy = await PythonBridge.Request(legacyBackend, "services.gallery_commands", """{"action":"warm_indexes"}""", root, token, null);
            using var json = System.Text.Json.JsonDocument.Parse(legacy);
            Require(json.RootElement.GetProperty("count").GetInt32() == 0, "The historical Python reader rejected native startup receipts.");
        }
        using (var db = WindowsSqlite.OpenIfPresent(dbPath)!)
        {
            var row = db.Read("SELECT md5, phash, sync_key FROM image_features WHERE image_path = ?", 3, token, Path.GetFileName(still)).Single();
            Require(row.SequenceEqual(new[] { "legacy-byte-hash", "legacy-phash", "p:6fd2f58323d227445b3d1b0af54f47a0" }),
                "Lightweight warmup changed an old hash or stored an incompatible v1 key.");
            Require(db.Read("SELECT image_path FROM sync_key_checks WHERE rule_version = 1", 1, token).Single()[0] == Path.GetFileName(apng),
                "An original APNG must be excluded, not hashed as a static frame.");
        }
        File.Copy(still, apng, overwrite: true);
        Require((await command.Run(new("warm_indexes"), false)).Result.Count == 1, "A changed file was skipped due to an old APNG receipt.");
        Require((await command.Run(new("warm_indexes"), false)).Result.Count == 0, "The updated receipt did not persist.");
        var legacyRoot = Path.Combine(root, "JSON only");
        var legacyImages = Directory.CreateDirectory(Path.Combine(legacyRoot, "images")).FullName;
        File.Copy(still, Path.Combine(legacyImages, Path.GetFileName(still)));
        File.WriteAllText(Path.Combine(legacyRoot, "hashes.json"), "{}");
        Require(LibraryIndexWarmup.Run(legacyRoot, token).Count == 0 && !File.Exists(Path.Combine(legacyRoot, "features.db")),
            "Startup must not shadow an old JSON-only hash library.");
    }

    internal const string Rgba = "iVBORw0KGgoAAAANSUhEUgAAAAMAAAABCAYAAAAb4BS0AAAAFUlEQVR4nGNgZGJmkDL61XDdQfQ/ABAcA/iN7WTQAAAAAElFTkSuQmCC";

    private static void Require(bool valid, string message)
    {
        if (!valid) throw new InvalidOperationException(message);
    }

    private static void Expect(string path, string expected)
    {
        string? actual = ResourceIdentityV1.TryRead(path, CancellationToken.None);
        if (actual != expected)
            throw new InvalidOperationException($"v1 identity mismatch for {Path.GetFileName(path)}: expected {expected}, got {actual ?? "unsupported"}.");
    }
}
