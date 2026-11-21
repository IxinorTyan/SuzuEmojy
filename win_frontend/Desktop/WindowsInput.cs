using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

namespace SuzuEmojy.Windows.Desktop;

internal sealed class WindowsInput : IPastePlatform
{
    private static readonly HashSet<string> ShellClasses = new(StringComparer.OrdinalIgnoreCase)
    {
        "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "CabinetWClass",
        "ExploreWClass", "Windows.UI.Core.CoreWindow", "Windows.UI.Composition.DesktopWindowContentBridge",
    };
    private static readonly int[] ModifierKeys = [0x10, 0x11, 0x12, 0x5B, 0x5C];

    public nint Foreground => GetForegroundWindow();
    public uint OwnProcess => (uint)Environment.ProcessId;
    public uint ClipboardSequence => FileClipboard.Sequence;
    public bool ModifiersDown
    {
        get
        {
            foreach (var key in ModifierKeys)
                if ((GetAsyncKeyState(key) & 0x8000) != 0) return true;
            return false;
        }
    }

    public WindowIdentity? Identify(nint window)
    {
        if (window == 0) return null;
        var thread = GetWindowThreadProcessId(window, out uint process);
        return thread == 0 || process == 0 ? null : new WindowIdentity(thread, process);
    }

    public bool IsPasteTarget(nint window)
    {
        if (!IsWindowVisible(window) || IsIconic(window)) return false;
        var name = new StringBuilder(256);
        return GetClassNameW(window, name, name.Capacity) != 0 && !ShellClasses.Contains(name.ToString());
    }

    public nint FocusedControl(PasteTarget target)
    {
        var info = new GuiThreadInfo { Size = (uint)Marshal.SizeOf<GuiThreadInfo>() };
        if (!GetGUIThreadInfo(target.Identity.Thread, ref info) || info.Focus == target.Handle) return 0;
        return IsChild(target.Handle, info.Focus) ? info.Focus : 0;
    }

    public void RestoreForeground(nint window) => SetForegroundWindow(window);

    [StructLayout(LayoutKind.Sequential)]
    private struct GuiThreadInfo
    {
        internal uint Size, Flags;
        internal nint Active, Focus, Capture, MenuOwner, MoveSize, Caret;
        internal int Left, Top, Right, Bottom;
    }

    public bool SendPaste()
    {
        Input[] keys = [Key(0x11), Key(0x56), Key(0x56, release: true), Key(0x11, release: true)];
        uint inserted = SendInput((uint)keys.Length, keys, Marshal.SizeOf<Input>());
        // Release only keys that the accepted prefix may have left down.
        if (inserted is > 0 and < 4)
        {
            Input[] releases = inserted == 2 ? [keys[2], keys[3]] : [keys[3]];
            SendInput((uint)releases.Length, releases, Marshal.SizeOf<Input>());
        }
        return inserted == keys.Length;
    }

    internal static int InputSize => Marshal.SizeOf<Input>();
    private static Input Key(ushort key, bool release = false) => new()
    {
        Type = 1, Data = new() { Keyboard = new() { Key = key, Flags = release ? 2u : 0u } }
    };

    // Win32 INPUT includes a union; MOUSEINPUT determines its size on x86 and x64.
    [StructLayout(LayoutKind.Sequential)]
    private struct Input { internal uint Type; internal InputData Data; }
    [StructLayout(LayoutKind.Explicit)]
    private struct InputData
    {
        [FieldOffset(0)] internal MouseInput Mouse;
        [FieldOffset(0)] internal KeyboardInput Keyboard;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct MouseInput { internal int X, Y; internal uint Data, Flags, Time; internal nuint Extra; }
    [StructLayout(LayoutKind.Sequential)]
    private struct KeyboardInput { internal ushort Key, Scan; internal uint Flags, Time; internal nuint Extra; }

    [DllImport("user32.dll")] private static extern nint GetForegroundWindow();
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(nint window, out uint process);
    [DllImport("user32.dll")] private static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(nint window);
    [DllImport("user32.dll")] private static extern bool IsIconic(nint window);
    [DllImport("user32.dll")] private static extern bool IsChild(nint parent, nint window);
    [DllImport("user32.dll")] private static extern bool GetGUIThreadInfo(uint thread, ref GuiThreadInfo info);
    [DllImport("user32.dll", ExactSpelling = true, CharSet = CharSet.Unicode)]
    private static extern int GetClassNameW(nint window, StringBuilder name, int capacity);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(nint window);
    [DllImport("user32.dll")] private static extern uint SendInput(uint count, Input[] inputs, int size);
}
