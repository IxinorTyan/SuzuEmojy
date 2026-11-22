using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using QRCoder;
using SuzuEmojy.Windows.Controls;

namespace SuzuEmojy.Windows;

internal sealed record LanImportResult(int Added, int Duplicates, int Failed, int Warnings);

internal sealed class LanSyncDialog
{
    private readonly FluentDialog dialog;
    private readonly LanSyncView view = new();
    private readonly Button sync, send, receive, approve, decline;
    private readonly Func<string, CancellationToken, Task> export;
    private readonly Func<string, CancellationToken, Task<LanImportResult>> import;
    private readonly Func<Stream, Action<string>, CancellationToken, bool, Task> synchronize;
    private TaskCompletionSource<bool>? decision;
    private LanSyncHost? host;
    private bool closed;

    private LanSyncDialog(Window owner, LibrarySnapshot library, Func<string, CancellationToken, Task> export,
        Func<string, CancellationToken, Task<LanImportResult>> import, Func<Stream, Action<string>, CancellationToken, bool, Task> synchronize)
    {
        this.export = export;
        this.import = import;
        this.synchronize = synchronize;
        dialog = new(owner, library, "局域网同步", width: 460);
        dialog.Add(view);
        sync = dialog.Button("同步两端", () => Request(LanTransfer.Sync));
        send = dialog.Button("发送到手机", () => Request(LanTransfer.SendToPhone));
        receive = dialog.Button("从手机接收", () => Request(LanTransfer.ReceiveFromPhone));
        approve = dialog.Button("确认", () => decision?.TrySetResult(true));
        decline = dialog.Button("拒绝", () => decision?.TrySetResult(false));
        dialog.Button("关闭连接", dialog.Close);
        SetActions(false);
        ApproveButtons(false);
        dialog.Closed += (_, _) => { closed = true; host?.Cancel(); };
        dialog.Loaded += async (_, _) => await Start();
    }

    internal static void Show(Window owner, LibrarySnapshot library, Func<string, CancellationToken, Task> export,
        Func<string, CancellationToken, Task<LanImportResult>> import, Func<Stream, Action<string>, CancellationToken, bool, Task> sync) =>
        new LanSyncDialog(owner, library, export, import, sync).dialog.ShowModal();

    private async Task Start()
    {
        try
        {
            using var opened = await Task.Run(() => new LanSyncHost());
            if (closed) return;
            host = opened;
            view.Qr.Source = PairingQr(opened.QrContent);
            view.Pairing.Visibility = view.Code.Visibility = Visibility.Visible;
            view.Code.Text = $"配对码：{opened.Code}";
            view.Addresses.Text = string.Join("\n", opened.Addresses);
            view.Status.Text = opened.CanDiscover ? "等待手机配对（2 分钟内有效）" : "自动发现不可用，请扫码或输入地址和配对码。";
            await opened.Run(
                (code, token) => Ask("请核对手机上的校验码。", "校验码一致", code, token), export, Import,
                message => Post(() => view.Status.Text = message),
                (transfer, token) => transfer switch
                {
                    LanTransfer.Sync => Ask("手机请求同步两端图库。", "允许同步", null, token),
                    LanTransfer.SendToPhone => Ask("手机请求接收电脑的资源包。", "允许发送", null, token),
                    _ => Ask("手机请求发送资源包到电脑。", "允许接收", null, token)
                },
                ready => Post(() => { if (ready) view.Code.Visibility = Visibility.Collapsed; SetActions(ready); }),
                (stream, status, token) => synchronize(stream, status, token, opened.StreamingSync), streamingSync: true);
            if (!closed) view.Status.Text = "连接已结束。";
        }
        catch (Exception ex) { if (!closed) view.Status.Text = $"连接已结束：{ex.Message}"; }
        finally
        {
            host = null;
            if (!closed)
            {
                view.Pairing.Visibility = view.Code.Visibility = Visibility.Collapsed;
                ApproveButtons(false);
                SetActions(false);
            }
        }
    }

    private async Task<bool> Ask(string message, string label, string? verification, CancellationToken token)
    {
        var next = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        using var registration = token.Register(() => next.TrySetResult(false));
        await dialog.Dispatcher.InvokeAsync(() =>
        {
            if (closed || token.IsCancellationRequested) return;
            decision = next;
            view.Pairing.Visibility = Visibility.Collapsed;
            if (verification is not null)
            {
                view.Code.Visibility = Visibility.Visible;
                view.Code.Text = $"校验码：{verification}";
            }
            view.Status.Text = message;
            approve.Content = label;
            ApproveButtons(true);
        });
        try { return await next.Task.ConfigureAwait(false); }
        finally
        {
            await dialog.Dispatcher.InvokeAsync(() =>
            {
                if (closed) return;
                decision = null;
                ApproveButtons(false);
                if (verification is not null && next.Task.Result) view.Status.Text = "等待手机确认…";
            });
        }
    }

    private void Request(LanTransfer transfer)
    {
        if (host?.TryRequest(transfer) != true) return;
        SetActions(false);
        view.Status.Text = transfer == LanTransfer.Sync ? "等待手机确认同步…" : "等待手机确认传输…";
    }

    private async Task<int> Import(string path, CancellationToken token)
    {
        var result = await import(path, token).ConfigureAwait(false);
        Post(() => view.Status.Text = $"电脑导入完成：新增 {result.Added}，重复 {result.Duplicates}，失败 {result.Failed}");
        return result.Failed > 0 || result.Warnings > 0 ? 2 : 1;
    }

    private void Post(Action change) => _ = dialog.Dispatcher.InvokeAsync(() => { if (!closed) change(); });
    private void SetActions(bool visible)
    {
        send.Visibility = receive.Visibility = visible ? Visibility.Visible : Visibility.Collapsed;
        sync.Visibility = visible && host?.CanSync == true ? Visibility.Visible : Visibility.Collapsed;
    }
    private void ApproveButtons(bool visible) => approve.Visibility = decline.Visibility = visible ? Visibility.Visible : Visibility.Collapsed;

    private static ImageSource PairingQr(string content)
    {
        using var generator = new QRCodeGenerator();
        using var qr = generator.CreateQrCode(content, QRCodeGenerator.ECCLevel.M);
        var bytes = new PngByteQRCode(qr).GetGraphic(6);
        using var stream = new MemoryStream(bytes);
        var bitmap = new BitmapImage();
        bitmap.BeginInit();
        bitmap.CacheOption = BitmapCacheOption.OnLoad;
        bitmap.StreamSource = stream;
        bitmap.EndInit();
        bitmap.Freeze();
        return bitmap;
    }
}
