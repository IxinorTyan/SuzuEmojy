using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using SuzuEmojy.Windows.Desktop;

internal static class DesktopTests
{
    internal static async Task CheckMainWindowPlacement(string output)
    {
        var primary = new DisplayArea("main", new(48, 0, 1872, 1040), true);
        var secondary = new DisplayArea("left", new(-1920, -280, 1920, 1040), false);
        var other = new DisplayArea("right", new(1920, 0, 1920, 1040), false);
        DisplayArea[] all = [primary, secondary, other];
        var bounds = new System.Drawing.Rectangle(-1500, -180, 810, 620);
        var saved = SavedWindowPlacement.From(bounds, secondary.Id);
        var size = new System.Drawing.Size(860, 640);

        Require(WindowPlacement.Resolve(saved, null, all, size) == saved,
            "A valid normal window on a negative-origin secondary display must restore exactly.");
        Require(WindowPlacement.Resolve(saved, null, [primary, secondary], size) == saved,
            "Disconnecting an unrelated monitor must not move the main window.");
        var centered = new SavedWindowPlacement(579, 210, 810, 620, primary.Id);
        Require(WindowPlacement.Resolve(saved, new System.Drawing.Rectangle(2200, 50, 810, 620), [primary, other], size) == centered,
            "A removed monitor must return to the primary work area, even if Windows moved it to another secondary first.");
        Require(WindowPlacement.Resolve(saved, new System.Drawing.Rectangle(-2800, -180, 810, 620), all, size) == centered,
            "A fully off-screen normal window must recover while all monitors are still connected.");
        var partiallyVisible = new System.Drawing.Rectangle(-2500, -180, 810, 620);
        Require(WindowPlacement.Resolve(saved, partiallyVisible, all, size)?.Bounds == partiallyVisible,
            "An intentionally partially visible window must not snap to the center.");
        Require(WindowPlacement.Resolve(null, new System.Drawing.Rectangle(0, 20, 48, 800), [primary], size)?.Display == primary.Id &&
            WindowPlacement.FindDisplay(new(0, 20, 48, 800), [primary]) is null,
            "Touching only the taskbar edge is not an intersection with the usable area.");
        var oversized = new SavedWindowPlacement(5000, 500, 3400, 1800, secondary.Id);
        Require(WindowPlacement.Resolve(oversized, null, [primary], size)?.Bounds == primary.WorkArea &&
            WindowPlacement.Resolve(saved, null, [], size) == saved,
            "Recovery must fit a smaller primary and preserve placement during an empty topology notification.");

        var folder = Path.Combine(output, $"placement-fixture-{Guid.NewGuid():N}");
        Directory.CreateDirectory(folder);
        var config = Path.Combine(folder, "config.json");
        const string qt = "{\"window_geometry\":[12,34,900,650]}";
        File.WriteAllText(config, qt);
        var store = new WindowPlacementStore(folder);
        Require(store.Read() is null && store.Write(saved) && store.Read() == saved, "Placement did not survive a file roundtrip.");
        var window = new Window { Width = 860, Height = 640, WindowStyle = WindowStyle.None };
        try
        {
            using var placement = new MainWindowPlacement(window, store, () => all);
            Require(!window.IsVisible && !window.IsActive && WindowPlacementNative.Bounds(placement.Handle) == bounds,
                $"Restoring a hidden window must position and resize its HWND without displaying or activating it: " +
                $"{WindowPlacementNative.Bounds(placement.Handle)}.");
            placement.BeforeShow();
            Require(store.Read() == saved, "Preparing the first show should not replace a valid saved placement.");
            all = [primary, other];
            SendMessageW(placement.Handle, 0x007E, 0, 0); // This process's own hidden HWND only.
            var time = System.Diagnostics.Stopwatch.StartNew();
            while (store.Read() != centered && time.Elapsed < TimeSpan.FromSeconds(3)) await Task.Delay(30);
            Require(!window.IsVisible && !window.IsActive && WindowPlacementNative.Bounds(placement.Handle) == centered.Bounds &&
                store.Read() == centered, "Display changes should repair a hidden window without making it visible.");

            var offscreen = new System.Drawing.Rectangle(-4000, -300, 750, 510);
            Require(WindowPlacementNative.Move(placement.Handle, offscreen), "Could not move the hidden test HWND.");
            placement.BeforeShow();
            var newCenter = new SavedWindowPlacement(609, 265, 750, 510, primary.Id);
            Require(!window.IsVisible && WindowPlacementNative.Bounds(placement.Handle) == newCenter.Bounds &&
                store.Read() == newCenter, "The pre-show check should repair a hidden HWND moved fully offscreen.");
            window.WindowState = WindowState.Maximized;
            placement.Flush();
            window.WindowState = WindowState.Minimized;
            placement.Flush();
            Require(store.Read() == newCenter && !window.IsVisible,
                "Maximized or minimized state must not overwrite the last normal rectangle.");
        }
        finally { window.Close(); }

        var restarted = new Window { Width = 860, Height = 640, WindowStyle = WindowStyle.None };
        try
        {
            using var placement = new MainWindowPlacement(restarted, new WindowPlacementStore(folder), () => all);
            Require(WindowPlacementNative.Bounds(placement.Handle) == store.Read()?.Bounds && !restarted.IsVisible,
                "A new window instance must restore the saved size and position.");
        }
        finally { restarted.Close(); }
        var placementFile = Path.Combine(folder, "window-placement.windows.json");
        File.WriteAllText(placementFile, "{broken");
        Require(store.Read() is null && File.ReadAllText(config) == qt,
            "A corrupt placement file should fall back without touching the shared Qt config.");
    }

