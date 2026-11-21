using System;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Threading;
using SuzuEmojy.Windows.Controls;
using Forms = System.Windows.Forms;
using Icon = System.Drawing.Icon;

namespace SuzuEmojy.Windows.Desktop;

internal sealed class TrayIcon : IDisposable
{
    private readonly Icon artwork;
    private readonly FluentMenu menu;
    private readonly Forms.NotifyIcon icon = new() { Text = "SuzuEmojy" };
    private Window? menuHost;

    internal static TrayIcon? TryCreate(FluentMenuPalette palette, Action show, Action settings, Action exit)
    {
        if (FindWindowW("Shell_TrayWnd", null) == 0) return null;
        using var stream = Application.GetResourceStream(new Uri("ico.ico", UriKind.Relative)).Stream;
        using var source = new Icon(stream);
        return new TrayIcon((Icon)source.Clone(), palette, show, settings, exit);
    }

    private TrayIcon(Icon artwork, FluentMenuPalette palette, Action show, Action settings, Action exit)
    {
        this.artwork = artwork;
        try
        {
            menu = CreateMenu(palette, () => Invoke(show), () => Invoke(settings), () => Invoke(exit));
            menu.Closed += (_, _) => menu.Dispatcher.InvokeAsync(HideMenuHost, DispatcherPriority.Background);
            icon.MouseClick += (_, e) =>
            {
                if (e.Button == Forms.MouseButtons.Left) Invoke(show);
                else if (e.Button == Forms.MouseButtons.Right) OpenMenu();
            };
            icon.Icon = artwork;
            icon.Visible = true;
        }
        catch
        {
            icon.Dispose();
            artwork.Dispose();
            throw;
        }
    }

    internal static FluentMenu CreateMenu(FluentMenuPalette palette, Action show, Action settings, Action exit)
    {
        var menu = new FluentMenu(palette) { Placement = PlacementMode.MousePoint };
        menu.AddItem("显示主面板", show);
        menu.AddItem("设置", settings);
        menu.AddSeparator();
        menu.AddItem("退出", exit);
        return menu;
    }

    private void OpenMenu()
    {
        if (menu.IsOpen) return;
        var host = menuHost ??= CreateMenuHost();
        CursorPlacement.Move(host, new System.Windows.Size(1, 1));
        host.Show();
        host.Activate();
        menu.PlacementTarget = host;
        menu.IsOpen = true;
    }

    private Window CreateMenuHost()
    {
        // A standalone HWND gives the popup a presentation source even when the
        // main panel is hidden. It has no taskbar button or visible contents.
        var host = new Window
        {
            Width = 1, Height = 1,
            ResizeMode = ResizeMode.NoResize,
            WindowStyle = WindowStyle.None,
            ShowInTaskbar = false,
            AllowsTransparency = true,
            Background = Brushes.Transparent,
        };
        host.Deactivated += (_, _) => menu.IsOpen = false;
        host.Closed += (_, _) => menuHost = null;
        return host;
    }

    private void Invoke(Action action)
    {
        menu.IsOpen = false;
        // Let the action transfer foreground focus directly to the main panel.
        try { action(); }
        finally { HideMenuHost(); }
    }

    private void HideMenuHost()
    {
        if (!menu.IsOpen) menuHost?.Hide();
    }

    internal void NotifyPasteSkipped() =>
        icon.ShowBalloonTip(4000, "未自动粘贴", "未能自动粘贴，请返回目标窗口手动粘贴。", Forms.ToolTipIcon.Info);

    internal void NotifyHotkeysUnavailable(string message) =>
        icon.ShowBalloonTip(5000, "快捷键不可用", message, Forms.ToolTipIcon.Warning);

    public void Dispose()
    {
        icon.Dispose();
        menu.IsOpen = false;
        menu.PlacementTarget = null;
        menuHost?.Close();
        artwork.Dispose();
    }

    [DllImport("user32.dll", ExactSpelling = true, CharSet = CharSet.Unicode)]
    private static extern nint FindWindowW(string className, string? windowName);
}
