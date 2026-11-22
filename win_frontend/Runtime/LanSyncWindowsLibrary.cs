using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading;
using static SuzuEmojy.Windows.LibrarySnapshotReader;

namespace SuzuEmojy.Windows;

internal sealed record SyncCheckpoint(string Revision, SyncLibrary Library, string[]? Held = null);
internal sealed record SyncHistory(SyncCheckpoint Latest, SyncCheckpoint? Previous);

internal sealed class LanSyncWindowsLibrary(string data)
{
    private static readonly StringComparer Names = LibraryImagePaths.Comparer;
    private static readonly string[] MirrorFiles = ["categories.json", "category_icons.json", "metadata.json", "order.json", "recent.json", "hashes.json"];
    private const string State = "lan_sync_state";
    private const string Schema = "CREATE TABLE IF NOT EXISTS " + State + " (key TEXT PRIMARY KEY, value TEXT NOT NULL)";
    private string Root => Path.GetFullPath(data);
    private string Images => Path.Combine(Root, "images");
    private string Staging => Path.Combine(Root, ".lan-sync-stage");
    private string Trash => Path.Combine(Root, "lan-sync-trash");
    private string DeviceFile => Path.Combine(Root, ".lan-sync-device");

    internal sealed record Folder(string Local, string Name, string? Icon, string[] Members);
    internal sealed record Capture(SyncLibrary Library, Dictionary<string, Folder> Folders,
        Dictionary<string, List<string>> Paths, Dictionary<string, string> Icons, string[] Order, string[] Skipped);
    internal sealed record Plan(Capture Before, SyncLibrary Target, Dictionary<string, string> Bindings,
        string Peer, SyncHistory History, HashSet<string>? Hold = null);
    internal sealed record FileChanges(string Peer, string Revision, string[] Adding, string[] Removing, string[] ExistingMirrors);

    internal static void RecoverPending(string data, CancellationToken token) => new LanSyncWindowsLibrary(data).Recover(token);

    internal string Device(CancellationToken token)
    {
        Directory.CreateDirectory(Root);
        RuntimePrimitives.EnsureNotReparse(DeviceFile);
        if (!File.Exists(DeviceFile))
        {
            var temp = Path.Combine(Root, $".lan-device-{Guid.NewGuid():N}");
            try
            {
                File.WriteAllText(temp, Guid.NewGuid().ToString("N"));
                RuntimePrimitives.CommitFile(temp, DeviceFile, overwrite: false, token);
            }
            finally { RuntimePrimitives.DiscardTemporaryFile(temp); }
        }
        var device = File.ReadAllText(DeviceFile);
        if (!LanSyncMerge.IsId(device)) throw new IOException("电脑同步设备标识无效。");
        Recover(token);
        return device;
    }

    internal static string? ReadState(WindowsSqlite? db, string key, CancellationToken token)
    {
        if (db is null || db.Read("SELECT name FROM sqlite_master WHERE type = 'table' AND name = '" + State + "'", 1, token).Count == 0)
            return null;
        return db.Read("SELECT value FROM " + State + " WHERE key = ?", 1, token, key).SingleOrDefault()?[0];
    }

    internal SyncHistory? History(string peer, CancellationToken token)
    {
        using var db = WindowsSqlite.OpenIfPresent(Path.Combine(Root, "features.db"));
        return ReadState(db, "revision:" + peer, token) is { } value ? JsonSerializer.Deserialize<SyncHistory>(value) : null;
    }

