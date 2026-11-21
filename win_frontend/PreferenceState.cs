using System;
using System.Collections.Generic;
using System.Text.Json;

namespace SuzuEmojy.Windows;

// Keep the last persisted snapshot apart from values previewed on screen.
// Writes consume only committed edits, never a slider still being dragged.
internal sealed class PreferenceState(LibrarySnapshot initial)
{
    private readonly Dictionary<string, object> preview = new();
    private readonly Dictionary<string, object> queued = new();
    private readonly HashSet<string> deferredLayout = new();
    internal LibrarySnapshot Saved { get; private set; } = initial;
    internal LibrarySnapshot Effective
    {
        get
        {
            if (preview.Count == 0) return Saved;
            var settings = new Dictionary<string, JsonElement>(Saved.Settings);
            foreach (var (key, value) in preview) settings[key] = JsonSerializer.SerializeToElement(value);
            return Saved with { Settings = settings };
        }
    }

    internal void Accept(LibrarySnapshot snapshot) => Saved = snapshot;
    internal void Preview(string key, object value) => preview[key] = value;

    internal void StageLayout(string key, object value)
    {
        Preview(key, value);
        deferredLayout.Add(key);
    }

    internal void FlushLayout()
    {
        foreach (var key in deferredLayout) queued[key] = preview[key];
        deferredLayout.Clear();
    }

    internal void Queue(IReadOnlyDictionary<string, object> changes)
    {
        foreach (var (key, value) in changes) queued[key] = value;
    }

    internal Dictionary<string, object>? TakeWrite()
    {
        if (queued.Count == 0) return null;
        var write = new Dictionary<string, object>(queued);
        queued.Clear();
        return write;
    }

    internal void Acknowledge(IReadOnlyDictionary<string, object> written)
    {
        foreach (var (key, value) in written)
        {
            if (preview.TryGetValue(key, out var latest) && Equals(value, latest))
            {
                preview.Remove(key);
                deferredLayout.Remove(key);
            }
        }
    }

    internal void Revert()
    {
        queued.Clear();
        preview.Clear();
        deferredLayout.Clear();
    }
}
