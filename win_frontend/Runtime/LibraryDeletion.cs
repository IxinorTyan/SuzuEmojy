using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using static SuzuEmojy.Windows.LibrarySnapshotReader;

namespace SuzuEmojy.Windows;

internal static class LibraryDeletion
{
    private static readonly StringComparer FileNames = LibraryImagePaths.Comparer;

    internal static LibraryResult? TryRun(string data, LibraryCommand command, CancellationToken token, IProgress<LibraryProgress>? progress)
    {
        token.ThrowIfCancellationRequested();
        if (!Directory.Exists(data)) return new(0, 0, 0, null, [], null, null);
        var root = RuntimePrimitives.ExistingPath(data);
        var images = Path.Combine(root, "images");
        RuntimePrimitives.EnsureNotReparse(images);
        var folders = new List<(string Name, string? Icon, List<string> Members)>();
        ReadCategories(root, ReadObject(root, "category_icons.json"), folders, token);
        string? removeGroup = null;
        List<string> targets;
        if (command.Action == "remove_category")
        {
            int selected = folders.FindIndex(group => group.Name == command.Category && !Reserved(group.Name));
            if (selected < 0) return new(0, 0, 0, null, [], null, null);
            var removed = folders[selected];
            removeGroup = removed.Name;
            folders.RemoveAt(selected);
            var shared = new HashSet<string>(folders.Where(group => !Reserved(group.Name)).SelectMany(group => group.Members), FileNames);
            targets = removed.Members.Where(name => IsImageName(name) && !shared.Contains(name))
                .Where(name => File.Exists(Path.Combine(images, name))).Select(name => Path.Combine(images, name))
                .Distinct(FileNames).ToList();
        }
        else
        {
            var paths = command.Paths ?? [];
            if (paths.Length == 0) return new(0, 0, 0, null, [], null, null);
            targets = LibraryImagePaths.Validate(RuntimePrimitives.ExistingPath(images), paths, token);
        }
        using var db = LibraryDatabases.TryOpen(root, recent: true, token, out bool hasRecent);
        if (db is null) return null;

        var staging = Path.Combine(images, $".suzu-delete-{Guid.NewGuid():N}");
        var staged = new List<(string Path, string Pending)>();
        var warnings = new List<string>();
        int failed = 0;
        bool committed = false;
        bool stopped = false;
        try
        {
            if (targets.Count > 0) Directory.CreateDirectory(staging);
            for (int i = 0; i < targets.Count; i++)
            {
                if (token.IsCancellationRequested) { stopped = true; break; }
                var original = targets[i];
                var name = Path.GetFileName(original);
                try
                {
                    if ((File.GetAttributes(original) & (FileAttributes.ReparsePoint | FileAttributes.Directory | FileAttributes.ReadOnly)) != 0)
                        throw new IOException("不支持删除链接、文件夹或只读文件");
                    var pending = Path.Combine(staging, name);
                    if (!RuntimePrimitives.CommitFile(original, pending, overwrite: false, CancellationToken.None))
                        throw new IOException("待删除文件重名");
                    staged.Add((original, pending));
                }
                catch (Exception error) when (error is IOException or UnauthorizedAccessException)
                {
                    failed++;
                    if (warnings.Count < 20) warnings.Add($"{name}: {error.Message}");
                }
                if ((i + 1) % 50 == 0 || i + 1 == targets.Count)
                    progress?.Report(new("delete", i + 1, targets.Count, null));
            }
            var deleting = new HashSet<string>(staged.Select(item => Path.GetFileName(item.Path)), FileNames);
            if (deleting.Count == 0 && removeGroup is null)
                return new(0, 0, failed, null, warnings.ToArray(), null, null, Cancelled: stopped ? 1 : 0);

            // Past the rename boundary: publish matching indexes even when a
            // cancellation stops the remainder of a batch.
            var finish = CancellationToken.None;
            var mirrors = new List<(string Name, object Data)>();
            if (removeGroup is not null)
            {
                db.Execute("DELETE FROM category.category_images WHERE category_name = ?", finish, removeGroup);
                db.Execute("DELETE FROM category.categories WHERE name = ?", finish, removeGroup);
            }
            if (deleting.Count != 0)
            {
                Remove("image_features");
                Remove("sync_key_checks");
                Remove("meta.image_metadata");
                Remove("category.category_images");
                Remove("gallery_order.item_orders");
                if (hasRecent) Remove("history.recent_history");
                CleanObject("metadata.json", pair => deleting.Contains(Name(pair.Key)));
                CleanObject("hashes.json", pair => deleting.Contains(Name(pair.Key)) || deleting.Contains(Name(Text(pair.Value))));
                CleanList("recent.json");
                CleanList("order.json");
            }
            var categoryMirror = folders.Where(group => !Reserved(group.Name)).ToDictionary(group => group.Name,
                group => group.Members.Where(name => !deleting.Contains(name)).ToArray(), StringComparer.Ordinal);
            mirrors.Add(("categories.json", categoryMirror));
            var iconMirror = folders.Where(group => !Reserved(group.Name) && group.Icon is not null)
                .ToDictionary(group => group.Name, group => group.Icon!, StringComparer.Ordinal);
            foreach (var (group, icon) in iconMirror.ToArray())
            {
                if (!IsIconImage(icon) || !deleting.Contains(Name(icon))) continue;
                // An archive's separate icon attachment or an absolute icon
                // outside this gallery is not the image being removed.
                if (icon.Replace('\\', '/').StartsWith("category_icons/", StringComparison.Ordinal) ||
                    (RuntimePrimitives.IsAbsolutePath(icon) && !FileNames.Equals(Path.GetDirectoryName(Path.GetFullPath(icon)), images)))
                    continue;
                iconMirror.Remove(group);
                db.Execute("UPDATE category.categories SET icon_path = NULL WHERE name = ?", finish, group);
            }
            mirrors.Add(("category_icons.json", iconMirror));
            LibraryEdits.Commit(db, root, finish, mirrors.ToArray());
            committed = true;

            void Remove(string table)
            {
                foreach (var row in db.Read($"SELECT DISTINCT image_path FROM {table}", 1, finish))
                    if (row[0] is { } stored && deleting.Contains(Name(stored)))
                        db.Execute($"DELETE FROM {table} WHERE image_path = ?", finish, stored);
            }

            void CleanObject(string file, Func<KeyValuePair<string, JsonElement>, bool> match)
            {
                var value = ReadObject(root, file);
                var remove = value.Where(match).Select(pair => pair.Key).ToArray();
                if (remove.Length == 0) return;
                foreach (var key in remove) value.Remove(key);
                mirrors.Add((file, value));
            }

            void CleanList(string file)
            {
                var previous = ReadNames(root, file).ToArray();
                var remaining = previous.Where(value => !deleting.Contains(Name(value))).ToArray();
                if (remaining.Length != previous.Length) mirrors.Add((file, remaining));
            }
        }
        finally
        {
            foreach (var item in staged)
            {
                if (committed) RuntimePrimitives.DiscardTemporaryFile(item.Pending);
                else
                {
                    try
                    {
                        if (!RuntimePrimitives.CommitFile(item.Pending, item.Path, overwrite: false, CancellationToken.None))
                            throw new IOException("原始位置已被占用");
                    }
                    catch (Exception error) when (error is IOException or UnauthorizedAccessException)
                    {
                        System.Diagnostics.Trace.TraceWarning("Recover the original from {0}. {1}", item.Pending, error.Message);
                    }
                }
            }
            try { if (Directory.Exists(staging)) Directory.Delete(staging); }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
        }
        return new(staged.Count, 0, failed, null, warnings.ToArray(), null, null, Cancelled: stopped ? 1 : 0);
    }
}
