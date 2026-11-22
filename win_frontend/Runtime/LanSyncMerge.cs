using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;

namespace SuzuEmojy.Windows;

internal sealed record SyncIcon(string Type, string Value);
internal sealed record SyncCategory(string Id, string Name, SyncIcon? Icon);
internal sealed record SyncResource(string Key, string[] Keywords, string[] Categories);
// Array order is the order of the sidebar and the All gallery respectively.
internal sealed record SyncLibrary(SyncCategory[] Categories, SyncResource[] Resources)
{
    internal static readonly SyncLibrary Empty = new([], []);
}
internal sealed record SyncAssets(string[] Keys, string[] Icons)
{
    internal bool Any => Keys.Length + Icons.Length != 0;
    internal static SyncAssets Needed(SyncLibrary have, SyncLibrary want)
    {
        static IEnumerable<string> IconsIn(SyncLibrary library) => library.Categories.Select(c => c.Icon)
            .OfType<SyncIcon>().Where(i => i.Type == "image").Select(i => i.Value).Distinct(StringComparer.Ordinal);
        return new(want.Resources.Select(r => r.Key).Except(have.Resources.Select(r => r.Key)).ToArray(),
            IconsIn(want).Except(IconsIn(have)).ToArray());
    }
}
internal sealed record SyncMerge(SyncLibrary Library, Dictionary<string, string> Windows, Dictionary<string, string> Android);

internal static class LanSyncMerge
{
    private static readonly Regex Id = new("\\A[a-f0-9]{32}\\z", RegexOptions.CultureInvariant);
    private static readonly Regex Key = new("\\A[pf]:[a-f0-9]{32}\\z", RegexOptions.CultureInvariant);
    internal static readonly Regex IconAsset = new("\\Aicons/[a-f0-9]{64}\\.(png|gif|jpg|jpeg|webp|bmp)\\z", RegexOptions.CultureInvariant);
    internal static bool IsId(string? value) => value is not null && Id.IsMatch(value);
    internal static bool IsKey(string? value) => value is not null && Key.IsMatch(value);

    internal static void Validate(SyncLibrary? library)
    {
        void Require(bool condition) { if (!condition) throw new InvalidDataException("同步清单无效。"); }
        if (library?.Categories is null || library.Resources is null) throw new InvalidDataException("同步清单无效。");
        Require(library.Categories.Length <= 10_000 && library.Resources.Length <= 35_000);
        var ids = new HashSet<string>(StringComparer.Ordinal);
        var names = new HashSet<string>(StringComparer.Ordinal);
        foreach (var c in library.Categories)
        {
            Require(c is not null && IsId(c.Id) && ids.Add(c.Id) && ValidName(c.Name) && names.Add(c.Name));
            if (c?.Icon is { } icon)
                Require(icon.Value is { Length: > 0 } && (icon.Type switch
                {
                    "text" => icon.Value.Length <= 256 && !string.IsNullOrWhiteSpace(icon.Value) && !icon.Value.Any(char.IsControl)
                        && !LibrarySnapshotReader.IsIconImage(icon.Value),
                    "image" => IconAsset.IsMatch(icon.Value),
                    _ => false
                }));
        }
        var keys = new HashSet<string>(StringComparer.Ordinal);
        foreach (var r in library.Resources)
        {
            Require(r is not null && r.Key is not null && Key.IsMatch(r.Key) && keys.Add(r.Key));
            if (r is null) throw new InvalidDataException("同步清单无效。");
            Require(r.Keywords is { Length: <= 1024 } && r.Keywords.All(w => w is { Length: > 0 and <= 1024 }
                && !w.Any(ch => char.IsWhiteSpace(ch) || char.IsControl(ch))) &&
                r.Keywords.Distinct(StringComparer.Ordinal).Count() == r.Keywords.Length);
            Require(r.Categories is not null && r.Categories.All(id => id is not null && ids.Contains(id))
                && r.Categories.Distinct(StringComparer.Ordinal).Count() == r.Categories.Length);
        }
    }