    internal Capture Read(string device, string peer, CancellationToken token)
    {
        RuntimePrimitives.EnsureNotReparse(Images);
        // The standard reader tolerates a corrupt legacy file for browsing.
        // A deletion-capable snapshot must fail closed instead.
        foreach (var name in new[] { "categories.json", "category_icons.json", "metadata.json", "order.json" })
        {
            var path = Path.Combine(Root, name);
            if (!File.Exists(path)) continue;
            using var input = new StreamReader(path);
            using var json = JsonDocument.Parse(input.ReadToEnd());
            if (json.RootElement.ValueKind != (name == "order.json" ? JsonValueKind.Array : JsonValueKind.Object))
                throw new IOException($"无法读取同步索引：{name}");
        }
        using var state = WindowsSqlite.OpenIfPresent(Path.Combine(Root, "features.db"));
        var bindings = ReadState(state, "bindings:" + peer, token) is { } jsonBindings
            ? JsonSerializer.Deserialize<Dictionary<string, string>>(jsonBindings)! : new();
        using var categoryDb = WindowsSqlite.OpenIfPresent(Path.Combine(Root, "categories.db"));
        var rowIds = categoryDb?.Read("SELECT name, id FROM categories", 2, token)
            .ToDictionary(row => row[0]!, row => "db:" + row[1]) ?? new();
        var groups = new List<(string Name, string? Icon, List<string> Members)>();
        ReadCategories(Root, ReadObject(Root, "category_icons.json"), groups, token);
        var folders = new Dictionary<string, Folder>();
        var categories = new List<SyncCategory>();
        var iconFiles = new Dictionary<string, string>();
        var library = LibrarySnapshotReader.Read(Root, token);
        foreach (var (name, icon, members) in groups.Where(g => !Reserved(g.Name)))
        {
            token.ThrowIfCancellationRequested();
            var local = rowIds.GetValueOrDefault(name) ?? "legacy:" + name;
            var id = bindings.GetValueOrDefault(local) ?? LanSyncHost.Hex(LanSyncHost.Hash(Encoding.UTF8.GetBytes(device + ":" + local))).Substring(0, 32);
            folders.Add(id, new(local, name, icon, members.ToArray()));
            SyncIcon? portable = null;
            if (icon is not null && !string.IsNullOrWhiteSpace(icon))
            {
                if (!IsIconImage(icon)) portable = new("text", icon);
                else
                {
                    var source = IconPath(Root, icon) ?? throw new IOException($"收藏夹图标无法读取：{name}");
                    using var file = File.OpenRead(source);
                    if (file.Length is <= 0 or > (48L << 20)) throw new IOException($"收藏夹图标过大：{name}");
                    var asset = "icons/" + LanSyncHost.Hex(LanSyncHost.HashFile(file, token)) + Path.GetExtension(source).ToLowerInvariant();
                    iconFiles[asset] = source;
                    portable = new("image", asset);
                }
            }
            categories.Add(new(id, name, portable));
        }
        var ids = categories.ToDictionary(c => c.Name, c => c.Id);
        var paths = new Dictionary<string, List<string>>();
        var resources = new Dictionary<string, SyncResource>();
        var skipped = new List<string>();
        var checkedImages = LanSyncWorkers.Map(library.Images.Length, i =>
            ReadImage(library.Images[i], token), token);
        for (int i = 0; i < checkedImages.Length; i++)
        {
            token.ThrowIfCancellationRequested();
            var sticker = library.Images[i];
            var (key, failed) = checkedImages[i];
            if (failed) skipped.Add(sticker.Name);
            if (key is null) continue;
            var tags = LanSyncMerge.Words(sticker.Keywords);
            var memberships = sticker.Categories.Select(name => ids[name]).OrderBy(id => id, StringComparer.Ordinal).ToArray();
            if (resources.TryGetValue(key, out var old))
            {
                resources[key] = new(key, old.Keywords.Union(tags).OrderBy(w => w, StringComparer.Ordinal).ToArray(),
                    old.Categories.Union(memberships).OrderBy(id => id, StringComparer.Ordinal).ToArray());
                paths[key].Add(sticker.Name);
            }
            else
            {
                resources.Add(key, new(key, tags, memberships));
                paths.Add(key, [sticker.Name]);
            }
        }
        var snapshot = new SyncLibrary(categories.ToArray(), resources.Values.ToArray());
        LanSyncMerge.Validate(snapshot);
        return new(snapshot, folders, paths, iconFiles, library.Images.Select(s => s.Name).ToArray(), skipped.ToArray());
    }

