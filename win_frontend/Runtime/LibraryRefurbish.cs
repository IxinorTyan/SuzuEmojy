using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using static SuzuEmojy.Windows.LibrarySnapshotReader;

namespace SuzuEmojy.Windows;

// Never publishes a half-processed gallery and never writes into the input gallery.
internal static class LibraryRefurbish
{
    private static readonly StringComparer Names = LibraryImagePaths.Comparer;
    internal static readonly string[] Databases = ["features.db", "metadata.db", "categories.db", "order.db", "recent.db"];
    internal static readonly string[] Mirrors = ["hashes.json", "metadata.json", "categories.json",
        "category_icons.json", "order.json", "recent.json"];
    internal const string ReportFile = "refurbish-report.json";
    private sealed record Cleaned(string Before, string After, string PixelKey, string FileMd5, long Size, long Modified);
    internal sealed record Report(int LongEdge, int Images, int Recleaned, int Resized, int PreservedAnimations,
        string[] UnchangedBecauseUnreadable, string[] PreservedSyncIdentity, Dictionary<string, string> Renames, string[][] ExactDuplicateGroups);

    internal static ulong RequiredStagingBytes(string root, CancellationToken token)
    {
        // Allow space for the source copy plus database/index growth and intermediate encodes.
        ulong bytes = 64UL << 20;
        AddFolder(Path.Combine(root, "images"));
        foreach (var name in Mirrors.Concat(Databases.SelectMany(name => new[] { name, name + "-wal", name + "-journal" })))
            AddFile(new FileInfo(Path.Combine(root, name)));
        return bytes;

        void AddFolder(string path)
        {
            RuntimePrimitives.EnsureNotReparse(path);
            foreach (var entry in new DirectoryInfo(path).EnumerateFileSystemInfos())
                if (entry is DirectoryInfo folder) AddFolder(folder.FullName);
                else AddFile((FileInfo)entry);
        }

        void AddFile(FileInfo file)
        {
            token.ThrowIfCancellationRequested();
            RuntimePrimitives.EnsureNotReparse(file.FullName);
            if (file.Exists) bytes = checked(bytes + (ulong)file.Length);
        }
    }

    internal static Report Run(string input, string output, CancellationToken token, Action<int, int>? progress = null)
    {
        var source = RuntimePrimitives.ExistingPath(Path.GetFullPath(input));
        var requested = Path.GetFullPath(output).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        if (!Directory.Exists(Path.Combine(source, "images"))) throw new ArgumentException("源目录不是图库：缺少 images 子目录。");
        if (Directory.Exists(requested) || File.Exists(requested)) throw new IOException("输出目录必须尚不存在。");
        var parent = RuntimePrimitives.ExistingPath(Path.GetDirectoryName(requested)!);
        var target = Path.Combine(parent, Path.GetFileName(requested));
        if (RuntimePrimitives.IsWithin(source, target)) throw new ArgumentException("输出目录不能放在源图库里。");
        var stage = Directory.CreateDirectory(Path.Combine(parent, ".rf-" + Guid.NewGuid().ToString("N").Substring(0, 10))).FullName;
        var result = Build(source, stage, token, standalone: true, progress);
        token.ThrowIfCancellationRequested();
        Directory.Move(stage, target);
        return result;
    }

