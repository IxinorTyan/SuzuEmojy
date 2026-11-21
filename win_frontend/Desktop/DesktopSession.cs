using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Threading;
using Microsoft.Win32;
using SuzuEmojy.Windows.Controls;

namespace SuzuEmojy.Windows.Desktop;

// Bridges the gallery window to the Windows shell; only the real entry point creates a session.
internal sealed class DesktopSession : IDisposable
{
    private readonly MainWindow window;
    private LibrarySnapshot settings;
    private readonly LibraryCommands commands;
    private readonly QuickPanelResults results;
    private StickerSendMedia sendMedia;
    private readonly string cache;
    private readonly WindowsInput input = new();
    private readonly AutoPaste paste;
    private readonly DispatcherTimer timer;
    private readonly DispatcherTimer wakeTimer;
    private readonly DispatcherTimer closeTimer;
    private readonly PanelWake wake = new();
    private Window? openingPanel;
    private readonly nint handle;
    private readonly MainWindowPlacement placement;
    private readonly InboxMonitor inbox;
    private readonly CancellationTokenSource maintenance = new();
    private readonly HwndSource hotkeyMessages;
    private readonly TrayIcon? tray;
    private GlobalHotkey? hotkey, quickHotkey;
    private int pendingRecents;
    private QuickPanel? quickPanel;
    private Action<Sticker>? quickActivate;
    private CancellationTokenSource? preparing;
    private string? mainHotkeyError, quickHotkeyError;
    private bool recordingHotkey;
    private bool disposed, exiting, exitRequested;

    internal DesktopSession(MainWindow window, LibrarySnapshot settings, string data, LibraryCommands commands)
    {
        this.window = window;
        this.settings = settings;
        this.commands = commands;
        cache = Path.Combine(data, "cache", "gif_output");
        results = new QuickPanelResults(settings);
        sendMedia = new StickerSendMedia(cache, settings.Flag("convert_static_to_gif", true));
        paste = new AutoPaste(input, TimeProvider.System);
        timer = new DispatcherTimer(DispatcherPriority.Input, window.Dispatcher) { Interval = TimeSpan.FromMilliseconds(20) };
        timer.Tick += OnPasteTick;
        wakeTimer = new DispatcherTimer(DispatcherPriority.Input, window.Dispatcher) { Interval = TimeSpan.FromMilliseconds(250) };
        wakeTimer.Tick += FinishActivation;
        closeTimer = new DispatcherTimer(DispatcherPriority.Background, window.Dispatcher) { Interval = TimeSpan.FromMilliseconds(100) };
        closeTimer.Tick += FinishClosing;
        placement = new MainWindowPlacement(window, new WindowPlacementStore(data));
        inbox = new InboxMonitor(data, window.Dispatcher, window.ImportInbox);
        handle = placement.Handle;
        // A message-only HWND keeps both shortcuts alive even while the main panel is hidden.
        hotkeyMessages = GlobalHotkey.CreateMessageHost();
        try { tray = TrayIcon.TryCreate(window.MenuPalette, ShowFromTray, OpenSettings, Exit); }
        catch (Exception error) when (error is ArgumentException or InvalidOperationException or Win32Exception)
        {
            Trace.TraceWarning("Cannot create tray icon: {0}", error.Message);
        }
        BindHotkeys();
        window.LibraryChanged += RefreshLibrary;
        window.HotkeyRecordingChanged += SetHotkeyRecording;
        window.StickerActivated += CopyMainSticker;
        window.Activated += WindowActivated;
        window.Deactivated += WindowDeactivated;
        window.Closing += WindowClosing;
        SystemEvents.UserPreferenceChanged += SystemPreferenceChanged;
    }

    private void SystemPreferenceChanged(object sender, UserPreferenceChangedEventArgs e)
    {
        // SystemEvents may call back on its own thread. Read the effective mode
        // on the UI thread; explicit light/dark modes are unaffected.
        window.Dispatcher.BeginInvoke(DispatcherPriority.Normal, new Action(() =>
        {
            if (disposed || window.AppliedDark == settings.Dark) return;
            window.RefreshAppearance();
            quickPanel?.RefreshAppearance(settings);
        }));
    }