    private static (string? Key, bool Failed) ReadImage(Sticker sticker, CancellationToken token)
    {
        var ext = Path.GetExtension(sticker.Name).ToLowerInvariant();
        if (ext is not (".png" or ".gif")) return (null, false);
        try
        {
            RuntimePrimitives.EnsureNotReparse(sticker.Path);
            using var file = File.OpenRead(sticker.Path);
            if (ext == ".png" && ApngDecoder.HasAnimationControl(file)) return (null, false);
            if (ext == ".gif" && !GifSyncValidation.Check(file, token)) return (null, true);
            var key = ResourceIdentityV1.TryRead(file, token);
            return key is not null && key.StartsWith(ext == ".gif" ? "f:" : "p:", StringComparison.Ordinal)
                ? (key, false) : (null, true);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or ArgumentException or FormatException or NotSupportedException)
        { return (null, true); }
    }

    internal void Export(string path, SyncAssets assets, Capture from, CancellationToken token) =>
        ExchangeV1Export.TryRun(Root, path, null, token, null, syncAssets: assets, syncPaths: from.Paths);

    internal void Apply(string device, Plan plan, string? package, CancellationToken token,
        ExchangeV1Archive? verified = null)
    {
        var before = Read(device, plan.Peer, token);
        if (!Same(before, plan.Before)) throw new IOException("同步期间电脑图库已修改，请重试。");
        var target = plan.Target;
        var needs = SyncAssets.Needed(before.Library, target);
        var hold = plan.Hold ?? new();
        var keep = target.Resources.Select(r => r.Key).Concat(hold).ToHashSet();
        var removing = before.Library.Resources.Where(r => !keep.Contains(r.Key)).SelectMany(r => before.Paths[r.Key]).ToArray();
        var managed = before.Paths.Where(pair => !hold.Contains(pair.Key)).SelectMany(pair => pair.Value).ToHashSet(Names);
        var allFiles = before.Order.ToHashSet(Names);
        var stayingFolders = plan.Bindings.Values.ToHashSet();
        if (before.Folders.Any(pair => !stayingFolders.Contains(pair.Key) &&
            pair.Value.Members.Any(name => allFiles.Contains(name) && !managed.Contains(name))))
            throw new IOException("要删除的收藏夹含有不在同步范围内的图片，请先在电脑上处理这些图片。");
        using var incoming = verified is not null || package is null ? null : File.OpenRead(package);
        using var zip = incoming is null ? null : new ZipArchive(incoming, ZipArchiveMode.Read);
        var archive = verified ?? (zip is null ? null : ExchangeV1Archive.Read(zip, token, null) ?? throw new IOException("收到不支持的资源包。"));
        var resources = archive?.Resources.Where(r => !hold.Contains(r.Spec["sync_key"]!.GetValue<string>()))
            .ToDictionary(r => r.Spec["sync_key"]!.GetValue<string>()) ?? new();
        var icons = archive?.Icons.ToDictionary(i => i.Path) ?? new();
        if (!resources.Keys.ToHashSet().SetEquals(needs.Keys) || !icons.Keys.ToHashSet().SetEquals(needs.Icons))
            throw new IOException("收到的资源包与同步清单不符。");

        var revision = plan.History.Latest.Revision;
        var stage = Path.Combine(Staging, revision);
        var removedDir = Path.Combine(Trash, revision);
        var additions = Path.Combine(stage, "add");
        var iconDir = Path.Combine(Root, "category_icons");
        RuntimePrimitives.EnsureNotReparse(Staging);
        RuntimePrimitives.EnsureNotReparse(Trash);
        RuntimePrimitives.EnsureNotReparse(iconDir);
        RuntimePrimitives.EnsureNotReparse(stage);
        RuntimePrimitives.EnsureNotReparse(removedDir);
        Directory.CreateDirectory(Images);
        Directory.CreateDirectory(additions);
        var namesByKey = new Dictionary<string, List<string>>(before.Paths);
        var newFiles = new List<string>();
        var publishedIcons = new List<string>();
        var committed = false;
        try
        {
            foreach (var (key, res) in resources)
            {
                var preferred = Name(res.Spec["display_name"]?.GetValue<string>());
                var suffix = key.StartsWith("p:", StringComparison.Ordinal) ? ".png" : ".gif";
                if (!LibraryResourceIngest.ValidName(preferred) || !Path.GetExtension(preferred).Equals(suffix, StringComparison.OrdinalIgnoreCase)
                    || allFiles.Contains(preferred) || File.Exists(Path.Combine(Images, preferred))) preferred = $"sync-{Guid.NewGuid():N}{suffix}";
                allFiles.Add(preferred);
                Copy(res.Asset, Path.Combine(additions, preferred), token);
                newFiles.Add(preferred);
                namesByKey.Add(key, [preferred]);
            }
            var iconNames = new Dictionary<string, string>();
            foreach (var icon in target.Categories.Select(c => c.Icon).OfType<SyncIcon>().Where(i => i.Type == "image").Distinct())
            {
                // Copy even a local gallery-backed icon to an independent attachment before an image can be deleted.
                var file = Path.Combine(iconDir, Path.GetFileName(icon.Value));
                Directory.CreateDirectory(iconDir);
                RuntimePrimitives.EnsureNotReparse(file);
                if (!File.Exists(file))
                {
                    var temp = Path.Combine(stage, Path.GetFileName(file));
                    if (icons.TryGetValue(icon.Value, out var asset)) Copy(asset, temp, token);
                    else File.Copy(before.Icons[icon.Value], temp);
                    VerifyIcon(temp, icon.Value, token);
                    if (!RuntimePrimitives.CommitFile(temp, file, overwrite: false, token)) throw new IOException("同步图标位置已被占用。");
                    publishedIcons.Add(file);
                }
                else VerifyIcon(file, icon.Value, token);
                iconNames.Add(icon.Value, $"category_icons/{Path.GetFileName(file)}");
            }
            token.ThrowIfCancellationRequested();
            // The rename journal is durable before touching the live image directory.
            // Its SQLite revision decides whether recovery replays or rolls back.
            var backup = Directory.CreateDirectory(Path.Combine(stage, "index")).FullName;
            var existingMirrors = new List<string>();
            foreach (var mirror in MirrorFiles)
            {
                var original = Path.Combine(Root, mirror);
                RuntimePrimitives.EnsureNotReparse(original);
                if (!File.Exists(original)) continue;
                using var input = File.OpenRead(original);
                using var copy = new FileStream(Path.Combine(backup, mirror), FileMode.CreateNew, FileAccess.Write);
                input.CopyTo(copy);
                copy.Flush(flushToDisk: true);
                existingMirrors.Add(mirror);
            }
            var changes = new FileChanges(plan.Peer, revision, newFiles.ToArray(), removing, existingMirrors.ToArray());
            var pending = Path.Combine(stage, "changes.tmp");
            using (var journal = new FileStream(pending, FileMode.CreateNew, FileAccess.Write))
            {
                JsonSerializer.Serialize(journal, changes);
                journal.Flush(flushToDisk: true);
            }
            if (!RuntimePrimitives.CommitFile(pending, Path.Combine(stage, "changes.json"), overwrite: false, token))
                throw new IOException("同步恢复记录位置已被占用。");
            if (removing.Length > 0) Directory.CreateDirectory(removedDir);
            foreach (var name in removing) File.Move(Path.Combine(Images, name), Path.Combine(removedDir, name));
            foreach (var name in newFiles) File.Move(Path.Combine(additions, name), Path.Combine(Images, name));
            Commit(plan, before, namesByKey, iconNames, removing);
            committed = true;
        }
        finally
        {
            if (!committed && File.Exists(Path.Combine(stage, "changes.json"))) RecoverStage(stage, CancellationToken.None);
            if (!committed) foreach (var file in publishedIcons) RuntimePrimitives.DiscardTemporaryFile(file);
            CleanStage(stage);
        }
    }

