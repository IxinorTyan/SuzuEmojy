using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using static SuzuEmojy.Windows.LibrarySnapshotReader;

namespace SuzuEmojy.Windows;

internal static class NativeLibraryMaintenance
{
    private static readonly string[] Mirrors = ["hashes.json", "metadata.json", "categories.json", "category_icons.json", "order.json", "recent.json"];
    private static readonly string[] Databases = ["features.db", "metadata.db", "categories.db", "order.db", "recent.db"];
    private const string HistorySchema = "CREATE TABLE IF NOT EXISTS recent_history (id INTEGER PRIMARY KEY AUTOINCREMENT, " +
        "image_path TEXT NOT NULL, used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)";
    private static readonly LibraryResult Empty = new(0, 0, 0, null, [], null, null);
    private const string StartupReceipt = ".windows-library-maintenance-v1";

    internal static LibraryResult? RunOnce(string data, CancellationToken token)
    {
        if (!Directory.Exists(data)) return Empty;
        var root = RuntimePrimitives.ExistingPath(Path.GetFullPath(data));
        if (!Directory.Exists(Path.Combine(root, "images")) && !Mirrors.Concat(Databases)
            .Any(name => File.Exists(Path.Combine(root, name)))) return Empty;
        var receipt = Path.Combine(root, StartupReceipt);
        RuntimePrimitives.EnsureNotReparse(receipt);
        if (File.Exists(receipt)) return Empty;
        // Serialize first-time maintenance across two instances of this build.
        // The lock name is stable; the temporary handle, not its contents, owns the lock.
        var guard = receipt + ".lock";
        RuntimePrimitives.EnsureNotReparse(guard);
        using var lease = File.Open(guard, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        if (File.Exists(receipt)) return Empty;
        var result = TryRun(root, token, null);
        if (result is { Error: null })
        {
            var pending = receipt + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                using (var stream = new FileStream(pending, FileMode.CreateNew, FileAccess.Write))
                using (var writer = new StreamWriter(stream))
                {
                    writer.WriteLine(result.Destination ?? "");
                    writer.Flush();
                    stream.Flush(flushToDisk: true);
                }
                RuntimePrimitives.CommitFile(pending, receipt, overwrite: false, token);
            }
            finally { RuntimePrimitives.DiscardTemporaryFile(pending); }
        }
        return result;
    }

