using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace SuzuEmojy.Windows.Controls;

// One extent for scrolling, with only the intersecting cards realized. Layout is
// rebuilt only when data/width changes; scrolling searches each column by Y.
internal sealed class VirtualMasonry<T>(ScrollViewer scroll, Canvas surface,
    Func<T, double> aspect, Func<T, FrameworkElement> make)
{
    private const double Gap = 10, TargetWidth = 134, Overscan = 100;
    private record struct Placement(int Index, Rect Bounds);
    private readonly Dictionary<int, FrameworkElement> live = new();
    private IReadOnlyList<T> items = [];
    private List<Placement>[] columns = [];
    private DispatcherOperation? render;
    private double lastWidth = -1;
    private bool disposed;

    internal IReadOnlyDictionary<int, FrameworkElement> Realized => live;
    internal event Action<IReadOnlyList<T>>? ViewportChanged;

    internal void Attach()
    {
        scroll.SizeChanged += Resized;
        scroll.ScrollChanged += Scrolled;
    }

    internal void SetItems(IReadOnlyList<T> value, bool resetScroll = true)
    {
        items = value;
        surface.Children.Clear();
        live.Clear();
        lastWidth = -1;
        if (items.Count == 0) surface.Height = 0;
        if (resetScroll) scroll.ScrollToTop();
        Schedule();
    }

    private void Resized(object sender, SizeChangedEventArgs e) => Schedule();
    private void Scrolled(object sender, ScrollChangedEventArgs e)
    {
        if (e.VerticalChange != 0 || e.ViewportWidthChange != 0 || e.ViewportHeightChange != 0) Schedule();
    }

    private void Schedule()
    {
        if (!disposed && render?.Status != DispatcherOperationStatus.Pending)
            render = surface.Dispatcher.BeginInvoke(Render, DispatcherPriority.Render);
    }

    private void ArrangeColumns(double width)
    {
        int count = Math.Max(1, (int)((width + Gap) / (TargetWidth + Gap)));
        double cellWidth = Math.Max(1, (width - (count - 1) * Gap) / count);
        columns = Enumerable.Range(0, count).Select(_ => new List<Placement>()).ToArray();
        var heights = new double[count];
        for (int index = 0; index < items.Count; index++)
        {
            int shortest = 0;
            for (int i = 1; i < count; i++)
                if (heights[i] < heights[shortest]) shortest = i;
            double height = Math.Clamp((cellWidth - 14) * aspect(items[index]), 62, 250) + 38;
            var bounds = new Rect(shortest * (cellWidth + Gap), heights[shortest], cellWidth, height);
            columns[shortest].Add(new(index, bounds));
            heights[shortest] = bounds.Bottom + Gap;
        }
        surface.Height = heights.Max();
    }

    private void Render()
    {
        double width = scroll.ViewportWidth, height = scroll.ViewportHeight;
        if (disposed || items.Count == 0 || width <= 0 || height <= 0) return;
        if (width != lastWidth)
        {
            ArrangeColumns(width);
            lastWidth = width;
        }
        double top = Math.Min(scroll.VerticalOffset, Math.Max(0, surface.Height - height));
        var visible = new List<Placement>();
        foreach (var column in columns)
        {
            int low = 0, high = column.Count;
            while (low < high)
            {
                int center = (low + high) / 2;
                if (column[center].Bounds.Bottom <= top - Overscan) low = center + 1;
                else high = center;
            }
            for (int i = low; i < column.Count && column[i].Bounds.Top < top + height + Overscan; i++)
                visible.Add(column[i]);
        }
        var indices = visible.Select(card => card.Index).ToHashSet();
        foreach (int index in live.Keys.Where(index => !indices.Contains(index)).ToArray())
        {
            surface.Children.Remove(live[index]);
            live.Remove(index);
        }
        foreach (var (index, bounds) in visible.OrderBy(card => card.Index))
        {
            if (!live.TryGetValue(index, out var view))
            {
                view = make(items[index]);
                live[index] = view;
                surface.Children.Add(view);
            }
            view.Width = bounds.Width;
            view.Height = bounds.Height;
            Canvas.SetLeft(view, bounds.X);
            Canvas.SetTop(view, bounds.Y);
        }
        ViewportChanged?.Invoke(visible
            .OrderBy(card => card.Bounds.Bottom <= top || card.Bounds.Top >= top + height)
            .ThenBy(card => card.Index).Select(card => items[card.Index]).ToArray());
    }

    internal void Dispose()
    {
        disposed = true;
        render?.Abort();
        scroll.SizeChanged -= Resized;
        scroll.ScrollChanged -= Scrolled;
        surface.Children.Clear();
        live.Clear();
        items = [];
        columns = [];
    }
}
