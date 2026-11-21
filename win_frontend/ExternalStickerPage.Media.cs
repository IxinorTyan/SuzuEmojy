using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using SuzuEmojy.Windows.Controls;

namespace SuzuEmojy.Windows;

public partial class ExternalStickerPage
{
    private const int ThumbnailBatch = 6, ThumbnailCache = 96;
    private readonly VirtualMasonry<ExternalSticker> masonry;
    private readonly GifPlayback playback = new();
    private readonly DispatcherTimer hoverIntent = new() { Interval = TimeSpan.FromMilliseconds(300) };
    private MotionReveal detailReveal = null!;
    private readonly Dictionary<string, BitmapSource?> cached = new();
    private readonly Queue<string> decoded = new();
    private IReadOnlyList<ExternalSticker> viewport = [];
    private ExternalSticker? inspected, queuedDetail, pinned, hovering;
    private FrameworkElement? hoverTile;
    private CancellationTokenSource? readingDetail;
    private int sourceVersion, detailVersion;
    private int? selectionAnchor;
    private bool loadingThumbs, loadingDetail, compact, mediaSuspended;
    private bool inspectorInitialized;

    private void SetupMediaMotion()
    {
        detailReveal = new(this, InspectorBody);
        InspectorEmpty.Text = telegram ? "未选中贴纸" : "未选中表情";
        FluentMotion.Observe(this, detailReveal.Snap);
        hoverIntent.Tick += (_, _) =>
        {
            hoverIntent.Stop();
            if (hoverTile is { IsMouseOver: true, IsVisible: true } && !busy && !left && !mediaSuspended) Inspect(hovering);
        };
    }

    private StickerResultTile MakeTile(ExternalSticker item)
    {
        var tile = new StickerResultTile(item, selected.Contains(item.Index));
        if (cached.TryGetValue(item.Id, out var still)) tile.Picture.Source = still;
        tile.Opened += modifiers => Activate(item, modifiers);
        tile.MouseEnter += (_, _) =>
        {
            if (!tile.IsLoaded || busy || left || mediaSuspended) return;
            hovering = item;
            hoverTile = tile;
            hoverIntent.Stop();
            hoverIntent.Start();
        };
        tile.MouseLeave += (_, _) => EndHover(tile);
        tile.Unloaded += (_, _) => EndHover(tile);
        tile.Toggled += chosen =>
        {
            if (chosen) selected.Add(item.Index);
            else selected.Remove(item.Index);
            selectionAnchor = item.Index;
            UpdateSelection();
            Pin(item);
        };
        return tile;
    }

    private void Activate(ExternalSticker item, ModifierKeys modifiers)
    {
        if ((modifiers & ModifierKeys.Shift) != 0 && selectionAnchor is { } anchor)
        {
            if ((modifiers & ModifierKeys.Control) == 0) selected.Clear();
            int lower = Math.Min(anchor, item.Index), upper = Math.Max(anchor, item.Index);
            selected.UnionWith(stickers.Where(sticker => sticker.Index >= lower && sticker.Index <= upper).Select(sticker => sticker.Index));
        }
        else
        {
            if ((modifiers & ModifierKeys.Control) == 0) selected.Clear();
            if (!selected.Add(item.Index)) selected.Remove(item.Index);
            selectionAnchor = item.Index;
        }
        UpdateSelection();
        Pin(item);
    }

    private void Pin(ExternalSticker item)
    {
        hoverIntent.Stop();
        pinned = selected.Contains(item.Index) ? item : null;
        Inspect(pinned ?? hovering);
    }

    private void EndHover(FrameworkElement tile)
    {
        if (!ReferenceEquals(hoverTile, tile)) return;
        hoverIntent.Stop();
        hoverTile = null;
        hovering = null;
        if (!left && !mediaSuspended) Inspect(pinned);
    }

    private void RequestVisible(IReadOnlyList<ExternalSticker> items)
    {
        viewport = items;
        _ = LoadVisible();
    }

    internal void SuspendMedia(bool suspend)
    {
        mediaSuspended = suspend;
        if (suspend)
        {
            readingDetail?.Cancel();
            hoverIntent.Stop();
            hoverTile = null;
            hovering = null;
            Inspect(pinned);
            playback.Stop();
        }
        else
        {
            _ = LoadVisible();
            if (inspected is { Animated: true } item)
            {
                inspected = null;
                Inspect(item);
            }
            else _ = LoadDetail();
        }
    }

    private async Task LoadVisible()
    {
        if (left || busy || mediaSuspended || loadingThumbs || viewport.Count == 0) return;
        loadingThumbs = true;
        try
        {
            while (!left && !busy && !mediaSuspended)
            {
                // Always take the latest viewport, not a queue of scroll positions.
                var batch = viewport.Where(item => !cached.ContainsKey(item.Id)).DistinctBy(item => item.Id)
                    .Take(ThumbnailBatch).ToArray();
                if (batch.Length == 0) break;
                int version = sourceVersion;
                var results = await ReadMedia(Request("previews", batch));
                if (left || version != sourceVersion) continue;
                foreach (var item in batch)
                {
                    var media = results.FirstOrDefault(row => row.Id == item.Id);
                    var still = media?.Still ?? Decode(media?.Data);
                    cached[item.Id] = still;
                    decoded.Enqueue(item.Id);
                    foreach (var tile in masonry.Realized.Values.OfType<StickerResultTile>().Where(tile => tile.Item.Id == item.Id))
                        tile.Picture.Source = still;
                    if (inspected?.Id == item.Id && DetailImage.Source is null) DetailImage.Source = still;
                }
                var current = viewport.Select(item => item.Id).ToHashSet();
                for (int remaining = decoded.Count; cached.Count > ThumbnailCache && remaining > 0; remaining--)
                {
                    string oldest = decoded.Dequeue();
                    if (current.Contains(oldest)) decoded.Enqueue(oldest);
                    else cached.Remove(oldest);
                }
            }
        }
        finally { loadingThumbs = false; }
    }

