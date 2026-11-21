using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using static SuzuEmojy.Windows.LibrarySnapshotReader;

namespace SuzuEmojy.Windows;

internal static class LibraryResourceIngest
{
    private static readonly StringComparer FileNames = LibraryImagePaths.Comparer;
    private sealed record Existing(string Path, string Revision);
    private sealed record Group(string Name, string? Icon, HashSet<string> Members);

    internal static LibraryResult? TryRun(string data, LibraryIngestPlan plan, CancellationToken token, IProgress<LibraryProgress>? progress)
    {
        token.ThrowIfCancellationRequested();
        var root = Path.GetFullPath(data);
        if (Directory.Exists(root)) root = RuntimePrimitives.ExistingPath(root);
        var images = Path.Combine(root, "images");
        var iconDirectory = Path.Combine(root, "category_icons");
        RuntimePrimitives.EnsureNotReparse(images);
        RuntimePrimitives.EnsureNotReparse(iconDirectory);
        RuntimePrimitives.EnsureNotReparse(Path.Combine(root, "features.db"));

        var existing = new Dictionary<string, Existing>(StringComparer.Ordinal);
        var occupied = new HashSet<string>(FileNames);
        var cached = ReadVerifiedKeys(root, token);
        if (Directory.Exists(images))
        {
            foreach (var path in Directory.EnumerateFileSystemEntries(images).OrderBy(path => path, FileNames))
            {
                token.ThrowIfCancellationRequested();
                occupied.Add(Path.GetFileName(path));
                if ((File.GetAttributes(path) & (FileAttributes.Directory | FileAttributes.ReparsePoint)) != 0) continue;
                using var source = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
                string? key;
                if (cached.TryGetValue(Path.GetFileName(path), out var receipt) &&
                    receipt.Size == source.Length && receipt.Modified == Modified(source))
                    key = receipt.Key;
                else key = ResourceIdentityV1.TryRead(source, token);
                if (key is not null)
                {
                    if (!existing.ContainsKey(key)) existing.Add(key, new(path, RuntimePrimitives.Revision(source.SafeFileHandle)));
                }
                else if (LooksLikePng(source) && !IsApng(source))
                    return null; // Don't duplicate a PNG the legacy codec might identify.
            }
        }

        // Initial empty schemas are durable: a rejected import can't leave an
        // empty DB file that prevents the read-only gallery from opening.
        Directory.CreateDirectory(images);
        using var db = LibraryDatabases.TryOpen(root, recent: false, token, out _);
        if (db is null) return null;
        InboxLibraryImport.EnsureFolders(root);
        foreach (var query in new[] { "SELECT image_path FROM image_features", "SELECT image_path FROM meta.image_metadata",
            "SELECT DISTINCT image_path FROM category.category_images", "SELECT image_path FROM gallery_order.item_orders" })
            foreach (var row in db.Read(query, 1, token)) occupied.Add(Name(row[0]));

        var groups = new List<(string Name, string? Icon, List<string> Members)>();
        ReadCategories(root, ReadObject(root, "category_icons.json"), groups, token);
        var categories = groups.Where(group => !Reserved(group.Name)).Select(group =>
            new Group(group.Name, group.Icon, new(group.Members.Where(member => member.Length != 0), FileNames))).ToList();
        var categoryMap = categories.ToDictionary(group => group.Name, StringComparer.Ordinal);
        var references = new Dictionary<int, string>();
        var order = OrderedImages(root, token).Select(file => file.Name).ToList();
        var orders = db.Read("SELECT image_path FROM gallery_order.item_orders", 1, token);
        long nextOrder = ParseInt(db.Read("SELECT COALESCE(MAX(sort_order), -1) FROM gallery_order.item_orders", 1, token)[0][0]) + 1;
        if (orders.Count == 0)
            foreach (var name in order)
                db.Execute("INSERT INTO gallery_order.item_orders VALUES (?, ?)", token, name, nextOrder++);
        var metadata = ReadObject(root, "metadata.json");
        var keywordsByPath = new Dictionary<string, string>(FileNames);
        foreach (var entry in metadata) keywordsByPath[Name(entry.Key)] = Text(entry.Value) ?? "";
        var keywordsDb = db.Read("SELECT image_path, keywords FROM meta.image_metadata", 2, token)
            .Where(row => row[0] is not null).ToLookup(row => Name(row[0]), FileNames);
        foreach (var row in keywordsDb.SelectMany(rows => rows)) keywordsByPath[Name(row[0])] = row[1] ?? "";
        var files = new List<string>();
        var buffer = new byte[128 * 1024];
        int added = 0, duplicates = 0;
        var promoted = new List<string>();
        var needsIcon = new HashSet<string>(StringComparer.Ordinal);
        string phase = plan.Promote ? "import" : "exchange";
        bool committed = false, cancelled = false;
        // Imported files are never interrupted halfway through filesystem +
        // index publication. A cancel on local batches takes effect between
        // items; v1 ZIP remains an all-or-nothing operation.
        var persist = plan.Promote ? CancellationToken.None : token;
        try
        {
            foreach (var category in plan.Categories)
            {
                persist.ThrowIfCancellationRequested();
                string name = category.Name;
                int reference = category.Ref;
                if (Reserved(name)) continue;
                references.Add(reference, name);
                if (!categoryMap.TryGetValue(name, out var group))
                {
                    group = new(name, null, new(FileNames));
                    categoryMap.Add(name, group);
                    categories.Add(group);
                }
                if (ValidIcon(group.Icon, root)) continue;
                if (plan.Promote) needsIcon.Add(group.Name);
                var value = category.Icon?.Text;
                if (category.Icon is { Attachment: { } asset, AttachmentName: { } fileName })
                {
                    value = "category_icons/" + fileName;
                    Directory.CreateDirectory(iconDirectory);
                    var destination = Path.Combine(iconDirectory, fileName);
                    if (File.Exists(destination))
                    {
                        // The name is content-addressed; don't reuse a stale or redirected file.
                        RuntimePrimitives.EnsureNotReparse(destination);
                        using var current = File.OpenRead(destination);
                        using var sha = System.Security.Cryptography.SHA256.Create();
                        if (!BitConverter.ToString(sha.ComputeHash(current)).Replace("-", "")
                            .Equals(Path.GetFileNameWithoutExtension(fileName), StringComparison.OrdinalIgnoreCase))
                            throw new InvalidDataException("本地图标附件校验失败");
                    }
                    else CopyAsset(asset, destination);
                }
                if (value is not null)
                {
                    var updated = group with { Icon = value };
                    categories[categories.IndexOf(group)] = updated;
                    categoryMap[name] = updated;
                }
            }

            for (int i = 0; i < plan.Resources.Length; i++)
            {
                if (plan.Promote && token.IsCancellationRequested) { cancelled = true; break; }
                persist.ThrowIfCancellationRequested();
                var item = plan.Resources[i];
                progress?.Report(new(phase, i, plan.Resources.Length, plan.Promote ? null : $"正在导入表情: {item.Name}"));
                if (plan.Promote && token.IsCancellationRequested) { cancelled = true; break; }
                string name;
                if (existing.TryGetValue(item.SyncKey, out var duplicate))
                {
                    using var current = File.Open(duplicate.Path, FileMode.Open, FileAccess.Read, FileShare.Read);
                    if (RuntimePrimitives.Revision(current.SafeFileHandle) != duplicate.Revision)
                        throw new IOException("导入时图库图片已变化");
                    name = Path.GetFileName(duplicate.Path);
                    duplicates++;
                }
                else
                {
                    name = item.Name;
                    for (int suffix = 1; !occupied.Add(name); suffix++)
                        name = $"{Path.GetFileNameWithoutExtension(item.Name)}_{suffix}{Path.GetExtension(item.Name)}";
                    var destination = Path.Combine(images, name);
                    CopyAsset(item.Asset, destination);
                    using var saved = File.OpenRead(destination);
                    var modified = Modified(saved);
                    db.Execute("INSERT INTO image_features(image_path, md5, quality_score, sync_key, file_size, mtime_ns) VALUES (?, ?, ?, ?, ?, ?)",
                        persist, name, item.Md5, item.Quality, item.SyncKey, saved.Length, modified);
                    db.Execute("INSERT INTO gallery_order.item_orders VALUES (?, ?)", persist, name, nextOrder++);
                    existing[item.SyncKey] = new(destination, RuntimePrimitives.Revision(saved.SafeFileHandle));
                    order.Add(name);
                    added++;
                }
                if (item.Keywords.Length > 0 || !keywordsByPath.ContainsKey(name))
                {
                    string labels = string.Join(" ", Words(keywordsByPath.GetValueOrDefault(name) ?? "").Union(item.Keywords, StringComparer.Ordinal));
                    var aliases = keywordsDb[name].ToArray();
                    if (aliases.Length == 0)
                        db.Execute("INSERT OR IGNORE INTO meta.image_metadata(image_path, tags, keywords) VALUES (?, '', ?)", persist, name, labels);
                    else
                        foreach (var row in aliases)
                            db.Execute("UPDATE meta.image_metadata SET keywords = ? WHERE image_path = ?", persist, labels, row[0]);
                    // Subsequent resources with the same identity may append more tags.
                    db.Execute("UPDATE meta.image_metadata SET keywords = ? WHERE image_path = ?", persist, labels, name);
                    keywordsByPath[name] = labels;
                    metadata[name] = JsonSerializer.SerializeToElement(labels);
                }
                if (plan.Promote) promoted.Add(name);
                foreach (var reference in item.Categories)
                {
                    if (!references.TryGetValue(reference, out var category)) continue;
                    var group = categoryMap[category];
                    group.Members.Add(name);
                    if (needsIcon.Remove(category))
                    {
                        var updated = group with { Icon = name };
                        categories[categories.IndexOf(group)] = updated;
                        categoryMap[category] = updated;
                    }
                }
            }
            if (cancelled && added + duplicates == 0)
                return new(0, 0, 0, null, [], null, null, Cancelled: 1);
            // Seed old JSON-only membership, then retain the DB's category row
            // IDs and add relations idempotently.
            for (int i = 0; i < categories.Count; i++)
            {
                var group = categories[i];
                db.Execute("INSERT OR IGNORE INTO category.categories(name, icon_path, sort_order) VALUES (?, ?, ?)", persist, group.Name, group.Icon, i);
                db.Execute("UPDATE category.categories SET icon_path = ?, sort_order = ? WHERE name = ?", persist, group.Icon, i, group.Name);
                foreach (var member in group.Members)
                    db.Execute("INSERT OR IGNORE INTO category.category_images VALUES (?, ?)", persist, group.Name, member);
            }
            if (plan.Promote)
            {
                order = promoted.Concat(order).Distinct(FileNames).ToList();
                db.Execute("DELETE FROM gallery_order.item_orders", persist);
                for (int i = 0; i < order.Count; i++)
                    db.Execute("INSERT INTO gallery_order.item_orders VALUES (?, ?)", persist, order[i], i);
            }
            LibraryEdits.Commit(db, root, persist, ("metadata.json", metadata), ("order.json", order),
                ("category_icons.json", categories.Where(group => group.Icon is not null).ToDictionary(group => group.Name, group => group.Icon)),
                ("categories.json", categories.ToDictionary(group => group.Name, group => group.Members.ToArray())));
            committed = true;
        }
        finally
        {
            if (!committed)
                foreach (var file in files) RuntimePrimitives.DiscardTemporaryFile(file);
        }
        progress?.Report(new(phase, added + duplicates, plan.Resources.Length, plan.Promote ? null : "导入完成"));
        return new(added, duplicates, 0, null, null, null, null, Cancelled: cancelled ? 1 : 0);

        void CopyAsset(IngestAsset asset, string destination)
        {
            var temp = Path.Combine(Path.GetDirectoryName(destination)!, $".asset-{Guid.NewGuid():N}.part");
            try
            {
                using (var source = asset.Open())
                using (var target = new FileStream(temp, FileMode.CreateNew, FileAccess.Write))
                {
                    uint crc = Crc32.Start;
                    long size = 0;
                    int count;
                    while ((count = source.Read(buffer, 0, buffer.Length)) > 0)
                    {
                        persist.ThrowIfCancellationRequested();
                        size += count;
                        if (size > asset.Length) throw new InvalidDataException("资源长度已变化");
                        crc = Crc32.Update(crc, buffer.AsSpan(0, count));
                        target.Write(buffer, 0, count);
                    }
                    if (size != asset.Length || Crc32.Finish(crc) != asset.Crc) throw new InvalidDataException("资源校验失败");
                    target.Flush(flushToDisk: true);
                }
                if (!RuntimePrimitives.CommitFile(temp, destination, overwrite: false, persist))
                    throw new IOException("目标文件已存在");
                files.Add(destination);
            }
            finally { RuntimePrimitives.DiscardTemporaryFile(temp); }
        }
    }

