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
using SuzuEmojy.Windows.Controls;

namespace SuzuEmojy.Windows;

public partial class ExternalStickerPage
{
    private const int ThumbnailBatch = 6, ThumbnailCache = 96;
    private readonly VirtualMasonry<ExternalSticker> masonry;
    private readonly GifPlayback playback = new();
    private readonly Dictionary<string, BitmapSource?> cached = new();
    private readonly Queue<string> decoded = new();
    private IReadOnlyList<ExternalSticker> viewport = [];
    private ExternalSticker? inspected, queuedDetail;
    private int sourceVersion, detailVersion;
    private int? selectionAnchor;
    private bool loadingThumbs, loadingDetail, compact, mediaSuspended;
    private bool inspectorInitialized;

    private StickerResultTile MakeTile(ExternalSticker item)
    {
        var tile = new StickerResultTile(item, selected.Contains(item.Index));
        if (cached.TryGetValue(item.Id, out var still)) tile.Picture.Source = still;
        tile.Opened += modifiers => Activate(item, modifiers);
        tile.Toggled += chosen =>
        {
            if (chosen) selected.Add(item.Index);
            else selected.Remove(item.Index);
            selectionAnchor = item.Index;
            UpdateSelection();
            Inspect(item);
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
        Inspect(item);
    }

    private void RequestVisible(IReadOnlyList<ExternalSticker> items)
    {
        viewport = items;
        _ = LoadVisible();
    }

    internal void SuspendMedia(bool suspend)
    {
        mediaSuspended = suspend;
        if (suspend) playback.Stop();
        else
        {
            _ = LoadVisible();
            if (inspected is { Animated: true } item)
            {
                inspected = null;
                Inspect(item);
            }
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
                    var still = Decode(results.FirstOrDefault(row => row.Id == item.Id)?.Data);
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

    private void Inspect(ExternalSticker item)
    {
        if (inspected?.Index == item.Index) return;
        inspected = queuedDetail = item;
        detailVersion++;
        playback.Stop();
        DetailImage.Source = cached.GetValueOrDefault(item.Id);
        PreviewInfo.Text = Describe(item, "正在加载预览…");
        UpdateInspectorLayout(ActualWidth);
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
                var media = (await ReadMedia(Request("detail", [item]))).FirstOrDefault(result => result.Id == item.Id);
                if (left || version != detailVersion) continue;
                var still = Decode(media?.Data);
                if (still is not null) DetailImage.Source = still;
                if (!mediaSuspended && media?.Animation is { } animation)
                {
                    try { playback.Play(DetailImage, Convert.FromBase64String(animation), 380); }
                    catch (FormatException) { }
                }
                PreviewInfo.Text = Describe(item, DetailImage.Source is null ? "预览加载失败" : null);
            }
        }
        finally { loadingDetail = false; }
    }

    private async Task<ExternalPreview[]> ReadMedia(LibraryCommand command)
    {
        // Supersede stale results without killing a converter mid-cleanup.
        try { return (await run(command, false, CancellationToken.None, null)).Result.Previews ?? []; }
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
        Inspector.Visibility = narrow && inspected is null ? Visibility.Collapsed : Visibility.Visible;
    }

    private void ResetMedia()
    {
        sourceVersion++;
        detailVersion++;
        selectionAnchor = null;
        inspected = queuedDetail = null;
        viewport = [];
        cached.Clear();
        decoded.Clear();
        playback.Stop();
        DetailImage.Source = null;
        PreviewInfo.Text = telegram ? "未选中贴纸" : "未选中表情";
        UpdateInspectorLayout(ActualWidth);
    }
}