    internal static LibraryResult? TryRun(string data, CancellationToken token, IProgress<LibraryProgress>? progress)
    {
        var root = Path.GetFullPath(data);
        if (!Directory.Exists(root)) return Empty;
        root = RuntimePrimitives.ExistingPath(root);
        var imageRoot = Path.Combine(root, "images");
        RuntimePrimitives.EnsureNotReparse(imageRoot);
        var paths = ImageFiles(root, token).ToArray();
        var live = paths.Select(path => path.Name).ToHashSet(LibraryImagePaths.Comparer);
        var history = Path.Combine(root, "recent.db");
        foreach (var name in Mirrors.Concat(Databases)) RuntimePrimitives.EnsureNotReparse(Path.Combine(root, name));

        // Avoid partial index edits across databases in WAL mode.
        if (!LibraryDatabases.CanAttach(root, recent: true, token)) return null;

        var legacy = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
        foreach (var name in Mirrors)
        {
            var path = Path.Combine(root, name);
            if (!File.Exists(path)) continue;
            var value = ReadJson(path);
            var expected = name is "order.json" or "recent.json" ? JsonValueKind.Array : JsonValueKind.Object;
            if (value.ValueKind != expected) return Empty with { Error = $"旧图库文件格式不正确：{name}" };
            legacy[name] = value;
        }

        string? backup = null;
        var existing = Mirrors.Concat(Databases).Where(name => File.Exists(Path.Combine(root, name))).ToArray();
        if (existing.Length != 0)
        {
            var parent = Path.Combine(root, "maintenance-backups");
            RuntimePrimitives.EnsureNotReparse(parent);
            backup = Directory.CreateDirectory(Path.Combine(parent,
                $"{DateTime.UtcNow:yyyyMMddHHmmss}-{Guid.NewGuid().ToString("N").Substring(0, 10)}")).FullName;
            foreach (var name in existing)
            {
                token.ThrowIfCancellationRequested();
                var source = Path.Combine(root, name);
                var target = Path.Combine(backup, name);
                if (name.EndsWith(".db", StringComparison.Ordinal))
                {
                    using var original = WindowsSqlite.OpenIfPresent(source) ?? throw new IOException("索引在备份时已移动");
                    original.BackupTo(target, token);
                }
                else File.Copy(source, target, overwrite: false);
            }
        }

        progress?.Report(new("migration", 0, 100, null));
        if (legacy.ContainsKey("recent.json") || File.Exists(history))
        {
            using var recent = LibraryEdits.Open(root, "recent.db", token, HistorySchema);
            if (!recent.Read("PRAGMA table_info(recent_history)", 2, token).Any(row => row[1] == "used_at"))
                recent.Execute("ALTER TABLE recent_history ADD COLUMN used_at TIMESTAMP", token);
            recent.Execute("COMMIT", token);
        }
        if (paths.Length != 0 || legacy.Count != 0)
        {
            using var db = LibraryDatabases.TryOpen(root, recent: true, token, out var hasHistory);
            if (db is null) return Empty with { Error = "旧图库没有完全迁移。", Destination = backup };
            Migrate(db, legacy, hasHistory, token);
            var mirrors = new List<(string Name, object Data)>();
            if (paths.Length != 0)
            {
                progress?.Report(new("cleanup", 0, 0, null));
                var tables = new List<string> { "image_features", "sync_key_checks", "meta.image_metadata",
                    "category.category_images", "gallery_order.item_orders" };
                if (hasHistory) tables.Add("history.recent_history");
                foreach (var table in tables)
                    foreach (var row in db.Read($"SELECT DISTINCT image_path FROM {table}", 1, token))
                        if (row[0] is { } value && !live.Contains(Name(value)))
                            db.Execute($"DELETE FROM {table} WHERE image_path = ?", token, value);
                foreach (var (name, value) in legacy)
                    switch (name)
                    {
                        case "categories.json":
                            mirrors.Add((name, value.EnumerateObject().ToDictionary(row => row.Name,
                                row => row.Value.ValueKind == JsonValueKind.Array ? row.Value.EnumerateArray()
                                    .Where(item => item.ValueKind == JsonValueKind.String && live.Contains(Name(item.GetString())))
                                    .ToArray() : [], StringComparer.Ordinal)));
                            break;
                        case "metadata.json":
                            mirrors.Add((name, value.EnumerateObject().Where(row => live.Contains(Name(row.Name)))
                                .ToDictionary(row => row.Name, row => row.Value, StringComparer.Ordinal)));
                            break;
                        case "order.json": case "recent.json":
                            mirrors.Add((name, value.EnumerateArray().Where(row => row.ValueKind == JsonValueKind.String &&
                                live.Contains(Name(row.GetString()))).ToArray()));
                            break;
                        case "hashes.json":
                            mirrors.Add((name, value.EnumerateObject().Where(row => row.Value.ValueKind == JsonValueKind.String &&
                                live.Contains(Name(IsMd5(row.Name) ? row.Value.GetString() : row.Name)))
                                .ToDictionary(row => row.Name, row => row.Value, StringComparer.Ordinal)));
                            break;
                    }
            }
            LibraryEdits.Commit(db, root, token, mirrors.ToArray());
        }
        progress?.Report(new("migration", 100, 100, null));
        if (paths.Length == 0) return Empty with { Destination = backup, LibraryChanged = legacy.Count > 0 };
        progress?.Report(new("index", 0, paths.Length, null));
        var keys = LibraryIndexWarmup.Run(root, token);
        var similarity = LibrarySimilarity.TryRun(root, new("similarity"), token, null, indexOnly: true);
        progress?.Report(new("index", paths.Length, paths.Length, null));
        return Empty with { Count = keys.Count, Destination = backup, LibraryChanged = true,
            Failed = similarity?.Failed ?? 0, Warnings = similarity?.Warnings };
    }

