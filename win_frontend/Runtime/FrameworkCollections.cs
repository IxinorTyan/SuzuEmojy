#if NETFRAMEWORK
using System;
using System.Collections.Generic;

namespace SuzuEmojy.Windows;

internal static class FrameworkCollections
{
    internal static void Deconstruct<TKey, TValue>(this KeyValuePair<TKey, TValue> pair, out TKey key, out TValue value)
    { key = pair.Key; value = pair.Value; }

    internal static TValue? GetValueOrDefault<TKey, TValue>(this IReadOnlyDictionary<TKey, TValue> source, TKey key) =>
        source.TryGetValue(key, out var value) ? value : default;

    internal static bool TryAdd<TKey, TValue>(this Dictionary<TKey, TValue> source, TKey key, TValue value)
    {
        if (source.ContainsKey(key)) return false;
        source.Add(key, value);
        return true;
    }

    internal static IEnumerable<TSource> DistinctBy<TSource, TKey>(this IEnumerable<TSource> source, Func<TSource, TKey> key)
    {
        var seen = new HashSet<TKey>();
        foreach (var item in source)
            if (seen.Add(key(item))) yield return item;
    }
}
#endif
