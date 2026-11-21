using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace SuzuEmojy.Windows.Controls;

// Non-virtualized counterpart of the gallery grid, for the small set of categories.
internal sealed class ResponsiveTilePanel(double preferredSize, ScrollViewer scroll) : Panel
{
    private readonly record struct Anchor(int Index, double Fraction, double ViewY);
    private readonly record struct Zoom(Anchor? Anchor, double Top, double Padding);
    private readonly record struct Gesture(Point Point, long Time, Anchor Anchor, double Offset);
    private Zoom? pendingZoom;
    private Gesture? gesture;
    private (double Tile, TileColumns Columns)? arrangedGrid;

    internal void SetPreferredSize(double size, Point? pointer)
    {
        if (preferredSize == size) return;
        if (arrangedGrid is not null && scroll.ViewportHeight > 0)
        {
            var top = TranslatePoint(new Point(), scroll).Y + scroll.VerticalOffset;
            var padding = Math.Max(0, scroll.ExtentHeight - RenderSize.Height);
            var now = RuntimePrimitives.MonotonicMilliseconds;
            var anchor = pointer is not { } point ? null :
                gesture is { } previous && now - previous.Time < 300 &&
                (point - previous.Point).LengthSquared <= 64 &&
                Math.Abs(previous.Offset - scroll.VerticalOffset) < 1 ? previous.Anchor : FindAnchor(point);
            pendingZoom = new(anchor, top, padding);
            gesture = pointer is { } at && anchor is { } current ? new(at, now, current, scroll.VerticalOffset) : null;
        }
        preferredSize = size;
        InvalidateMeasure();
    }

    protected override Size MeasureOverride(Size availableSize)
    {
        var tile = TileSize(availableSize.Width);
        var columns = TileColumns.Fit(WidthOrDefault(availableSize.Width), tile, 0, distribute: true);
        foreach (UIElement child in InternalChildren)
            child.Measure(new Size(tile, tile));
        int rows = (InternalChildren.Count + columns.Count - 1) / columns.Count;
        return new Size(tile, rows * tile);
    }

    protected override Size ArrangeOverride(Size finalSize)
    {
        var tile = TileSize(finalSize.Width);
        var columns = TileColumns.Fit(finalSize.Width, tile, 0, distribute: true);
        var grid = (tile, columns);
        var zoom = pendingZoom;
        pendingZoom = null;
        bool animate = zoom is not null && FluentMotion.Enabled(this);
        var before = animate ? new Rect[InternalChildren.Count] : null;
        if (before is not null)
            for (int i = 0; i < before.Length; i++)
                before[i] = TileMotion.VisualBounds(InternalChildren[i], Bounds(InternalChildren[i]));

        for (int i = 0; i < InternalChildren.Count; i++)
            InternalChildren[i].Arrange(new Rect(i % columns.Count * columns.Stride, i / columns.Count * tile, tile, tile));

        if (zoom is { } request)
        {
            var offset = scroll.VerticalOffset;
            var target = offset;
            if (request.Anchor is { } anchor && anchor.Index < InternalChildren.Count)
            {
                var bounds = Bounds(InternalChildren[anchor.Index]);
                if (!bounds.IsEmpty)
                    target = request.Top + bounds.Y + anchor.Fraction * bounds.Height - anchor.ViewY;
            }
            // The ScrollViewer receives its new extent from this arrange pass.
            target = RuntimePrimitives.Clamp(target, 0, Math.Max(0, request.Padding + finalSize.Height - scroll.ViewportHeight));
            if (Math.Abs(target - offset) > .01) scroll.ScrollToVerticalOffset(target);
            if (gesture is { } current) gesture = current with { Offset = target };

            var previousViewport = new Rect(0, offset - request.Top, finalSize.Width, scroll.ViewportHeight);
            var targetViewport = new Rect(0, target - request.Top, finalSize.Width, scroll.ViewportHeight);
            for (int i = 0; i < InternalChildren.Count; i++)
            {
                var child = InternalChildren[i];
                var bounds = Bounds(child);
                if (before is not null && (before[i].IntersectsWith(previousViewport) || bounds.IntersectsWith(targetViewport)))
                    TileMotion.Reflow(child, before[i], bounds, target - offset);
                else if (child.RenderTransform != Transform.Identity) child.RenderTransform = Transform.Identity;
            }
        }
        else if (arrangedGrid != grid)
        {
            // A splitter/window resize is a direct reflow, not part of the wheel gesture.
            gesture = null;
            foreach (UIElement child in InternalChildren)
                if (child.RenderTransform != Transform.Identity) child.RenderTransform = Transform.Identity;
        }
        arrangedGrid = grid;
        return finalSize;
    }

    private Anchor? FindAnchor(Point pointer)
    {
        var local = scroll.TranslatePoint(pointer, this);
        int nearest = -1;
        double distance = double.PositiveInfinity;
        Rect bounds = Rect.Empty;
        for (int i = 0; i < InternalChildren.Count; i++)
        {
            var child = InternalChildren[i];
            var visual = TileMotion.VisualBounds(child, Bounds(child));
            if (visual.IsEmpty) continue;
            var x = local.X - RuntimePrimitives.Clamp(local.X, visual.Left, visual.Right);
            var y = local.Y - RuntimePrimitives.Clamp(local.Y, visual.Top, visual.Bottom);
            var squared = x * x + y * y;
            // Later tiles in the visual tree are drawn on top if animated tiles overlap.
            if (squared > distance) continue;
            (nearest, distance, bounds) = (i, squared, visual);
        }
        return nearest < 0 ? null : new(nearest, RuntimePrimitives.Clamp((local.Y - bounds.Top) / bounds.Height, 0, 1), pointer.Y);
    }

    private static Rect Bounds(UIElement child)
    {
        if (child.RenderSize.Width <= 0 || child.RenderSize.Height <= 0) return Rect.Empty;
        var position = VisualTreeHelper.GetOffset(child);
        return new Rect(new Point(position.X, position.Y), child.RenderSize);
    }

    private double WidthOrDefault(double width) => RuntimePrimitives.IsFinite(width) ? width : preferredSize;
    private double TileSize(double width) => RuntimePrimitives.Clamp(WidthOrDefault(width), 1, preferredSize);
}
