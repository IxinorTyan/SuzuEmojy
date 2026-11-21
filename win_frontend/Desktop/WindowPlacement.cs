using System;
using System.Collections.Generic;
using System.Drawing;
using System.Linq;

namespace SuzuEmojy.Windows.Desktop;

internal sealed record SavedWindowPlacement(int X, int Y, int Width, int Height, string? Display)
{
    internal Rectangle Bounds => new(X, Y, Width, Height);
    internal bool IsValid => Width is > 0 and <= 100_000 && Height is > 0 and <= 100_000 &&
        (long)X + Width <= int.MaxValue && (long)Y + Height <= int.MaxValue;

    internal static SavedWindowPlacement From(Rectangle bounds, string display) =>
        new(bounds.X, bounds.Y, bounds.Width, bounds.Height, display);
}

internal sealed record DisplayArea(string Id, Rectangle WorkArea, bool Primary);

internal static class WindowPlacement
{
    // All coordinates come from the desktop API in the same coordinate space,
    // including negative origins and monitor work areas that exclude the taskbar.
    internal static SavedWindowPlacement? Resolve(SavedWindowPlacement? remembered, Rectangle? live,
        IReadOnlyList<DisplayArea> displays, Size defaultSize)
    {
        if (displays.Count == 0) return remembered;
        var primary = displays.FirstOrDefault(display => display.Primary) ?? displays[0];

        // Compare identity before sampling a move Windows may have made itself
        // when the display disappeared, possibly onto a different secondary.
        if (remembered is { IsValid: true, Display: not null } &&
            !displays.Any(display => display.Id == remembered.Display))
            return Center(remembered.Bounds.Size, primary);

        Rectangle? candidate = live ?? (remembered is { IsValid: true } ? remembered.Bounds : null);
        if (candidate is { Width: > 0, Height: > 0 } bounds)
        {
            var best = FindDisplay(bounds, displays, remembered?.Display);
            if (best is not null) return SavedWindowPlacement.From(bounds, best.Id);
            return Center(bounds.Size, primary);
        }
        return Center(defaultSize, primary);
    }

    internal static DisplayArea? FindDisplay(Rectangle bounds, IReadOnlyList<DisplayArea> displays, string? prefer = null)
    {
        DisplayArea? best = null;
        long bestOverlap = 0;
        foreach (var display in displays)
        {
            var overlap = Rectangle.Intersect(bounds, display.WorkArea);
            var area = (long)overlap.Width * overlap.Height;
            if (area > bestOverlap || (area > 0 && area == bestOverlap && display.Id == prefer))
            {
                best = display;
                bestOverlap = area;
            }
        }
        return best;
    }

    private static SavedWindowPlacement Center(Size requested, DisplayArea primary)
    {
        var work = primary.WorkArea;
        int width = Math.Clamp(requested.Width, 1, work.Width);
        int height = Math.Clamp(requested.Height, 1, work.Height);
        return new(work.Left + (work.Width - width) / 2, work.Top + (work.Height - height) / 2, width, height, primary.Id);
    }
}
