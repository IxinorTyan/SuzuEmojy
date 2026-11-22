using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using SuzuEmojy.Windows.Controls;

namespace SuzuEmojy.Windows;

internal readonly record struct GalleryLayout(double Gap, double Side, double Top, double Bottom, int ZoomStep, int MaxSize,
    int MinZoom = 40, bool CenterColumns = false, bool DistributeColumns = false)
{
    internal static GalleryLayout Main => new(10, 16, 8, 16, 10, 300, MinZoom: 60, DistributeColumns: true);
    internal static GalleryLayout Quick => new(8, 8, 0, 0, 6, 200, CenterColumns: true);
}

internal sealed class VirtualGallery : IDisposable
{
    private readonly GalleryLayout layout;
    private readonly ScrollViewer scroll;
    private readonly Canvas surface;
    private readonly Action<Sticker> activate;
    private readonly Action<Sticker, Button> hover;
    private readonly Action unhover;
    private readonly bool offscreen;
    private readonly Canvas? motionLayer;
    private readonly Dictionary<string, Card> live = new(StringComparer.OrdinalIgnoreCase);
    private readonly GalleryThumbnailCache thumbnails = new();
    private readonly SemaphoreSlim decoders = new(2);
    private readonly DispatcherTimer exitHold = new(DispatcherPriority.Render) { Interval = TimeSpan.FromMilliseconds(100) };
    private DispatcherOperation? render;
    private HashSet<Sticker> selection = new();
    private GridGeometry? placedGrid;
    private ViewAnchor? zoomAnchor;
    private (Point Point, long Time, ViewAnchor Anchor)? wheelGesture;
    private bool resetScroll, switchingCategory, waitingToFade;
    private bool disposed;
    private int size;
    internal List<Sticker> Items { get; } = new();
    internal event Action? SizeChanged;
    internal event Action<int>? ZoomChanged;
    internal int CardSize => size;
    internal int LoadedThumbnails => live.Values.Count(card => card.Thumbnail.Source is not null);
    internal int ActiveCards => live.Count;

    private sealed class Card(Sticker item, Button button, Border surface, TextBlock mark, Image thumbnail)
    {
        internal Sticker Item = item;
        internal Button Button { get; } = button;
        internal Border Surface { get; } = surface;
        internal TextBlock Mark { get; } = mark;
        internal Image Thumbnail { get; } = thumbnail;
        internal Rect Bounds = Rect.Empty;
        internal int RequestedPixels;
        internal CancellationTokenSource? Request;
        internal bool FadeOnLoad;
    }

    private readonly record struct GridGeometry(int Columns, double ColumnStride, double RowStride, double Left, double Offset, int Size);
    private readonly record struct ViewAnchor(int Index, double Fraction, double ViewY);

    internal VirtualGallery(ScrollViewer scroll, Canvas surface, int size, GalleryLayout layout,
        Action<Sticker> activate, Action<Sticker, Button> hover, Action unhover, bool offscreen = false, Canvas? motionLayer = null)
    {
        this.layout = layout;
        this.scroll = scroll;
        this.surface = surface;
        this.size = size;
        this.activate = activate;
        this.hover = hover;
        this.unhover = unhover;
        this.offscreen = offscreen;
        this.motionLayer = motionLayer;
        exitHold.Tick += (_, _) => StartExit();
        scroll.SizeChanged += (_, _) => { SizeChanged?.Invoke(); Schedule(); };
        scroll.ScrollChanged += (_, e) => {
            if (e.VerticalChange != 0)
            {
                unhover();
                if (placedGrid is { } grid && Math.Abs(e.VerticalOffset - grid.Offset) > .5) wheelGesture = null;
            }
            if (e.VerticalChange != 0 || e.ViewportWidthChange != 0 || e.ViewportHeightChange != 0) Schedule();
        };
        scroll.PreviewMouseWheel += (_, e) => e.Handled = ApplyWheel(e.Delta, Keyboard.Modifiers, e.GetPosition(scroll));
        scroll.IsVisibleChanged += (_, _) => { if (scroll.IsVisible || offscreen) Schedule(); else Clear(); };
        FluentMotion.Observe(surface, () =>
        {
            if (FluentMotion.Enabled(surface)) return;
            switchingCategory = false;
            ClearExits();
            foreach (var card in live.Values)
            {
                card.Button.RenderTransform = Transform.Identity;
                card.Thumbnail.BeginAnimation(UIElement.OpacityProperty, null);
            }
        });
    }

