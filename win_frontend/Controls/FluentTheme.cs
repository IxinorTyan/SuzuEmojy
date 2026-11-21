using System.Windows;
using System.Windows.Media;

namespace SuzuEmojy.Windows.Controls;

internal static class FluentTheme
{
    internal static void Apply(Window window, LibrarySnapshot library)
    {
        var resources = window.Resources;
        var dark = library.Dark;
        var theme = library.Theme(dark);
        resources["CanvasColor"] = Brush(theme.Bg);
        resources["Ink"] = dark ? Brushes.White : Brushes.Black;
        resources["Hint"] = Brush(dark ? "#A5A5A5" : "#666666");
        resources["Accent"] = Brush(theme.Accent);
        resources["Divider"] = Brush(dark ? "#1AFFFFFF" : "#1A000000");
        resources["Hover"] = Brush(dark ? "#14FFFFFF" : "#0D000000");
        resources["CardEdge"] = Brush(dark ? "#26FFFFFF" : "#1F000000");
        resources["ScrollThumb"] = Brush(dark ? "#99FFFFFF" : "#80000000");
        resources["CategorySurface"] = Brush(dark ? "#330078D4" : "#1A0078D4");
        resources["CategoryOutline"] = Brush(dark ? "#60FFFFFF" : "#55000000");
        resources["SearchSurface"] = Brush(dark ? "#13FFFFFF" : "#B3FFFFFF");
        resources["SearchUnderline"] = Brush(dark ? "#50FFFFFF" : "#66000000");
        resources["SettingCardSurface"] = Brush(dark ? "#0DFFFFFF" : "#99FFFFFF");
        resources["SettingCardEdge"] = Brush(dark ? "#1AFFFFFF" : "#14000000");
        resources["FieldSurface"] = Brush(dark ? "#15FFFFFF" : "#CCFFFFFF");
        resources["SliderRail"] = Brush(dark ? "#82FFFFFF" : "#66000000");
        resources["SliderThumb"] = Brush(dark ? "#454545" : "#FFFFFF");
        resources["ToggleRail"] = Brush(dark ? "#A5FFFFFF" : "#8F000000");
        resources["OnAccent"] = Brush(dark ? "#121212" : "#FFFFFF");
        resources["PromptSurface"] = Brush(dark ? "#282828" : "#F4F4F4");
        resources["PreviewSurface"] = Brush(dark ? "#F21E1E1E" : "#F2FFFFFF");
        resources["PreviewEdge"] = Brush(dark ? "#F23A3A3A" : "#F2DFDFDF");
        resources["NoticeSurface"] = Brush(dark ? "#2B2B2B" : "#F9F9F9");
        resources["NoticeSuccess"] = Brush(dark ? "#6CCB5F" : "#0F7B0F");
        resources["NoticeWarning"] = Brush(dark ? "#FCE100" : "#9D5D00");
        resources["QuickPanelEdge"] = Brush(dark ? "#444444" : "#CCCCCC");
        var accent = ((SolidColorBrush)resources["Accent"]).Color;
        var canvas = ((SolidColorBrush)resources["CanvasColor"]).Color;
        resources["QuickPanelSurface"] = new SolidColorBrush(Color.FromArgb(240, canvas.R, canvas.G, canvas.B));
        resources["SelectionSurface"] = new SolidColorBrush(Color.FromArgb(dark ? (byte)71 : (byte)31,
            accent.R, accent.G, accent.B));
        window.Foreground = (Brush)resources["Ink"];
    }

    internal static SolidColorBrush Brush(string color) => new((Color)ColorConverter.ConvertFromString(color));
}
