using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;

namespace SuzuEmojy.Windows;

internal static class LibrarySnapshotReader
{
    private static readonly StringComparer FileNames = StringComparer.OrdinalIgnoreCase;
    private static readonly HashSet<string> PictureFormats = new(FileNames) { ".png", ".apng", ".jpg", ".jpeg", ".gif", ".webp", ".webm", ".tgs" };
    internal static bool IsImageName(string name) => PictureFormats.Contains(Extension(name));
    private static readonly HashSet<string> AnimatedFormats = new(FileNames) { ".gif", ".apng", ".webp" };
    private static readonly HashSet<string> IconFormats = new(FileNames) { ".png", ".gif", ".jpg", ".jpeg", ".webp", ".bmp" };
    internal static bool IsIconImage(string name) => IconFormats.Contains(Extension(name));
    internal static bool Reserved(string name) => name is "全部表情" or "未分类" or "新建分类";

    internal static LibrarySnapshot Read(string data, CancellationToken token)
    {
        var root = Path.GetFullPath(data);
        if (Directory.Exists(root)) root = RuntimePrimitives.ExistingPath(root);
        var paths = OrderedImages(root, token);
        var byName = new Dictionary<string, (string Path, string Name)>(FileNames);
        foreach (var item in paths) byName[item.Name] = item;

        var icons = ReadObject(root, "category_icons.json");
        var groups = new List<(string Name, string? Icon, List<string> Members)>();
        ReadCategories(root, icons, groups, token);
        var categories = new List<Category>();
        var membership = new Dictionary<string, List<string>>(FileNames);
        foreach (var group in groups)
        {
            if (Reserved(group.Name)) continue;
            string? icon = group.Icon;
            string? iconPath = null;
            if (icon is not null && IconFormats.Contains(Extension(icon)))
            {
                iconPath = IconPath(root, icon);
                icon = null;
            }
            categories.Add(new(group.Name, icon, iconPath));
            var seen = new HashSet<string>(FileNames);
            foreach (var name in group.Members)
            {
                if (!byName.ContainsKey(name) || !seen.Add(name)) continue;
                if (!membership.TryGetValue(name, out var list)) membership.Add(name, list = new());
                list.Add(group.Name);
            }
        }

        var keywords = new Dictionary<string, string>(FileNames);
        foreach (var (path, value) in ReadObject(root, "metadata.json")) keywords[Name(path)] = Keyword(value);
        foreach (var row in Rows(root, "metadata.db", "SELECT image_path, keywords FROM image_metadata", 2, token))
            keywords[Name(row[0])] = row[1] ?? "";
        var stickers = new List<Sticker>(paths.Count);
        foreach (var file in paths)
        {
            token.ThrowIfCancellationRequested();
            stickers.Add(new(file.Path, file.Name, keywords.GetValueOrDefault(file.Name) ?? "",
                membership.TryGetValue(file.Name, out var list) ? list.ToArray() : [],
                AnimatedFormats.Contains(Path.GetExtension(file.Name))));
        }

        return new(ReadObject(root, "config.json"), LibraryPalettes.Create(), categories.ToArray(),
            stickers.ToArray(), ReadRecent(root, byName, token), ReadWallpaper(root));
    }

    internal static List<(string Path, string Name)> ImageFiles(string root, CancellationToken token)
    {
        var directory = Path.Combine(root, "images");
        var paths = new List<(string Path, string Name)>();
        if (Directory.Exists(directory))
        {
            foreach (var file in new DirectoryInfo(directory).EnumerateFiles())
            {
                token.ThrowIfCancellationRequested();
                if (IsImageName(file.Name) &&
                    ((file.Attributes & FileAttributes.ReparsePoint) == 0 || File.Exists(file.FullName)))
                    paths.Add((file.FullName, file.Name));
            }
        }
        paths.Sort((a, b) => CompareNames(b.Name, a.Name));
        return paths;
    }

    internal static List<(string Path, string Name)> OrderedImages(string root, CancellationToken token, WindowsSqlite? db = null)
    {
        var paths = ImageFiles(root, token);
        var byName = new Dictionary<string, (string Path, string Name)>(FileNames);
        foreach (var item in paths) byName[item.Name] = item;

        const string query = "SELECT image_path FROM item_orders ORDER BY sort_order ASC";
        var order = db?.Read(query, 1, token) ?? Rows(root, "order.db", query, 1, token);
        var ordered = (order.Count > 0 ? order.Select(row => row[0]) : ReadNames(root, "order.json"))
            .Select(Name).Distinct(FileNames).ToArray();
        var alreadyOrdered = new HashSet<string>(ordered, FileNames);
        var sorted = new List<(string Path, string Name)>(paths.Count);
        foreach (var file in paths)
            if (!alreadyOrdered.Contains(file.Name)) sorted.Add(file);
        foreach (var name in ordered)
            if (byName.TryGetValue(name, out var file)) sorted.Add(file);
        return sorted;
    }

