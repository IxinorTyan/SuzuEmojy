using System.Windows;

namespace SuzuEmojy.Windows.Controls;

public static class FluentScroll
{
    // Grids with their own right inset can put the thumb in that inset. Column
    // fitting then doesn't depend on whether the previous layout needed scrolling.
    public static readonly DependencyProperty OverlayVerticalBarProperty = DependencyProperty.RegisterAttached(
        "OverlayVerticalBar", typeof(bool), typeof(FluentScroll), new PropertyMetadata(false));

    public static bool GetOverlayVerticalBar(DependencyObject element) => (bool)element.GetValue(OverlayVerticalBarProperty);
    public static void SetOverlayVerticalBar(DependencyObject element, bool value) => element.SetValue(OverlayVerticalBarProperty, value);
}