    private const string KeyFormat = "^[pf]:[0-9a-f]{32}$";
    private static long Modified(FileStream stream) =>
        checked((RuntimePrimitives.LastWriteTimeUtc(stream.SafeFileHandle).Ticks - RuntimePrimitives.UnixEpoch.Ticks) * 100);
    private static Dictionary<string, (string Key, long Size, long Modified)> ReadVerifiedKeys(string root, CancellationToken token)
    {
        var receipts = new Dictionary<string, (string, long, long)>(FileNames);
        try
        {
            using var db = WindowsSqlite.OpenIfPresent(Path.Combine(root, "features.db"));
            if (db is not null)
                foreach (var row in db.Read("SELECT image_path, sync_key, file_size, mtime_ns FROM image_features WHERE sync_key IS NOT NULL", 4, token))
                    if (row[0] is { } name && row[1] is { } key && System.Text.RegularExpressions.Regex.IsMatch(key, KeyFormat) &&
                        long.TryParse(row[2], NumberStyles.Integer, CultureInfo.InvariantCulture, out long size) &&
                        long.TryParse(row[3], NumberStyles.Integer, CultureInfo.InvariantCulture, out long modified))
                        receipts[name] = (key, size, modified);
        }
        catch (LibrarySqliteException) { } // Older feature DBs have no receipt columns.
        return receipts;
    }

