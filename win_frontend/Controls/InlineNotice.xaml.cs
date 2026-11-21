using System;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Automation.Peers;
using System.Windows.Controls;
using System.Windows.Threading;

namespace SuzuEmojy.Windows.Controls;

public partial class InlineNotice : UserControl
{
    private readonly DispatcherTimer timer = new(DispatcherPriority.Background);

    public InlineNotice()
    {
        InitializeComponent();
        timer.Tick += (_, _) => Dismiss();
        Unloaded += (_, _) => Dismiss();
    }

    internal void Show(string title, string message, bool warning = false)
    {
        Heading.Text = title;
        Detail.Text = message;
        Symbol.Text = warning ? "!" : "✓";
        Symbol.SetResourceReference(TextBlock.ForegroundProperty, warning ? "NoticeWarning" : "NoticeSuccess");
        AutomationProperties.SetName(this, $"{title}。{message}");
        Visibility = Visibility.Visible;
        if (AutomationPeer.ListenerExists(AutomationEvents.LiveRegionChanged))
            UIElementAutomationPeer.CreatePeerForElement(this)?.RaiseAutomationEvent(AutomationEvents.LiveRegionChanged);
        timer.Stop();
        timer.Interval = TimeSpan.FromSeconds(warning ? 6 : 2);
        timer.Start();
    }

    internal void Dismiss()
    {
        timer.Stop();
        Visibility = Visibility.Collapsed;
    }
}
