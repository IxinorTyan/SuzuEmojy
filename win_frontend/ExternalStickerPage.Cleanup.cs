using System;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;

namespace SuzuEmojy.Windows;

public partial class ExternalStickerPage
{
    private readonly Button? cleanupButton;
    private ExternalSticker[]? cleanup;

    private void BeginCleanup()
    {
        if (telegram || busy || left || cleanup is not null) return;
        var items = stickers.Where(item => selected.Contains(item.Index)).ToArray();
        if (items.Length == 0) return;

        cleanup = items;
        CleanupWarning.Text = $"将 {items.Length} 张所选图片从 QQ 缓存移到 Windows 回收站。"
            + "这些图片可能不再出现在 QQ 中；已导入 SuzuEmojy 的副本不受影响。";
        CleanupConfirmation.Visibility = Visibility.Visible;
        Options.IsEnabled = Actions.IsEnabled = Scroller.IsEnabled = false;
        if (IsVisible) CancelCleanup.Focus();
    }

    private void CloseCleanup(bool restoreFocus = true)
    {
        if (cleanup is null) return;
        cleanup = null;
        CleanupConfirmation.Visibility = Visibility.Collapsed;
        Options.IsEnabled = Actions.IsEnabled = Scroller.IsEnabled = true;
        if (restoreFocus && IsVisible) cleanupButton?.Focus();
    }

    internal bool TryGoBack()
    {
        if (cleanup is null) return false;
        CloseCleanup();
        return true;
    }

    private async Task RecycleConfirmed()
    {
        if (left || busy || cleanup is not { } items) return;
        CloseCleanup(restoreFocus: false);
        await Execute(Request("recycle", items), "正在清理 QQ 缓存…", LeavePolicy.FinishBeforeLeaving, result =>
        {
            var removed = (result.RemovedIds ?? []).ToHashSet(StringComparer.OrdinalIgnoreCase);
            if (removed.Count > 0)
            {
                selected.ExceptWith(stickers.Where(item => removed.Contains(item.Id)).Select(item => item.Index));
                stickers = stickers.Where(item => !removed.Contains(item.Id)).ToArray();
                ResetMedia();
                masonry.SetItems(stickers, resetScroll: false);
                if (stickers.Length == 0) Results.Visibility = Visibility.Collapsed;
                UpdateSelection();
            }
            var summary = $"已移到回收站 {result.Count} 张，未移除 {result.Failed} 张";
            Status.Text = string.Join("\n", new[] { summary }.Concat(result.Warnings ?? []));
        });
    }
}
