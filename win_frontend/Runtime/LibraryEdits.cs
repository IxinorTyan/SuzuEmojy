using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using static SuzuEmojy.Windows.LibrarySnapshotReader;

namespace SuzuEmojy.Windows;

// Legacy DBs stay independently readable; writes that touch two indexes never
// pretend to be one SQLite transaction. Each action below owns a single DB.
internal static class LibraryEdits
{
    private static readonly StringComparer FileNames = LibraryImagePaths.Comparer;
    private static readonly JsonSerializerOptions Json = new() { WriteIndented = true };

    internal static bool Handles(LibraryCommand command) => command.Action switch
    {
        "create_category" or "rename_category" or "category_icon" or "category_order" or "add" or "move" or "remove" or
            "tags" or "add_tags" or "remove_tags" or "order" or "recent" => true,
        "remove_category" => !command.DeleteImages,
        _ => false
    };

    internal static LibraryResult Run(string data, LibraryCommand command, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        var root = Path.GetFullPath(data);
        string[] names = command.Paths is { Length: > 0 } paths
            ? LibraryImagePaths.Validate(RuntimePrimitives.ExistingPath(Path.Combine(root, "images")), paths, token)
                .Select(path => Path.GetFileName(path)!).ToArray()
            : [];
        Directory.CreateDirectory(root);
        string[]? recent = null;
        switch (command.Action)
        {
            case "tags": case "add_tags": case "remove_tags":
                EditTags(root, command, names, token);
                break;
            case "order":
                EditOrder(root, names, token);
                break;
            case "recent":
                if (names.Length != 1 || command.Limit is < 0 or > 300) throw new ArgumentException("无效的最近使用记录");
                recent = AddRecent(root, names[0], command.Limit, token);
                break;
            default:
                EditCategories(root, command, names, token);
                break;
        }
        return new(0, 0, 0, null, null, null, null, Recent: recent);
    }

