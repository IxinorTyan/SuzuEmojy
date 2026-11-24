using System;
using System.Windows;
using System.Windows.Controls.Primitives;
using System.Windows.Input;

namespace SuzuEmojy.Windows.Controls;

// Attaches the gesture to an existing WPF scroll viewer without replacing its wheel,
// scrollbar or button input. The window sees cancelling clicks before the buttons do.
internal sealed class MiddleScroll : IDisposable
{
    private readonly Window owner;
    private readonly FluentScrollViewer scroll;
    private readonly Action started;
    private readonly MiddleScrollGesture gesture = new(
        SystemParameters.MinimumHorizontalDragDistance, SystemParameters.MinimumVerticalDragDistance);
    private readonly QueryCursorEventHandler queryCursor;
    private MouseButton? cancelButton;

    internal bool Active => gesture.Active;
    internal double AutoSpeed => gesture.AutoSpeed(Mouse.GetPosition(scroll));

    internal MiddleScroll(Window owner, FluentScrollViewer scroll, Action started)
    {
        this.owner = owner;
        this.scroll = scroll;
        this.started = started;
        scroll.PreviewMouseDown += Start;
        scroll.LostMouseCapture += LostCapture;
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
        if (e.ChangedButton != MouseButton.Middle || scroll.CanContentScroll || scroll.ScrollableHeight <= 0 ||
            (scroll.Template.FindName("PART_VerticalScrollBar", scroll) is ScrollBar bar && bar.IsMouseOver))
            return;
        if (!Mouse.Capture(scroll, CaptureMode.Element)) return;
        gesture.Start(e.GetPosition(scroll));
        scroll.BeginMiddleDrag();
        started();
        Mouse.UpdateCursor();
        e.Handled = true;
    }

    private void Drag(object sender, MouseEventArgs e)
    {
        if (!gesture.Pressed) return;
        scroll.MoveMiddleDrag(gesture.Drag(e.GetPosition(scroll)));
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
        var position = e.GetPosition(scroll);
        scroll.MoveMiddleDrag(gesture.Drag(position));
        var latched = gesture.Release(position);
        ReleaseCapture();
        if (latched) scroll.BeginMiddleAuto();
        else scroll.EndMiddleDrag();
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
        scroll.CancelMiddleMotion();
        ReleaseCapture();
        Mouse.UpdateCursor();
    }

    public void Dispose()
    {
        Stop();
        scroll.PreviewMouseDown -= Start;
        scroll.LostMouseCapture -= LostCapture;
        owner.PreviewMouseDown -= CancelClick;
        owner.PreviewMouseUp -= Release;
        owner.PreviewMouseMove -= Drag;
        owner.PreviewKeyDown -= KeyDown;
        owner.PreviewMouseWheel -= Wheel;
        owner.Deactivated -= Deactivated;
        owner.RemoveHandler(Mouse.QueryCursorEvent, queryCursor);
    }
}
