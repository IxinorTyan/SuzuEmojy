using System.Windows;
using System.Windows.Media;

namespace SuzuEmojy.Windows.Controls;

// Shared by every menu in a desktop session, including the tray. Popup trees
// merge this dictionary instead of taking a one-time copy of the current colors.
internal sealed class FluentMenuPalette
{
    internal ResourceDictionary Resources { get; } = new();
    private (bool Dark, Colors Theme)? applied;

    internal FluentMenuPalette(LibrarySnapshot library) => Apply(library);

    internal void Apply(LibrarySnapshot library)
    {
        bool dark = library.Dark;
        var theme = library.Theme(dark);
        if (applied == (dark, theme)) return;
        applied = (dark, theme);
        var canvas = (Color)ColorConverter.ConvertFromString(theme.Bg);
        var accent = (Color)ColorConverter.ConvertFromString(theme.Accent);
        var neutral = (Color)ColorConverter.ConvertFromString(dark ? "#2B2B2B" : "#F9F9F9");
        bool tinted = canvas.R != canvas.G || canvas.G != canvas.B || accent.R != accent.G || accent.G != accent.B;
        var surface = tinted ? Blend(neutral, canvas, .65) : neutral;
        Resources["FluentMenuInk"] = dark ? Brushes.White : Brushes.Black;
        Resources["FluentMenuSurface"] = Frozen(surface);
        Resources["FluentMenuEdge"] = FluentTheme.Brush(dark ? "#1AFFFFFF" : "#1A000000");
        Resources["FluentMenuHover"] = tinted ? Frozen(Blend(surface, accent, dark ? .25 : .22)) :
            FluentTheme.Brush(dark ? "#14FFFFFF" : "#09000000");
        Resources["ScrollThumb"] = FluentTheme.Brush(dark ? "#99FFFFFF" : "#80000000");
    }

    private static Color Blend(Color from, Color to, double amount)
    {
        byte Mix(byte a, byte b) => (byte)System.Math.Round(a + (b - a) * amount);
        return Color.FromRgb(Mix(from.R, to.R), Mix(from.G, to.G), Mix(from.B, to.B));
    }

    private static Brush Frozen(Color color)
    {
        var brush = new SolidColorBrush(color);
        brush.Freeze();
        return brush;
    }
}
