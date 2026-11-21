using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using Microsoft.Win32;
using SuzuEmojy.Windows.Desktop;

namespace SuzuEmojy.Windows;

public partial class AboutPage : UserControl
{
    private readonly Func<CancellationToken, Task<AvailableRelease?>> request;
    private CancellationTokenSource? checking;
    private AvailableRelease? available;
    private readonly OperationScope work = new();
    internal OperationScope PageWork => work;
    internal static readonly string InstalledVersion = (typeof(AboutPage).Assembly
        .GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion ?? "2.0.0-preview").Split('+')[0];

    internal AboutPage(WindowsReleaseService updates) : this(cancellation => updates.Check(InstalledVersion, cancellation))
    {
        DownloadUpdate.Click += async (_, _) => await Download(updates);
    }

    internal AboutPage(Func<CancellationToken, Task<AvailableRelease?>> request, Action<string>? launchLink = null,
        Action<string>? copyText = null)
    {
        this.request = request;
        InitializeComponent();
        VersionText.Text = "版本 " + InstalledVersion;
        CheckUpdate.Click += async (_, _) => await Check();
        Link(OpenProject, DesktopLinks.Project);
        Link(OpenAuthor, DesktopLinks.Author);
        Link(OpenReleases, WindowsReleaseService.Releases);
        Link(OpenLicense, DesktopLinks.Project + "/blob/main/LICENSE");
        CopyGroup.Click += (_, _) =>
        {
            try { (copyText ?? Clipboard.SetText)("834586488"); }
            catch (System.Runtime.InteropServices.ExternalException error) { Status.Show("复制群号", error.Message, warning: true); }
        };
        foreach (var (name, author) in new[] { ("PyQt-Fluent-Widgets", "zhiyiYo"), ("QQFavoriteExtract", "VanillaNahida"),
            ("tg_sticker_downloader", "Kiowx") })
        {
            var entry = new Button { Content = name, Style = (Style)FindResource("FluentPushButton"),
                HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 0, 0, 6) };
            Link(entry, $"https://github.com/{author}/{name}");
            Credits.Children.Add(entry);
        }

        void Link(Button button, string url) => button.Click += (_, _) =>
        {
            if (launchLink is not null) launchLink(url);
            else DesktopLinks.Open(url, error => Status.Show("打开链接失败", error, warning: true));
        };
    }

    private async Task Check()
    {
        if (checking is not null) return;
        var operation = new CancellationTokenSource(TimeSpan.FromSeconds(38));
        checking = operation;
        CheckUpdate.IsEnabled = false;
        DownloadUpdate.Visibility = Visibility.Collapsed;
        available = null;
        Status.Show("检查更新", "正在检查…", persistent: true);
        Notes.Text = "";
        try
        {
            var release = await request(operation.Token);
            if (!ReferenceEquals(checking, operation)) return;
            if (release is null) Status.Show("检查更新", "没有可用的 Windows 2.x 更新。", persistent: true);
            else
            {
                Status.Show("发现新版本", release.Version, persistent: true);
                Notes.Text = release.Notes;
                available = release;
                if (release.Name is not null) DownloadUpdate.Visibility = Visibility.Visible;
            }
        }
        catch (OperationCanceledException)
        {
            if (ReferenceEquals(checking, operation)) Status.Show("检查更新失败", "检查更新超时。", warning: true);
        }
        catch (Exception error)
        {
            if (ReferenceEquals(checking, operation))
                Status.Show("检查更新失败", "暂时无法读取发布信息，可以重试或打开发布页。", warning: true);
            Trace.TraceWarning("Release check failed: {0}", error.Message);
        }
        finally
        {
            operation.Dispose();
            if (ReferenceEquals(checking, operation)) { checking = null; CheckUpdate.IsEnabled = true; }
        }
    }

    private async Task Download(WindowsReleaseService updates)
    {
        if (checking is not null || work.IsBusy || available is not { Name: { } name } release) return;
        var picker = new SaveFileDialog { FileName = name, Filter = "ZIP|*.zip", OverwritePrompt = true };
        if (picker.ShowDialog(Window.GetWindow(this)) != true) return;
        using var operation = work.Begin(LeavePolicy.CancelAndWait, DownloadProgress.ShowCancelling);
        CheckUpdate.IsEnabled = DownloadUpdate.IsEnabled = false;
        Status.Dismiss();
        DownloadProgress.Begin("下载更新", "正在校验发布信息…", () => work.RequestCancel());
        try
        {
            await updates.Save(InstalledVersion, release.Version, picker.FileName,
                operation.Progress(update =>
                {
                    if (!operation.Token.IsCancellationRequested)
                        DownloadProgress.Report(update with { Detail = update.Detail ?? $"正在下载：{update.Current}%" });
                }), operation.Token);
            Status.Show("下载及校验完成", "请解压到新目录；不会覆盖当前安装或图库。", persistent: true);
        }
        catch (Exception error)
        {
            if (!operation.Token.IsCancellationRequested)
                Status.Show("下载更新失败", "可以重试或打开发布页。", warning: true, persistent: true);
            Trace.TraceWarning("Release download failed: {0}", error.Message);
        }
        finally
        {
            operation.StopReporting();
            DownloadProgress.Dismiss();
            CheckUpdate.IsEnabled = DownloadUpdate.IsEnabled = true;
        }
    }

    internal void Cancel()
    {
        var operation = checking;
        checking = null;
        operation?.Cancel();
        Status.Dismiss();
    }
}
