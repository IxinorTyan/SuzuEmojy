using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Threading;

namespace SuzuEmojy.Windows;

// config.json is also read by the legacy frontend. Merge only the supplied keys
// and leave unknown settings, including their JSON types, intact.
internal static class LibraryPreferences
{
    private static readonly JsonSerializerOptions Output = new() { WriteIndented = true };

    internal static Dictionary<string, JsonElement> Save(string data, IReadOnlyDictionary<string, object> changes,
        CancellationToken cancellation)
    {
        foreach (var pair in changes)
            if (!Valid(pair.Key, pair.Value)) throw new ArgumentException($"无效的设置项: {pair.Key}");

        var path = Path.Combine(data, "config.json");
        var temp = Path.Combine(data, $".config-{Guid.NewGuid():N}.tmp");
        var settings = Read(path);
        foreach (var pair in changes) settings[pair.Key] = JsonSerializer.SerializeToElement(pair.Value);
        cancellation.ThrowIfCancellationRequested();
        Directory.CreateDirectory(data);
        try
        {
            using (var file = new FileStream(temp, FileMode.CreateNew, FileAccess.Write))
            {
                JsonSerializer.Serialize(file, settings, Output);
                file.Flush(flushToDisk: true);
            }
            RuntimePrimitives.CommitFile(temp, path, overwrite: true, cancellation);
        }
        finally { RuntimePrimitives.DiscardTemporaryFile(temp); }
        return settings;
    }

    private static Dictionary<string, JsonElement> Read(string path)
    {
        try
        {
            using var file = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read | FileShare.Delete);
            using var reader = new StreamReader(file); // Accept the legacy UTF-8 BOM.
            return JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(reader.ReadToEnd())
                ?? throw new JsonException("无法读取已有设置。");
        }
        catch (FileNotFoundException) { return new(); }
        catch (DirectoryNotFoundException) { return new(); }
    }

    private static bool Valid(string key, object value) => key switch
    {
        "appearance_mode" => value is "system" or "light" or "dark",
        "light_theme_key" or "dark_theme_key" => value is string theme && LibraryPalettes.Contains(theme),
        "always_on_top" or "hide_main_after_paste" or "show_setting_button" or "show_multi_select_button"
            or "convert_static_to_gif" or "sidebar_is_grid_mode" or "use_system_font" or "show_sidebar_tooltip" or "disable_animations" => value is bool,
        "global_hotkey" or "quick_panel_hotkey" => value is string { Length: <= 60 },
        "qq_data_path" or "qq_save_path" or "tg_save_path" or "tg_batch_save_path" =>
            value is string { Length: > 0 and <= 32767 } path && RuntimePrimitives.IsAbsolutePath(path),
        _ => value is int n && (key switch
        {
            "thumbnail_size" => n is >= 40 and <= 300,
            "sidebar_icon_size" => n is >= 12 and <= 40,
            "category_grid_icon_size" => n is >= 20 and <= 200,
            "sidebar_width_grid" or "sidebar_width_list" => n is >= 60 and <= 400,
            "preview_delay" => n is >= 0 and <= 5000,
            "preview_size" => n is >= 80 and <= 800,
            "quick_panel_width" => n is >= 250 and <= 800,
            "quick_panel_height" => n is >= 150 and <= 1000,
            "quick_panel_thumbnail_size" => n is >= 40 and <= 200,
            "recent_limit" => n is >= 1 and <= 300,
            "wallpaper_blur" => n is >= 0 and <= 40,
            "wallpaper_brightness" => n is >= -100 and <= 100,
            "wallpaper_overlay_opacity" => n is >= 0 and <= 100,
            _ => false
        })
    };
}