    internal void SetItems(IEnumerable<Sticker> items, bool categoryChange = false, bool preserveScroll = false)
    {
        Items.Clear();
        Items.AddRange(items);
        resetScroll = !preserveScroll;
        switchingCategory = categoryChange && motionLayer is not null && FluentMotion.Enabled(surface);
        ResetZoom();
        Schedule();
    }

    internal void SetSize(int requested, Point? pointer = null)
    {
        if (requested == size) return;
        unhover();
        if (!resetScroll && placedGrid is { } grid && Items.Count != 0)
        {
            var now = RuntimePrimitives.MonotonicMilliseconds;
            zoomAnchor = pointer is { } at && wheelGesture is { } gesture && now - gesture.Time < 300 &&
                (at - gesture.Point).LengthSquared <= 64 ? gesture.Anchor : FindAnchor(grid, pointer);
            wheelGesture = pointer is { } point ? (point, now, zoomAnchor.Value) : null;
        }
        size = requested;
        Schedule();
    }

    internal bool ApplyWheel(int delta, ModifierKeys modifiers, Point? pointer = null) =>
        WheelZoom.TryApply(delta, modifiers, size, layout.MinZoom, layout.MaxSize, layout.ZoomStep, next =>
        {
            SetSize(next, pointer);
            ZoomChanged?.Invoke(next);
        });

    internal Func<Sticker, ContextMenu?>? ContextMenuFor { get; set; }
    internal const string InternalDragFormat = "SuzuEmojy.Windows.GalleryItem";
    internal Func<bool>? CanReorder { get; set; }
    internal event Action<Sticker, Sticker>? ReorderRequested;

    internal void ApplySelection(HashSet<Sticker> selected)
    {
        selection = selected;
        foreach (var card in live.Values)
            Paint(card, card.Item);
    }

    private void Schedule()
    {
        if (!disposed && render?.Status != DispatcherOperationStatus.Pending)
            render = surface.Dispatcher.BeginInvoke(Render, DispatcherPriority.Render);
    }

    private void Render()
    {
        var width = scroll.ViewportWidth;
        var height = scroll.ViewportHeight;
        if ((!scroll.IsVisible && !offscreen) || width <= 0 || height <= 0)
            return;
        var stride = size + layout.Gap;
        // Keep the quick panel's existing outer gutter; justify only the main gallery.
        double usable = width - layout.Side * 2 - (layout.CenterColumns ? layout.Gap : 0);
        var columns = TileColumns.Fit(usable, size, layout.Gap, layout.DistributeColumns);
        int cols = columns.Count;
        int rows = (Items.Count + cols - 1) / cols;
        var fullHeight = layout.Top + layout.Bottom + Math.Max(0, rows * stride - layout.Gap);
        surface.Height = fullHeight;
        var previous = placedGrid;
        var oldOffset = previous?.Offset ?? scroll.VerticalOffset;
        var offset = resetScroll ? 0 : zoomAnchor is { } at
            ? layout.Top + at.Index / cols * stride + at.Fraction * size - at.ViewY : scroll.VerticalOffset;
        offset = RuntimePrimitives.Clamp(offset, 0, Math.Max(0, fullHeight - height));
        if (Math.Abs(offset - scroll.VerticalOffset) > .01)
            scroll.ScrollToVerticalOffset(offset);
        // Apply the extent and scroll request before the next paint. A render
        // scheduled by the resulting ScrollChanged will see this same offset.
        bool animateLayout = FluentMotion.Enabled(surface) &&
            (switchingCategory || (!resetScroll && previous is { } old && old.Size != size));
        // Only realized rows need controls or decoded bitmaps, even for large libraries.
        int first = Math.Min(Items.Count, Math.Max(0, (int)((offset - layout.Top) / stride) - 1) * cols);
        int end = Math.Min(Items.Count, ((int)Math.Ceiling((offset + height - layout.Top) / stride) + 1) * cols);
        var left = layout.CenterColumns ? Math.Max(layout.Side, (width - (cols * stride - layout.Gap)) / 2) : layout.Side;
        if (resetScroll) ClearExits();
        var visible = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        for (int index = first; index < end; index++) visible.Add(Items[index].Path);
        HashSet<string>? kept = switchingCategory ? new(Items.Select(item => item.Path), StringComparer.OrdinalIgnoreCase) : null;
        foreach (var path in live.Keys.Where(path => !visible.Contains(path)).ToArray())
        {
            if (kept is not null && !kept.Contains(path)) AddExit(live[path], oldOffset, width, height);
            Remove(path);
        }
        var pixels = (int)Math.Ceiling((size - 16) * VisualTreeHelper.GetDpi(surface).PixelsPerDip / 48) * 48;
        for (int index = first; index < end; index++)
        {
            var item = Items[index];
            if (!live.TryGetValue(item.Path, out var card))
            {
                card = MakeCard(item);
                card.FadeOnLoad = switchingCategory;
                live.Add(item.Path, card);
                surface.Children.Add(card.Button);
            }
            else if (!ReferenceEquals(card.Item, item))
            {
                card.Item = item;
                System.Windows.Automation.AutomationProperties.SetName(card.Button, item.Name);
                Paint(card, item);
            }
            var bounds = new Rect(left + index % cols * columns.Stride, layout.Top + index / cols * stride, size, size);
            Place(card, bounds, offset - oldOffset, animateLayout);
            EnsureThumbnail(card, pixels);
        }
        placedGrid = new(cols, columns.Stride, stride, left, offset, size);
        if (switchingCategory && motionLayer!.Children.Count != 0)
        {
            waitingToFade = true;
            if (Items.Count == 0 || LoadedThumbnails > 0) StartExit();
            else exitHold.Start(); // Briefly hold the old scene if the new category is cold.
        }
        resetScroll = switchingCategory = false;
        zoomAnchor = null;
    }

