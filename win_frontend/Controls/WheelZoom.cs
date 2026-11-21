using System;
using System.Windows.Input;

namespace SuzuEmojy.Windows.Controls;

internal static class WheelZoom
{
    internal static bool TryApply(int delta, ModifierKeys modifiers, int current, int min, int max, int step,
        Action<int> apply)
    {
        if (delta == 0 || (modifiers & ModifierKeys.Control) == 0) return false;
        var next = Math.Clamp(current + Math.Sign(delta) * step, min, max);
        if (next != current) apply(next);
        return true; // Don't scroll the viewport when zoom has reached a limit.
    }
}
