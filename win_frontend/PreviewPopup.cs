using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using SuzuEmojy.Windows.Controls;

namespace SuzuEmojy.Windows;

internal sealed class PreviewPopup : IDisposable
{
    private readonly Window owner;
    private readonly DispatcherTimer delay;
    private readonly GifPlayback playback = new();
    private readonly Popup popup;
    private readonly StickerPreview content = new();
    private Sticker? candidate;
    private Button? target;

    internal PreviewPopup(Window owner, LibrarySnapshot library)
    {
        this.owner = owner;
        delay = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(library.Number("preview_delay", 500, 0, 5000)) };
        delay.Tick += Open;
        content.FontFamily = owner.FontFamily;
        content.Configure(library, owner.Resources);
        popup = new Popup {
            AllowsTransparency = true, Placement = PlacementMode.MousePoint,
            HorizontalOffset = 15, VerticalOffset = 15, IsHitTestVisible = false,
            Focusable = false, Child = content, PlacementTarget = owner,
        };
        owner.LocationChanged += OwnerMoved;
        owner.StateChanged += OwnerMoved;
        owner.IsVisibleChanged += OwnerAvailabilityChanged;
        owner.IsEnabledChanged += OwnerAvailabilityChanged;
    }

    internal Popup Surface => popup;

    internal void Refresh(LibrarySnapshot library)
    {
        Hide();
        delay.Interval = TimeSpan.FromMilliseconds(library.Number("preview_delay", 500, 0, 5000));
        content.Configure(library, owner.Resources);
    }

    internal void Schedule(Sticker item, Button card)
    {
        Hide();
        candidate = item;
        target = card;
        delay.Start();
    }

    private void Open(object? sender, EventArgs e)
    {
        delay.Stop();
        if (candidate is not Sticker item || !CanOpen(owner.IsVisible && owner.IsEnabled, owner.WindowState,
            target is { IsMouseOver: true, IsVisible: true, IsEnabled: true },
            Mouse.LeftButton == MouseButtonState.Pressed || Mouse.RightButton == MouseButtonState.Pressed ||
            Mouse.MiddleButton == MouseButtonState.Pressed))
        {
            Hide();
            return;
        }
        int pixels = (int)Math.Ceiling(content.PictureSide * VisualTreeHelper.GetDpi(owner).PixelsPerDip);
        if (MediaThumbnail.Load(item.Path, pixels) is not { } source) return;
        content.ShowSticker(item, source);
        popup.IsOpen = true;
        playback.Play(content.Picture, item.Path, pixels);
    }

    // A background window can still be under the pointer. The passive popup never activates it.
    internal static bool CanOpen(bool ownerAvailable, WindowState state, bool cardHovered, bool mousePressed) =>
        ownerAvailable && state != WindowState.Minimized && cardHovered && !mousePressed;

    internal void Hide()
    {
        delay.Stop();
        playback.Stop();
        popup.IsOpen = false;
        content.Picture.Source = null;
        candidate = null;
        target = null;
    }

    private void OwnerMoved(object? sender, EventArgs e) => Hide();
    private void OwnerAvailabilityChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        if (e.NewValue is false) Hide();
    }

    public void Dispose()
    {
        owner.LocationChanged -= OwnerMoved;
        owner.StateChanged -= OwnerMoved;
        owner.IsVisibleChanged -= OwnerAvailabilityChanged;
        owner.IsEnabledChanged -= OwnerAvailabilityChanged;
        Hide();
    }
}