    internal static Report Build(string source, string stage, CancellationToken token, bool standalone, Action<int, int>? progress = null)
    {
        foreach (var name in Mirrors)
        {
            token.ThrowIfCancellationRequested();
            var from = Path.Combine(source, name);
            if (File.Exists(from)) CopyFile(from, Path.Combine(stage, name));
        }
        foreach (var name in Databases)
        {
            token.ThrowIfCancellationRequested();
            var from = Path.Combine(source, name);
            RuntimePrimitives.EnsureNotReparse(from);
            using var original = WindowsSqlite.OpenIfPresent(from);
            original?.BackupTo(Path.Combine(stage, name), token);
        }
        CopyFolder(Path.Combine(source, "images"), Path.Combine(stage, "images"), token);
        if (standalone)
        {
            foreach (var name in new[] { "config.json", "window-placement.windows.json" })
            {
                var from = Path.Combine(source, name);
                if (File.Exists(from)) CopyFile(from, Path.Combine(stage, name));
            }
            foreach (var name in new[] { "category_icons", "appearance" })
                CopyFolder(Path.Combine(source, name), Path.Combine(stage, name), token);
        }

        if (NativeLibraryMaintenance.TryRun(stage, token, null, buildIndexes: false, createBackup: standalone) is not { Error: null })
            throw new InvalidDataException("无法整理旧图库索引；源图库未修改。未完成的副本：" + stage);

        var images = Path.Combine(stage, "images");
        var files = OrderedImages(stage, token);
        var names = Directory.EnumerateFileSystemEntries(images).Select(Path.GetFileName).ToHashSet(Names);
        var cleaned = new List<Cleaned>();
        var renames = new Dictionary<string, string>(Names);
        var unreadable = new List<string>();
        var preservedSync = new List<string>();
        var synced = LanSyncWindowsLibrary.SyncedKeys(Path.Combine(stage, "features.db"), token);
        string temporary = Path.Combine(stage, ".image.part");
        int resized = 0, animations = 0;
        for (int i = 0; i < files.Count; i++)
        {
            token.ThrowIfCancellationRequested();
            if (i % 100 == 0) progress?.Invoke(i, files.Count);
            var (path, name) = files[i];
            try
            {
                switch (LocalImageImport.InspectForRefurbish(path, token))
                {
                    case LocalImageImport.RefurbishImage.WithinLimit: continue;
                    case LocalImageImport.RefurbishImage.Animation: animations++; continue;
                    case LocalImageImport.RefurbishImage.Unreadable: unreadable.Add(name); continue;
                }
                if (LocalImageImport.PrepareForRefurbish(path, temporary, token) is not { } image)
                {
                    unreadable.Add(name);
                    continue;
                }
                if (image.SyncKey.StartsWith("f:", StringComparison.Ordinal)) { animations++; continue; }
                if (synced.Count > 0)
                {
                    var oldKey = ResourceIdentityV1.TryRead(path, token);
                    if (oldKey != image.SyncKey && (oldKey is not null && synced.Contains(oldKey) || synced.Contains(image.SyncKey)))
                    {
                        preservedSync.Add(name);
                        continue;
                    }
                }
                string newName = name;
                if (!name.EndsWith(".png", StringComparison.OrdinalIgnoreCase))
                {
                    var stem = Path.GetFileNameWithoutExtension(name);
                    newName = stem + ".png";
                    for (int suffix = 2; !names.Add(newName); suffix++) newName = $"{stem}_{suffix}.png";
                    renames.Add(name, newName);
                }
                var destination = Path.Combine(images, newName);
                if (!RuntimePrimitives.CommitFile(temporary, destination, overwrite: name == newName, token))
                    throw new IOException("清洗后的文件名已存在：" + newName);
                if (name != newName) File.Delete(path); // Only the staging copy is replaced.
                var saved = new FileInfo(destination);
                cleaned.Add(new(name, newName, image.SyncKey, image.Md5, saved.Length,
                    checked((saved.LastWriteTimeUtc.Ticks - RuntimePrimitives.UnixEpoch.Ticks) * 100)));
                resized++;
            }
            finally { RuntimePrimitives.DiscardTemporaryFile(temporary); }
        }
        progress?.Invoke(files.Count, files.Count);
        UpdateIndexes();
        LibraryIndexWarmup.Run(stage, token);

        var duplicates = new List<string[]>();
        using (var db = WindowsSqlite.OpenIfPresent(Path.Combine(stage, "features.db")))
        {
            foreach (var group in (db?.Read("SELECT sync_key, image_path FROM image_features WHERE sync_key IS NOT NULL ORDER BY image_path", 2, token) ?? [])
                .GroupBy(row => row[0]!, StringComparer.Ordinal))
            {
                var candidates = group.Select(row => Name(row[1])).Distinct(Names)
                    .Where(name => name.Length > 0 && File.Exists(Path.Combine(images, name))).ToArray();
                if (candidates.Length < 2) continue;
                var matches = new List<List<string>>();
                foreach (var candidate in candidates)
                {
                    token.ThrowIfCancellationRequested();
                    using var first = File.OpenRead(Path.Combine(images, candidate));
                    var match = matches.FirstOrDefault(items =>
                    {
                        using var second = File.OpenRead(Path.Combine(images, items[0]));
                        return LibraryContentMatch.Exact(first, second, group.Key, token);
                    });
                    if (match is null) matches.Add([candidate]);
                    else match.Add(candidate);
                }
                duplicates.AddRange(matches.Where(match => match.Count > 1).Select(match => match.ToArray()));
            }
        }
        var result = new Report(ImportImageSize.MaxSide, files.Count, cleaned.Count, resized, animations, unreadable.ToArray(), preservedSync.ToArray(), renames, duplicates.ToArray());
        using (var report = File.Create(Path.Combine(stage, ReportFile)))
            JsonSerializer.Serialize(report, result, new JsonSerializerOptions { WriteIndented = true });
        StartupLibraryRefurbish.WriteReceipt(stage, token);
        return result;

        string NewName(string value) => renames.TryGetValue(Name(value), out var newName) ? newName : Name(value);

        string? Icon(string? value)
        {
            if (value is null || !IsIconImage(value) || value.StartsWith("category_icons/", StringComparison.OrdinalIgnoreCase) ||
                value.StartsWith("category_icons\\", StringComparison.OrdinalIgnoreCase)) return value;
            if (!Path.IsPathRooted(value)) return renames.TryGetValue(Name(value), out var next) ? next : value;
            var full = Path.GetFullPath(value);
            if (RuntimePrimitives.IsWithin(Path.Combine(source, "images"), full)) return NewName(value);
            var iconDir = Path.Combine(source, "category_icons");
            return RuntimePrimitives.IsWithin(iconDir, full) ? "category_icons/" + full.Substring(iconDir.Length + 1).Replace('\\', '/') : value;
        }

        void UpdateIndexes()
        {
            using var db = LibraryDatabases.TryOpen(stage, recent: true, token, out var recent)
                ?? throw new InvalidDataException("无法更新翻新副本的索引：" + stage);
            foreach (var table in new[] { "image_features", "sync_key_checks", "meta.image_metadata", "category.category_images" })
                RenameRows(table);
            if (recent) RenameRows("history.recent_history");
            foreach (var row in db.Read("SELECT name, icon_path FROM category.categories WHERE icon_path IS NOT NULL", 2, token))
                if (Icon(row[1]) != row[1])
                    db.Execute("UPDATE category.categories SET icon_path = ? WHERE name = ?", token, Icon(row[1]), row[0]);
            db.Execute("DELETE FROM gallery_order.item_orders", token);
            for (int i = 0; i < files.Count; i++)
                db.Execute("INSERT INTO gallery_order.item_orders VALUES (?, ?)", token, NewName(files[i].Name), i);
            foreach (var item in cleaned)
            {
                db.Execute("INSERT OR IGNORE INTO image_features(image_path) VALUES (?)", token, item.After);
                db.Execute("UPDATE image_features SET md5 = ?, dhash = NULL, phash = NULL, sync_key = ?, file_size = ?, mtime_ns = ? WHERE image_path = ?",
                    token, item.FileMd5, item.PixelKey, item.Size, item.Modified, item.After);
                db.Execute("DELETE FROM sync_key_checks WHERE image_path = ?", token, item.After);
            }
            var metadata = ReadObject(stage, "metadata.json").ToDictionary(pair => RenameReference(pair.Key), pair => pair.Value, StringComparer.Ordinal);
            var categories = ReadObject(stage, "categories.json").ToDictionary(pair => pair.Key,
                pair => pair.Value.ValueKind == JsonValueKind.Array ? pair.Value.EnumerateArray().Select(value =>
                    Text(value) is { } entry ? JsonSerializer.SerializeToElement(RenameReference(entry)) : value).ToArray() : [], StringComparer.Ordinal);
            var icons = ReadObject(stage, "category_icons.json").ToDictionary(pair => pair.Key, pair => Icon(Text(pair.Value)), StringComparer.Ordinal);
            var refreshed = cleaned.Select(item => item.Before).ToHashSet(Names);
            var hashes = ReadObject(stage, "hashes.json").Where(pair =>
                !refreshed.Contains(Name(pair.Key.Length == 32 && pair.Key.All(Uri.IsHexDigit) ? Text(pair.Value) : pair.Key)))
                .ToDictionary(pair => pair.Key, pair => pair.Value, StringComparer.Ordinal);
            foreach (var item in cleaned) hashes[item.FileMd5] = JsonSerializer.SerializeToElement(item.After);
            LibraryEdits.Commit(db, stage, token, ("metadata.json", metadata), ("categories.json", categories), ("category_icons.json", icons),
                ("hashes.json", hashes), ("order.json", files.Select(file => NewName(file.Name)).ToArray()),
                ("recent.json", ReadNames(stage, "recent.json").Select(path => path is null ? null : RenameReference(path)).ToArray()));

            void RenameRows(string table)
            {
                foreach (var row in db.Read($"SELECT DISTINCT image_path FROM {table}", 1, token))
                    if (row[0] is { } old && renames.ContainsKey(Name(old)))
                        db.Execute($"UPDATE {table} SET image_path = ? WHERE image_path = ?", token, RenameReference(old), old);
            }
        }

        string RenameReference(string value) => renames.TryGetValue(Name(value), out var next)
            ? value.Substring(0, value.Length - Name(value).Length) + next : value;
    }

    private static void CopyFolder(string origin, string destination, CancellationToken token)
    {
        if (!Directory.Exists(origin)) return;
        RuntimePrimitives.EnsureNotReparse(origin);
        Directory.CreateDirectory(destination);
        foreach (string path in Directory.EnumerateFiles(origin))
        {
            token.ThrowIfCancellationRequested();
            CopyFile(path, Path.Combine(destination, Path.GetFileName(path)));
        }
        foreach (string path in Directory.EnumerateDirectories(origin))
        {
            token.ThrowIfCancellationRequested();
            CopyFolder(path, Path.Combine(destination, Path.GetFileName(path)), token);
        }
    }

    private static void CopyFile(string source, string target)
    {
        RuntimePrimitives.EnsureNotReparse(source);
        File.Copy(source, target);
    }
}