    private void Commit(Plan plan, Capture before, Dictionary<string, List<string>> namesByKey,
        Dictionary<string, string> iconNames, string[] removing)
    {
        var token = CancellationToken.None;
        using var db = LibraryDatabases.TryOpen(Root, recent: true, token, out bool hasRecent)
            ?? throw new IOException("电脑图库数据库无法安全写入。");
        var removingSet = removing.ToHashSet(Names);
        var known = before.Paths.Where(pair => plan.Hold?.Contains(pair.Key) != true).SelectMany(x => x.Value).ToHashSet(Names);
        var mirrors = new List<(string Name, object Data)>();
        var categories = new Dictionary<string, HashSet<string>>();
        var iconMirror = new Dictionary<string, string>();
        var metadata = ReadObject(Root, "metadata.json");
        var bindings = new Dictionary<string, string>();
        var catTable = "category.categories";
        var winRows = db.Read($"SELECT name, id FROM {catTable}", 2, token).ToDictionary(row => row[0]!, row => row[1]!);
        var survivingNames = plan.Bindings.Values.Select(id => before.Folders[id].Name).ToHashSet();
        foreach (var (name, id) in winRows)
            if (!survivingNames.Contains(name)) db.Execute($"DELETE FROM {catTable} WHERE id = ?", token, id);
            else if (!Reserved(name)) db.Execute($"UPDATE {catTable} SET name = ? WHERE id = ?", token, $"__suzu_sync_{Guid.NewGuid():N}", id);
        db.Execute("DELETE FROM category.category_images", token);
        for (int i = 0; i < plan.Target.Categories.Length; i++)
        {
            var cat = plan.Target.Categories[i];
            var old = plan.Bindings.TryGetValue(cat.Id, out var oldId) ? before.Folders[oldId] : null;
            var icon = cat.Icon switch { { Type: "text" } text => text.Value, { Type: "image" } image => iconNames[image.Value], _ => null };
            if (old is not null && winRows.TryGetValue(old.Name, out var id))
                db.Execute($"UPDATE {catTable} SET name = ?, icon_path = ?, sort_order = ? WHERE id = ?", token, cat.Name, icon, i, id);
            else
                db.Execute($"INSERT INTO {catTable}(name, icon_path, sort_order) VALUES (?, ?, ?)", token, cat.Name, icon, i);
            var persisted = db.Read($"SELECT id FROM {catTable} WHERE name = ?", 1, token, cat.Name).Single()[0];
            bindings.Add("db:" + persisted, cat.Id);
            if (icon is not null) iconMirror.Add(cat.Name, icon);
            categories.Add(cat.Name, (old?.Members ?? []).Where(name => !known.Contains(name)).ToHashSet(Names));
        }
        var categoryNames = plan.Target.Categories.ToDictionary(c => c.Id, c => c.Name);
        var existingMeta = db.Read("SELECT image_path FROM meta.image_metadata", 1, token)
            .Where(row => row[0] is not null).ToLookup(row => Name(row[0]), Names);
        foreach (var aliases in metadata.Keys.Where(key => known.Contains(Name(key))).ToArray()) metadata.Remove(aliases);
        var order = new List<string>();
        var surviving = plan.Target.Resources.SelectMany(r => namesByKey[r.Key]).GetEnumerator();
        // Leave PC-only files in their relative positions; replace only synced slots.
        foreach (var name in before.Order)
            if (!known.Contains(name)) order.Add(name);
            else if (surviving.MoveNext()) order.Add(surviving.Current);
        while (surviving.MoveNext()) order.Add(surviving.Current);
        foreach (var row in plan.Target.Resources)
        {
            var tags = string.Join(" ", row.Keywords);
            foreach (var name in namesByKey[row.Key])
            {
                metadata[name] = JsonSerializer.SerializeToElement(tags);
                if (!existingMeta.Contains(name)) db.Execute("INSERT INTO meta.image_metadata(image_path, keywords) VALUES (?, ?)", token, name, tags);
                else foreach (var alias in existingMeta[name])
                    db.Execute("UPDATE meta.image_metadata SET keywords = ? WHERE image_path = ?", token, tags, alias[0]);
                foreach (var id in row.Categories) categories[categoryNames[id]].Add(name);
            }
        }
        foreach (var (category, members) in categories)
            foreach (var member in members)
                db.Execute("INSERT INTO category.category_images(category_name, image_path) VALUES (?, ?)", token, category, member);
        if (removing.Length > 0)
        {
            foreach (var table in new[] { "image_features", "sync_key_checks", "meta.image_metadata" }.Concat(hasRecent ? ["history.recent_history"] : Array.Empty<string>()))
                foreach (var row in db.Read($"SELECT DISTINCT image_path FROM {table}", 1, token))
                    if (row[0] is { } path && removingSet.Contains(Name(path)))
                        db.Execute($"DELETE FROM {table} WHERE image_path = ?", token, path);
            var hashes = ReadObject(Root, "hashes.json");
            foreach (var entry in hashes.Where(pair => removingSet.Contains(Name(pair.Key)) || removingSet.Contains(Name(Text(pair.Value)))).ToArray())
                hashes.Remove(entry.Key);
            mirrors.Add(("hashes.json", hashes));
            mirrors.Add(("recent.json", ReadNames(Root, "recent.json").Where(p => !removingSet.Contains(Name(p))).ToArray()));
        }
        db.Execute("DELETE FROM gallery_order.item_orders", token);
        for (int i = 0; i < order.Count; i++)
            db.Execute("INSERT INTO gallery_order.item_orders(image_path, sort_order) VALUES (?, ?)", token, order[i], i);
        mirrors.Add(("categories.json", categories));
        mirrors.Add(("category_icons.json", iconMirror));
        mirrors.Add(("metadata.json", metadata));
        mirrors.Add(("order.json", order));
        db.Execute(Schema, token);
        db.Execute("INSERT OR REPLACE INTO " + State + "(key,value) VALUES (?,?)", token, "revision:" + plan.Peer, JsonSerializer.Serialize(plan.History));
        db.Execute("INSERT OR REPLACE INTO " + State + "(key,value) VALUES (?,?)", token, "bindings:" + plan.Peer, JsonSerializer.Serialize(bindings));
        LibraryEdits.Commit(db, Root, token, mirrors.ToArray());
    }