    private static void Migrate(WindowsSqlite db, Dictionary<string, JsonElement> legacy, bool history, CancellationToken token)
    {
        if (legacy.TryGetValue("hashes.json", out var hashes) && !HasRows("image_features"))
            foreach (var row in hashes.EnumerateObject().Where(row => row.Value.ValueKind == JsonValueKind.String))
            {
                var value = row.Value.GetString()!;
                bool reverse = IsMd5(row.Name);
                db.Execute("INSERT OR IGNORE INTO image_features(image_path, md5) VALUES (?, ?)", token,
                    Name(reverse ? value : row.Name), reverse ? row.Name : value);
            }
        if (legacy.TryGetValue("metadata.json", out var meta) && !HasRows("meta.image_metadata"))
            foreach (var row in meta.EnumerateObject())
            {
                string? keywords = Text(row.Value), tags = "";
                if (row.Value.ValueKind == JsonValueKind.Object)
                {
                    if (row.Value.TryGetProperty("tags", out var field)) tags = Text(field) ?? field.GetRawText();
                    if (row.Value.TryGetProperty("keywords", out field) || row.Value.TryGetProperty("keyword", out field))
                        keywords = Text(field) ?? field.ToString();
                    else keywords = "";
                }
                if (keywords is not null) db.Execute("INSERT OR IGNORE INTO meta.image_metadata(image_path, tags, keywords) VALUES (?, ?, ?)",
                    token, Name(row.Name), tags, keywords);
            }
        if (!HasRows("category.categories"))
        {
            var categories = Read("categories.json");
            var icons = Read("category_icons.json").ToDictionary(row => row.Name, row => Text(row.Value), StringComparer.Ordinal);
            int index = 0;
            foreach (string name in categories.Select(row => row.Name).Concat(icons.Keys).Distinct(StringComparer.Ordinal).Where(x => !Reserved(x)))
            {
                string? icon = icons.GetValueOrDefault(name);
                if (icon is not null && IsIconImage(icon) && !icon.Replace('\\', '/').StartsWith("category_icons/", StringComparison.Ordinal))
                    icon = Name(icon);
                db.Execute("INSERT OR IGNORE INTO category.categories(name, icon_path, sort_order) VALUES (?, ?, ?)", token, name, icon, index++);
            }
            foreach (var row in categories.Where(row => !Reserved(row.Name) && row.Value.ValueKind == JsonValueKind.Array))
                foreach (var value in row.Value.EnumerateArray().Where(x => x.ValueKind == JsonValueKind.String))
                    db.Execute("INSERT OR IGNORE INTO category.category_images(category_name, image_path) VALUES (?, ?)", token, row.Name, Name(value.GetString()));
        }
        if (legacy.TryGetValue("order.json", out var orders) && !HasRows("gallery_order.item_orders"))
        {
            int index = 0;
            foreach (var value in orders.EnumerateArray().Where(x => x.ValueKind == JsonValueKind.String))
                db.Execute("INSERT OR IGNORE INTO gallery_order.item_orders(image_path, sort_order) VALUES (?, ?)", token, Name(value.GetString()), index++);
        }
        if (history && legacy.TryGetValue("recent.json", out var recent) && !HasRows("history.recent_history"))
            foreach (var value in recent.EnumerateArray().Where(x => x.ValueKind == JsonValueKind.String).Reverse())
                db.Execute("INSERT OR IGNORE INTO history.recent_history(image_path) VALUES (?)", token, Name(value.GetString()));

        JsonProperty[] Read(string name) => legacy.TryGetValue(name, out var value) ? value.EnumerateObject().ToArray() : [];
        bool HasRows(string table) => db.Read($"SELECT 1 FROM {table} LIMIT 1", 1, token).Count != 0;
    }

    private static JsonElement ReadJson(string path)
    {
        try
        {
            using var file = new StreamReader(path, detectEncodingFromByteOrderMarks: true);
            using var document = JsonDocument.Parse(file.ReadToEnd());
            return document.RootElement.Clone();
        }
        catch (JsonException) { return default; }
    }

    private static bool IsMd5(string value) => Regex.IsMatch(value, @"\A[0-9A-Fa-f]{32}\z", RegexOptions.CultureInvariant);
}