    internal static void Run()
    {
        Require(HotkeyChord.TryParse(" Ctrl + SHIFT + e ", out var chord) && chord == new HotkeyChord(6, 'E'),
            "Cannot parse the legacy main-panel hotkey.");
        Require(HotkeyChord.TryParse("alt+2", out var digit) && digit == new HotkeyChord(1, '2') &&
            HotkeyChord.TryParse("win+f8", out var function) && function == new HotkeyChord(8, 0x77),
            "Digit and function chords should retain their virtual keys.");
        Require(HotkeyChord.TryFormat(6, 'E', out var recorded) && recorded == "ctrl+shift+e" &&
            HotkeyChord.TryFormat(1, 0xBC, out var comma) && HotkeyChord.TryParse(comma, out var punctuation) &&
            punctuation == new HotkeyChord(1, 0xBC), "Settings must write the same syntax the registrar reads.");
        Require(GlobalHotkey.Matches(0x312, 1, (nint)(('E' << 16) | 6), 1, chord) &&
            !GlobalHotkey.Matches(0x312, 2, (nint)(('E' << 16) | 6), 1, chord) &&
            !GlobalHotkey.Matches(0x312, 1, (nint)(('2' << 16) | 1), 1, chord),
            "A queued message for a different chord or panel must not toggle this panel.");
        Require(GlobalHotkey.TryRegister(null, 1, "", () => {}, out var disabled) is null && disabled is null &&
            GlobalHotkey.TryRegister(null, 1, "alt+2", () => {}, out var missing) is null && missing is not null,
            "Disabled shortcuts and failed registrations must have distinct statuses.");
        using (var host = GlobalHotkey.CreateMessageHost())
        {
            // Only dispatch to this process's own invisible HWND. No system shortcut,
            // foreground window, keyboard state or clipboard is touched by this test.
            bool routed = false;
            nint id = 42;
            nint keyData = ('2' << 16) | 1;
            host.AddHook((nint _, int msg, nint keyId, nint data, ref bool handled) =>
            {
                routed = handled = GlobalHotkey.Matches(msg, keyId, data, (int)id, digit);
                return 0;
            });
            SendMessageW(host.Handle, 0x0312, id, keyData);
            Require(routed, "The real message-only WPF host must receive and dispatch its hotkey message.");
        }
        foreach (string value in new[] { "", "e", "shift+e", "ctrl++e", "ctrl+ctrl+e", "ctrl+shift", "ctrl+f12", "ctrl+unknown" })
            Require(!HotkeyChord.TryParse(value, out _), $"Invalid or unsafe chord was accepted: {value}");
        Require(WindowsInput.InputSize == (IntPtr.Size == 8 ? 40 : 28), "Win32 INPUT layout is incorrect.");
        const string path = "D:\\表情\\动图.gif";
        byte[] drop = FileClipboard.DropFilesPayload(path);
        Require(BinaryPrimitives.ReadUInt32LittleEndian(drop.AsSpan(0, 4)) == 20 &&
            BinaryPrimitives.ReadUInt32LittleEndian(drop.AsSpan(16, 4)) == 1 &&
            Encoding.Unicode.GetString(drop.AsSpan(20)) == path + "\0\0", "CF_HDROP header or Unicode path is incorrect.");

        byte[] bgra = [10, 20, 30, 128, 40, 50, 60, 255];
        var image = BitmapSource.Create(1, 2, 96, 96, PixelFormats.Bgra32, null, bgra, 4);
        byte[] dib = StickerSendMedia.DibPayload(image);
        Require(BinaryPrimitives.ReadUInt32LittleEndian(dib) == 124 &&
                BinaryPrimitives.ReadUInt32LittleEndian(dib.AsSpan(4)) == 1 &&
                BinaryPrimitives.ReadInt32LittleEndian(dib.AsSpan(8)) == -2 &&
                BinaryPrimitives.ReadUInt32LittleEndian(dib.AsSpan(52)) == 0xFF000000 &&
                dib.AsSpan(124).SequenceEqual(bgra), "CF_DIBV5 must retain the image's orientation and straight alpha.");

        var area = new System.Drawing.Rectangle(-1920, 0, 1920, 1040);
        var panelSize = new System.Drawing.Size(360, 480);
        Require(CursorPlacement.Choose(new(-1900, 20), area, panelSize, 10) == new System.Drawing.Point(-1890, 30) &&
                CursorPlacement.Choose(new(-20, 1020), area, panelSize, 10) == new System.Drawing.Point(-390, 530) &&
                CursorPlacement.Choose(new(-2, 3), area, new(1900, 1036), 10) == new System.Drawing.Point(-1912, 0),
            "Quick panel should respect work areas and negative monitor coordinates.");

        var platform = new FakePlatform();
        var clock = new FakeClock();
        var paste = new AutoPaste(platform, clock);
        platform.Foreground = 1;
        Require(paste.CaptureForeground() is null, "Own window must not be captured.");
        platform.Foreground = 2;
        platform.Allowed = false;
        Require(paste.CaptureForeground() is null, "Shell/hidden windows must not be captured.");
        platform.Allowed = true;
        platform.FocusHandle = 22;
        var target = paste.CaptureForeground() ?? throw new InvalidOperationException("Could not capture target.");
        Require(target.FocusedControl == 22, "Capture the original focused control when it has its own HWND.");

        var wake = new PanelWake();
        foreach (nint panel in new nint[] { 1, 4 })
        {
            Require(wake.Consume(panel, panel) is null, "Normal opening must be clipboard-only.");
            wake.Begin(panel, target);
            Require(!wake.Confirm(panel, 2) && !wake.Activated && wake.Consume(panel, 3) is null,
                "Capture must survive delayed foreground activation without becoming sendable prematurely.");
            Require(wake.Confirm(panel, panel) && wake.Activated && wake.Consume(panel, panel) == target &&
                wake.Consume(panel, panel) is null, "Both panel hotkeys must keep their input target until the first successful handoff.");
        }
        wake.Begin(4, target);
        Require(wake.Consume(1, 1) is null, "One panel must never use another panel's target.");
        wake.Clear();

        foreach (nint panel in new nint[] { 1, 4 })
        {
            var sessionPlatform = new FakePlatform { Foreground = 2, ClipboardSequence = 10, FocusHandle = 22 };
            var sessionClock = new FakeClock();
            var sessionPaste = new AutoPaste(sessionPlatform, sessionClock);
            var sessionWake = new PanelWake();
            sessionWake.Begin(panel, sessionPaste.CaptureForeground());
            Require(!sessionWake.Confirm(panel, sessionPlatform.Foreground), "Showing a panel is not yet foreground activation.");
            sessionPlatform.Foreground = panel;
            sessionPlatform.ClipboardSequence++; // Copy selected sticker.
            var destination = sessionWake.Consume(panel, sessionPlatform.Foreground);
            Require(destination is { } captured && captured == target && sessionPaste.Begin(captured, panel, sessionPlatform.ClipboardSequence),
                "Both hotkey panels must hand their selected sticker back to the original input.");
            sessionClock.Now = 100;
            Require(sessionPaste.Tick() == PasteStatus.Pasted && sessionPaste.Tick() == PasteStatus.Idle &&
                sessionPlatform.Foreground == target.Handle && sessionPlatform.Pastes == 1,
                "Hotkey-to-selection flow must send the original input exactly one paste.");
        }

        platform.Foreground = 1;
        Require(paste.Begin(target, 1, 11) && platform.Restores == 1, "A valid request must restore the original window once.");
        clock.Now = 99;
        Require(paste.Tick() == PasteStatus.Waiting && platform.Pastes == 0, "Paste fired before the settle delay.");
        clock.Now = 100;
        platform.ModifiersDown = true;
        Require(paste.Tick() == PasteStatus.Waiting && platform.Pastes == 0, "Paste fired with a modifier held.");
        platform.ModifiersDown = false;
        Require(paste.Tick() == PasteStatus.Pasted && platform.Pastes == 1 &&
            paste.Tick() == PasteStatus.Idle, "A settled request should issue exactly one batch.");

        void Begin()
        {
            platform.Foreground = 1;
            Require(paste.Begin(target, 1, 11), "Could not start fake request.");
        }
        Begin();
        platform.Foreground = 3;
        Require(paste.Tick() == PasteStatus.Skipped, "Focus moved: pending paste must be dropped.");
        int restores = platform.Restores;
        Require(!paste.Begin(target, 1, 11) && platform.Restores == restores,
            "Must not steal focus from an unrelated foreground window.");
        platform.Foreground = 4;
        Require(!paste.Begin(target, 1, 11) && platform.Restores == restores,
            "The wrong Suzu panel cannot deliver a pending paste either.");

        Begin();
        platform.ClipboardSequence = 12;
        Require(paste.Tick() == PasteStatus.Skipped, "A subsequent clipboard write invalidates the request.");
        platform.ClipboardSequence = 11;
        Begin();
        platform.Windows[2] = new(33, 44);
        Require(paste.Tick() == PasteStatus.Skipped, "An HWND with a different owner must not receive a paste.");
        platform.Windows[2] = target.Identity;
        Begin();
        platform.FocusHandle = 23;
        clock.Now += 100;
        Require(paste.Tick() == PasteStatus.Skipped, "Don't paste if the focused child HWND changed.");
        platform.FocusHandle = 22;
        Begin();
        clock.Now += 300;
        Require(paste.Tick() == PasteStatus.Skipped, "An expired request must never paste.");

        Begin();
        Require(!paste.Begin(target, 1, 0) && paste.Tick() == PasteStatus.Idle, "A new request supersedes even if invalid.");
        Begin();
        paste.Cancel();
        Require(paste.Tick() == PasteStatus.Idle, "Canceled request remained queued.");
        platform.Foreground = 1;
        platform.RestoreAllowed = false;
        Require(paste.Begin(target, 1, 11) && paste.Tick() == PasteStatus.Waiting && platform.Pastes == 1,
            "Wait briefly for the operating system to restore focus when the panel hides.");
        platform.Foreground = 0;
        Require(paste.Tick() == PasteStatus.Waiting && platform.Pastes == 1, "A transient null foreground cannot receive input.");
        platform.Foreground = 2;
        clock.Now += 100;
        Require(paste.Tick() == PasteStatus.Pasted && platform.Pastes == 2,
            "Delayed restoration to the original input must still paste exactly once.");
        platform.Foreground = 1;
        Require(paste.Begin(target, 1, 11), "A failed activation attempt must start a bounded handoff.");
        clock.Now += 300;
        Require(paste.Tick() == PasteStatus.Skipped && platform.Pastes == 2,
            "A window that never regained focus must leave clipboard-only fallback.");

        platform.RestoreAllowed = true;
        Begin();
        clock.Now += 100;
        var modifierReads = 0;
        platform.ModifierRead = () => { if (++modifierReads == 2) platform.Foreground = 3; return false; };
        Require(paste.Tick() == PasteStatus.Skipped && platform.Pastes == 2,
            "Foreground must be rechecked immediately before injection.");
    }

