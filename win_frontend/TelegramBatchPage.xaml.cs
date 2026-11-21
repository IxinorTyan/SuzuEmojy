using System;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;

namespace SuzuEmojy.Windows;

public partial class TelegramBatchPage : UserControl
{
    private readonly LibraryCommand requestTemplate;
    private readonly Func<LibraryCommand, bool, CancellationToken, IProgress<LibraryProgress>?,
        Task<(LibraryResult Result, LibrarySnapshot? Snapshot)>> run;
    private readonly Action<LibrarySnapshot> accept;
    private readonly ObservableCollection<BatchRow> rows = [];
    private readonly OperationScope work = new();
    internal OperationScope PageWork => work;
    internal bool IsRunning => work.IsBusy;

    internal TelegramBatchPage(LibraryCommand request, Func<LibraryCommand, bool, CancellationToken, IProgress<LibraryProgress>?,
        Task<(LibraryResult Result, LibrarySnapshot? Snapshot)>> run, Func<string, string?> chooseFolder,
        Action<LibrarySnapshot> accept, Action<string> showDetails)
    {
        requestTemplate = request;
        this.run = run;
        this.accept = accept;
        InitializeComponent();
        Packages.ItemsSource = rows;
        Browse.Click += (_, _) =>
        {
            if (chooseFolder("选择资源包保存目录") is { } folder) Folder.Text = folder;
        };
        Links.TextChanged += (_, _) => UpdateStart();
        Folder.TextChanged += (_, _) => UpdateStart();
        Start.Click += async (_, _) => await RunBatch();
        Cancel.Click += (_, _) => RequestCancel();
        Packages.MouseDoubleClick += (_, e) =>
        {
            if (e.OriginalSource is DependencyObject source &&
                ItemsControl.ContainerFromElement(Packages, source) is ListBoxItem { Content: BatchRow row } &&
                row.Detail.Length > 0) showDetails(row.Detail);
        };
        Packages.KeyDown += (_, e) =>
        {
            if (e.Key == Key.Enter && Packages.SelectedItem is BatchRow { Detail.Length: > 0 } row)
            {
                e.Handled = true;
                showDetails(row.Detail);
            }
        };
        UpdateStart();
    }

    private void UpdateStart() => Start.IsEnabled =
        !IsRunning && !string.IsNullOrWhiteSpace(Links.Text) && !string.IsNullOrWhiteSpace(Folder.Text);

    internal void RequestCancel()
        => work.RequestCancel();

    private void ShowCancelling()
    {
        Cancel.IsEnabled = false;
        Status.Text = "正在取消，已完成的 ZIP 将保留。";
    }

    internal async Task RunBatch()
    {
        if (!Start.IsEnabled || IsRunning) return;
        var links = Links.Text.Split(["\r\n", "\r", "\n"], StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);
        var destination = Folder.Text.Trim();
        if (links.Length == 0 || destination.Length == 0) return;
        rows.Clear();
        foreach (string link in links)
            rows.Add(new BatchRow(string.IsNullOrEmpty(requestTemplate.BotToken) ? link : link.Replace(requestTemplate.BotToken, "***")));
        using var operation = work.Begin(LeavePolicy.CancelAndWait, ShowCancelling);
        Inputs.IsEnabled = false;
        Cancel.IsEnabled = true;
        Cancel.Visibility = Visibility.Visible;
        UpdateStart();
        Status.Text = "";
        Work.Begin("正在批量打包…", "");
        int completed = 0;
        ReportTotal();
        try
        {
            var progress = operation.Progress(update =>
            {
                if (update.Row is not int index || index < 0 || index >= rows.Count) return;
                var row = rows[index];
                if (row.IsFinal) return;
                row.Apply(update.Phase, update.Current, update.Total, update.Detail, update.Name);
                if (row.IsFinal) { completed++; ReportTotal(); }
            });
            var (result, snapshot) = await run(requestTemplate with { Links = links, Destination = destination },
                true, operation.Token, progress);
            operation.StopReporting();
            if (snapshot is not null) accept(snapshot);
            if (result.Error is { } error) FinishUnreported("failed", error);
            else
            {
                foreach (var row in result.BatchRows ?? [])
                    if (row.Index >= 0 && row.Index < rows.Count) rows[row.Index].Apply(row.Status, 0, 0, row.Message);
                Status.Text = $"批量处理结束：成功 {result.Count} 包，失败 {result.Failed} 包，取消 {result.Cancelled} 包。";
            }
        }
        catch (OperationCanceledException) when (operation.Token.IsCancellationRequested)
        {
            FinishUnreported("cancelled", "已取消");
        }
        catch (Exception)
        {
            FinishUnreported("failed", "批量打包失败");
        }
        finally
        {
            operation.Dispose();
            Inputs.IsEnabled = true;
            Cancel.Visibility = Visibility.Collapsed;
            Work.Dismiss();
            UpdateStart();
        }

        void ReportTotal() => Work.Report(new("batch", completed, rows.Count, $"{completed} / {rows.Count}"));

        void FinishUnreported(string stage, string message)
        {
            foreach (var row in rows.Where(row => !row.IsFinal)) row.Apply(stage, 0, 0, message);
            Status.Text = message;
        }
    }

    internal sealed class BatchRow(string link) : INotifyPropertyChanged
    {
        public string Link { get; } = link;
        public string Title { get; private set; } = link;
        public string Stage { get; private set; } = "等待中";
        public string Progress { get; private set; } = "";
        public string Detail { get; private set; } = "";
        public string Summary => Detail.Split('\n', 2)[0];
        internal bool IsFinal { get; private set; }
        public event PropertyChangedEventHandler? PropertyChanged;

        internal void Apply(string phase, int done, int total, string? detail, string? title = null)
        {
            if (!string.IsNullOrWhiteSpace(title)) Title = title;
            Stage = phase switch
            {
                "parse" => "解析中", "parsed" => "等待下载", "download" => "下载中", "ready" => "等待入库",
                "import" => "入库中", "export" => "打包中", "done" => "已完成", "failed" => "失败",
                "cancelled" => "已取消", _ => Stage
            };
            IsFinal = phase is "done" or "failed" or "cancelled";
            Progress = total > 0 && !IsFinal ? $"{done}/{total}" : "";
            Detail = detail ?? "";
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(null));
        }
    }
}