    private void BindHotkeys()
    {
        if (recordingHotkey) return;
        hotkey?.Dispose();
        quickHotkey?.Dispose();
        hotkey = GlobalHotkey.TryRegister(hotkeyMessages, 1, settings.String("global_hotkey", "ctrl+shift+e"), ToggleFromHotkey,
            out var mainError);
        quickHotkey = GlobalHotkey.TryRegister(hotkeyMessages, 2, settings.String("quick_panel_hotkey", "alt+2"), ToggleQuickPanel,
            out var quickError);
        bool changed = mainError != mainHotkeyError || quickError != quickHotkeyError;
        mainHotkeyError = mainError;
        quickHotkeyError = quickError;
        window.ShowHotkeyStatus(mainError, quickError, announce: changed && window.IsVisible);
    }

    private void SetHotkeyRecording(bool recording)
    {
        if (recordingHotkey == recording) return;
        recordingHotkey = recording;
        if (!recording) { BindHotkeys(); return; }
        // Let the settings field receive even the two currently registered combinations.
        hotkey?.Dispose();
        quickHotkey?.Dispose();
        hotkey = quickHotkey = null;
    }

    private void RefreshLibrary(LibrarySnapshot snapshot)
    {
        results.Refresh(snapshot);
        bool bindingsChanged = settings.String("global_hotkey", "ctrl+shift+e") != snapshot.String("global_hotkey", "ctrl+shift+e") ||
            settings.String("quick_panel_hotkey", "alt+2") != snapshot.String("quick_panel_hotkey", "alt+2");
        bool sendFormatChanged = settings.Flag("convert_static_to_gif", true) != snapshot.Flag("convert_static_to_gif", true);
        settings = snapshot;
        if (sendFormatChanged) sendMedia = new StickerSendMedia(cache, settings.Flag("convert_static_to_gif", true));
        if (bindingsChanged) BindHotkeys();
        quickPanel?.RefreshFromLibrary(snapshot, window.QuickThumbnailSize);
    }

    private void OpenSettings()
    {
        if (window.LibraryBusy || !window.IsEnabled) return;
        ShowFromTray();
        window.ShowSettings();
    }

    internal void Start(bool autostart)
    {
        _ = WarmIndexes(maintenance.Token);
        inbox.Start();
        if (!autostart || tray is null)
        {
            placement.BeforeShow();
            window.Show();
            window.ShowHotkeyStatus(mainHotkeyError, quickHotkeyError, announce: true);
        }
        else if ((mainHotkeyError ?? quickHotkeyError) is { } message) tray.NotifyHotkeysUnavailable(message);
    }

    private async Task WarmIndexes(CancellationToken cancellation)
    {
        try
        {
            var repair = await commands.Run(new("startup_maintenance"), reload: true, cancellation);
            if (disposed || cancellation.IsCancellationRequested) return;
            if (repair.Result.Error is { } error) Trace.TraceWarning("Could not maintain the old library: {0}", error);
            if (repair.Snapshot is { } snapshot) window.AcceptSnapshot(snapshot, null, updateSettings: false, preserveSelection: true);
            await commands.Run(new("warm_indexes"), reload: false, cancellation);
        }
        catch (OperationCanceledException) { }
        catch (Exception error) { Trace.TraceWarning("Could not warm library indexes: {0}", error.Message); }
    }

    private void ShowMain()
    {
        placement.BeforeShow();
        if (!window.IsVisible) window.Show();
        window.Activate();
    }

    private void ShowFromTray()
    {
        CancelSend();
        ClearWake(); // A tray click is not a request to send to the previous app.
        quickPanel?.Hide();
        if (!window.ShowGalleryPage()) return;
        ShowMain();
    }

    private void ToggleFromHotkey()
    {
        if (window.RecordingHotkey) return;
        CancelSend();
        ClearWake();
        // Capture before hiding an active quick panel, since hiding may change the foreground.
        var target = paste.CaptureForeground();
        bool hideMain = window.IsVisible && window.WindowState != WindowState.Minimized && input.Foreground == handle;
        quickPanel?.Hide();
        if (hideMain)
        {
            placement.Flush();
            window.Hide();
            return;
        }
        if (!window.ShowGalleryPage()) return;
        BeginActivation(window, handle, target);
        ShowMain();
        ActivateFromHotkey(window, handle);
    }

    private void ToggleQuickPanel()
    {
        if (window.RecordingHotkey) return;
        CancelSend();
        ClearWake();
        var panel = quickPanel ?? CreateQuickPanel();
        if (panel.IsVisible) { panel.Hide(); return; }
        var target = paste.CaptureForeground();
        panel.ResetSearch();
        var panelHandle = CursorPlacement.Move(panel, panel.RequestedSize);
        BeginActivation(panel, panelHandle, target);
        panel.Show();
        ActivateFromHotkey(panel, panelHandle);
    }

