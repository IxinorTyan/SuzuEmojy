using System;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Animation;

namespace SuzuEmojy.Windows.Controls;

// Animate from the position currently on screen to the new layout, without relayout on each frame.
internal static class TileMotion
{
    internal static Rect VisualBounds(UIElement tile, Rect layout)
    {
        if (layout.IsEmpty || tile.RenderTransform is not TransformGroup pose) return layout;
        var visual = pose.TransformBounds(new Rect(layout.Size));
        visual.Offset(layout.Location.X, layout.Location.Y);
        return visual;
    }

    internal static void Reflow(UIElement tile, Rect previous, Rect target, double offsetDelta)
    {
        if (previous.IsEmpty || target.IsEmpty || target.Width <= 0 || target.Height <= 0)
        {
            tile.RenderTransform = Transform.Identity;
            return;
        }
        previous.Offset(0, offsetDelta);
        if (previous == target)
        {
            tile.RenderTransform = Transform.Identity;
            return;
        }
        var scale = new ScaleTransform();
        var translate = new TranslateTransform();
        tile.RenderTransform = new TransformGroup { Children = { scale, translate } };
        const int duration = 145;
        Move(translate, TranslateTransform.XProperty, previous.X - target.X, 0, duration);
        Move(translate, TranslateTransform.YProperty, previous.Y - target.Y, 0, duration);
        Move(scale, ScaleTransform.ScaleXProperty, previous.Width / target.Width, 1, duration);
        Move(scale, ScaleTransform.ScaleYProperty, previous.Height / target.Height, 1, duration);
    }

    internal static void Move(Animatable target, DependencyProperty property, double from, double to, int ms)
    {
        if (Math.Abs(from - to) < .0001) return;
        target.BeginAnimation(property, FluentMotion.Ease(from, to, ms));
    }

}
