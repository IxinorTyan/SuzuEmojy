using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Windows.Interop;

namespace SuzuEmojy.Windows.Desktop;

internal readonly record struct HotkeyChord(uint Modifiers, uint VirtualKey)
{
    private static readonly Dictionary<string, uint> NamedKeys = new(StringComparer.OrdinalIgnoreCase)
    {
        ["backspace"] = 0x08, ["tab"] = 0x09, ["enter"] = 0x0D, ["return"] = 0x0D,
        ["esc"] = 0x1B, ["escape"] = 0x1B, ["space"] = 0x20, ["pgup"] = 0x21, ["pgdown"] = 0x22,
        ["end"] = 0x23, ["home"] = 0x24, ["left"] = 0x25, ["up"] = 0x26, ["right"] = 0x27,
        ["down"] = 0x28, ["ins"] = 0x2D, ["insert"] = 0x2D, ["del"] = 0x2E, ["delete"] = 0x2E,
        [";"] = 0xBA, ["="] = 0xBB, [","] = 0xBC, ["comma"] = 0xBC, ["-"] = 0xBD, ["."] = 0xBE,
        ["/"] = 0xBF, ["`"] = 0xC0, ["["] = 0xDB, ["\\"] = 0xDC, ["]"] = 0xDD, ["'"] = 0xDE
    };

    internal static bool TryParse(string text, out HotkeyChord chord)
    {
        uint modifiers = 0, key = 0;
        chord = default;
        var parts = text.Trim().ToLowerInvariant().Split('+', StringSplitOptions.TrimEntries);
        for (int i = 0; i < parts.Length; i++)
        {
            var part = parts[i];
            uint flag = part switch { "alt" => 1, "ctrl" => 2, "shift" => 4, "win" => 8, _ => 0 };
            if (flag != 0)
            {
                if ((modifiers & flag) != 0 || i == parts.Length - 1) return false;
                modifiers |= flag;
                continue;
            }
            if (i != parts.Length - 1) return false;
            if (part.Length == 1 && part[0] is >= 'a' and <= 'z') key = (uint)char.ToUpperInvariant(part[0]);
            else if (part.Length == 1 && part[0] is >= '0' and <= '9') key = part[0];
            else if (part.Length is 2 or 3 && part[0] == 'f' && int.TryParse(part.AsSpan(1), out int f) &&
                     f is >= 1 and <= 24 and not 12)
                key = (uint)(0x6F + f); // F12 is reserved by the debugger.
            else key = NamedKeys.GetValueOrDefault(part);
        }
        // RegisterHotKey consumes the key. Do not install global unmodified keys
        // or Shift-only typing chords from a configuration written by the old listener.
        bool hasNonShiftModifier = (modifiers & (1 | 2 | 8)) != 0;
        bool isShiftFunctionKey = modifiers == 4 && key is >= 0x70 and <= 0x87;
        if (key == 0 || (!hasNonShiftModifier && !isShiftFunctionKey)) return false;
        chord = new HotkeyChord(modifiers, key);
        return true;
    }

    internal static bool TryFormat(uint modifiers, uint key, out string text)
    {
        string? button = key switch
        {
            >= 'A' and <= 'Z' => ((char)(key + 32)).ToString(),
            >= '0' and <= '9' => ((char)key).ToString(),
            >= 0x70 and <= 0x87 => $"f{key - 0x6F}",
            _ => null,
        };
        if (button is null)
        {
            foreach (var (name, code) in NamedKeys)
                if (code == key) { button = name; break; }
        }
        text = "";
        if (button is null) return false;
        var parts = new List<string>(5);
        if ((modifiers & 2) != 0) parts.Add("ctrl");
        if ((modifiers & 1) != 0) parts.Add("alt");
        if ((modifiers & 4) != 0) parts.Add("shift");
        if ((modifiers & 8) != 0) parts.Add("win");
        parts.Add(button);
        var formatted = string.Join('+', parts);
        if (!TryParse(formatted, out _)) return false;
        text = formatted;
        return true;
    }
}

internal sealed class GlobalHotkey : IDisposable
{
    private static readonly nint MessageOnlyParent = new(-3); // HWND_MESSAGE
    private const int HotkeyMessage = 0x0312;
    private const uint NoRepeat = 0x4000;
    private readonly HwndSource source;
    private readonly int id;
    private readonly HotkeyChord chord;
    private readonly Action activated;
    private bool registered;

    internal static HwndSource CreateMessageHost() => new(new HwndSourceParameters("SuzuEmojy.Hotkeys")
    {
        ParentWindow = MessageOnlyParent, WindowStyle = 0,
    });

    private GlobalHotkey(HwndSource source, int id, HotkeyChord chord, Action activated)
    {
        this.source = source;
        this.id = id;
        this.chord = chord;
        this.activated = activated;
        source.AddHook(WindowMessage);
        registered = true;
    }

    internal static GlobalHotkey? TryRegister(HwndSource? source, int id, string text, Action activated, out string? error)
    {
        error = null;
        if (string.IsNullOrWhiteSpace(text)) return null;
        if (!HotkeyChord.TryParse(text, out var chord))
        {
            error = $"不支持此快捷键：{text}";
            return null;
        }
        if (source is null || !RegisterHotKey(source.Handle, id, chord.Modifiers | NoRepeat, chord.VirtualKey))
        {
            int code = source is null ? 0 : Marshal.GetLastWin32Error();
            error = code == 1409 ? $"快捷键已被占用：{text}" : $"无法注册快捷键：{text}";
            Trace.TraceWarning("Cannot register hotkey {0}: {1}", text, new Win32Exception(code).Message);
            return null;
        }
        return new GlobalHotkey(source, id, chord, activated);
    }

    private nint WindowMessage(nint window, int message, nint wParam, nint lParam, ref bool handled)
    {
        if (registered && Matches(message, wParam, lParam, id, chord))
        {
            handled = true;
            activated();
        }
        return 0;
    }

    internal static bool Matches(int message, nint wParam, nint lParam, int id, HotkeyChord chord) =>
        message == HotkeyMessage && wParam == id && ((uint)lParam & 0x000F) == chord.Modifiers &&
        (((uint)lParam >> 16) & 0xFFFF) == chord.VirtualKey;

    public void Dispose()
    {
        if (!registered) return;
        registered = false;
        if (!source.IsDisposed)
        {
            UnregisterHotKey(source.Handle, id);
            source.RemoveHook(WindowMessage);
        }
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool RegisterHotKey(nint window, int id, uint modifiers, uint key);
    [DllImport("user32.dll")] private static extern bool UnregisterHotKey(nint window, int id);
}