    private static bool ValidName(string? name) => name is { Length: > 0 and <= 256 } && name.Trim() == name
        && !LibrarySnapshotReader.Reserved(name) && !name.Any(char.IsControl);
    internal static string[] Words(string text) => text.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries)
        .Distinct(StringComparer.Ordinal).OrderBy(s => s, StringComparer.Ordinal).ToArray();

    internal static SyncMerge Reconcile(SyncLibrary? baseline, SyncLibrary windows, SyncLibrary android,
        ISet<string>? previouslyHeld = null)
    {
        Validate(windows);
        Validate(android);
        if (baseline is not null) Validate(baseline);
        var old = baseline ?? SyncLibrary.Empty;
        var oldCats = old.Categories.ToDictionary(c => c.Id);
        var winIds = windows.Categories.ToDictionary(c => c.Id, c => c.Id);
        var phoneIds = android.Categories.ToDictionary(c => c.Id, c => c.Id);
        var unmatchedWin = windows.Categories.Where(c => !oldCats.ContainsKey(c.Id)).ToDictionary(c => c.Name);
        var winSet = winIds.Keys.ToHashSet();
        var phoneSet = phoneIds.Keys.ToHashSet();
        foreach (var c in android.Categories)
            if (!oldCats.ContainsKey(c.Id) && !winSet.Contains(c.Id) &&
                unmatchedWin.TryGetValue(c.Name, out var match) && !phoneSet.Contains(match.Id))
                phoneIds[c.Id] = match.Id;

        var a = Remap(windows, winIds);
        var b = Remap(android, phoneIds);
        var ac = a.Categories.ToDictionary(c => c.Id);
        var bc = b.Categories.ToDictionary(c => c.Id);
        var ar = a.Resources.ToDictionary(r => r.Key);
        var am = Members(a);
        var om = Members(old);
        var cats = new Dictionary<string, SyncCategory>();
        var restoreWindowsMembers = new HashSet<string>();
        foreach (var id in ac.Keys.Union(bc.Keys))
        {
            var prior = oldCats.GetValueOrDefault(id);
            var left = ac.GetValueOrDefault(id);
            var right = bc.GetValueOrDefault(id);
            if (left is null || right is null)
            {
                var kept = (left ?? right)!;
                if (prior is null) cats.Add(id, kept);
                else if (left is not null && (left != prior || !am[id].SetEquals(om[id].Where(ar.ContainsKey))))
                {
                    // The phone removed the folder, so its missing links are not individual edits.
                    cats.Add(id, left);
                    restoreWindowsMembers.Add(id);
                }
                continue;
            }
            cats.Add(id, new(id, PreferWindows(prior?.Name, left.Name, right.Name)!,
                PreferWindows(prior?.Icon, left.Icon, right.Icon)));
        }
        ResolveCategoryNames(cats, ac);

        var br = b.Resources.ToDictionary(r => r.Key);
        var or = old.Resources.ToDictionary(r => r.Key);
        var resources = new Dictionary<string, SyncResource>();
        foreach (var key in ar.Keys.Union(br.Keys))
        {
            var prior = or.GetValueOrDefault(key);
            var left = ar.GetValueOrDefault(key);
            var right = br.GetValueOrDefault(key);
            if (left is null || right is null)
            {
                var kept = (left ?? right)!;
                // Removed category links alone are not edits to the image.
                if (prior is null || left is not null && Edited(left, prior, ac))
                    resources.Add(key, kept with { Categories = kept.Categories.Where(cats.ContainsKey).ToArray() });
                continue;
            }
            var categories = Set(prior?.Categories, left.Categories, right.Categories)
                .Concat(left.Categories.Where(restoreWindowsMembers.Contains))
                .Where(cats.ContainsKey).Distinct(StringComparer.Ordinal).OrderBy(id => id, StringComparer.Ordinal).ToArray();
            resources.Add(key, new(key, Set(prior?.Keywords, left.Keywords, right.Keywords),
                categories));
        }
        var catOrder = Order(old.Categories.Select(c => c.Id), a.Categories.Select(c => c.Id),
            b.Categories.Select(c => c.Id), cats.Keys);
        // During a partial round each endpoint keeps held items in its own
        // slots. Their positions are not a common baseline after recovery.
        var galleryOrder = Order(old.Resources.Select(r => r.Key).Where(key => previouslyHeld?.Contains(key) != true), a.Resources.Select(r => r.Key),
            b.Resources.Select(r => r.Key), resources.Keys);
        var target = new SyncLibrary(catOrder.Select(id => cats[id]).ToArray(), galleryOrder.Select(key => resources[key]).ToArray());
        Validate(target);
        return new(target, Reverse(winIds), Reverse(phoneIds));

        Dictionary<string, string> Reverse(Dictionary<string, string> ids) => ids
            .Where(pair => cats.ContainsKey(pair.Value)).ToDictionary(pair => pair.Value, pair => pair.Key);
    }

    private static SyncLibrary Remap(SyncLibrary library, Dictionary<string, string> ids) => new(
        library.Categories.Select(c => c with { Id = ids[c.Id] }).ToArray(),
        library.Resources.Select(r => r with { Categories = r.Categories.Select(id => ids[id]).ToArray() }).ToArray());

    private static Dictionary<string, HashSet<string>> Members(SyncLibrary library)
    {
        var result = library.Categories.ToDictionary(c => c.Id, _ => new HashSet<string>(StringComparer.Ordinal));
        foreach (var r in library.Resources)
            foreach (var id in r.Categories) result[id].Add(r.Key);
        return result;
    }

    // Take a phone-only edit; in a conflict the Windows value (including null) wins.
    private static T? PreferWindows<T>(T? prior, T? left, T? right) where T : class =>
        Equals(left, prior) ? right : left;

    private static bool Edited(SyncResource current, SyncResource prior, Dictionary<string, SyncCategory> categories) =>
        !current.Keywords.ToHashSet().SetEquals(prior.Keywords) ||
        !current.Categories.ToHashSet().SetEquals(prior.Categories.Where(categories.ContainsKey));

    private static void ResolveCategoryNames(Dictionary<string, SyncCategory> merged, Dictionary<string, SyncCategory> windows)
    {
        // Reserve every uncontested name first. Resolve duplicates without losing a folder or its links.
        var used = merged.Values.Select(c => c.Name).ToHashSet(StringComparer.Ordinal);
        var displaced = merged.Values.GroupBy(c => c.Name, StringComparer.Ordinal).Where(g => g.Count() > 1)
            .SelectMany(g => g.OrderBy(c => windows.GetValueOrDefault(c.Id)?.Name == c.Name ? 0 : 1)
                .ThenBy(c => windows.ContainsKey(c.Id) ? 0 : 1).ThenBy(c => c.Id, StringComparer.Ordinal).Skip(1))
            .ToArray();
        var rename = new List<SyncCategory>();
        foreach (var folder in displaced)
        {
            if (windows.TryGetValue(folder.Id, out var local) && used.Add(local.Name))
                merged[folder.Id] = folder with { Name = local.Name };
            else
                rename.Add(folder);
        }
        foreach (var folder in rename)
        {
            for (int i = 2; ; i++)
            {
                string suffix = " (" + i.ToString(CultureInfo.InvariantCulture) + ")";
                int length = Math.Min(folder.Name.Length, 256 - suffix.Length);
                if (char.IsHighSurrogate(folder.Name[length - 1])) length--;
                string name = folder.Name.Substring(0, length).TrimEnd() + suffix;
                if (!used.Add(name)) continue;
                merged[folder.Id] = folder with { Name = name };
                break;
            }
        }
    }

    private static string[] Set(string[]? prior, string[] left, string[] right)
    {
        var old = (prior ?? []).ToHashSet();
        var a = left.ToHashSet();
        var b = right.ToHashSet();
        return left.Union(right).Where(item => !old.Contains(item) || (a.Contains(item) && b.Contains(item)))
            .OrderBy(s => s, StringComparer.Ordinal).ToArray();
    }

    private static string[] Order(IEnumerable<string> prior, IEnumerable<string> left, IEnumerable<string> right,
        IEnumerable<string> keep)
    {
        var remaining = keep.ToHashSet();
        var a = left.Where(remaining.Contains).ToArray();
        var b = right.Where(remaining.Contains).ToArray();
        var common = a.Intersect(b).ToHashSet();
        var old = prior.Where(common.Contains).ToArray();
        var known = old.ToHashSet();
        var aa = a.Where(known.Contains).ToArray();
        var bb = b.Where(known.Contains).ToArray();
        bool movedA = !aa.SequenceEqual(old), movedB = !bb.SequenceEqual(old);
        var primary = movedB && !movedA ? b : a;
        var secondary = ReferenceEquals(primary, a) ? b : a;
        var primarySet = primary.ToHashSet();
        var order = new LinkedList<string>(primary);
        var anchors = new Dictionary<string, LinkedListNode<string>>();
        for (var node = order.First; node is not null; node = node.Next) anchors.Add(node.Value, node);
        // Keep new items adjacent to a surviving neighbor on their source device.
        LinkedListNode<string>? next = null;
        for (int i = secondary.Length - 1; i >= 0; i--)
        {
            var id = secondary[i];
            if (anchors.TryGetValue(id, out var match)) next = match;
            else if (next is not null) next = anchors[id] = order.AddBefore(next, id);
        }
        int tailStart = Array.FindLastIndex(secondary, id => primarySet.Contains(id));
        for (int i = tailStart + 1; i < secondary.Length; i++)
            if (!anchors.ContainsKey(secondary[i])) order.AddLast(secondary[i]);
        return order.ToArray();
    }
}
