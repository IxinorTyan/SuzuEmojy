using System;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;

namespace SuzuEmojy.Windows.Controls;

// Inherited from each window. System reduced-motion preferences still apply.
internal static class FluentMotion
{
    internal static readonly DependencyProperty DisabledProperty = DependencyProperty.RegisterAttached(
        "Disabled", typeof(bool), typeof(FluentMotion), new FrameworkPropertyMetadata(false,
            FrameworkPropertyMetadataOptions.Inherits, (sender, _) => (sender.GetValue(MotionChangedProperty) as Action)?.Invoke()));
    private static readonly DependencyProperty MotionChangedProperty = DependencyProperty.RegisterAttached(
        "MotionChanged", typeof(Action), typeof(FluentMotion), new PropertyMetadata(null));

    internal static void Observe(DependencyObject host, Action action) =>
        host.SetValue(MotionChangedProperty, (host.GetValue(MotionChangedProperty) as Action) + action);

    internal static bool Enabled(DependencyObject host) => !(bool)host.GetValue(DisabledProperty) && SystemParameters.ClientAreaAnimation;
    internal static bool VisibleMotion(FrameworkElement host) => host.IsLoaded && host.IsVisible && Enabled(host);

    internal static DoubleAnimation Ease(double from, double to, int ms, EasingMode easing = EasingMode.EaseOut) => new(from, to, TimeSpan.FromMilliseconds(ms))
    {
        FillBehavior = FillBehavior.Stop, EasingFunction = new CubicEase { EasingMode = easing }
    };

    internal static void Fade(UIElement element, double to, int ms, bool animate, Action? finished = null,
        EasingMode easing = EasingMode.EaseOut)
    {
        var from = element.Opacity;
        element.BeginAnimation(UIElement.OpacityProperty, null);
        element.SetCurrentValue(UIElement.OpacityProperty, to);
        if (!animate || Math.Abs(from - to) < .001) { finished?.Invoke(); return; }
        var fade = Ease(from, to, ms, easing);
        if (finished is not null) fade.Completed += (_, _) => finished();
        element.BeginAnimation(UIElement.OpacityProperty, fade);
    }

    internal static void Slide(TranslateTransform offset, Vector to, int ms, bool animate, EasingMode easing = EasingMode.EaseOut)
    {
        SlideAxis(TranslateTransform.XProperty, offset.X, to.X);
        SlideAxis(TranslateTransform.YProperty, offset.Y, to.Y);

        void SlideAxis(DependencyProperty axis, double from, double destination)
        {
            offset.BeginAnimation(axis, null);
            offset.SetCurrentValue(axis, destination);
            if (animate && Math.Abs(from - destination) > .001)
                offset.BeginAnimation(axis, Ease(from, destination, ms, easing));
        }
    }

    // Paint only a themed overlay; hit boxes and layout never move under the pointer.
    internal static void Focus(FrameworkElement hit, FrameworkElement overlay)
    {
        void Update(bool animate) => Fade(overlay,
            hit.IsEnabled && (hit.IsMouseOver || hit.IsKeyboardFocusWithin) ? 1 : 0, 125,
            animate && VisibleMotion(hit));
        hit.MouseEnter += (_, _) => Update(true);
        hit.MouseLeave += (_, _) => Update(true);
        hit.IsKeyboardFocusWithinChanged += (_, _) => Update(true);
        hit.IsEnabledChanged += (_, _) => Update(false);
        hit.IsVisibleChanged += (_, _) => Update(false);
        Observe(hit, () => Update(false));
        Update(false);
    }
}

