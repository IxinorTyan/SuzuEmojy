using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using SuzuEmojy.Windows;

internal static class NativeMigrationBoundaryTests
{
    private const string StaticWebp = "UklGRjgAAABXRUJQVlA4TCsAAAAvAgAAEBcgEG1TNarDXCCbhnafUCIQoChOWmK/kCDht9XhQdX6j5JE9D8AAA==";
    private const string AnimatedWebp = "UklGRp4AAABXRUJQVlA4WAoAAAASAAAAAgAAAAAAQU5JTQYAAAAAAAAAAgBBTk1GQAAAAAAAAAAAAAIAAAAAAFoAAAJWUDhMJwAAAC8CAAAQFyAQSHFcsywQSHFCmywQSPLcY34hQcJvq8MDqv5Rkoj+BwBBTk1GKgAAAAAAAAAAAAIAAAAAAJYAAABWUDhMEQAAAC8CAAAAB1CUktWj/4GI6H8AAA==";
    private const string Tgs = "H4sIAAAAAAAC/31R0QqDMAz8lZJnEauujv2K+FCcokxmUdkm0n9f0lqnqIPSXu+Sa5JO8IIbg4uf+DF4DMoOryJAVCtEBFrlqDeBGEHlQCPHouvxlk4wjHgSWT/viPixRz/M1IPSJmjNLh1JiUGgEXY7wdByR6cIaGUk93sZ/TzmNhOkliDugrD8Ocd0w4XHopBMkSmIia8rxhb9oQw/ipAYZ0ju9UoUySKKRKNq3xHLQz9bnWnTQCVVsZ4odDlsat70fdo0evIwOxukrcTYlw3Z5wcWdqzoY2z+fhTXVP8XJPybQE0CAAA=";

    internal static async Task Webm(string output, string reference, string ffmpeg, string[] paths)
    {
        var native = Path.Combine(output, $"webm-native-{Guid.NewGuid():N}");
        var old = Path.Combine(output, $"webm-reference-{Guid.NewGuid():N}");
        var token = CancellationToken.None;
        string[] Identity(LibrarySnapshot state) => state.Images.Select(item =>
            ResourceIdentityV1.TryRead(item.Path, token) ?? "").ToArray();
        string request = JsonSerializer.Serialize(new { action = "import", paths });
        using var referenceReport = JsonDocument.Parse(await PythonBridge.Request(reference, "services.gallery_commands", request, old, token, null));
        Require(referenceReport.RootElement.GetProperty("count").GetInt32() == paths.Length, "Legacy WebM fixture conversion");
        var result = LocalImageImport.TryRun(native, paths, null, token, null, ffmpeg);
        Require(result?.Count == paths.Length &&
            Identity(await LibrarySnapshot.ReadAsync(native)).SequenceEqual(Identity(await LibrarySnapshot.ReadAsync(old))),
            "WebM single-frame and animated v1 identities must match the historical importer");
    }

    internal static async Task Run(string output, WindowsInstall? install = null)
    {
        var folder = Directory.CreateDirectory(Path.Combine(output, $"原生流转-{Guid.NewGuid():N}")).FullName;
        var data = Path.Combine(folder, "data");
        var backend = Path.Combine(folder, "no-python");
        var png = Convert.FromBase64String(NativeIdentityTests.Rgba);
        using var token = new CancellationTokenSource(TimeSpan.FromSeconds(15));
        if (install is not null) await CheckDropIn(install, folder, png, token.Token);
        if (LegacyAnimatedGif.Available)
        {
            using var apng = new MemoryStream(Convert.FromBase64String(NativeApngTests.Fixture));
            CheckGif(LegacyAnimatedGif.FromApng(apng, token.Token), "f:8c2d79519b1240a9405c54edbbd0a3d3");
            if (NativeLottie.Available)
            {
                var source = Convert.FromBase64String(Tgs);
                var native = NativeLottie.Gif(source, token.Token);
                File.WriteAllBytes(Path.Combine(folder, "native-tgs.gif"), native);
                File.WriteAllBytes(Path.Combine(folder, "source.tgs"), source);
                CheckGif(native, "f:72d1bd66059e6fc3b7a909a102648d65");
            }
        }
        int reads = 0;
        using (var fake = new HttpClient(new Handler((_, uri) =>
        {
            reads++;
            if (uri.AbsolutePath == "/start")
            {
                var redirect = new HttpResponseMessage(HttpStatusCode.Redirect);
                redirect.Headers.Location = new Uri("/image", UriKind.Relative);
                return redirect;
            }
            Require(uri.AbsolutePath == "/image");
            var image = new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(png) };
            image.Content.Headers.ContentType = new("image/png");
            return image;
        })))
        {
            var saved = await LinkedImageImport.TryRun(data, new("import_link", Value: "https://example.test/start"),
                token.Token, null, fake);
            Require(saved is { Count: 1, Error: null } && reads == 2, "link redirect or ingest");
            var rejected = await LinkedImageImport.TryRun(data, new("import_link", Value: "file:///C:/unsafe.png"),
                token.Token, null, fake);
            Require(rejected?.Error is not null && reads == 2, "invalid URI must not reach the network");
        }

