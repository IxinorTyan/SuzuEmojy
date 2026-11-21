using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;

namespace SuzuEmojy.Windows.Controls;

internal sealed record PageRoute(string Title, FrameworkElement View, Action? Suspend = null, Action? Resume = null,
    Action? Leave = null, Func<bool>? HandleBack = null, OperationScope? Work = null);

// Owns the page stack; a suspended view keeps its state until it is either resumed or removed.
public partial class FluentPageHost : UserControl
{
    private sealed record Entry(PageRoute Route, string Caption, string BackLabel);
    private readonly List<Entry> stack = [];
    private Button? rootOpener;
    internal FrameworkElement? Page => stack.Count == 0 ? null : stack[^1].Route.View;
    internal bool HasBlockingOperation => stack.Any(entry => entry.Route.Work?.BlocksLeave == true);
    internal event Action? RootClosed;

    public FluentPageHost()
    {
        InitializeComponent();
        Back.Click += (_, _) => RequestBack();
        PreviewKeyDown += (_, e) =>
        {
            if (e.Key != Key.Escape || Keyboard.Modifiers != ModifierKeys.None) return;
            e.Handled = true;
            RequestBack();
        };
    }

    internal bool Push(PageRoute route, Button? opener = null)
    {
        if (HasBlockingOperation || stack.Any(entry => entry.Route.View == route.View)) return false;
        string back;
        if (stack.Count == 0)
        {
            rootOpener = opener;
            back = "返回主面板";
        }
        else
        {
            var parent = stack[^1].Route;
            parent.Suspend?.Invoke();
            back = "返回" + parent.Title;
        }
        stack.Add(new(route, route.Title, back));
        PresentCurrent();
        route.Resume?.Invoke();
        FocusBack();
        return true;
    }

    internal void SetCaption(string title, string backLabel)
    {
        if (stack.Count == 0) return;
        stack[^1] = stack[^1] with { Caption = title, BackLabel = backLabel };
        ApplyCaption(stack[^1]);
    }

    private void ApplyCaption(Entry entry)
    {
        Heading.Text = entry.Caption;
        Back.ToolTip = entry.BackLabel;
        System.Windows.Automation.AutomationProperties.SetName(Back, entry.BackLabel);
    }

    internal bool TryClose()
    {
        if (stack.Count == 0) return true;
        if (HasBlockingOperation) return false;
        while (stack.Count != 0) PopCurrent();
        PresentCurrent();
        return true;
    }

    internal void ReplaceCurrent(FrameworkElement expected, PageRoute replacement)
    {
        if (Page != expected) return;
        var current = stack[^1];
        stack[^1] = current with { Route = replacement, Caption = replacement.Title };
        PresentCurrent();
        replacement.Resume?.Invoke();
        FocusBack();
    }

    internal void RequestBack()
    {
        if (stack.Count == 0) return;
        var route = stack[^1].Route;
        if (route.HandleBack?.Invoke() == true) return;
        if (route.Work?.BlocksLeave == true)
        {
            if (route.Work.WaitsForCancellation) route.Work.RequestCancel();
            return;
        }
        PopCurrent();
        PresentCurrent();
        if (stack.Count == 0) return;
        stack[^1].Route.Resume?.Invoke();
        FocusBack();
    }

    private void PopCurrent()
    {
        var route = stack[^1].Route;
        stack.RemoveAt(stack.Count - 1);
        route.Work?.RequestCancel();
        route.Leave?.Invoke();
    }

    private void PresentCurrent()
    {
        PageBody.Content = Page;
        Visibility = stack.Count == 0 ? Visibility.Collapsed : Visibility.Visible;
        if (stack.Count > 0)
        {
            ApplyCaption(stack[^1]);
        }
        else
        {
            var opener = rootOpener;
            rootOpener = null;
            RootClosed?.Invoke();
            if (Window.GetWindow(this)?.IsActive == true && opener is { IsVisible: true }) opener.Focus();
        }
    }

    private void FocusBack()
    {
        if (Window.GetWindow(this)?.IsActive == true) Back.Focus();
    }
}