    private sealed class FakeClock : TimeProvider
    {
        internal long Now;
        public override long TimestampFrequency => 1000;
        public override long GetTimestamp() => Now;
    }

    private sealed class FakePlatform : IPastePlatform
    {
        internal Dictionary<nint, WindowIdentity> Windows = new() { [1] = new(1, 1), [2] = new(2, 2), [3] = new(3, 3), [4] = new(1, 1) };
        internal nint FocusHandle;
        internal bool Allowed = true, RestoreAllowed = true, ModifiersDown;
        internal Func<bool>? ModifierRead;
        internal int Pastes, Restores;
        public uint OwnProcess => 1;
        public nint Foreground { get; set; }
        public uint ClipboardSequence { get; set; } = 11;
        bool IPastePlatform.ModifiersDown => ModifierRead?.Invoke() ?? ModifiersDown;
        public WindowIdentity? Identify(nint window) => Windows.TryGetValue(window, out var id) ? id : null;
        public bool IsPasteTarget(nint window) => Allowed && Windows.ContainsKey(window);
        public nint FocusedControl(PasteTarget target) => FocusHandle;
        public void RestoreForeground(nint window)
        {
            Restores++;
            if (RestoreAllowed) Foreground = window;
        }
        public bool SendPaste() { Pastes++; return true; }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    [DllImport("user32.dll", ExactSpelling = true)]
    private static extern nint SendMessageW(nint window, int message, nint wParam, nint lParam);
}