        var inbox = Directory.CreateDirectory(Path.Combine(data, "inbox")).FullName;
        var ready = Path.Combine(inbox, "收件.png");
        var changing = Path.Combine(inbox, "变化.png");
        File.WriteAllBytes(ready, png);
        File.WriteAllBytes(changing, png);
        InboxFile Receipt(string path) => new(Path.GetFileName(path), png.Length, (File.GetLastWriteTimeUtc(path) - RuntimePrimitives.UnixEpoch).Ticks);
        var changed = Receipt(changing);
        File.SetLastWriteTimeUtc(changing, DateTime.UtcNow.AddHours(-1));
        var command = new LibraryCommands(backend, data);
        var imported = await command.Run(new("import_inbox", InboxFiles: [Receipt(ready), changed]), true, token.Token);
        Require(imported.Result is { Skipped: 1, Failed: 0 } && !File.Exists(ready) && File.Exists(changing) &&
            !Directory.EnumerateFiles(Path.Combine(inbox, ".processing")).Any(), "inbox receipt/consume");

        var oldRoot = Path.Combine(folder, "legacy-json");
        var oldImages = Directory.CreateDirectory(Path.Combine(oldRoot, "images")).FullName;
        var original = Path.Combine(oldImages, "live.png");
        File.WriteAllBytes(original, png);
        const string oldCategories = """{"Old":["live.png","dead.png"]}""";
        File.WriteAllText(Path.Combine(oldRoot, "categories.json"), oldCategories);
        File.WriteAllText(Path.Combine(oldRoot, "category_icons.json"), """{"Old":"live.png"}""");
        File.WriteAllText(Path.Combine(oldRoot, "metadata.json"), """{"live.png":{"tags":["old"],"keywords":"keyword"},"dead.png":"dead"}""");
        File.WriteAllText(Path.Combine(oldRoot, "order.json"), """["dead.png","live.png"]""");
        using (var db = WindowsSqlite.OpenForWrite(Path.Combine(oldRoot, "features.db")))
        {
            db.Execute("CREATE TABLE image_features(image_path TEXT PRIMARY KEY, md5 TEXT, phash TEXT)", token.Token);
            db.Execute("INSERT INTO image_features VALUES ('live.png', 'old-bytes', 'old-phash'), ('dead.png', 'dead', null)", token.Token);
        }
        var maintained = await new LibraryCommands(backend, oldRoot).Run(new("maintain_library"), true, token.Token);
        Require(maintained.Result is { Count: 1, LibraryChanged: true, Destination: not null } &&
            maintained.Snapshot?.Categories.Single().Name == "Old", "migrate JSON and repair existing DB");
        Require(File.ReadAllBytes(original).SequenceEqual(png) &&
            File.ReadAllText(Path.Combine(maintained.Result.Destination!, "categories.json")) == oldCategories,
            "Maintenance must keep original images byte-identical and back up JSON before pruning dead references.");
        using (var backup = WindowsSqlite.OpenIfPresent(Path.Combine(maintained.Result.Destination!, "features.db"))!)
            Require(backup.Read("SELECT image_path FROM image_features", 1, token.Token).Count == 2, "maintenance backup predates cleanup");
        using (var db = WindowsSqlite.OpenIfPresent(Path.Combine(oldRoot, "features.db"))!)
            Require(db.Read("SELECT image_path, md5, phash FROM image_features", 3, token.Token).Single()
                .SequenceEqual(new[] { "live.png", "old-bytes", "old-phash" }), "maintenance preserves old hash columns and removes dead rows");
        Require((await new LibraryCommands(backend, oldRoot).Run(new("warm_indexes"), false, token.Token)).Result.Count == 0,
            "maintenance leaves a durable startup receipt");
        Require(NativeLibraryMaintenance.RunOnce(oldRoot, token.Token) is { Error: null, LibraryChanged: true } &&
            NativeLibraryMaintenance.RunOnce(oldRoot, token.Token) is { Error: null, LibraryChanged: false, Destination: null },
            "The versioned startup migration is not idempotent");

