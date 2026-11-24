using System;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Threading;
using SuzuEmojy.Windows.Controls;

internal static class FluentInputTests
{
    internal static async Task Run()
    {
        CheckKeyboard();
        var content = new Canvas { Height = 2500 };
        var scroll = CreateScroll(content);
        Layout(scroll);

        Require(scroll.ScrollWheel(-120, 3, animate: true) && scroll.ScrollWheel(-120, 3, animate: true),
            "An overflowing viewport must consume wheel input.");
        At(0);
        scroll.AdvanceMotion(.05);
        Layout(scroll);
        Require(scroll.VerticalOffset is > 28 and < 36, "A wheel burst should ease in through a softer intermediate offset.");
        scroll.AdvanceMotion(.35);
        At(96);

        scroll.ScrollWheel(-120, 3, animate: true);
        scroll.AdvanceFrame(.2);
        Layout(scroll);
        Require(scroll.VerticalOffset is > 96 and < 111,
            "A late composition frame must advance smoothly rather than spending all of the missed motion at once.");
        scroll.AdvanceMotion(.4);
        At(144);
        scroll.ScrollToVerticalOffset(96);
        At(96);

        scroll.ScrollWheel(-120, 3, animate: true);
        scroll.AdvanceMotion(.016);
        Layout(scroll);
        double firstFrame = scroll.VerticalOffset;
        scroll.ScrollWheel(-120, 3, animate: true);
        scroll.AdvanceMotion(.016);
        Layout(scroll);
        double beforeReverse = scroll.VerticalOffset;
        Require(beforeReverse - firstFrame > 7, "A same-direction notch must retain the velocity already in flight.");
        scroll.ScrollWheel(60, 3, animate: true);
        scroll.AdvanceMotion(.35);
        At(beforeReverse - 24); // Reverse at the displayed offset; preserve half a wheel detent.

        scroll.ScrollWheel(-120, 3, animate: true);
        scroll.ScrollToVerticalOffset(1000);
        At(1000);
        scroll.AdvanceMotion(.15);
        At(1000);

        scroll.ScrollWheel(-30, 3, animate: false);
        At(1012);
        scroll.ScrollWheel(-120, -1, animate: false);
        At(1012 + scroll.ViewportHeight);
        scroll.ScrollWheel(-120, 0, animate: false);
        At(1012 + scroll.ViewportHeight);

        double end = scroll.VerticalOffset + 48;
        var reached = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        int intermediateFrames = 0;
        ScrollChangedEventHandler onScrolled = (_, e) =>
        {
            if (Math.Abs(e.VerticalOffset - end) < .01) reached.TrySetResult(true);
            else if (e.VerticalChange > 0) intermediateFrames++;
        };
        scroll.ScrollChanged += onScrolled;
        try
        {
            scroll.ScrollWheel(-120, 3, animate: true);
            await reached.Task.WaitAsync(TimeSpan.FromSeconds(3));
            Require(intermediateFrames >= 3, "Composition frames must advance the real scroll offset, not jump to the final offset.");
        }
        finally
        {
            scroll.ScrollChanged -= onScrolled;
            scroll.CancelScrollMotion();
        }

        scroll.SetValue(FluentMotion.DisabledProperty, true);
        var wheel = new MouseWheelEventArgs(Mouse.PrimaryDevice, 0, -120) { RoutedEvent = Mouse.MouseWheelEvent };
        content.RaiseEvent(wheel);
        Require(wheel.Handled, "A routed wheel event from inside the viewport must reach FluentScrollViewer.");
        if (SystemParameters.WheelScrollLines != 0)
            At(end + (SystemParameters.WheelScrollLines < 0
                ? scroll.ViewportHeight : 16.0 * SystemParameters.WheelScrollLines));

        scroll.ScrollWheel(-120, 1000, animate: false);
        At(scroll.ScrollableHeight);
        scroll.ScrollWheel(120, 1000, animate: false);
        At(0);
        CheckMiddleMotion(scroll, At);
        content.Height = 50;
        Layout(scroll);
        Require(!scroll.ScrollWheel(-120, 3, animate: true), "A viewport that fits must not consume the wheel.");

        await CheckMasonry();

        void At(double expected)
        {
            Layout(scroll);
            Require(Math.Abs(scroll.VerticalOffset - Math.Min(expected, scroll.ScrollableHeight)) < .01,
                $"Expected wheel offset {expected}, got {scroll.VerticalOffset}.");
        }
    }

