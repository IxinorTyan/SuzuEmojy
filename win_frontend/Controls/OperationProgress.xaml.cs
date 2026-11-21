using System;
using System.Windows;
using System.Windows.Controls;

namespace SuzuEmojy.Windows.Controls;

public partial class OperationProgress : UserControl
{
    private Action? cancel;

    public OperationProgress()
    {
        InitializeComponent();
        CancelButton.Click += (_, _) => cancel?.Invoke();
    }

    internal void Begin(string title, string detail, Action? cancel = null, int waiting = 0)
    {
        this.cancel = cancel;
        Heading.Text = title;
        Detail.Text = detail;
        Bar.Value = 0;
        Bar.IsIndeterminate = true;
        CancelButton.IsEnabled = cancel is not null;
        CancelButton.Visibility = cancel is null ? Visibility.Collapsed : Visibility.Visible;
        IsHitTestVisible = cancel is not null;
        SetWaiting(waiting);
        Visibility = Visibility.Visible;
    }

    internal void SetWaiting(int count)
    {
        Waiting.Text = count > 0 ? $"等待中的任务：{count}" : "";
        Actions.Visibility = count > 0 || cancel is not null ? Visibility.Visible : Visibility.Collapsed;
    }

    internal void ShowCancelling()
    {
        CancelButton.IsEnabled = false;
        Detail.Text = "正在取消…";
    }

    internal void Report(LibraryProgress update)
    {
        if (Visibility != Visibility.Visible) return;
        Bar.IsIndeterminate = update.Total <= 0;
        if (!Bar.IsIndeterminate)
        {
            Bar.Maximum = update.Total;
            Bar.Value = RuntimePrimitives.Clamp(update.Current, 0, update.Total);
        }
        Detail.Text = update.Detail ?? (update.Phase == "import" ? $"正在处理: {update.Current} / {update.Total}" : Detail.Text);
    }

    internal void Dismiss()
    {
        cancel = null;
        IsHitTestVisible = false;
        Visibility = Visibility.Collapsed;
    }
}