// Expand/collapse a surface without changing its hit region mid-animation.
// Its owner decides when it should actually be attached/visible.
internal sealed class MotionReveal(FrameworkElement policy, FrameworkElement surface, Func<Vector>? hiddenOffset = null,
    int enterMilliseconds = 145, int exitMilliseconds = 115, EasingMode exitEasing = EasingMode.EaseOut)
{
    private readonly TranslateTransform offset = new();
    private bool visible;
    private int revision;
    private Action? finishHide;
    private Vector HiddenOffset => hiddenOffset?.Invoke() ?? new Vector(0, 5);

    internal void Show(Action show)
    {
        if (visible) return;
        visible = true;
        revision++;
        surface.RenderTransform = offset;
        if (finishHide is null)
        {
            surface.Opacity = 0;
            FluentMotion.Slide(offset, HiddenOffset, 0, false);
        }
        finishHide = null;
        show();
        bool animated = FluentMotion.VisibleMotion(policy);
        FluentMotion.Slide(offset, default, enterMilliseconds, animated);
        FluentMotion.Fade(surface, 1, enterMilliseconds, animated);
    }

    internal void Hide(Action hide, bool immediate = false)
    {
        if (!visible && finishHide is null) { hide(); return; }
        if (!visible && !immediate) return;
        visible = false;
        int current = ++revision;
        finishHide = hide;
        bool animated = !immediate && FluentMotion.VisibleMotion(policy);
        FluentMotion.Slide(offset, HiddenOffset, exitMilliseconds, animated, exitEasing);
        FluentMotion.Fade(surface, 0, exitMilliseconds, animated, () => { if (current == revision) CompleteHide(); }, exitEasing);
    }

    internal void Snap()
    {
        revision++;
        FluentMotion.Fade(surface, visible ? 1 : 0, 0, false);
        FluentMotion.Slide(offset, visible ? default : HiddenOffset, 0, false);
        CompleteHide();
    }

    private void CompleteHide()
    {
        if (visible) return;
        var complete = finishHide;
        finishHide = null;
        complete?.Invoke();
    }
}

// Keep routing/lifetimes synchronous. Only a bounded outgoing image lasts past
// navigation, so an old page cannot keep handling input or running its tasks.
internal sealed class PageMotion(FrameworkElement policy, System.Windows.Controls.Image overlay)
{
    private FrameworkElement? incoming;
    private int revision;

    internal ImageSource? Capture(FrameworkElement view)
    {
        var (w, h) = (view.ActualWidth, view.ActualHeight);
        if (!FluentMotion.VisibleMotion(policy) || !view.IsVisible || w < 1 || h < 1) return null;
        var display = VisualTreeHelper.GetDpi(view).DpiScaleX;
        var scale = Math.Min(display, Math.Min(Math.Sqrt(1_000_000 / (w * h)), 1800 / Math.Max(w, h)));
        try
        {
            var drawing = new DrawingVisual();
            using (var dc = drawing.RenderOpen()) dc.DrawRectangle(new VisualBrush(view) { Stretch = Stretch.Fill }, null,
                new Rect(0, 0, w * scale, h * scale));
            var image = new RenderTargetBitmap(Math.Max(1, (int)Math.Ceiling(w * scale)),
                Math.Max(1, (int)Math.Ceiling(h * scale)), 96, 96, PixelFormats.Pbgra32);
            image.Render(drawing);
            image.Freeze();
            return image;
        }
        catch (Exception error) when (error is InvalidOperationException or System.Runtime.InteropServices.COMException) { return null; }
    }

    internal void Swap(ImageSource? outgoing, FrameworkElement next)
    {
        Reset();
        if (outgoing is null || !FluentMotion.VisibleMotion(policy)) return;
        incoming = next;
        int current = revision;
        overlay.Source = outgoing;
        overlay.Visibility = Visibility.Visible;
        overlay.Opacity = 1;
        next.Opacity = .2;
        FluentMotion.Fade(next, 1, 185, true);
        FluentMotion.Fade(overlay, 0, 185, true, () => { if (current == revision) Reset(); });
    }

    internal void Reset()
    {
        revision++;
        if (incoming is { } previous) FluentMotion.Fade(previous, 1, 0, false);
        incoming = null;
        FluentMotion.Fade(overlay, 0, 0, false);
        overlay.Source = null;
        overlay.Visibility = Visibility.Collapsed;
    }
}
