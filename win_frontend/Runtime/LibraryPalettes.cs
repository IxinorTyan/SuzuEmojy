using System.Collections.Generic;

namespace SuzuEmojy.Windows;

internal static class LibraryPalettes
{
    // Keep the development Qt reader's palette names and values as the compatibility baseline.
    private static readonly Dictionary<string, Palette> Themes = new()
    {
        ["white"] = new("白色", new("#5C5C5C", "#FFFFFF"), new("#A6A6A6", "#000000")),
        ["black"] = new("黑色", new("#000000", "#FFFFFF"), new("#A6A6A6", "#000000")),
        ["rouge"] = new("胭脂红", new("#C97C86", "#FCF1EF"), new("#E2897F", "#201A1F")),
        ["sky"] = new("天空蓝", new("#6FA0D9", "#EFF5FB"), new("#6FA0E0", "#161B2A")),
        ["lavender"] = new("薰衣草紫", new("#A594D9", "#F4F1FB"), new("#A594E8", "#1C1830")),
        ["sage"] = new("鼠尾草绿", new("#82AD8E", "#F0F5EE"), new("#7FB08F", "#161F19")),
        ["apricot"] = new("暮霞杏橙", new("#D9A05A", "#FBF3E7"), new("#D6A15E", "#241C14")),
    };

    internal static Dictionary<string, Palette> Create() => new(Themes);
    internal static bool Contains(string key) => Themes.ContainsKey(key);
}
