using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;

namespace SuzuEmojy.Windows.Controls;

// One owned GIF decoder and timer, shared by hover popups and page inspectors.
internal sealed class GifPlayback
{
    private readonly DispatcherTimer clock = new(DispatcherPriority.Background);
    private GifPreview? animation;
    private Image? target;
    private ImageSource? still;

    internal GifPlayback() => clock.Tick += (_, _) => Advance();

    internal bool Play(Image picture, string path, int pixels)
        => Path.GetExtension(path).Equals(".gif", StringComparison.OrdinalIgnoreCase)
            ? Play(picture, () => GifPreview.Open(path, pixels))
            : Play(picture, () => null);

    internal bool Play(Image picture, byte[] data, int pixels) =>
        Play(picture, () => GifPreview.Open(new MemoryStream(data, writable: false), pixels));

    private bool Play(Image picture, Func<GifPreview?> open)
    {
        Stop();
        try
        {
            animation = open();
            if (animation is null) return false;
            target = picture;
            still = picture.Source;
            picture.Source = animation.Bitmap;
            clock.Interval = animation.Delay;
            clock.Start();
            return true;
        }
        catch (Exception error) when (error is IOException or ArgumentException or UnauthorizedAccessException or ExternalException)
        {
            Stop();
            return false;
        }
    }

    private void Advance()
    {
        if (animation is null) return;
        try
        {
            if (animation.Advance()) clock.Interval = animation.Delay;
            else clock.Stop(); // Keep the final frame until the owner closes the preview.
        }
        catch (Exception error) when (error is ArgumentException or IOException or ExternalException) { Stop(); }
    }

    internal void Stop()
    {
        clock.Stop();
        if (target is not null) target.Source = still;
        target = null;
        still = null;
        animation?.Dispose();
        animation = null;
    }
}