    private void Inspect(ExternalSticker? item)
    {
        if (inspected?.Index == item?.Index) return;
        inspected = queuedDetail = item;
        detailVersion++;
        readingDetail?.Cancel();
        playback.Stop(restoreStill: false);
        if (item is null)
        {
            detailReveal.Hide(ClearInspection);
            return;
        }
        DetailImage.Source = cached.GetValueOrDefault(item.Id);
        PreviewInfo.Text = Describe(item, "正在加载预览…");
        UpdateInspectorLayout(ActualWidth);
        detailReveal.Show(() =>
        {
            InspectorEmpty.Visibility = Visibility.Hidden;
            InspectorBody.Visibility = Visibility.Visible;
        });
        _ = LoadDetail();
    }

    private async Task LoadDetail()
    {
        if (loadingDetail || left || mediaSuspended) return;
        loadingDetail = true;
        try
        {
            while (!left && !mediaSuspended && queuedDetail is { } item)
            {
                queuedDetail = null;
                int version = detailVersion;
                using var request = new CancellationTokenSource();
                readingDetail = request;
                var media = (await ReadMedia(Request("detail", [item]), request.Token)).FirstOrDefault(result => result.Id == item.Id);
                readingDetail = null;
                if (left || version != detailVersion) continue;
                if (mediaSuspended) { queuedDetail = item; break; }
                var still = media?.Still ?? Decode(media?.Data);
                if (still is not null) DetailImage.Source = still;
                if (!mediaSuspended && media?.Native is { } frames)
                    playback.Play(DetailImage, frames);
                else if (!mediaSuspended && media?.Animation is { } animation)
                {
                    try { playback.Play(DetailImage, Convert.FromBase64String(animation), 380); }
                    catch (FormatException) { }
                }
                PreviewInfo.Text = Describe(item, DetailImage.Source is null ? "预览加载失败" : null);
            }
        }
        finally { loadingDetail = false; }
    }

    private async Task<ExternalPreview[]> ReadMedia(LibraryCommand command, CancellationToken token = default)
    {
        try { return (await run(command, false, token, null)).Result.Previews ?? []; }
        catch (Exception) { return []; }
    }

    private static BitmapSource? Decode(string? image)
    {
        if (image is null) return null;
        try
        {
            using var stream = new MemoryStream(Convert.FromBase64String(image), writable: false);
            var bitmap = new BitmapImage();
            bitmap.BeginInit();
            bitmap.CacheOption = BitmapCacheOption.OnLoad;
            bitmap.StreamSource = stream;
            bitmap.EndInit();
            bitmap.Freeze();
            return bitmap;
        }
        catch (Exception ex) when (ex is FormatException or IOException or NotSupportedException
            or ArgumentException or InvalidOperationException) { return null; }
    }

    private static string Describe(ExternalSticker item, string? state) => string.Join("\n",
        new[] { item.Label, item.Width > 0 && item.Height > 0 ? $"{item.Width} × {item.Height}" : null, state }
            .Where(value => !string.IsNullOrWhiteSpace(value)));

    private void UpdateInspectorLayout(double width)
    {
        bool narrow = width < 680;
        if (!inspectorInitialized || compact != narrow)
        {
            inspectorInitialized = true;
            compact = narrow;
            DockPanel.SetDock(Inspector, narrow ? Dock.Top : Dock.Right);
            DockPanel.SetDock(InspectorText, narrow ? Dock.Right : Dock.Top);
            InspectorText.Width = narrow ? 128 : double.NaN;
            Inspector.Width = narrow ? double.NaN : 246;
            Inspector.Height = narrow ? 112 : double.NaN;
            Inspector.Margin = narrow ? new Thickness(0, 0, 0, 10) : new Thickness(12, 0, 0, 0);
        }
        // Keep the inspector slot stable: its appearance must not push the
        // hovered tile out from under the pointer and immediately close it.
    }

    private void ResetMedia()
    {
        sourceVersion++;
        detailVersion++;
        readingDetail?.Cancel();
        selectionAnchor = null;
        inspected = queuedDetail = pinned = hovering = null;
        hoverTile = null;
        hoverIntent.Stop();
        viewport = [];
        cached.Clear();
        decoded.Clear();
        playback.Stop();
        detailReveal.Hide(ClearInspection, immediate: true);
        UpdateInspectorLayout(ActualWidth);
    }

    private void ClearInspection()
    {
        DetailImage.Source = null;
        PreviewInfo.Text = "";
        InspectorBody.Visibility = Visibility.Hidden;
        InspectorEmpty.Visibility = Visibility.Visible;
    }
}