    private void Recover(CancellationToken token)
    {
        RuntimePrimitives.EnsureNotReparse(Staging);
        if (!Directory.Exists(Staging)) return;
        foreach (var stage in Directory.EnumerateDirectories(Staging))
        {
            if (!LanSyncMerge.IsId(Path.GetFileName(stage))) continue;
            RuntimePrimitives.EnsureNotReparse(stage);
            if (File.Exists(Path.Combine(stage, "changes.json"))) RecoverStage(stage, token);
            CleanStage(stage);
        }
    }

    private void RecoverStage(string stage, CancellationToken token)
    {
        var changes = JsonSerializer.Deserialize<FileChanges>(File.ReadAllText(Path.Combine(stage, "changes.json")))
            ?? throw new IOException("无法恢复上次同步。");
        if (!LanSyncMerge.IsId(changes.Peer) || changes.Revision != Path.GetFileName(stage) ||
            changes.ExistingMirrors is null || changes.ExistingMirrors.Any(name => !MirrorFiles.Contains(name)) ||
            changes.Adding.Concat(changes.Removing).Any(name => !LibraryResourceIngest.ValidName(name) || Name(name) != name))
            throw new IOException("上次同步的恢复记录无效。");
        if (History(changes.Peer, token)?.Latest.Revision == changes.Revision) return;
        var removed = Path.Combine(Trash, changes.Revision);
        RuntimePrimitives.EnsureNotReparse(Trash);
        RuntimePrimitives.EnsureNotReparse(removed);
        foreach (var name in changes.Adding)
        {
            var path = Path.Combine(Images, name);
            var staged = Path.Combine(stage, "add", name);
            if (File.Exists(path) && !File.Exists(staged)) File.Move(path, staged);
        }
        foreach (var name in changes.Removing)
        {
            var path = Path.Combine(removed, name);
            if (File.Exists(path)) File.Move(path, Path.Combine(Images, name));
        }
        var backup = Path.Combine(stage, "index");
        RuntimePrimitives.EnsureNotReparse(backup);
        var existing = changes.ExistingMirrors.ToHashSet();
        foreach (var mirror in MirrorFiles)
        {
            var target = Path.Combine(Root, mirror);
            RuntimePrimitives.EnsureNotReparse(target);
            if (!existing.Contains(mirror)) { File.Delete(target); continue; }
            var temp = Path.Combine(stage, $"restore-{Guid.NewGuid():N}.tmp");
            var original = Path.Combine(backup, mirror);
            RuntimePrimitives.EnsureNotReparse(original);
            File.Copy(original, temp);
            RuntimePrimitives.CommitFile(temp, target, overwrite: true, CancellationToken.None);
        }
    }

