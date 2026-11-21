using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using SuzuEmojy.Windows;

internal static class NativeQqTests
{
    internal static async Task Run(string output, string? legacy = null)
    {
        var fixture = Directory.CreateDirectory(Path.Combine(output, $"QQ-{Guid.NewGuid():N}")).FullName;
        var root = Directory.CreateDirectory(Path.Combine(fixture, "QQ缓存")).FullName;
        var account = Path.Combine(root, "12345", "nt_qq", "nt_data");
        var png = Convert.FromBase64String(NativeIdentityTests.Rgba);
        var apng = Convert.FromBase64String(NativeApngTests.Fixture);
        var gif = Convert.FromBase64String("R0lGODlhBgAFAIEAAMg8tAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQACQAAACwAAAAABgAFAAAIDAABCBxIsKDBgwIDAgAh+QQBDwABACwAAAAABgAFAIEoZCgAAAAAAAAAAAAIDAABCBxIsKDBgwIDAgA7");
        var hidden = (byte[])gif.Clone();
        for (int i = 0; i < hidden.Length; i++) if (i % 50 < 20) hidden[i] ^= 255;
        Put("Emoji/personal_emoji/Thumb/ignore.png", png);
        Put("Emoji/personal_emoji/Ori/png/first.png", png);
        var first = Put("Emoji/personal_emoji/Ori/apng/first_1.png", apng);
        Put("Emoji/personal_emoji/Ori/png/second.png", png);
        var second = Put("Emoji/personal_emoji/Ori/ori/second.gif", gif);
        var chat = Put("Pic/2026-11/Ori/chat", png);
        Put("Pic/2026-11/Thumb/ignore.png", png);
        var market = Put("Emoji/marketface/collection/sticker", hidden);
        Put("Emoji/marketface/collection/not-an-image", [1, 2, 3]);
        var data = Directory.CreateDirectory(Path.Combine(fixture, "data")).FullName;
        var commands = new LibraryCommands(Path.Combine(fixture, "no-python"), data);
        var discover = await commands.Run(new("qq_discover", Value: root), false);
        Require(discover.Result.Sources?.Single() is { Key: "12345", Options.Length: 3 }, "QQ discover");
        var ini = Path.Combine(fixture, "UserDataInfo.ini");
        File.WriteAllText(ini, $"[UserDataSet]\nUserDataSavePath={root}\n", Encoding.Unicode);
        Require(QqCacheDiscovery.ConfiguredPath(ini) == root, "QQ UTF-16 discovery");
#if NETFRAMEWORK
        File.WriteAllText(ini, $"[UserDataSet]\nUserDataSavePath={root}\n", Encoding.GetEncoding(54936));
        Require(QqCacheDiscovery.ConfiguredPath(ini) == root, "QQ GB18030 discovery");
#endif
        var docs = Path.Combine(fixture, "documents");
        var tencent = Path.Combine(docs, "Tencent Files");
        Directory.CreateDirectory(Path.Combine(tencent, "124", "nt_qq"));
        Require(QqCacheDiscovery.FindDocuments([fixture, docs]) == tencent, "QQ documents fallback");
        var personal = await Scan("personal_emoji", [first, second], true);
        var pictures = await Scan("pic", [chat], false);
        var mall = await Scan("marketface", [market], true);
        LibraryCommand Request(string action, string kind, ExternalSticker[] items) =>
            new(action, Value: root, Kind: kind, Account: "12345", Items: items);
        var detail = await commands.Run(Request("qq_detail", "personal_emoji", personal), false);
        Require(detail.Result.Previews?.Single() is { Native.HasMotion: true, Animation: null }, "QQ APNG detail");
        var tiles = await commands.Run(Request("qq_previews", "marketface", mall), false);
        Require(tiles.Result.Previews?.Single() is { Still.IsFrozen: true, Animation: null }, "QQ marketface tile must cross threads without a PNG round trip");
        var mallDetail = await commands.Run(Request("qq_detail", "marketface", mall), false);
        Require(mallDetail.Result.Previews?.Single() is { Native: { HasMotion: true } frames, Animation: null, Still.IsFrozen: true } &&
            frames.Current.IsFrozen && frames.Advance() && frames.Current.IsFrozen,
            "QQ marketface animation must deliver predecoded frames, not compressed bytes for UI-thread decoding");
        var destination = Directory.CreateDirectory(Path.Combine(fixture, "exports")).FullName;
        File.WriteAllText(Path.Combine(destination, "chat.png"), "existing");
        var export = await commands.Run(Request("qq_export", "pic", pictures) with { Destination = destination }, false);
        Require(export.Result.Count == 1 && File.ReadAllText(Path.Combine(destination, "chat.png")) == "existing" &&
            File.ReadAllBytes(Path.Combine(destination, "chat_1.png")).SequenceEqual(png), "QQ export without overwrite");
        var imported = await commands.Run(Request("qq_import", "marketface", mall) with
            { Paths = [market], Category = "QQ 新分类" }, true);
        Require(imported.Result.Count == 1 && imported.Snapshot?.Categories.Single().IconPath == imported.Snapshot?.Images.Single().Path &&
            File.ReadAllBytes(imported.Snapshot!.Images.Single().Path).SequenceEqual(gif), "QQ marketface import and auto icon");
        var toRecycle = new List<string>();
        var recycle = QqStickerOperations.Run(data, Request("qq_recycle", "personal_emoji", [personal[1], personal[1]]),
            CancellationToken.None, null, toRecycle.Add);
        Require(recycle is { Count: 1, Failed: 0 } && toRecycle.SequenceEqual(new[] { second }) && File.Exists(second),
            $"QQ recycle guard: {recycle?.Count}/{recycle?.Failed}; {recycle?.Error} {string.Join("; ", recycle?.Warnings ?? [])}; paths {string.Join("; ", toRecycle)} vs {second}");
        toRecycle.Clear();
        File.SetLastWriteTimeUtc(chat, DateTime.UtcNow.AddDays(-1));
        recycle = QqStickerOperations.Run(data, Request("qq_recycle", "pic", pictures), CancellationToken.None, null, toRecycle.Add);
        Require(recycle is { Count: 0, Failed: 1 } && toRecycle.Count == 0 && File.Exists(chat), "QQ refuses a changed file");

        string Put(string relative, byte[] bytes)
        {
            var path = Path.GetFullPath(Path.Combine(account, relative));
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllBytes(path, bytes);
            return path;
        }

        async Task<ExternalSticker[]> Scan(string kind, string[] paths, bool animated)
        {
            var progress = new Capture();
            var native = await commands.Run(new("qq_scan", Value: root, Account: "12345", Kind: kind), false, progress: progress);
            var items = native.Result.Items ?? throw new InvalidOperationException("Missing QQ scan results.");
            Require(items.Select(item => item.Id).SequenceEqual(paths) && items.All(item =>
                item.Animated == animated && item.Width > 0 && item.Height > 0 && item.FileIdentity is not null), $"QQ scan: {kind}");
            Require(progress.Values.Any(value => value.Phase == "qq_walk") &&
                progress.Values.Any(value => value is { Phase: "qq_check", Current: > 0 } && value.Current == value.Total) &&
                progress.Values.Last() is { Phase: "qq_metadata" } last && last.Current == paths.Length, $"QQ progress: {kind}");
            if (legacy is null) return items;
            string request = JsonSerializer.Serialize(new { action = "qq_scan", value = root, account = "12345", kind });
            using var result = JsonDocument.Parse(await PythonBridge.Request(legacy, "services.gallery_commands", request, data,
                CancellationToken.None, null));
            var original = result.RootElement.GetProperty("items").EnumerateArray().ToArray();
            Require(original.Length == items.Length, $"Legacy QQ count: {kind}");
            for (int i = 0; i < original.Length; i++)
            {
                var oldId = original[i].GetProperty("file_identity").GetString();
                Require(oldId == items[i].FileIdentity && original[i].GetProperty("id").GetString() == items[i].Id,
                    $"QQ scan differs from legacy: {kind}; expected {oldId}; got {items[i].FileIdentity}");
            }
            return items;
        }
    }

    private sealed class Capture : IProgress<LibraryProgress>
    {
        internal List<LibraryProgress> Values { get; } = [];
        public void Report(LibraryProgress value) => Values.Add(value);
    }

    private static void Require(bool valid, string message)
    {
        if (!valid) throw new InvalidOperationException(message);
    }
}