    private static long ParseInt(string? text) => long.Parse(text!, NumberStyles.Integer, CultureInfo.InvariantCulture);
    internal static IEnumerable<string> Words(string value) => value.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
    private static bool ValidIcon(string? icon, string root) => !string.IsNullOrWhiteSpace(icon) &&
        (!IsIconImage(icon!) || IconPath(root, icon!) is { } path && MediaThumbnail.Load(path, 64) is not null);
    internal static bool ValidName(string name) => name.Length is > 0 and <= 200 && !name.EndsWith(".", StringComparison.Ordinal) &&
        !name.EndsWith(" ", StringComparison.Ordinal) && name.IndexOfAny(Path.GetInvalidFileNameChars()) < 0 &&
        !System.Text.RegularExpressions.Regex.IsMatch(name, @"^(CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])(?:\.|$)",
            System.Text.RegularExpressions.RegexOptions.IgnoreCase | System.Text.RegularExpressions.RegexOptions.CultureInvariant);
    private static bool LooksLikePng(Stream stream)
    {
        stream.Position = 0;
        var bytes = new byte[8];
        return stream.Read(bytes, 0, bytes.Length) == 8 &&
            bytes.AsSpan().SequenceEqual(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 });
    }
    private static bool IsApng(Stream stream)
    {
        stream.Position = 0;
        return ApngDecoder.HasAnimationControl(stream);
    }
}