        if (NativeWebp.Available)
        {
            var photo = Path.Combine(folder, "不透明像素.webp");
            var bytes = Convert.FromBase64String(StaticWebp);
            File.WriteAllBytes(photo, bytes);
            var still = NativeWebp.Still(bytes, token.Token);
            Require(ResourceIdentityV1.HashPixels(still, token.Token) == "p:a63efaf42a796eb04d9ce295f67391b6", "WebP hidden RGB parity");
            var animation = NativeWebp.Preview(Convert.FromBase64String(AnimatedWebp), 80, token.Token);
            Require(animation is { HasMotion: true } && animation.Delay.TotalMilliseconds == 90 && animation.Advance() &&
                animation.Delay.TotalMilliseconds == 150 && animation.Advance() && animation.Advance() && !animation.Advance(), "WebP timing and plays");
            if (LegacyAnimatedGif.Available)
                CheckGif(NativeWebp.Gif(Convert.FromBase64String(AnimatedWebp), token.Token), "f:09320b2742217fe7dba7334b56593a34");
            var added = await command.Run(new("import", Paths: [photo]), true, token.Token);
            Require(added.Result.Count == 1 && added.Snapshot?.Images.Length == 2, "native WebP ingest");
            var motion = Path.Combine(folder, "动图.webp");
            File.WriteAllBytes(motion, Convert.FromBase64String(AnimatedWebp));
            var importedMotion = await command.Run(new("import", Paths: [motion]), true, token.Token);
            Require(importedMotion.Result.Count == 1 && importedMotion.Snapshot!.Images.Any(image =>
                ResourceIdentityV1.TryRead(image.Path, token.Token) == "f:09320b2742217fe7dba7334b56593a34"), "animated WebP ingest identity");
        }