    internal static void ReadCategories(string root, Dictionary<string, JsonElement> icons,
        List<(string Name, string? Icon, List<string> Members)> groups, CancellationToken token)
    {
        using var db = WindowsSqlite.OpenIfPresent(Path.Combine(root, "categories.db"));
        if (db is not null)
        {
            // Both tables describe one view of the sidebar. Read them at the same revision.
            db.Read("BEGIN", 0, token);
            var rows = db.Read("SELECT name, icon_path FROM categories ORDER BY sort_order ASC, id ASC", 2, token);
            var relations = db.Read("SELECT category_name, image_path FROM category_images", 2, token);
            db.Read("COMMIT", 0, token);
            if (rows.Count != 0)
            {
                var byCategory = new Dictionary<string, List<string>>(StringComparer.Ordinal);
                foreach (var row in rows)
                {
                    if (row[0] is not { } name || byCategory.ContainsKey(name)) continue;
                    var members = new List<string>();
                    byCategory.Add(name, members);
                    groups.Add((name, string.IsNullOrEmpty(row[1]) ? Text(icons.GetValueOrDefault(name)) : row[1], members));
                }
                foreach (var row in relations)
                    if (row[0] is { } name && byCategory.TryGetValue(name, out var list)) list.Add(Name(row[1]));
                return;
            }
        }
        foreach (var (name, members) in ReadObject(root, "categories.json"))
        {
            token.ThrowIfCancellationRequested();
            groups.Add((name, Text(icons.GetValueOrDefault(name)), members.ValueKind == JsonValueKind.Array
                ? members.EnumerateArray().Select(value => Name(Text(value))).ToList() : []));
        }
    }

    private static string[] ReadRecent(string root, Dictionary<string, (string Path, string Name)> byName, CancellationToken token)
    {
        const string query = "SELECT image_path FROM recent_history ORDER BY ";
        List<string?[]> rows;
        try { rows = Rows(root, "recent.db", query + "updated_at DESC, id DESC", 1, token); }
        catch (LibrarySqliteException)
        {
            // Qt archives may still have used_at. Never run a migration just to display them.
            try { rows = Rows(root, "recent.db", query + "used_at DESC, id DESC", 1, token); }
            catch (LibrarySqliteException) { rows = []; }
        }
        return (rows.Count > 0 ? rows.Select(row => row[0]) : ReadNames(root, "recent.json"))
            .Select(Name).Distinct(FileNames).Where(byName.ContainsKey).Select(name => byName[name].Name).ToArray();
    }

    private static List<string?[]> Rows(string root, string name, string query, int columns, CancellationToken token)
    {
        using var db = WindowsSqlite.OpenIfPresent(Path.Combine(root, name));
        return db?.Read(query, columns, token) ?? [];
    }

    internal static Dictionary<string, JsonElement> ReadObject(string root, string name) =>
        ReadJson<Dictionary<string, JsonElement>>(root, name) ?? new(StringComparer.Ordinal);

    internal static IEnumerable<string?> ReadNames(string root, string name) =>
        (ReadJson<JsonElement[]>(root, name) ?? []).Select(Text);

    private static T? ReadJson<T>(string root, string name) where T : class
    {
        try
        {
            using var file = File.Open(Path.Combine(root, name), FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            using var text = new StreamReader(file); // Includes support for old Windows UTF-8 BOMs.
            return JsonSerializer.Deserialize<T>(text.ReadToEnd());
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or JsonException) { return null; }
    }

    internal static string? Text(JsonElement value) => value.ValueKind == JsonValueKind.String ? value.GetString() : null;

    private static string Keyword(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.String => value.GetString() ?? "",
        JsonValueKind.True => "True",
        JsonValueKind.Number when value.TryGetDouble(out double number) && number != 0 => value.GetRawText(),
        _ => "",
    };

    internal static string Name(string? path) => path is null ? "" : path.Substring(Math.Max(path.LastIndexOf('/'), path.LastIndexOf('\\')) + 1);

    private static string Extension(string name)
    {
        int index = name.LastIndexOf('.');
        return index > 0 && index < name.Length - 1 ? name.Substring(index) : "";
    }

    internal static string? IconPath(string root, string icon)
    {
        try
        {
            icon = icon.Replace('/', '\\');
            string path = RuntimePrimitives.IsAbsolutePath(icon) ? icon : Path.Combine(root,
                icon.StartsWith("category_icons\\", StringComparison.Ordinal) ? icon : Path.Combine("images", icon));
            return File.Exists(path) ? path : null;
        }
        catch (ArgumentException) { return null; }
    }

    private static WallpaperAsset? ReadWallpaper(string root)
    {
        var appearance = Path.Combine(root, ManagedWallpaper.Folder);
        var path = Path.Combine(appearance, ManagedWallpaper.FileName);
        try
        {
            if ((File.GetAttributes(appearance) & FileAttributes.ReparsePoint) != 0 ||
                (File.GetAttributes(path) & (FileAttributes.ReparsePoint | FileAttributes.Directory)) != 0) return null;
            using var file = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            return new(path, RuntimePrimitives.Revision(file.SafeFileHandle));
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException) { return null; }
    }

    // The Python reader sorts by Unicode scalar value, not locale/Windows UTF-16 code units.
    internal static int CompareNames(string a, string b)
    {
        for (int i = 0; i < Math.Min(a.Length, b.Length); i++)
        {
            if (a[i] == b[i]) continue;
            bool upperA = char.IsSurrogatePair(a, i), upperB = char.IsSurrogatePair(b, i);
            if (upperA != upperB) return upperA ? 1 : -1;
            return a[i].CompareTo(b[i]);
        }
        return a.Length.CompareTo(b.Length);
    }
}
