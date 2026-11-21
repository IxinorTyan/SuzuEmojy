using System.Collections.Generic;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Media;
using System.Windows.Shapes;

namespace SuzuEmojy.Windows.Controls;

internal static class FluentGlyph
{
    private static readonly Dictionary<string, string> Icons = new()
    {
        ["home"] = "M1,7 L8,1 15,7 V15 H10 V10 H6 V15 H1 Z",
        ["folder"] = "M1,5 V3 Q1,2 2,2 H6 L8,4 H14 Q15,4 15,5 V13 Q15,14 14,14 H2 Q1,14 1,13 Z M1,5 H6 L8,4",
        ["add"] = "M8,1 V15 M1,8 H15",
        ["tiles"] = "M1,1 H8 V7 H1 Z M11,1 H15 V7 H11 Z M1,10 H5 V15 H1 Z M8,10 H15 V15 H8 Z",
        ["filter"] = "M1,2 H15 L10,8 V14 L6,12 V8 Z",
        ["search"] = "M7,1 A5,5 0 1 0 7,11 A5,5 0 1 0 7,1 M11,10 L15,15",
        ["share"] = "M8,4 H3 Q1,4 1,6 V13 Q1,15 3,15 H12 Q14,15 14,13 V10 M4,12 C4,7 7,6 10,6 V2 L15,7 10,11 V8 C8,8 6,9 4,12",
        ["external-link"] = "M9,2 H14 V7 M14,2 L7,9 M7,4 H2 V14 H12 V9",
        ["settings"] = "M6,1 H10 L10.5,3.2 12,4 14,3.5 15.5,6 14,7.4 V8.6 L15.5,10 14,12.5 12,12 10.5,12.8 10,15 H6 L5.5,12.8 4,12 2,12.5 .5,10 2,8.6 V7.4 L.5,6 2,3.5 4,4 5.5,3.2 Z M8,5.5 A2.5,2.5 0 1 0 8,10.5 A2.5,2.5 0 1 0 8,5.5",
        ["close"] = "M3,3 L13,13 M13,3 L3,13",
        ["minimize"] = "M3,8 H13",
        ["maximize"] = "M3,3 H13 V13 H3 Z",
        ["restore"] = "M3,5 H11 V13 H3 Z M5,5 V3 H13 V11 H11",
    };

    internal static FrameworkElement Create(string name, int size, Control source)
    {
        var path = new Path
        {
            Data = Geometry.Parse(Icons[name]), StrokeThickness = 1.05,
            StrokeLineJoin = PenLineJoin.Round, StrokeStartLineCap = PenLineCap.Round,
            StrokeEndLineCap = PenLineCap.Round
        };
        path.SetBinding(Shape.StrokeProperty, new Binding(nameof(Control.Foreground)) { Source = source });
        var drawing = new Canvas { Width = 16, Height = 16 };
        drawing.Children.Add(path);
        return new Viewbox { Width = size, Height = size, Child = drawing, IsHitTestVisible = false };
    }
}