        const string dummyToken = "123456:FAKE_Bot_Token";
        const string name = "SamplePack";
        var baseRequest = new LibraryCommand("tg_parse", Value: "tg://resolve?domain=addemoji&set=SamplePack", BotToken: dummyToken);
        HttpMessageHandler CreateHandler() => new Handler((_, uri) =>
        {
            Require(uri.Host == "api.telegram.org" && uri.AbsolutePath.Contains(dummyToken));
            if (uri.AbsolutePath.EndsWith("/getStickerSet", StringComparison.Ordinal))
                return Json(new { ok = true, result = new { name, title = dummyToken, sticker_type = "custom_emoji",
                    stickers = new[] { new { file_id = "asset_123", emoji = dummyToken, width = 3, height = 1, thumbnail = new { file_id = "thumb_1" } } } } });
            if (uri.AbsolutePath.EndsWith("/getFile", StringComparison.Ordinal))
                return Json(new { ok = true, result = new { file_path = "stickers/file_1.png" } });
            if (uri.AbsolutePath.EndsWith("/file_1.png", StringComparison.Ordinal))
                return new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(png) };
            throw new InvalidOperationException("unexpected route");
        });
        var parsed = await TelegramStickerOperations.Run(backend, data, baseRequest, token.Token, null, CreateHandler());
        Require(parsed is { SourceTitle: "***", SourceName: name, Items.Length: 1, SourceUrl: "https://t.me/addemoji/SamplePack" } &&
            parsed.Items[0] is { Label: "***", PreviewId: "thumb_1", Width: 3, Height: 1 }, "Telegram parsing, thumbnail and token redaction");
        var export = await TelegramStickerOperations.Run(backend, data, baseRequest with { Action = "tg_single_package",
            Value = parsed!.SourceName, SourceUrl = parsed.SourceUrl, PackageId = "测试收藏夹", Items = parsed.Items, Destination = folder },
            token.Token, null, CreateHandler());
        Require(export is { Error: null, Skipped: 1, LibraryChanged: true, Destination: not null }, "native single TG exchange");
        using (var zip = ZipFile.OpenRead(export!.Destination!))
        {
            using var manifest = JsonDocument.Parse(zip.GetEntry("manifest.json")!.Open());
            using var source = new StreamReader(zip.GetEntry("TG.txt")!.Open());
            Require(manifest.RootElement.GetProperty("package_id").GetString() == "测试收藏夹" &&
                source.ReadToEnd() == "https://t.me/addemoji/SamplePack\n", "TG exchange metadata");
        }
        var batch = await TelegramStickerOperations.Run(backend, data, baseRequest with { Action = "tg_batch_package",
            Links = ["SamplePack", "https://t.me/addemoji/SamplePack", "bad link"], Destination = folder },
            token.Token, null, CreateHandler());
        Require(batch is { Count: 2, Failed: 1, LibraryChanged: true, BatchRows.Length: 3 } &&
            batch.BatchRows.Select(row => row.Status).SequenceEqual(new[] { "done", "done", "failed" }) &&
            batch.BatchRows.Where(row => row.Path is not null).Select(row => row.Path).Distinct().Count() == 2, "native ordered TG batch");
        var redirects = 0;
        var redirectFailure = await TelegramStickerOperations.Run(backend, data, baseRequest with { Action = "tg_test_connection" }, token.Token, null,
            new Handler((_, _) =>
            {
                redirects++;
                var response = new HttpResponseMessage(HttpStatusCode.Found);
                response.Headers.Location = new Uri("https://example.test/" + dummyToken);
                return response;
            }));
        Require(redirectFailure?.Error == TelegramStickerClient.Failure && redirects == 1, "TG must never follow a redirect");
        await CheckHiddenTelegramDefaults(token.Token);
    }

    private static async Task CheckHiddenTelegramDefaults(CancellationToken token)
    {
        const string hiddenToken = "9876:synthetic-default";
        const string overrideToken = "1234:synthetic-override";
        const string hiddenProxy = "https://trusted.test/telegram";
        string? Defaults(string name) => name switch
        {
            TelegramConnectionDefaults.TokenVariable => hiddenToken,
            TelegramConnectionDefaults.ProxyVariable => hiddenProxy,
            _ => null,
        };
        var request = new LibraryCommand("tg_test_connection", BotToken: " ");
        int attempts = 0;
        using (var client = TelegramStickerClient.Create(request, out var validation, new Handler((_, url) =>
        {
            if (++attempts == 1)
            {
                Require(url.Host == "api.telegram.org", "A hidden default should try Telegram directly before the fallback proxy.");
                return new HttpResponseMessage(HttpStatusCode.ServiceUnavailable) { Content = new StringContent("{}") };
            }
            Require(url.Host == "trusted.test" && url.AbsolutePath == $"/telegram/bot{hiddenToken}/getMe", "Hidden TG configuration not used.");
            return Json(new { ok = true, result = new { is_bot = true } });
        }), Defaults))
        {
            Require(client is not null && validation is null && client.Redact(hiddenProxy + "/bot" + hiddenToken) == "***/bot***",
                "Hidden defaults must be usable and redacted.");
            await client!.Check(token);
            Require(attempts == 2, "Connection check must try the configured default fallback.");
        }
        using (var client = TelegramStickerClient.Create(request with { BotToken = overrideToken }, out _, new Handler((_, url) =>
        {
            Require(url.Host == "api.telegram.org" && url.AbsolutePath == $"/bot{overrideToken}/getMe", "Entered token must not reach the hidden proxy.");
            return Json(new { ok = true, result = new { is_bot = true } });
        }), Defaults)) await client!.Check(token);
        using var unsafeDefault = TelegramStickerClient.Create(request with { ApiProxy = "https://untrusted.test" }, out var error, readDefaults: Defaults);
        Require(unsafeDefault is null && error is not null, "A hidden token must not be sent to an override proxy.");
    }

    private static async Task CheckDropIn(WindowsInstall install, string folder, byte[] png, CancellationToken token)
    {
        var previous = Path.Combine(folder, "旧版", "bin", "data");
        var originals = Directory.CreateDirectory(Path.Combine(previous, "images")).FullName;
        File.WriteAllBytes(Path.Combine(originals, "甲.png"), png);
        File.WriteAllBytes(Path.Combine(originals, "乙.png"), png);
        File.WriteAllText(Path.Combine(previous, "config.json"), """{"appearance_mode":"dark","legacy_setting":true}""");
        Seed("categories.db", [
            "CREATE TABLE categories(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, icon_path TEXT, sort_order INTEGER DEFAULT 0)",
            "CREATE TABLE category_images(category_name TEXT, image_path TEXT, PRIMARY KEY(category_name, image_path))",
            "INSERT INTO categories(name, icon_path) VALUES ('旧分类','甲.png')",
            "INSERT INTO category_images VALUES ('旧分类','甲.png')"]);
        Seed("metadata.db", [
            "CREATE TABLE image_metadata(image_path TEXT PRIMARY KEY, tags TEXT, keywords TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "INSERT INTO image_metadata(image_path, tags, keywords) VALUES ('甲.png','[]','旧关键词')"]);
        Seed("order.db", [
            "CREATE TABLE item_orders(image_path TEXT PRIMARY KEY, sort_order INTEGER NOT NULL)",
            "INSERT INTO item_orders VALUES ('甲.png',0),('乙.png',1)"]);
        Seed("recent.db", [
            "CREATE TABLE recent_history(id INTEGER PRIMARY KEY AUTOINCREMENT, image_path TEXT UNIQUE NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "INSERT INTO recent_history(image_path) VALUES ('乙.png')"]);
        Seed("features.db", [
            "CREATE TABLE image_features(image_path TEXT PRIMARY KEY, md5 TEXT, dhash TEXT, phash TEXT, quality_score REAL DEFAULT 0.0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, sync_key TEXT, file_size INTEGER, mtime_ns INTEGER)",
            "INSERT INTO image_features(image_path, md5, dhash, phash) VALUES ('甲.png','old-md5','old-dhash','old-phash')"]);

        Directory.CreateDirectory(install.Data);
        File.WriteAllText(Path.Combine(install.Data, ".windows-library-maintenance-v1"), "stale receipt");
        var reserved = Path.Combine(Path.GetDirectoryName(install.Data)!, "data-before-upgrade");
        Directory.Move(install.Data, reserved);
        CopyTree(previous, install.Data);
        var original = Path.Combine(install.Data, "images", "甲.png");
        var before = await LibrarySnapshot.ReadAsync(install.Data, token);
        var commands = new LibraryCommands(install.Root, install.Data);
        var first = await commands.Run(new("startup_maintenance"), reload: true, token);
        var after = first.Snapshot!;
        Require(first.Result is { Error: null, Destination: not null } && after is not null, "A copied old data folder must migrate in place.");
        Require(after!.Images.Select(image => image.Name).SequenceEqual(new[] { "甲.png", "乙.png" }) &&
            after.Images[0].Keywords == "旧关键词" && after.Images[0].Categories is ["旧分类"] &&
            after.Categories is [{ Name: "旧分类" }] && after.Categories[0].IconPath == original && after.Recent is ["乙.png"] &&
            after.Flag("legacy_setting", false) && after.Dark && before.Images.Length == after.Images.Length &&
            File.ReadAllBytes(original).SequenceEqual(png) && !Directory.Exists(Path.Combine(install.Root, "data")),
            "Replacing the outer data directory must preserve the old images, categories, icon, keywords, order, settings and recent items.");
        using (var db = WindowsSqlite.OpenIfPresent(Path.Combine(install.Data, "features.db"))!)
            Require(db.Read("SELECT md5, dhash, phash FROM image_features WHERE image_path = '甲.png'", 3, token).Single()
                .SequenceEqual(new[] { "old-md5", "old-dhash", "old-phash" }), "Old hashes must survive the drop-in upgrade.");
        Require((await commands.Run(new("startup_maintenance"), reload: true, token)).Result is { LibraryChanged: false, Destination: null } &&
            !Directory.Exists(Path.Combine(previous, "maintenance-backups")) &&
            File.ReadAllText(Path.Combine(reserved, ".windows-library-maintenance-v1")) == "stale receipt",
            "Only the copied gallery should migrate, and only once; no stale migration receipt may carry over.");

        void Seed(string name, string[] sql)
        {
            using var db = WindowsSqlite.OpenForWrite(Path.Combine(previous, name));
            foreach (var statement in sql) db.Execute(statement, token);
        }

        static void CopyTree(string source, string destination)
        {
            Directory.CreateDirectory(destination);
            foreach (var file in Directory.EnumerateFiles(source)) File.Copy(file, Path.Combine(destination, Path.GetFileName(file)));
            foreach (var child in Directory.EnumerateDirectories(source)) CopyTree(child, Path.Combine(destination, Path.GetFileName(child)));
        }
    }

    private static void CheckGif(byte[] bytes, string expected)
    {
        using var gif = new MemoryStream(bytes);
        var actual = ResourceIdentityV1.TryRead(gif, CancellationToken.None);
        Require(actual == expected, $"Historical GIF byte parity: expected {expected}, got {actual}");
    }

    private static HttpResponseMessage Json(object payload) => new(HttpStatusCode.OK) { Content = new StringContent(JsonSerializer.Serialize(payload)) };

    private sealed class Handler(Func<HttpRequestMessage, Uri, HttpResponseMessage> create) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var response = create(request, request.RequestUri!);
            response.RequestMessage = request;
            return Task.FromResult(response);
        }
    }

    private static void Require(bool valid, string message = "Unexpected request")
    {
        if (!valid) throw new InvalidOperationException(message);
    }
}
