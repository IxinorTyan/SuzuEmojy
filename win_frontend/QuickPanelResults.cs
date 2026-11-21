using System;
using System.Collections.Generic;
using System.Linq;

namespace SuzuEmojy.Windows;

internal sealed class QuickPanelResults
{
    private Sticker[] library = [];
    private readonly List<Sticker> recent = [];
    private int limit;

    internal QuickPanelResults(LibrarySnapshot snapshot) => Refresh(snapshot);

    internal void Refresh(LibrarySnapshot snapshot)
    {
        library = snapshot.Images;
        limit = snapshot.Number("recent_limit", 30, 1, 300);
        var byName = new Dictionary<string, Sticker>(StringComparer.OrdinalIgnoreCase);
        foreach (var item in library) byName.TryAdd(item.Name, item);
        var ordered = recent.Select(sticker => sticker.Name).Concat(snapshot.Recent ?? [])
            .Where(byName.ContainsKey).Select(name => byName[name]).Distinct().Take(limit).ToArray();
        recent.Clear();
        recent.AddRange(ordered);
    }

    internal Sticker[] Find(string input)
    {
        var query = input.Trim();
        return query.Length == 0 ? recent.ToArray() : library
            .Where(item => item.Keywords.IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0).Take(30).ToArray();
    }

    internal void Record(Sticker original)
    {
        recent.Remove(original);
        recent.Insert(0, original);
        if (recent.Count > limit) recent.RemoveAt(limit);
    }
}