    private static void CheckMiddleMotion(FluentScrollViewer scroll, Action<double> at)
    {
        scroll.BeginMiddleDrag();
        scroll.MoveMiddleDrag(12.5);
        scroll.MoveMiddleDrag(7.75);
        Require(scroll.IsContinuousScrolling, "A held middle button must claim the shared motion state.");
        at(0);
        scroll.AdvanceFrame(.016);
        at(20.25);
        scroll.MoveMiddleDrag(4.5);
        scroll.EndMiddleDrag();
        at(24.75);
        scroll.AdvanceFrame(.016);
        at(24.75);
        Require(!scroll.IsContinuousScrolling, "Releasing a drag must not leave an inertial tail.");

        scroll.BeginMiddleAuto();
        double top = scroll.VerticalOffset;
        scroll.AdvanceFrame(.008, 240);
        Layout(scroll);
        double first = scroll.VerticalOffset;
        Require(first > top && first - top < 1, "Autoscroll must ease into fractional-pixel motion at a high refresh rate.");
        scroll.AdvanceFrame(.008, 240);
        Layout(scroll);
        double second = scroll.VerticalOffset;
        Require(second - first > first - top, "Autoscroll speed should build up across frames.");
        scroll.AdvanceFrame(.2, 240);
        Layout(scroll);
        double late = scroll.VerticalOffset;
        Require(late > second && late - second < 8,
            "Autoscroll must use the same late-frame cap as the wheel.");
        scroll.AdvanceFrame(.016, 0);
        at(late);
        scroll.AdvanceFrame(.016, -240);
        Layout(scroll);
        Require(scroll.VerticalOffset < late, "Moving to the opposite side of the anchor must not drift in the old direction.");

        scroll.ScrollWheel(-120, 3, animate: true);
        double wheelStart = scroll.VerticalOffset;
        scroll.AdvanceMotion(.4);
        at(wheelStart + 48);
        scroll.BeginMiddleAuto();
        scroll.AdvanceFrame(.02, 100, animate: false);
        at(wheelStart + 50);
        scroll.StopMiddleScroll();
        scroll.AdvanceFrame(.025, 400);
        at(wheelStart + 50);
        Require(!scroll.IsContinuousScrolling, "Stopping the gesture must discard pending autoscroll.");
    }

    private static void CheckKeyboard()
    {
        FluentKeyboard.Install();
        var field = new TextBox();
        var window = new Window { Content = field };
        try
        {
            var source = HwndSource.FromHwnd(new WindowInteropHelper(window).EnsureHandle());
            Key seen = Key.None;
            field.KeyDown += (_, e) => seen = e.SystemKey;
            Require(Route(Key.LeftAlt, Keyboard.KeyDownEvent, system: true) && seen == Key.LeftAlt,
                "The focused control must see Alt before the window consumes WPF's keyboard cue.");
            Require(Route(Key.RightAlt, Keyboard.KeyDownEvent, system: true) &&
                Route(Key.LeftAlt, Keyboard.KeyUpEvent, system: true) && Route(Key.RightAlt, Keyboard.KeyUpEvent, system: true),
                "Both Alt keys, including an unpaired release after activation, must not invoke WPF menu mode.");
            Require(!Route(Key.D2, Keyboard.KeyDownEvent, system: true) && !Route(Key.F4, Keyboard.KeyDownEvent, system: true) &&
                !Route(Key.Tab, Keyboard.KeyDownEvent, system: false), "Alt chords and Tab must not be consumed by the window policy.");

            bool Route(Key key, RoutedEvent routed, bool system)
            {
                var e = new KeyEventArgs(Keyboard.PrimaryDevice, source, 0, key) { RoutedEvent = routed };
                if (system) typeof(KeyEventArgs).GetMethod("MarkSystem", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(e, null);
                field.RaiseEvent(e);
                return e.Handled;
            }
        }
        finally { window.Close(); }
    }

    private static async Task CheckMasonry()
    {
        var surface = new Canvas();
        var scroll = CreateScroll(surface);
        var masonry = new VirtualMasonry<int>(scroll, surface, index => .6 + index % 3 * .7, _ => new Border());
        int changes = 0;
        masonry.ViewportChanged += _ => changes++;
        masonry.Attach();
        try
        {
            var items = Enumerable.Range(0, 1200).ToArray();
            masonry.SetItems(items);
            await Render();
            Require(masonry.Realized.Count is > 0 and < 35 && changes > 0, "Masonry must realize only the viewport and its buffer.");
            int initialChanges = changes;
            var first = masonry.Realized[0];
            scroll.ScrollWheel(-1, 3, animate: false);
            await Render();
            Require(ReferenceEquals(first, masonry.Realized[0]) && changes == initialChanges,
                "Subpixel scroll must not rebuild cards or wake the loader for the same items.");

            scroll.ScrollWheel(-120, 60, animate: true);
            scroll.AdvanceMotion(.075);
            scroll.CancelScrollMotion();
            await Render();
            Require(!masonry.Realized.ContainsKey(0) && masonry.Realized.Count is > 0 and < 35 && changes > initialChanges,
                $"A smooth scroll frame must realize the new viewport without retaining distant cards " +
                $"(offset {scroll.VerticalOffset}, {masonry.Realized.Count} cards, {changes} viewport updates).");
            scroll.ScrollWheel(-120, 60, animate: true);
            masonry.SetItems(items);
            await Render();
            scroll.AdvanceMotion(.15);
            Require(scroll.VerticalOffset == 0 && masonry.Realized.ContainsKey(0), "Reload must cancel pending scrolling and reset the viewport.");
        }
        finally { scroll.CancelScrollMotion(); masonry.Dispose(); }

        async Task Render()
        {
            Layout(scroll);
            await scroll.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
            Layout(scroll);
        }
    }

    private static FluentScrollViewer CreateScroll(Canvas content)
    {
        var resources = (ResourceDictionary)Application.LoadComponent(new Uri("/SuzuEmojy.Windows;component/Controls/FluentScroll.xaml", UriKind.Relative));
        return new FluentScrollViewer
        {
            Template = (ControlTemplate)resources["FluentScrollTemplate"], Content = content,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
        };
    }

    private static void Layout(FrameworkElement view)
    {
        view.Measure(new Size(500, 300));
        view.Arrange(new Rect(0, 0, 500, 300));
        view.UpdateLayout();
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }
}
