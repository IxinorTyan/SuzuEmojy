namespace SuzuEmojy.Windows.Desktop;

// A hotkey starts the handoff before Show/Activate. Only the requested panel, once
// actually foreground, can consume the captured target; a normal click cannot arm it.
internal sealed class PanelWake
{
    internal nint Panel { get; private set; }
    internal bool Activated { get; private set; }
    private PasteTarget? target;

    internal void Begin(nint panel, PasteTarget? destination)
    {
        Panel = panel;
        target = destination;
        Activated = false;
    }

    internal bool Confirm(nint panel, nint foreground)
    {
        if (Panel == 0 || Panel != panel || Panel != foreground) return false;
        Activated = true;
        return true;
    }

    internal PasteTarget? Consume(nint panel, nint foreground)
    {
        if (!Confirm(panel, foreground)) return null;
        var destination = target;
        Clear();
        return destination;
    }

    internal void Clear()
    {
        Panel = 0;
        target = null;
        Activated = false;
    }
}
