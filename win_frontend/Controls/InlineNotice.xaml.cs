using System;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Automation.Peers;
using System.Windows.Controls;
using System.Windows.Media.Animation;
using System.Windows.Threading;

namespace SuzuEmojy.Windows.Controls;

public partial class InlineNotice : UserControl
{
    private readonly DispatcherTimer timer = new(DispatcherPriority.Background);
    private readonly MotionReveal reveal;
    private bool sliding;

    public InlineNotice()
    {
        InitializeComponent();
        reveal = new(this, NoticeCard, RightOffset, enterMilliseconds: 235, exitMilliseconds: 205, exitEasing: EasingMode.EaseIn);
        FluentMotion.Observe(this, () => { if (sliding) reveal.Snap(); });
        timer.Tick += (_, _) => Close(immediate: false);
        Unloaded += (_, _) => Dismiss();
    }

    internal void Show(string title, string message, bool warning = false, bool persistent = false, bool slideFromRight = false)
    {
        timer.Stop();
        if (sliding && !slideFromRight) Close(immediate: true);
        Heading.Text = title;
        Detail.Text = message;
        Symbol.Text = warning ? "!" : "✓";
        Symbol.SetResourceReference(TextBlock.ForegroundProperty, warning ? "NoticeWarning" : "NoticeSuccess");
        AutomationProperties.SetName(this, $"{title}。{message}");
        sliding = slideFromRight;
        if (sliding) reveal.Show(() => Visibility = Visibility.Visible);
        else
        {
            NoticeCard.RenderTransform = System.Windows.Media.Transform.Identity;
            NoticeCard.Opacity = 1;
            Visibility = Visibility.Visible;
        }
        if (AutomationPeer.ListenerExists(AutomationEvents.LiveRegionChanged))
            UIElementAutomationPeer.CreatePeerForElement(this)?.RaiseAutomationEvent(AutomationEvents.LiveRegionChanged);
        timer.Interval = TimeSpan.FromSeconds(warning ? 6 : 2);
        if (!persistent) timer.Start();
    }

    internal void Dismiss() => Close(immediate: true);

    private void Close(bool immediate)
    {
        timer.Stop();
        if (sliding) reveal.Hide(Collapse, immediate);
        else Collapse();
    }

    private void Collapse()
    {
        sliding = false;
        Visibility = Visibility.Collapsed;
    }

    private Vector RightOffset()
    {
        double width = MaxWidth;
        if (Parent is FrameworkElement host && host.ActualWidth > 0)
            width = Math.Min(width, Math.Max(1, host.ActualWidth - Margin.Left - Margin.Right));
        NoticeCard.Measure(new Size(width, double.PositiveInfinity));
        return new(NoticeCard.DesiredSize.Width + Margin.Right + 2, 0);
    }
}