    private QuickPanel CreateQuickPanel()
    {
        var panel = new QuickPanel(settings, results, commands: commands);
        quickPanel = panel;
        panel.ThumbnailSizeChanged += window.SetQuickThumbnailSize;
        quickActivate = sticker => CopySticker(panel, sticker, hide: true);
        panel.StickerActivated += quickActivate;
        panel.Activated += WindowActivated;
        panel.Deactivated += WindowDeactivated;
        panel.IsVisibleChanged += QuickVisibilityChanged;
        panel.Closing += QuickClosing;
        return panel;
    }

    private void QuickVisibilityChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        if (e.NewValue is false && sender is QuickPanel hidden && WakeBelongsTo(hidden)) ClearWake();
    }

    private void QuickClosing(object? sender, CancelEventArgs e)
    {
        if (exiting || sender is not QuickPanel panel) return;
        e.Cancel = true; // Alt+F4 dismisses the panel without invalidating the reusable HWND.
        panel.Hide();
    }

    private void BeginActivation(Window panel, nint panelHandle, PasteTarget? target)
    {
        openingPanel = panel;
        wake.Begin(panelHandle, target);
        wakeTimer.Start();
    }

    private void ActivateFromHotkey(Window panel, nint panelHandle)
    {
        // Invoke synchronously from WM_HOTKEY, while Windows is processing the user's gesture.
        if (input.Foreground != panelHandle) input.RestoreForeground(panelHandle);
        panel.Activate();
        ConfirmActivation(panel);
    }

    private bool ConfirmActivation(Window panel)
    {
        if (!ReferenceEquals(panel, openingPanel) || !wake.Confirm(new WindowInteropHelper(panel).Handle, input.Foreground))
            return false;
        openingPanel = null;
        wakeTimer.Stop();
        if (panel is QuickPanel quick) quick.FocusSearch();
        return true;
    }

    private void FinishActivation(object? sender, EventArgs e)
    {
        wakeTimer.Stop();
        var panel = openingPanel;
        if (panel is null || ConfirmActivation(panel)) return;
        ClearWake();
        if (panel is QuickPanel quick) quick.Hide();
    }

    private void CopyMainSticker(Sticker sticker) =>
        CopySticker(window, sticker, settings.Flag("hide_main_after_paste", false) && (tray is not null || hotkey is not null));

    private async void CopySticker(Window panel, Sticker sticker, bool hide)
    {
        CancelSend();
        var panelHandle = new WindowInteropHelper(panel).Handle;
        if (input.Foreground != panelHandle) { ClearWake(); return; }
        uint baseline = input.ClipboardSequence;
        using var request = new CancellationTokenSource();
        preparing = request;
        FileClipboard.CopyResult copied = FileClipboard.CopyResult.Failed;
        uint sequence = 0;
        try
        {
            var media = await sendMedia.Prepare(sticker.Path, request.Token);
            for (int attempt = 0; attempt < 4; attempt++)
            {
                if (request.IsCancellationRequested || disposed || input.Foreground != panelHandle) return;
                copied = FileClipboard.TryCopy(panelHandle, media, baseline, out sequence);
                if (copied != FileClipboard.CopyResult.Busy || attempt == 3) break;
                await Task.Delay(20, request.Token);
            }
        }
        catch (OperationCanceledException) { return; }
        finally { if (ReferenceEquals(preparing, request)) preparing = null; }

        if (copied != FileClipboard.CopyResult.Copied)
        {
            if (copied != FileClipboard.CopyResult.Superseded)
                MessageBox.Show(panel, "无法复制图片，请检查文件后重试。", "复制失败");
            return;
        }
        var target = wake.Consume(panelHandle, input.Foreground);
        ClearWake();
        results.Record(sticker);
        bool armed = target is { } destination && paste.Begin(destination, panelHandle, sequence);
        if (hide)
        {
            if (ReferenceEquals(panel, window)) placement.Flush();
            panel.Hide();
        }
        if (armed) timer.Start();
        else if (target is not null) tray?.NotifyPasteSkipped();
        SaveRecent(sticker);
    }

    private async void SaveRecent(Sticker sticker)
    {
        pendingRecents++;
        try
        {
            await window.RecordRecent(sticker, settings.Number("recent_limit", 30, 1, 300));
        }
        catch (Exception error) { Trace.TraceWarning("Cannot save recent sticker: {0}", error.Message); }
        finally
        {
            pendingRecents--;
            if (exitRequested && !disposed && pendingRecents == 0) Exit();
        }
    }

    private void OnPasteTick(object? sender, EventArgs e)
    {
        var status = paste.Tick();
        if (status == PasteStatus.Waiting) return;
        timer.Stop();
        if (status == PasteStatus.Skipped) tray?.NotifyPasteSkipped();
    }

    private void CancelSend()
    {
        preparing?.Cancel();
        preparing = null;
        timer.Stop();
        paste.Cancel();
    }

    private void ClearWake()
    {
        wakeTimer.Stop();
        openingPanel = null;
        wake.Clear();
    }

    private bool WakeBelongsTo(Window panel) => wake.Panel != 0 && wake.Panel == new WindowInteropHelper(panel).Handle;

    private void WindowActivated(object? sender, EventArgs e)
    {
        CancelSend();
        if (sender is Window panel) ConfirmActivation(panel);
    }

    private void WindowDeactivated(object? sender, EventArgs e)
    {
        preparing?.Cancel();
        preparing = null;
        if (sender is Window panelWindow && WakeBelongsTo(panelWindow))
        {
            if (!wake.Activated) return; // Transient deactivation before the hotkey show finishes.
            ClearWake();
        }
        if (sender is QuickPanel { IsVisible: true } panel) panel.Hide();
    }

    private void WindowClosing(object? sender, CancelEventArgs e)
    {
        placement.Flush();
        window.FlushSettings();
        if (DefersWindowClose(exiting, window.HasPendingWrite, pendingRecents))
        {
            // FlushSettings can start a write during Closing. Preserve the
            // request so the user does not have to click × again.
            e.Cancel = true;
            closeTimer.Start();
            return;
        }
        closeTimer.Stop();
        CancelSend();
        ClearWake();
        if (exiting || tray is null) return;
        e.Cancel = true;
        window.Hide();
    }

    internal static bool DefersWindowClose(bool exiting, bool hasPendingWrite, int pendingRecents) =>
        !exiting && (hasPendingWrite || pendingRecents != 0);

    private void FinishClosing(object? sender, EventArgs e)
    {
        if (DefersWindowClose(exiting, window.HasPendingWrite, pendingRecents)) return;
        closeTimer.Stop();
        window.Close();
    }

    private void Exit()
    {
        placement.Flush();
        window.FlushSettings();
        if (window.HasPendingWrite || !window.IsEnabled) return;
        if (pendingRecents != 0) { exitRequested = true; return; }
        exiting = true;
        Application.Current.Shutdown();
    }

    public void Dispose()
    {
        if (disposed) return;
        disposed = true;
        SystemEvents.UserPreferenceChanged -= SystemPreferenceChanged;
        placement.Dispose();
        inbox.Dispose();
        maintenance.Cancel();
        maintenance.Dispose();
        CancelSend();
        ClearWake();
        timer.Tick -= OnPasteTick;
        closeTimer.Stop();
        closeTimer.Tick -= FinishClosing;
        wakeTimer.Tick -= FinishActivation;
        window.StickerActivated -= CopyMainSticker;
        window.Activated -= WindowActivated;
        window.Deactivated -= WindowDeactivated;
        window.Closing -= WindowClosing;
        window.LibraryChanged -= RefreshLibrary;
        window.HotkeyRecordingChanged -= SetHotkeyRecording;
        ReleaseQuickPanel();
        quickHotkey?.Dispose();
        hotkey?.Dispose();
        hotkeyMessages.Dispose();
        tray?.Dispose();
    }

    private void ReleaseQuickPanel()
    {
        if (quickPanel is { } panel)
        {
            if (WakeBelongsTo(panel)) ClearWake();
            panel.StickerActivated -= quickActivate;
            panel.ThumbnailSizeChanged -= window.SetQuickThumbnailSize;
            panel.Activated -= WindowActivated;
            panel.Deactivated -= WindowDeactivated;
            panel.IsVisibleChanged -= QuickVisibilityChanged;
            panel.Closing -= QuickClosing;
            if (!disposed) panel.Close();
            quickPanel = null;
        }
    }
}
