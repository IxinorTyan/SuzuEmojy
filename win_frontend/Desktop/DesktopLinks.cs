using System;
using System.ComponentModel;
using System.Diagnostics;

namespace SuzuEmojy.Windows.Desktop;

internal static class DesktopLinks
{
    internal const string ResourceSite = "https://suzuemojy-share.pages.dev/";
    internal const string Project = "https://github.com/IxinorTyan/SuzuEmojy";
    internal const string Author = "https://github.com/IxinorTyan";

    internal static void Open(string url, Action<string> failed)
    {
        try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true })?.Dispose(); }
        catch (Exception error) when (error is Win32Exception or InvalidOperationException) { failed(error.Message); }
    }
}