    internal const string CreateCategories = "CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT, " +
        "name TEXT UNIQUE NOT NULL, icon_path TEXT, sort_order INTEGER DEFAULT 0)";
    internal const string CreateMembership = "CREATE TABLE IF NOT EXISTS category_images (category_name TEXT, image_path TEXT, " +
        "PRIMARY KEY(category_name, image_path))";
    internal const string CreateMetadata = "CREATE TABLE IF NOT EXISTS image_metadata (image_path TEXT PRIMARY KEY, tags TEXT, keywords TEXT, " +
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)";
    internal const string CreateOrder = "CREATE TABLE IF NOT EXISTS item_orders (image_path TEXT PRIMARY KEY, sort_order INTEGER NOT NULL)";

    internal static WindowsSqlite Open(string root, string dbName, CancellationToken token, params string[] schema)
    {
        token.ThrowIfCancellationRequested();
        var path = Path.Combine(root, dbName);
        RuntimePrimitives.EnsureNotReparse(path);
        bool initialize = !File.Exists(path);
        var db = WindowsSqlite.OpenForWrite(path);
        try
        {
            // A canceled or rejected first edit must not leave an empty DB that a
            // read-only snapshot mistakes for a ready-to-use index.
            var opening = initialize ? CancellationToken.None : token;
            db.Execute("BEGIN IMMEDIATE", opening);
            foreach (var sql in schema) db.Execute(sql, opening);
            if (initialize)
            {
                db.Execute("COMMIT", opening);
                db.Execute("BEGIN IMMEDIATE", token);
            }
            return db;
        }
        catch { db.Dispose(); throw; }
    }

    private sealed record CategoryRow(string Name, string? Icon, List<string> Members);

    private static void EditCategories(string root, LibraryCommand request, string[] names, CancellationToken token)
    {
        using var db = Open(root, "categories.db", token, CreateCategories, CreateMembership);
        // The old service also removes these virtual views from historical DBs.
        const string reserved = " IN ('全部表情', '未分类', '新建分类')";
        db.Execute("DELETE FROM categories WHERE name" + reserved, token);
        db.Execute("DELETE FROM category_images WHERE category_name" + reserved, token);

        var icons = ReadObject(root, "category_icons.json");
        var rows = db.Read("SELECT name, icon_path FROM categories ORDER BY sort_order, id", 2, token)
            .Select(row => new CategoryRow(row[0]!, string.IsNullOrEmpty(row[1]) ? Text(icons.GetValueOrDefault(row[0]!)) : row[1], [])).ToList();
        if (rows.Count == 0)
        {
            foreach (var pair in ReadObject(root, "categories.json"))
            {
                if (Reserved(pair.Key)) continue;
                var icon = Text(icons.GetValueOrDefault(pair.Key));
                var members = pair.Value.ValueKind == JsonValueKind.Array
                    ? pair.Value.EnumerateArray().Select(x => Name(Text(x))).Where(x => x.Length != 0).Distinct(FileNames).ToList()
                    : new List<string>();
                db.Execute("INSERT INTO categories(name, icon_path, sort_order) VALUES (?, ?, ?)", token, pair.Key, icon, rows.Count);
                foreach (var name in members)
                    db.Execute("INSERT OR IGNORE INTO category_images VALUES (?, ?)", token, pair.Key, name);
                rows.Add(new(pair.Key, icon, members));
            }
        }
        else
        {
            var lookup = rows.ToDictionary(row => row.Name, StringComparer.Ordinal);
            foreach (var row in db.Read("SELECT category_name, image_path FROM category_images ORDER BY category_name, image_path", 2, token))
                if (row[0] is { } group && row[1] is { } path && lookup.TryGetValue(group, out var match))
                    match.Members.Add(path);
        }

        CategoryRow? Find(string? name) => rows.Find(row => row.Name == name);
        CategoryRow Require(string? name) => Find(name) ?? throw new ArgumentException("分类不存在");
        var value = request.Value ?? "";
        switch (request.Action)
        {
            case "create_category":
            case "rename_category":
                var name = value.Trim();
                bool creating = request.Action == "create_category";
                if (name.Length == 0 || Reserved(name) || Find(name) is not null ||
                    creating && (name is "全部" or "最近" || name.Equals("all", StringComparison.OrdinalIgnoreCase)))
                    throw new ArgumentException("分类已存在或名称不合法");
                if (creating)
                {
                    db.Execute("INSERT INTO categories(name, sort_order) VALUES (?, ?)", token, name, rows.Count);
                    rows.Add(new(name, null, []));
                }
                else
                {
                    var row = Require(request.Category);
                    db.Execute("UPDATE categories SET name = ? WHERE name = ?", token, name, row.Name);
                    db.Execute("UPDATE category_images SET category_name = ? WHERE category_name = ?", token, name, row.Name);
                    rows[rows.IndexOf(row)] = row with { Name = name };
                }
                break;
            case "category_order":
                var order = request.Categories ?? [];
                if (order.Length != rows.Count || order.Distinct(StringComparer.Ordinal).Count() != rows.Count)
                    throw new ArgumentException("分类已经变化");
                rows = order.Select(name => Find(name) ?? throw new ArgumentException("分类已经变化")).ToList();
                break;
            case "category_icon":
                var target = Require(request.Category);
                var icon = names.FirstOrDefault() ?? value;
                db.Execute("UPDATE categories SET icon_path = ? WHERE name = ?", token, icon, target.Name);
                rows[rows.IndexOf(target)] = target with { Icon = icon };
                break;
            case "remove_category":
                if (request.DeleteImages) throw new InvalidOperationException();
                if (Find(request.Category) is { } removed)
                {
                    db.Execute("DELETE FROM category_images WHERE category_name = ?", token, removed.Name);
                    db.Execute("DELETE FROM categories WHERE name = ?", token, removed.Name);
                    rows.Remove(removed);
                }
                break;
            case "add": case "remove": case "move":
                var source = request.Action == "add" ? null : Require(request.Category);
                var destination = request.Action == "remove" ? null : Require(value);
                if (source is not null)
                {
                    var selected = new HashSet<string>(names, FileNames);
                    foreach (var member in source.Members.Where(member => selected.Contains(Name(member))).ToArray())
                        db.Execute("DELETE FROM category_images WHERE category_name = ? AND image_path = ?", token, source.Name, member);
                    source.Members.RemoveAll(member => selected.Contains(Name(member)));
                }
                if (destination is not null)
                {
                    var present = new HashSet<string>(destination.Members.Select(Name), FileNames);
                    foreach (var member in names)
                    {
                        if (!present.Add(member)) continue;
                        db.Execute("INSERT OR IGNORE INTO category_images VALUES (?, ?)", token, destination.Name, member);
                        destination.Members.Add(member);
                    }
                }
                break;
            default:
                throw new ArgumentException("Unsupported operation");
        }

        for (int i = 0; i < rows.Count; i++)
            db.Execute("UPDATE categories SET sort_order = ? WHERE name = ? AND sort_order IS NOT ?", token, i, rows[i].Name, i);
        Commit(db, root, token,
            ("category_icons.json", rows.Where(row => row.Icon is not null)
                .ToDictionary(row => row.Name, row => row.Icon, StringComparer.Ordinal)),
            ("categories.json", rows.ToDictionary(row => row.Name,
                row => row.Members.Select(Name).Where(x => x.Length > 0).Distinct(FileNames).ToArray(), StringComparer.Ordinal)));
    }

    private static void EditTags(string root, LibraryCommand request, string[] names, CancellationToken token)
    {
        using var db = Open(root, "metadata.db", token, CreateMetadata);
        var mirror = ReadObject(root, "metadata.json");
        var legacy = mirror.ToLookup(pair => Name(pair.Key), FileNames);
        var records = db.Read("SELECT image_path, keywords FROM image_metadata", 2, token)
            .Where(row => row[0] is not null).ToLookup(row => Name(row[0]), FileNames);
        var value = request.Value ?? "";
        var tags = Tags(value).ToArray();
        foreach (var name in names)
        {
            var existing = records[name].LastOrDefault();
            var previous = existing is not null ? existing[1] ?? "" : LegacyTag(legacy[name].LastOrDefault().Value);
            var updated = request.Action switch
            {
                "add_tags" => string.Join(" ", Tags(previous).Union(tags, StringComparer.Ordinal)),
                "remove_tags" => string.Join(" ", Tags(previous).Except(tags, StringComparer.Ordinal)),
                _ => value
            };
            foreach (var entry in legacy[name]) mirror.Remove(entry.Key);
            mirror[name] = JsonSerializer.SerializeToElement(updated);
            if (existing is null)
                db.Execute("INSERT INTO image_metadata(image_path, keywords) VALUES (?, ?)", token, name, updated);
            else
                foreach (var row in records[name]) // Update old case/absolute aliases without replacing other columns.
                    db.Execute("UPDATE image_metadata SET keywords = ? WHERE image_path = ?", token, updated, row[0]);
        }
        Commit(db, root, token, ("metadata.json", mirror));
    }

    private static IEnumerable<string> Tags(string value) =>
        value.Split([' '], StringSplitOptions.RemoveEmptyEntries).Distinct(StringComparer.Ordinal);

    private static string LegacyTag(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.String => value.GetString() ?? "",
        JsonValueKind.True => "True",
        JsonValueKind.False => "False",
        JsonValueKind.Null => "None",
        JsonValueKind.Undefined => "",
        _ => value.GetRawText()
    };

    private static void EditOrder(string root, string[] names, CancellationToken token)
    {
        using var db = Open(root, "order.db", token, CreateOrder);
        var current = OrderedImages(root, token, db).Select(item => item.Name).ToArray();
        var known = new HashSet<string>(current, FileNames);
        var selected = new HashSet<string>(names, FileNames);
        if (!selected.IsSubsetOf(known)) throw new ArgumentException("图片不存在或不在当前图库");
        int index = 0;
        var ordered = current.Select(name => selected.Contains(name) ? names[index++] : name).ToArray();
        db.Execute("DELETE FROM item_orders", token);
        for (int i = 0; i < ordered.Length; i++)
            db.Execute("INSERT INTO item_orders(image_path, sort_order) VALUES (?, ?)", token, ordered[i], i);
        Commit(db, root, token, ("order.json", ordered));
    }

    private const string RecentSchema = " (id INTEGER PRIMARY KEY AUTOINCREMENT, image_path TEXT UNIQUE NOT NULL, " +
        "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)";
    private const string RecentSort = " ORDER BY updated_at DESC, id DESC";

    private static string[] AddRecent(string root, string name, int limit, CancellationToken token)
    {
        using var db = Open(root, "recent.db", token, "CREATE TABLE IF NOT EXISTS recent_history" + RecentSchema);
        if (!db.Read("PRAGMA table_info(recent_history)", 2, token).Any(column => column[1] == "updated_at"))
        {
            db.Execute("CREATE TABLE recent_history_native" + RecentSchema, token);
            db.Execute("INSERT OR REPLACE INTO recent_history_native(id, image_path, updated_at) " +
                "SELECT id, image_path, used_at FROM recent_history ORDER BY used_at, id", token);
            db.Execute("DROP TABLE recent_history", token);
            db.Execute("ALTER TABLE recent_history_native RENAME TO recent_history", token);
        }
        var rows = db.Read("SELECT image_path, updated_at FROM recent_history" + RecentSort, 2, token);
        var kept = new HashSet<string>(StringComparer.Ordinal);
        var seen = new HashSet<string>(FileNames) { name };
        var result = new List<string>();
        if (limit != 0)
        {
            result.Add(name);
            var images = Path.Combine(root, "images");
            var history = rows.Count != 0 ? rows.Select(row => row[0]) : ReadNames(root, "recent.json");
            foreach (var item in history)
            {
                if (result.Count == limit) break;
                var previous = Name(item);
                if (!IsImageName(previous) || !seen.Add(previous)) continue;
                var path = Path.Combine(images, previous);
                if (!File.Exists(path)) continue;
                result.Add(Path.GetFileName(RuntimePrimitives.ExistingPath(path)));
                kept.Add(item!);
            }
            if (rows.Count == 0)
                foreach (var previous in result.Skip(1).Reverse())
                    db.Execute("INSERT INTO recent_history(image_path, updated_at) VALUES (?, ?)", token,
                        previous, "1970-01-01 00:00:00");
            kept.Add(name);
            var utc = DateTime.UtcNow;
            if (DateTime.TryParse(rows.FirstOrDefault()?[1], CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal |
                DateTimeStyles.AdjustToUniversal, out var latest) && latest >= utc && latest.Ticks <= DateTime.MaxValue.Ticks - 10)
                utc = latest.AddTicks(10);
            db.Execute("INSERT OR IGNORE INTO recent_history(image_path) VALUES (?)", token, name);
            db.Execute("UPDATE recent_history SET updated_at = ? WHERE image_path = ?", token,
                utc.ToString("yyyy-MM-dd HH:mm:ss.ffffff", CultureInfo.InvariantCulture), name);
        }
        foreach (var row in rows)
            if (row[0] is { } previous && !kept.Contains(previous))
                db.Execute("DELETE FROM recent_history WHERE image_path = ?", token, previous);
        var order = result.ToArray();
        Commit(db, root, token, ("recent.json", order));
        return order;
    }

    // Prepare every mirror before publication. Publish before committing the DB
    // so an empty SQLite table cannot fall back to an old, just-deleted JSON list.
    internal static void Commit(WindowsSqlite db, string root, CancellationToken token,
        params (string Name, object Data)[] mirrors)
    {
        var staged = new List<(string Temp, string Target, string? Backup)>();
        int published = 0;
        bool committed = false;
        try
        {
            foreach (var mirror in mirrors)
            {
                token.ThrowIfCancellationRequested();
                var temp = Path.Combine(root, $".index-{Guid.NewGuid():N}.tmp");
                var target = Path.Combine(root, mirror.Name);
                RuntimePrimitives.EnsureNotReparse(target);
                var backup = File.Exists(target) ? Path.Combine(root, $".index-{Guid.NewGuid():N}.bak") : null;
                staged.Add((temp, target, backup));
                using var file = new FileStream(temp, FileMode.CreateNew, FileAccess.Write);
                JsonSerializer.Serialize(file, mirror.Data, Json);
                file.Flush(flushToDisk: true);
            }
            token.ThrowIfCancellationRequested();
            foreach (var item in staged)
            {
                if (item.Backup is { } backup) File.Copy(item.Target, backup);
                if (!RuntimePrimitives.CommitFile(item.Temp, item.Target, overwrite: item.Backup is not null, CancellationToken.None))
                    throw new IOException("图库索引已被其他程序修改。");
                published++;
            }
            db.Execute("COMMIT", CancellationToken.None);
            committed = true;
        }
        finally
        {
            for (int i = staged.Count - 1; i >= 0; i--)
            {
                var item = staged[i];
                RuntimePrimitives.DiscardTemporaryFile(item.Temp);
                if (!committed && i < published)
                {
                    try
                    {
                        if (item.Backup is { } backup)
                            RuntimePrimitives.CommitFile(backup, item.Target, overwrite: true, CancellationToken.None);
                        else File.Delete(item.Target);
                    }
                    catch (Exception e) when (e is IOException or UnauthorizedAccessException)
                    {
                        System.Diagnostics.Trace.TraceWarning("Could not restore index mirror {0}; backup: {1}. {2}",
                            item.Target, item.Backup, e.Message);
                        continue;
                    }
                }
                if (item.Backup is { } abandoned) RuntimePrimitives.DiscardTemporaryFile(abandoned);
            }
        }
    }
}
