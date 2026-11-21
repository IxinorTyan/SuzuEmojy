using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Shapes;

namespace SuzuEmojy.Windows.Controls;

internal sealed class SettingCard : Border
{
    private readonly FrameworkElement input;

    internal SettingCard(string title, string description, string glyph, FrameworkElement control)
    {
        input = control;
        MinHeight = 69;
        Margin = new Thickness(0, 0, 0, 3);
        Padding = new Thickness(14, 10, 14, 10);
        CornerRadius = new CornerRadius(6);
        BorderThickness = new Thickness(1);
        SetResourceReference(BackgroundProperty, "SettingCardSurface");
        SetResourceReference(BorderBrushProperty, "SettingCardEdge");
        System.Windows.Automation.AutomationProperties.SetName(control, title);

        var grid = new Grid();
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(31) });
        grid.ColumnDefinitions.Add(new ColumnDefinition());
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var icon = new Path { Data = Geometry.Parse(glyph), Width = 16, Height = 16, Stretch = Stretch.Uniform,
            StrokeThickness = 1, HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Center,
            StrokeStartLineCap = PenLineCap.Round, StrokeEndLineCap = PenLineCap.Round, StrokeLineJoin = PenLineJoin.Round };
        icon.SetResourceReference(Shape.StrokeProperty, "Ink");
        grid.Children.Add(icon);

        var text = new StackPanel { VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 12, 0) };
        var label = new TextBlock { Text = title, FontSize = 14, TextWrapping = TextWrapping.Wrap };
        label.SetResourceReference(TextBlock.ForegroundProperty, "Ink");
        text.Children.Add(label);
        var caption = new TextBlock { Text = description, FontSize = 11, TextWrapping = TextWrapping.Wrap,
            Margin = new Thickness(0, 1, 0, 0) };
        caption.SetResourceReference(TextBlock.ForegroundProperty, "Hint");
        text.Children.Add(caption);
        Grid.SetColumn(text, 1);
        grid.Children.Add(text);
        Grid.SetColumn(control, 2);
        control.VerticalAlignment = VerticalAlignment.Center;
        grid.Children.Add(control);
        Child = grid;
    }

    internal void SetCompact(bool compact)
    {
        Grid.SetRow(input, compact ? 1 : 0);
        Grid.SetColumn(input, compact ? 0 : 2);
        Grid.SetColumnSpan(input, compact ? 3 : 1);
        input.HorizontalAlignment = HorizontalAlignment.Right;
        input.Margin = compact ? new Thickness(0, 8, 0, 0) : new Thickness();
    }
}
