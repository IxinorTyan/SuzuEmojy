using System;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;

namespace SuzuEmojy.Windows.Controls;

public partial class StickerResultTile : UserControl
{
    private bool syncing;
    internal ExternalSticker Item { get; }
    internal event Action<ModifierKeys>? Opened;
    internal event Action<bool>? Toggled;

    internal StickerResultTile(ExternalSticker item, bool selected)
    {
        Item = item;
        InitializeComponent();
        Caption.Text = string.IsNullOrWhiteSpace(item.Label) ? $"{item.Index + 1:000}" : item.Label;
        AutomationProperties.SetName(Open, Caption.Text);
        AutomationProperties.SetName(Toggle, Caption.Text);
        ToolTip = Caption.Text;
        SetSelected(selected);
        Open.Click += (_, _) => Opened?.Invoke(Keyboard.Modifiers);
        Toggle.Checked += (_, _) => Changed();
        Toggle.Unchecked += (_, _) => Changed();
        MouseEnter += (_, _) => Paint();
        MouseLeave += (_, _) => Paint();
        IsKeyboardFocusWithinChanged += (_, _) => Paint();
    }

    internal void SetSelected(bool selected)
    {
        syncing = true;
        Toggle.IsChecked = selected;
        syncing = false;
        Paint();
    }

    private void Changed()
    {
        Paint();
        if (!syncing) Toggled?.Invoke(Toggle.IsChecked == true);
    }

    private void Paint()
    {
        Frame.SetResourceReference(Border.BorderBrushProperty, Toggle.IsChecked == true ? "Accent"
            : IsMouseOver || IsKeyboardFocusWithin ? "CardEdge" : "PreviewEdge");
        Frame.SetResourceReference(Border.BackgroundProperty, Toggle.IsChecked == true ? "SelectionSurface" : "PreviewSurface");
    }
}
