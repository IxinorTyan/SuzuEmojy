using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace SuzuEmojy.Windows.Controls;

internal static class CategoryIcon
{
    internal static readonly Category All = new("全部表情", null, null);
    internal static readonly Category CreateCategory = new("新建分类", null, null);
    internal static readonly Category Unclassified = new("未分类", null, null);
    private static readonly FontFamily EmojiFont = new("Segoe UI Emoji");

    internal static FrameworkElement Create(Category category, int size, Control source, int? decodeSize = null)
    {
        if (category.IconPath is { } path && MediaThumbnail.Load(path, decodeSize ?? size) is { } bitmap)
            return new Image { Source = bitmap, Width = size, Height = size, IsHitTestVisible = false };
        if (!string.IsNullOrEmpty(category.Icon))
            return new TextBlock { Text = category.Icon, FontFamily = EmojiFont, FontSize = size - 2,
                IsHitTestVisible = false };
        return FluentGlyph.Create(category == All ? "home" : category == CreateCategory ? "add" : "folder", size, source);
    }

    internal static FrameworkElement Small(Category category, Control source) =>
        new Viewbox { Child = Create(category, 16, source), Width = 16, Height = 16,
            StretchDirection = StretchDirection.DownOnly, IsHitTestVisible = false };

    internal static DockPanel Label(Category category, Control source, bool wrap, string? text = null)
    {
        var icon = Small(category, source);
        icon.VerticalAlignment = VerticalAlignment.Center;
        icon.Margin = new Thickness(0, 0, 8, 0);
        DockPanel.SetDock(icon, Dock.Left);
        var label = new TextBlock { Text = text ?? category.Name, VerticalAlignment = VerticalAlignment.Center,
            TextWrapping = wrap ? TextWrapping.Wrap : TextWrapping.NoWrap,
            TextTrimming = wrap ? TextTrimming.None : TextTrimming.CharacterEllipsis };
        var row = new DockPanel();
        row.Children.Add(icon);
        row.Children.Add(label);
        return row;
    }
}
