using System;
using System.Diagnostics;
using System.IO;
using System.Text.Json;

namespace SuzuEmojy.Windows.Desktop;

// Separate from config.json: moving the window must not refresh
// the library, or race a write to shared Qt/WPF preferences.
internal sealed class WindowPlacementStore(string data)
{
    private readonly string path = Path.Combine(data, "window-placement.windows.json");

    internal SavedWindowPlacement? Read()
    {
        try
        {
            using var file = File.OpenRead(path);
            if (file.Length > 4096) return null;
            return JsonSerializer.Deserialize<SavedWindowPlacement>(file) is { IsValid: true } saved ? saved : null;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or JsonException)
        {
            return null;
        }
    }

    internal bool Write(SavedWindowPlacement placement)
    {
        var folder = Path.GetDirectoryName(path)!;
        var temporary = Path.Combine(folder, $".window-placement-{Guid.NewGuid():N}.tmp");
        try
        {
            Directory.CreateDirectory(folder);
            using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write))
                JsonSerializer.Serialize(file, placement);
            RuntimePrimitives.ReplaceFile(temporary, path);
            return true;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            Trace.TraceWarning("Could not save window placement: {0}", error.Message);
            return false;
        }
        finally
        {
            try { File.Delete(temporary); }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
        }
    }
}
