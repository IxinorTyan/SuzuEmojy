using System;
using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;

namespace SuzuEmojy.Windows.Controls;

// All continuous input updates the real scroll position on the composition
// clock, so the scrollbar, hit testing and virtualized content stay in sync.
public sealed class FluentScrollViewer : ScrollViewer
{
    private enum Motion { None, Wheel, Drag, Auto }
    private const double WheelResponse = 23, AutoResponse = 24, LineHeight = 16;
    private const double MaxFrameSeconds = 1.0 / 30;
    private Motion motion;
    private double target, requested, velocity;
    private int wheelDirection;
    private TimeSpan? lastFrame;
    private long started;
    private MiddleScroll? middle;

    internal bool IsMiddleScrolling => middle?.Active == true;
    internal bool IsContinuousScrolling => motion != Motion.None || IsMiddleScrolling;
    internal event Action? MiddleScrollStarted;

    public FluentScrollViewer()
    {
        Loaded += (_, _) =>
        {
            if (middle is null && Window.GetWindow(this) is { } owner)
                middle = new MiddleScroll(owner, this, () => MiddleScrollStarted?.Invoke());
        };
        IsVisibleChanged += (_, _) => { if (!IsVisible) CancelScrollMotion(); };
        Unloaded += (_, _) =>
        {
            CancelScrollMotion();
            middle?.Dispose();
            middle = null;
        };
    }

    protected override void OnMouseWheel(MouseWheelEventArgs e)
    {
        if (e.Handled) return;
        if (CanContentScroll)
        {
            base.OnMouseWheel(e);
            return;
        }

        e.Handled = ScrollWheel(e.Delta, SystemParameters.WheelScrollLines, FluentMotion.Enabled(this));
    }

    internal bool ScrollWheel(int delta, int lines, bool animate)
    {
        if (ScrollableHeight <= 0) return false;
        if (delta == 0 || lines == 0) return true;

        double step = -(delta / (double)Mouse.MouseWheelDeltaForOneLine) * (lines < 0 ? ViewportHeight : lines * LineHeight);
        int direction = Math.Sign(step);
        double offset = VerticalOffset;
        bool sameDirection = motion == Motion.Wheel && wheelDirection == direction;
        double next = ClampOffset((animate && sameDirection ? target : offset) + step);
        if (!animate || next == offset)
        {
            CancelScrollMotion();
            SetOffset(next);
            return true;
        }
        if (!sameDirection)
        {
            if (motion != Motion.Wheel) Begin(Motion.Wheel);
            else { requested = offset; velocity = 0; }
        }
        wheelDirection = direction;
        target = next;
        return true;
    }

    internal void BeginMiddleDrag() => Begin(Motion.Drag);

    internal void MoveMiddleDrag(double delta)
    {
        if (motion == Motion.Drag) target = ClampOffset(target + delta);
    }

    internal void EndMiddleDrag()
    {
        if (motion != Motion.Drag) return;
        // Keep the final pointer position even if the button is released before
        // the compositor paints. A released grab has no inertial tail.
        SetOffset(target);
        End();
    }

    internal void BeginMiddleAuto() => Begin(Motion.Auto);

    internal void StopMiddleScroll()
    {
        middle?.Stop();
        if (motion is Motion.Drag or Motion.Auto) End();
    }

    private void Begin(Motion kind)
    {
        if (motion == Motion.None) CompositionTarget.Rendering += OnRendering;
        motion = kind;
        target = requested = VerticalOffset;
        velocity = 0;
        started = Stopwatch.GetTimestamp();
        lastFrame = null;
    }

    private void OnRendering(object? sender, EventArgs e)
    {
        var frame = ((RenderingEventArgs)e).RenderingTime;
        if (lastFrame == frame) return;
        var seconds = lastFrame is { } previous ? (frame - previous).TotalSeconds :
            Math.Min(1.0 / 60, (Stopwatch.GetTimestamp() - started) / (double)Stopwatch.Frequency);
        lastFrame = frame;
        AdvanceFrame(seconds, motion == Motion.Auto ? middle?.AutoSpeed ?? 0 : 0, FluentMotion.Enabled(this));
    }

    internal void AdvanceFrame(double elapsed, double autoSpeed = 0, bool animate = true) =>
        AdvanceMotion(RuntimePrimitives.Clamp(elapsed, 0, MaxFrameSeconds), autoSpeed, animate);

    internal void AdvanceMotion(double elapsed, double autoSpeed = 0, bool animate = true)
    {
        if (elapsed <= 0) return;
        switch (motion)
        {
            case Motion.Wheel:
                // Critically damped position tracking preserves speed when another notch arrives.
                double distance = requested - target;
                double slope = velocity + WheelResponse * distance;
                double decay = Math.Exp(-WheelResponse * elapsed);
                double position = target + (distance + slope * elapsed) * decay;
                velocity = (velocity - WheelResponse * slope * elapsed) * decay;
                bool settled = !animate || (Math.Abs(target - position) < .2 && Math.Abs(velocity) < 4);
                SetOffset(settled ? target : position);
                if (settled) End();
                break;
            case Motion.Drag:
                // Coalesce every mouse event since the last paint, not their layout work.
                SetOffset(target);
                break;
            case Motion.Auto:
                if (!animate || autoSpeed == 0 || autoSpeed * velocity < 0) velocity = animate ? 0 : autoSpeed;
                double difference = velocity - autoSpeed;
                double blend = 1 - Math.Exp(-AutoResponse * elapsed);
                double delta = autoSpeed * elapsed + difference * blend / AutoResponse;
                velocity -= difference * blend;
                double next = ClampOffset(requested + delta);
                if (next == 0 || next == ScrollableHeight) velocity = 0;
                SetOffset(next);
                break;
        }
    }

    private double ClampOffset(double value) => RuntimePrimitives.Clamp(value, 0, ScrollableHeight);

    private void SetOffset(double value)
    {
        bool changed = value != requested || value != VerticalOffset;
        requested = value;
        if (changed) ScrollInfo.SetVerticalOffset(value);
    }

    protected override void OnScrollChanged(ScrollChangedEventArgs e)
    {
        // A resize, thumb drag or other scroll command owns the new position.
        if (motion != Motion.None && (e.ExtentHeightChange != 0 || e.ViewportHeightChange != 0 ||
            (e.VerticalChange != 0 && Math.Abs(e.VerticalOffset - requested) > .01)))
            CancelScrollMotion();
        base.OnScrollChanged(e);
    }

    protected override void OnPreviewMouseDown(MouseButtonEventArgs e)
    {
        if (motion == Motion.Wheel) End();
        base.OnPreviewMouseDown(e);
    }

    protected override void OnPreviewMouseWheel(MouseWheelEventArgs e)
    {
        // Gallery zoom runs during the preview route and consumes Ctrl+wheel.
        if ((Keyboard.Modifiers & ModifierKeys.Control) != 0) CancelScrollMotion();
        base.OnPreviewMouseWheel(e);
    }

    internal void CancelScrollMotion()
    {
        StopMiddleScroll();
        End();
    }

    internal void CancelMiddleMotion()
    {
        if (motion is Motion.Drag or Motion.Auto) End();
    }

    private void End()
    {
        if (motion == Motion.None) return;
        CompositionTarget.Rendering -= OnRendering;
        motion = Motion.None;
        velocity = 0;
        lastFrame = null;
    }
}
