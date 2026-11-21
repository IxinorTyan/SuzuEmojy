using System;
using System.Linq;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Threading;
using Rectangle = System.Drawing.Rectangle;
using Size = System.Drawing.Size;

namespace SuzuEmojy.Windows.Desktop;

// Owns placement only for the main window. Quick-panel placement still follows the cursor.
internal sealed class MainWindowPlacement : IDisposable
{
    private readonly Window window;
    private readonly HwndSource source;
    private readonly DispatcherTimer settle;
    private readonly WindowPlacementStore store;
    private readonly Func<DisplayArea[]> displays;
    private readonly Size defaultSize;
    private SavedWindowPlacement? normal, persisted;
    private Rectangle? observedNormal;
    private string? maximizedDisplay;
    private bool moving, applying, needsPlacement = true;

    internal nint Handle { get; }

    internal MainWindowPlacement(Window window, WindowPlacementStore store, Func<DisplayArea[]>? displays = null)
    {
        this.window = window;
        this.store = store;
        this.displays = displays ?? WindowPlacementNative.Displays;
        normal = persisted = store.Read();
        window.WindowStartupLocation = WindowStartupLocation.Manual;
        Handle = new WindowInteropHelper(window).EnsureHandle();
        source = HwndSource.FromHwnd(Handle);
        defaultSize = WindowPlacementNative.Bounds(Handle)?.Size ?? new Size(860, 640);
        if (defaultSize.Width <= 0 || defaultSize.Height <= 0) defaultSize = new Size(860, 640);

        settle = new DispatcherTimer(DispatcherPriority.Background, window.Dispatcher)
        {
            Interval = TimeSpan.FromMilliseconds(250),
        };
        settle.Tick += OnSettled;
        source.AddHook(OnMessage);
        window.LocationChanged += OnWindowChanged;
        window.SizeChanged += OnWindowChanged;
        window.StateChanged += OnWindowChanged;
        Flush();
    }

    private bool IsWindowed => window.WindowState == WindowState.Normal && WindowPlacementNative.IsWindowed(Handle);

    private void OnWindowChanged(object? sender, EventArgs e)
    {
        if (applying) return;
        // Cache the last normal rectangle even if the user immediately
        // maximizes/minimizes before the debounce expires.
        if (window.IsVisible && IsWindowed && !needsPlacement) observedNormal = WindowPlacementNative.Bounds(Handle);
        Schedule();
    }

    private void Schedule()
    {
        settle.Stop();
        if (!moving && !applying) settle.Start();
    }

    private nint OnMessage(nint hwnd, int message, nint wParam, nint lParam, ref bool handled)
    {
        switch (message)
        {
            case 0x0231: // WM_ENTERSIZEMOVE: do not interrupt a drag across a gap between monitors.
                moving = true;
                settle.Stop();
                break;
            case 0x0232: // WM_EXITSIZEMOVE
                moving = false;
                Schedule();
                break;
            case 0x007E: // WM_DISPLAYCHANGE
            case 0x02E0: // WM_DPICHANGED
            case 0x001A when wParam == 0x002F: // WM_SETTINGCHANGE / SPI_SETWORKAREA
            case 0x0219 when wParam == 0x0007: // WM_DEVICECHANGE / DBT_DEVNODES_CHANGED
                Schedule();
                break;
        }
        return 0;
    }

    private void OnSettled(object? sender, EventArgs e) => Flush();

    internal void Flush() => Reconcile(beforeShow: false);

    internal void BeforeShow()
    {
        if (window.WindowState == WindowState.Minimized) window.WindowState = WindowState.Normal;
        Reconcile(beforeShow: true);
    }

    private void Reconcile(bool beforeShow)
    {
        settle.Stop();
        if (applying || moving) return;
        var activeDisplays = displays();
        if (activeDisplays.Length == 0) return;
        var nativeBounds = WindowPlacementNative.Bounds(Handle);
        bool windowed = IsWindowed;
        var observed = needsPlacement ? null : windowed && (window.IsVisible || beforeShow) ? nativeBounds : observedNormal;
        observedNormal = null;

        bool visibleMax = window.IsVisible && window.WindowState == WindowState.Maximized;
        var actualMax = visibleMax && nativeBounds is { } maxBounds
            ? WindowPlacement.FindDisplay(maxBounds, activeDisplays, maximizedDisplay) : null;
        bool strandedMax = visibleMax && (actualMax is null ||
            maximizedDisplay is not null && !activeDisplays.Any(display => display.Id == maximizedDisplay));
        var desired = strandedMax
            ? WindowPlacement.Resolve(null, null, activeDisplays, normal?.Bounds.Size ?? defaultSize)
            : WindowPlacement.Resolve(normal, observed, activeDisplays, defaultSize);
        if (desired is null) return;

        needsPlacement |= strandedMax || desired.Bounds != (observed ?? normal?.Bounds);
        normal = desired;
        if (needsPlacement && windowed)
        {
            applying = true;
            try
            {
                needsPlacement = !WindowPlacementNative.Move(Handle, desired.Bounds);
            }
            finally { applying = false; }
        }
        else if (window.WindowState == WindowState.Maximized && (strandedMax ||
            (beforeShow && !window.IsVisible && needsPlacement)))
        {
            var target = activeDisplays.First(display => display.Id == desired.Display);
            // Move the maximized HWND without activating it or restoring it. Apply
            // the saved normal rectangle only when it returns to the normal state.
            applying = true;
            try
            {
                if (WindowPlacementNative.Move(Handle, target.WorkArea)) maximizedDisplay = target.Id;
            }
            finally { applying = false; }
        }
        else maximizedDisplay = actualMax?.Id ?? (window.WindowState == WindowState.Normal ? null : maximizedDisplay);
        if (normal != persisted && store.Write(normal)) persisted = normal;
    }

    public void Dispose()
    {
        settle.Stop();
        settle.Tick -= OnSettled;
        if (!source.IsDisposed) source.RemoveHook(OnMessage);
        window.LocationChanged -= OnWindowChanged;
        window.SizeChanged -= OnWindowChanged;
        window.StateChanged -= OnWindowChanged;
    }
}
