using System.Windows;
using System.Windows.Input;

namespace SuzuEmojy.Windows.Controls;

internal static class FluentKeyboard
{
    internal static void Install()
    {
        EventManager.RegisterClassHandler(typeof(Window), Keyboard.KeyDownEvent, new KeyEventHandler(HandleAlt));
        EventManager.RegisterClassHandler(typeof(Window), Keyboard.KeyUpEvent, new KeyEventHandler(HandleAlt));
    }

    // Leave physical modifiers and combinations intact. After the focused control
    // has seen the key, prevent WPF from interpreting Alt itself as keyboard UI mode.
    private static void HandleAlt(object sender, KeyEventArgs e)
    {
        var key = e.Key == Key.System ? e.SystemKey : e.Key;
        if (key is Key.LeftAlt or Key.RightAlt) e.Handled = true;
    }
}