    private ViewAnchor FindAnchor(GridGeometry grid, Point? pointer)
    {
        var viewY = pointer?.Y ?? 0;
        var localY = grid.Offset + viewY - layout.Top;
        int row = Math.Max(0, (int)Math.Floor(localY / grid.RowStride));
        int col = RuntimePrimitives.Clamp((int)Math.Floor(((pointer?.X ?? grid.Left) - grid.Left) / grid.ColumnStride), 0, grid.Columns - 1);
        int index = Math.Min(Items.Count - 1, row * grid.Columns + col);
        // If the pointer is in a gap, keep the gap anchored as well.
        var fraction = (localY - (index / grid.Columns) * grid.RowStride) / grid.Size;
        return new(index, fraction, viewY);
    }

    private static void Place(Card card, Rect bounds, double offsetDelta, bool animate)
    {
        if (card.Bounds == bounds && (!animate || Math.Abs(offsetDelta) < .01)) return;
        var previous = animate ? TileMotion.VisualBounds(card.Button, card.Bounds) : Rect.Empty;
        card.Button.Width = bounds.Width;
        card.Button.Height = bounds.Height;
        Canvas.SetLeft(card.Button, bounds.X);
        Canvas.SetTop(card.Button, bounds.Y);
        card.Bounds = bounds;
        TileMotion.Reflow(card.Button, previous, bounds, offsetDelta);
    }

    private void AddExit(Card card, double offset, double width, double height)
    {
        if (card.Thumbnail.Source is not { } source) return;
        var bounds = TileMotion.VisualBounds(card.Button, card.Bounds);
        if (bounds.IsEmpty) return;
        bounds.Offset(0, -offset);
        if (!bounds.IntersectsWith(new Rect(0, 0, width, height))) return;
        var ghost = new Border
        {
            Width = bounds.Width, Height = bounds.Height, Padding = new Thickness(8),
            Child = new Image { Source = source, Stretch = Stretch.Uniform },
            RenderTransform = new ScaleTransform(), RenderTransformOrigin = new(.5, .5)
        };
        Canvas.SetLeft(ghost, bounds.X);
        Canvas.SetTop(ghost, bounds.Y);
        motionLayer!.Children.Add(ghost);
    }

    private void StartExit()
    {
        exitHold.Stop();
        if (!waitingToFade) return;
        waitingToFade = false;
        foreach (var ghost in motionLayer!.Children.OfType<Border>().ToArray())
        {
            var fade = FluentMotion.Ease(1, 0, 160);
            ghost.Opacity = 0;
            fade.Completed += (_, _) => motionLayer.Children.Remove(ghost);
            ghost.BeginAnimation(UIElement.OpacityProperty, fade);
            var scale = (ScaleTransform)ghost.RenderTransform;
            scale.ScaleX = scale.ScaleY = .90;
            TileMotion.Move(scale, ScaleTransform.ScaleXProperty, 1, .90, 160);
            TileMotion.Move(scale, ScaleTransform.ScaleYProperty, 1, .90, 160);
        }
    }

    private void ClearExits()
    {
        exitHold.Stop();
        waitingToFade = false;
        motionLayer?.Children.Clear();
    }

