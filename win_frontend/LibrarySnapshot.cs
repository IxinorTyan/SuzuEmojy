using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Win32;

namespace SuzuEmojy.Windows;

internal sealed record Sticker(string Path, string Name, string Keywords, string[] Categories, bool Animated);
internal sealed record Category(string Name, string? Icon, string? IconPath);
internal sealed record Colors(string Accent, string Bg);
internal sealed record Palette(string Name, Colors Light, Colors Dark);
internal sealed record WallpaperAsset(string Path, string Revision);

internal sealed record LibrarySnapshot(Dictionary<string, JsonElement> Settings,
    Dictionary<string, Palette> Themes, Category[] Categories, Sticker[] Images, string[] Recent, WallpaperAsset? Wallpaper = null)
{
    internal static async Task<LibrarySnapshot> ReadAsync(string repository, string data)
    {
        var result = await PythonBridge.Run(repository, "services.gallery_snapshot", TimeSpan.FromSeconds(20),
            CancellationToken.None, data);
        return JsonSerializer.Deserialize<LibrarySnapshot>(result, new JsonSerializerOptions
        {
            PropertyNameCaseInsensitive = true, MaxDepth = 32
        }) ?? throw new InvalidOperationException("无法读取图库。");
    }

    internal string String(string key, string fallback) =>
        Settings.TryGetValue(key, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? fallback : fallback;

    internal bool Flag(string key, bool fallback) =>
        Settings.TryGetValue(key, out var value) ? value.ValueKind switch
        {
            JsonValueKind.True => true, JsonValueKind.False => false, _ => fallback,
        } : fallback;

    internal int Number(string key, int fallback, int low, int high) =>
        Settings.TryGetValue(key, out var value) && value.ValueKind == JsonValueKind.Number &&
        value.TryGetInt32(out var number) ? Math.Clamp(number, low, high) : fallback;

    internal bool Dark => String("appearance_mode", String("theme_mode", "system")) switch
    {
        "dark" => true,
        "light" => false,
        _ => Registry.GetValue(@"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            "AppsUseLightTheme", 1) is 0,
    };

    internal Colors Theme(bool dark)
    {
        var palette = Themes.GetValueOrDefault(String(dark ? "dark_theme_key" : "light_theme_key", "white"))
                      ?? Themes["white"];
        return dark ? palette.Dark : palette.Light;
    }
}
