using System;
using System.Collections.Generic;
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

    internal static SyncMerge Reconcile(SyncLibrary? baseline, SyncLibrary windows, SyncLibrary android)
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
        var am = Members(a);
        var bm = Members(b);
        var om = Members(old);
        var cats = new Dictionary<string, SyncCategory>();
        foreach (var id in ac.Keys.Union(bc.Keys))
        {
            var prior = oldCats.GetValueOrDefault(id);
            var left = ac.GetValueOrDefault(id);
            var right = bc.GetValueOrDefault(id);
            if (left is null || right is null)
            {
                var kept = (left ?? right)!;
                if (prior is null) cats.Add(id, kept);
                else if (kept != prior || !(left is null ? bm : am)[id].IsSubsetOf(om[id]))
                    throw Conflict(prior.Name, "一端删除了收藏夹，另一端修改了它");
                continue;
            }
            cats.Add(id, new(id, Field(prior?.Name, left.Name, right.Name, "收藏夹名称", left.Name)!,
                Field(prior?.Icon, left.Icon, right.Icon, "收藏夹图标", left.Name)));
        }
        var collision = cats.Values.GroupBy(c => c.Name).FirstOrDefault(g => g.Count() > 1);
        if (collision is not null) throw Conflict(collision.Key, "两端修改后出现同名收藏夹");

        var ar = a.Resources.ToDictionary(r => r.Key);
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
                if (prior is null) resources.Add(key, kept with { Categories = kept.Categories.Where(cats.ContainsKey).ToArray() });
                else
                {
                    // A category deletion also removes memberships; this is not
                    // an independent edit of each image in that category.
                    var ownCats = left is null ? bc : ac;
                    if (!kept.Keywords.ToHashSet().SetEquals(prior.Keywords) ||
                        !kept.Categories.ToHashSet().SetEquals(prior.Categories.Where(ownCats.ContainsKey)))
                        throw Conflict(key, "一端删除了表情，另一端修改了它的关键词或收藏夹");
                }
                continue;
            }
            resources.Add(key, new(key, Set(prior?.Keywords, left.Keywords, right.Keywords),
                Set(prior?.Categories, left.Categories, right.Categories).Where(cats.ContainsKey).ToArray()));
        }
        var catOrder = Order(old.Categories.Select(c => c.Id), a.Categories.Select(c => c.Id),
            b.Categories.Select(c => c.Id), cats.Keys, "收藏夹排序");
        var galleryOrder = Order(old.Resources.Select(r => r.Key), a.Resources.Select(r => r.Key),
            b.Resources.Select(r => r.Key), resources.Keys, "全部表情排序");
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

    private static T? Field<T>(T? prior, T? left, T? right, string label, string name) where T : class
    {
        if (Equals(left, right) || Equals(right, prior)) return left;
        if (Equals(left, prior)) return right;
        throw Conflict(name, $"两端修改了同一{label}");
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
        IEnumerable<string> keep, string label)
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
        if (movedA && movedB && !aa.SequenceEqual(bb)) throw Conflict(label, "两端同时修改了排序");
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

    private static IOException Conflict(string item, string reason) =>
        new($"同步冲突：{item}。{reason}，请调整后重试。");
}