    private Card MakeCard(Sticker item)
    {
        var image = new Image { Stretch = Stretch.Uniform, Margin = new Thickness(7) };
        var mark = new TextBlock {
            Text = "✓", FontSize = 13, FontWeight = FontWeights.Bold, Foreground = Brushes.White,
            HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center
        };
        var badge = new Border {
            Child = mark, Width = 18, Height = 18, CornerRadius = new CornerRadius(9), Margin = new Thickness(0, 5, 5, 0),
            HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Top
        };
        badge.SetResourceReference(Border.BackgroundProperty, "Accent");
        var content = new Grid { IsHitTestVisible = false };
        content.Children.Add(image);
        content.Children.Add(badge);
        var frame = new Border { CornerRadius = new CornerRadius(8), Child = content };
        var button = new Button {
            Width = size, Height = size, Cursor = Cursors.Hand, Content = frame,
            Padding = new Thickness(0),
        };
        button.SetResourceReference(FrameworkElement.StyleProperty, "FluentGalleryCardButton");
        System.Windows.Automation.AutomationProperties.SetName(button, item.Name);
        var card = new Card(item, button, frame, mark, image);
        Point? dragOrigin = null;
        button.MouseEnter += (_, _) => hover(card.Item, button);
        button.MouseLeave += (_, _) => unhover();
        button.Click += (_, _) => { unhover(); activate(card.Item); };
        // Create lazily and retain it only on the currently realized card.
        if (ContextMenuFor is not null) button.ContextMenuOpening += (_, args) =>
        {
            if (ContextMenuFor.Invoke(card.Item) is not { } menu) return;
            args.Handled = true;
            menu.PlacementTarget = button;
            menu.IsOpen = true;
        };
        button.AllowDrop = true;
        void ReorderEffect(object sender, DragEventArgs e)
        {
            if (ReorderRequested is null || CanReorder?.Invoke() != true ||
                !e.Data.GetDataPresent(InternalDragFormat, autoConvert: false)) return;
            e.Effects = e.AllowedEffects & DragDropEffects.Copy;
            e.Handled = true;
        }
        button.DragEnter += ReorderEffect;
        button.DragOver += ReorderEffect;
        button.Drop += (_, e) =>
        {
            if (ReorderRequested is null || CanReorder?.Invoke() != true ||
                (e.AllowedEffects & DragDropEffects.Copy) == 0 ||
                e.Data.GetData(InternalDragFormat, autoConvert: false) is not string path) return;
            var source = Items.Find(sticker => sticker.Path == path);
            if (source is null || source == card.Item) return;
            e.Effects = DragDropEffects.Copy;
            e.Handled = true;
            ReorderRequested?.Invoke(source, card.Item);
        };
        button.PreviewMouseLeftButtonDown += (_, e) => { unhover(); dragOrigin = e.GetPosition(button); };
        button.PreviewMouseLeftButtonUp += (_, _) => dragOrigin = null;
        button.PreviewMouseMove += (_, e) => {
            if (dragOrigin is not Point start || e.LeftButton != MouseButtonState.Pressed)
                return;
            var pos = e.GetPosition(button);
            if (Math.Abs(start.X - pos.X) < SystemParameters.MinimumHorizontalDragDistance &&
                Math.Abs(start.Y - pos.Y) < SystemParameters.MinimumVerticalDragDistance)
                return;
            dragOrigin = null;
            unhover();
            e.Handled = true;
            button.ReleaseMouseCapture();
            var data = new DataObject(DataFormats.FileDrop, new[] { card.Item.Path });
            if (ReorderRequested is not null) data.SetData(InternalDragFormat, card.Item.Path);
            DragDrop.DoDragDrop(button, data, DragDropEffects.Copy);
        };
        Paint(card, item);
        return card;
    }

    private void Paint(Card card, Sticker item)
    {
        var picked = selection.Contains(item);
        if (picked) card.Surface.SetResourceReference(Border.BackgroundProperty, "SelectionSurface");
        else card.Surface.Background = Brushes.Transparent;
        card.Surface.BorderThickness = new Thickness(picked ? 2 : 1);
        if (picked) card.Surface.SetResourceReference(Border.BorderBrushProperty, "Accent");
        else card.Surface.BorderBrush = Brushes.Transparent;
        ((Border)card.Mark.Parent).Visibility = picked ? Visibility.Visible : Visibility.Collapsed;
    }

    private void EnsureThumbnail(Card card, int pixels)
    {
        if (card.RequestedPixels == 0 && thumbnails.TryGet(card.Item.Path, out var bitmap, out int cachedPixels))
        {
            card.RequestedPixels = cachedPixels;
            SetThumbnail(card, bitmap);
        }
        if (pixels <= card.RequestedPixels) return;
        card.RequestedPixels = pixels;
        card.Request?.Cancel();
        var request = new CancellationTokenSource();
        card.Request = request;
        _ = Decode(card, pixels, request);
    }

