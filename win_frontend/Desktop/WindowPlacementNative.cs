using System.Collections.Generic;
using System.Drawing;
using System.Runtime.InteropServices;

namespace SuzuEmojy.Windows.Desktop;

internal static class WindowPlacementNative
{
    internal static DisplayArea[] Displays()
    {
        var displays = new List<DisplayArea>();
        // Enumerate rather than using a cached monitor list during hot unplug.
        EnumDisplayMonitors(0, 0, (monitor, _, _, _) =>
        {
            var info = new MonitorInfo { Size = Marshal.SizeOf<MonitorInfo>(), Device = "" };
            if (GetMonitorInfoW(monitor, ref info) && info.Work.Bounds is { Width: > 0, Height: > 0 } work)
                displays.Add(new(info.Device, work, (info.Flags & 1) != 0));
            return true;
        }, 0);
        return displays.ToArray();
    }

    internal static Rectangle? Bounds(nint window) => GetWindowRect(window, out var rect) ? rect.Bounds : null;

    internal static bool IsWindowed(nint window) => !IsIconic(window) && !IsZoomed(window);

    internal static bool Move(nint window, Rectangle bounds) =>
        SetWindowPos(window, 0, bounds.X, bounds.Y, bounds.Width, bounds.Height,
            0x0004 | 0x0010 | 0x0200); // SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER

    [StructLayout(LayoutKind.Sequential)]
    private struct Rect
    {
        internal int Left, Top, Right, Bottom;
        internal readonly Rectangle Bounds => Rectangle.FromLTRB(Left, Top, Right, Bottom);
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct MonitorInfo
    {
        internal int Size;
        internal Rect Monitor, Work;
        internal uint Flags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)]
        internal string Device;
    }

    private delegate bool MonitorCallback(nint monitor, nint dc, nint bounds, nint data);
    [DllImport("user32.dll")] private static extern bool EnumDisplayMonitors(nint dc, nint clip, MonitorCallback callback, nint data);
    [DllImport("user32.dll", ExactSpelling = true)] private static extern bool GetMonitorInfoW(nint monitor, ref MonitorInfo info);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(nint window, out Rect bounds);
    [DllImport("user32.dll")] private static extern bool IsIconic(nint window);
    [DllImport("user32.dll")] private static extern bool IsZoomed(nint window);
    [DllImport("user32.dll")] private static extern bool SetWindowPos(nint window, nint after, int x, int y, int width, int height, uint flags);
}
