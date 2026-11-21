using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using Forms = System.Windows.Forms;

namespace SuzuEmojy.Windows.Desktop;

internal static class CursorPlacement
{
    internal static nint Move(Window window, System.Windows.Size size)
    {
        var cursor = Forms.Cursor.Position;
        var work = Forms.Screen.FromPoint(cursor).WorkingArea;
        var handle = new WindowInteropHelper(window).EnsureHandle();
        const uint placeFlags = 0x0010 | 0x0004; // SWP_NOACTIVATE | SWP_NOZORDER
        // Move the still-hidden HWND to the target monitor before querying its DPI.
        SetWindowPos(handle, 0, cursor.X, cursor.Y, 0, 0, placeFlags | 0x0001);
        var dpi = GetDpiForWindow(handle);
        double scale = dpi > 0 ? dpi / 96.0 : 1;
        int pixelWidth = Math.Min((int)Math.Round(size.Width * scale), work.Width);
        int pixelHeight = Math.Min((int)Math.Round(size.Height * scale), work.Height);
        window.Width = pixelWidth / scale;
        window.Height = pixelHeight / scale;
        var point = Choose(cursor, work, new System.Drawing.Size(pixelWidth, pixelHeight), (int)Math.Round(10 * scale));
        SetWindowPos(handle, 0, point.X, point.Y, pixelWidth, pixelHeight, placeFlags);
        return handle;
    }

    internal static System.Drawing.Point Choose(System.Drawing.Point cursor, Rectangle work, System.Drawing.Size panel, int gap)
    {
        int x = cursor.X + gap, y = cursor.Y + gap;
        if (x + panel.Width > work.Right) x = cursor.X - gap - panel.Width;
        if (y + panel.Height > work.Bottom) y = cursor.Y - gap - panel.Height;
        return new(Math.Clamp(x, work.Left, Math.Max(work.Left, work.Right - panel.Width)),
            Math.Clamp(y, work.Top, Math.Max(work.Top, work.Bottom - panel.Height)));
    }

    [DllImport("user32.dll")] private static extern uint GetDpiForWindow(nint window);
    [DllImport("user32.dll")] private static extern bool SetWindowPos(nint window, nint after, int x, int y,
        int width, int height, uint flags);
}