    private void SetThumbnail(Card card, BitmapSource source)
    {
        card.Thumbnail.Source = source;
        if (card.FadeOnLoad)
        {
            card.FadeOnLoad = false;
            if (FluentMotion.Enabled(surface)) card.Thumbnail.BeginAnimation(UIElement.OpacityProperty, FluentMotion.Ease(0, 1, 125));
        }
        if (waitingToFade) StartExit();
    }

    private async Task Decode(Card card, int pixels, CancellationTokenSource request)
    {
        var path = card.Item.Path;
        var token = request.Token;
        try
        {
            await decoders.WaitAsync(token);
            try
            {
                var source = await Task.Run(() => MediaThumbnail.Load(path, pixels), token);
                if (!token.IsCancellationRequested && source is not null)
                {
                    thumbnails.Put(path, source, pixels);
                    SetThumbnail(card, source);
                }
            }
            finally { decoders.Release(); }
        }
        catch (OperationCanceledException) { }
        finally
        {
            if (card.Request == request) card.Request = null;
            request.Dispose();
        }
    }

    private void Remove(string path)
    {
        var card = live[path];
        card.Request?.Cancel();
        surface.Children.Remove(card.Button);
        live.Remove(path);
    }

    private void Clear()
    {
        foreach (var path in live.Keys.ToArray()) Remove(path);
        ClearExits();
        placedGrid = null;
        ResetZoom();
    }

    private void ResetZoom()
    {
        zoomAnchor = null;
        wheelGesture = null;
    }

    public void Dispose()
    {
        disposed = true;
        render?.Abort();
        Clear();
        thumbnails.Clear();
        // An already-running decode may still return and release its slot.
    }
}

internal sealed class GalleryThumbnailCache
{
    private const int Entries = 112;
    private const long Budget = 8 * 1024 * 1024;
    private sealed record Entry(string Path, BitmapSource Source, int Pixels)
    {
        internal long Bytes => (long)Source.PixelWidth * Source.PixelHeight * 4;
    }

    private readonly LinkedList<Entry> recency = new();
    private readonly Dictionary<string, LinkedListNode<Entry>> byPath = new(StringComparer.OrdinalIgnoreCase);
    private long used;

    internal bool TryGet(string path, out BitmapSource source, out int pixels)
    {
        if (!byPath.TryGetValue(path, out var node))
        {
            source = null!;
            pixels = 0;
            return false;
        }
        recency.Remove(node);
        recency.AddFirst(node);
        source = node.Value.Source;
        pixels = node.Value.Pixels;
        return true;
    }

    internal void Put(string path, BitmapSource source, int pixels)
    {
        if (byPath.TryGetValue(path, out var existing))
        {
            if (existing.Value.Pixels > pixels) return;
            Remove(existing);
        }
        var entry = new Entry(path, source, pixels);
        if (entry.Bytes > Budget) return;
        byPath[path] = recency.AddFirst(entry);
        used += entry.Bytes;
        while (byPath.Count > Entries || used > Budget) Remove(recency.Last!);
    }

    private void Remove(LinkedListNode<Entry> node)
    {
        byPath.Remove(node.Value.Path);
        recency.Remove(node);
        used -= node.Value.Bytes;
    }

    internal void Clear()
    {
        byPath.Clear();
        recency.Clear();
        used = 0;
    }
}

internal static class MediaThumbnail
{
    internal static BitmapSource? Load(string path, int size)
    {
        try
        {
            using var stream = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            if (NativeWebp.Available && NativeWebp.IsWebp(stream))
                return NativeWebp.ResizeStill(NativeWebp.Still(NativeWebp.Read(stream, CancellationToken.None), CancellationToken.None), size);
            var decoder = BitmapDecoder.Create(stream, BitmapCreateOptions.DelayCreation, BitmapCacheOption.None);
            var frame = decoder.Frames[0];
            var width = frame.PixelWidth;
            var height = frame.PixelHeight;
            stream.Position = 0;
            var bitmap = new BitmapImage();
            bitmap.BeginInit();
            bitmap.CacheOption = BitmapCacheOption.OnLoad;
            bitmap.StreamSource = stream;
            if (width >= height) bitmap.DecodePixelWidth = Math.Min(size, width);
            else bitmap.DecodePixelHeight = Math.Min(size, height);
            bitmap.EndInit();
            bitmap.Freeze();
            return bitmap;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or
            ArgumentException or NotSupportedException or InvalidOperationException or FormatException or System.Runtime.InteropServices.COMException)
        {
            return null;
        }
    }
}
