using System;
using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Threading;

namespace SuzuEmojy.Windows.Controls;

// Attaches the gesture to an existing WPF scroll viewer without replacing its wheel,
// scrollbar or button input. The window sees cancelling clicks before the buttons do.
internal sealed class MiddleScroll : IDisposable
{
    private readonly Window owner;
    private readonly ScrollViewer scroll;
    private readonly Action started;
    private readonly MiddleScrollGesture gesture = new(
        SystemParameters.MinimumHorizontalDragDistance, SystemParameters.MinimumVerticalDragDistance);
    private readonly DispatcherTimer timer;
    private readonly QueryCursorEventHandler queryCursor;
    private MouseButton? cancelButton;
    private long lastTick;
    private double dragTarget;

    internal bool Active => gesture.Active;

    internal MiddleScroll(Window owner, ScrollViewer scroll, Action started)
    {
        this.owner = owner;
        this.scroll = scroll;
        this.started = started;
        timer = new DispatcherTimer(DispatcherPriority.Input, scroll.Dispatcher) { Interval = TimeSpan.FromMilliseconds(16) };
        timer.Tick += Tick;
        scroll.PreviewMouseDown += Start;
        scroll.LostMouseCapture += LostCapture;
        scroll.IsVisibleChanged += VisibilityChanged;
        scroll.ScrollChanged += ScrollChanged;
        owner.PreviewMouseDown += CancelClick;
        owner.PreviewMouseUp += Release;
        owner.PreviewMouseMove += Drag;
        owner.PreviewKeyDown += KeyDown;
        owner.PreviewMouseWheel += Wheel;
        owner.Deactivated += Deactivated;
        queryCursor = QueryCursor;
        owner.AddHandler(Mouse.QueryCursorEvent, queryCursor, handledEventsToo: true);
    }

    private void Start(object sender, MouseButtonEventArgs e)
    {
        if (e.ChangedButton != MouseButton.Middle || scroll.ScrollableHeight <= 0 ||
            (scroll.Template.FindName("PART_VerticalScrollBar", scroll) is ScrollBar bar && bar.IsMouseOver))
            return;
        if (!Mouse.Capture(scroll, CaptureMode.Element)) return;
        gesture.Start(e.GetPosition(scroll));
        dragTarget = scroll.VerticalOffset;
        started();
        Mouse.UpdateCursor();
        e.Handled = true;
    }

    private void Drag(object sender, MouseEventArgs e)
    {
        if (!gesture.Pressed) return;
        // ScrollViewer applies offsets during layout. Multiple moves can arrive
        // before that happens, so accumulate pressed motion against our own target.
        var delta = gesture.Drag(e.GetPosition(scroll));
        if (delta != 0)
        {
            dragTarget = RuntimePrimitives.Clamp(dragTarget + delta, 0, scroll.ScrollableHeight);
            scroll.ScrollToVerticalOffset(dragTarget);
        }
        e.Handled = true;
    }

    private void Release(object sender, MouseButtonEventArgs e)
    {
        if (e.ChangedButton == cancelButton)
        {
            cancelButton = null;
            e.Handled = true;
            return;
        }
        if (!gesture.Pressed || e.ChangedButton != MouseButton.Middle) return;
        var latched = gesture.Release(e.GetPosition(scroll));
        ReleaseCapture();
        if (latched)
        {
            lastTick = Stopwatch.GetTimestamp();
            timer.Start();
        }
        Mouse.UpdateCursor();
        e.Handled = true;
    }

    private void CancelClick(object sender, MouseButtonEventArgs e)
    {
        // A release outside the window may never route here. Don't swallow a
        // later unrelated click merely because it uses the same button.
        cancelButton = null;
        if (!gesture.Active) return;
        cancelButton = e.ChangedButton;
        Stop();
        e.Handled = true;
    }

    private void KeyDown(object sender, KeyEventArgs e)
    {
        if (!gesture.Active) return;
        Stop();
        if (e.Key == Key.Escape) e.Handled = true;
    }

    private void Tick(object? sender, EventArgs e)
    {
        long now = Stopwatch.GetTimestamp();
        var delta = gesture.Advance(Mouse.GetPosition(scroll), (now - lastTick) / (double)Stopwatch.Frequency);
        lastTick = now;
        if (delta != 0)
        {
            var target = RuntimePrimitives.Clamp(scroll.VerticalOffset + delta, 0, scroll.ScrollableHeight);
            if (target != scroll.VerticalOffset) scroll.ScrollToVerticalOffset(target);
        }
    }

    private void QueryCursor(object sender, QueryCursorEventArgs e)
    {
        if (!gesture.Active) return;
        e.Cursor = gesture.Pressed ? Cursors.SizeNS : Cursors.ScrollNS;
        e.Handled = true;
    }

    private void LostCapture(object sender, MouseEventArgs e)
    {
        if (gesture.Pressed && Mouse.Captured != scroll) Stop();
    }

    private void VisibilityChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        if (!scroll.IsVisible) Stop();
    }

    private void ScrollChanged(object sender, ScrollChangedEventArgs e)
    {
        if (scroll.ScrollableHeight <= 0) Stop();
    }

    private void Deactivated(object? sender, EventArgs e) { cancelButton = null; Stop(); }
    private void Wheel(object sender, MouseWheelEventArgs e) => Stop();

    private void ReleaseCapture()
    {
        if (Mouse.Captured == scroll) scroll.ReleaseMouseCapture();
    }

    internal void Stop()
    {
        if (!gesture.Active) return;
        gesture.Stop();
        timer.Stop();
        ReleaseCapture();
        Mouse.UpdateCursor();
    }

    public void Dispose()
    {
        Stop();
        timer.Tick -= Tick;
        scroll.PreviewMouseDown -= Start;
        scroll.LostMouseCapture -= LostCapture;
        scroll.IsVisibleChanged -= VisibilityChanged;
        scroll.ScrollChanged -= ScrollChanged;
        owner.PreviewMouseDown -= CancelClick;
        owner.PreviewMouseUp -= Release;
        owner.PreviewMouseMove -= Drag;
        owner.PreviewKeyDown -= KeyDown;
        owner.PreviewMouseWheel -= Wheel;
        owner.Deactivated -= Deactivated;
        owner.RemoveHandler(Mouse.QueryCursorEvent, queryCursor);
    }
}
