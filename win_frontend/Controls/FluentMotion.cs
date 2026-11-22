using System;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Animation;

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
    internal static void Hover(FrameworkElement hit, FrameworkElement overlay)
    {
        void Update(bool animate) => Fade(overlay,
            hit.IsEnabled && hit.IsMouseOver ? 1 : 0, 125,
            animate && VisibleMotion(hit));
        hit.MouseEnter += (_, _) => Update(true);
        hit.MouseLeave += (_, _) => Update(true);
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

// Fade only the incoming surface. Capturing and blending an outgoing page
// makes navigation wait for a full-window bitmap to render on the UI thread.
internal sealed class PageMotion
{
    private readonly FrameworkElement surface;

    internal PageMotion(FrameworkElement surface)
    {
        this.surface = surface;
        FluentMotion.Observe(surface, Reset);
        surface.Unloaded += (_, _) => Reset();
    }

    internal void Show(bool animate = true)
    {
        Reset();
        if (animate && FluentMotion.VisibleMotion(surface))
            surface.BeginAnimation(UIElement.OpacityProperty, FluentMotion.Ease(.86, 1, 130));
    }

    internal void Reset() => surface.BeginAnimation(UIElement.OpacityProperty, null);
}