    private static void CleanStage(string stage)
    {
        try
        {
            // Retire the journal before removing the files it references. A
            // second crash during cleanup must not replay an incomplete backup.
            File.Delete(Path.Combine(stage, "changes.json"));
            foreach (var child in new[] { "add", "index" }.Select(name => Path.Combine(stage, name)))
            {
                if (!Directory.Exists(child)) continue;
                RuntimePrimitives.EnsureNotReparse(child);
                foreach (var file in Directory.EnumerateFiles(child)) File.Delete(file);
                Directory.Delete(child);
            }
            foreach (var file in Directory.EnumerateFiles(stage)) File.Delete(file);
            Directory.Delete(stage);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private static bool Same(Capture a, Capture b) => JsonSerializer.Serialize(a.Library) == JsonSerializer.Serialize(b.Library)
        && a.Order.SequenceEqual(b.Order, Names) && a.Folders.Keys.SequenceEqual(b.Folders.Keys)
        && a.Skipped.SequenceEqual(b.Skipped, Names)
        && a.Folders.All(pair => pair.Value.Local == b.Folders[pair.Key].Local &&
            pair.Value.Members.SequenceEqual(b.Folders[pair.Key].Members, Names))
        && a.Paths.All(pair => b.Paths.TryGetValue(pair.Key, out var paths) && pair.Value.SequenceEqual(paths, Names));

    private static void Copy(ExchangeV1Archive.Asset asset, string file, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        using var input = asset.Entry.Open();
        using var output = new FileStream(file, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        input.CopyTo(output);
        output.Flush(flushToDisk: true);
        if (output.Length != asset.Entry.Length) throw new IOException("资源写入不完整。");
    }

    private static void VerifyIcon(string file, string asset, CancellationToken token)
    {
        using var stream = File.OpenRead(file);
        if (!asset.StartsWith("icons/" + LanSyncHost.Hex(LanSyncHost.HashFile(stream, token)) + ".", StringComparison.Ordinal))
            throw new IOException("收藏夹图标校验失败。");
    }
}
