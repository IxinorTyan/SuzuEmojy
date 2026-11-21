using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Controls;
using System.Windows.Media;

namespace SuzuEmojy.Windows.Controls;

// One owned player per preview surface. GIF/APNG stay in-process.
internal sealed class GifPlayback
{
    private PreviewFrames? frames;
    private CancellationTokenSource? loading;
    private readonly LibraryCommands? commands;
    private Image? target;
    private ImageSource? still;
    private double due;
    private bool ticking;

    internal GifPlayback(LibraryCommands? commands = null)
    {
        this.commands = commands;
    }

    internal bool Play(Image picture, string path, int pixels, Action? ready = null)
    {
        if (Path.GetExtension(path).Equals(".gif", StringComparison.OrdinalIgnoreCase))
            return PlayGif(picture, () => File.Open(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete), pixels, ready);
        Stop();
        if (commands is null || !PreviewFrames.CanDecode(path, picture.Source is not null)) return false;
        loading = new();
        _ = Load(picture, path, pixels, commands, loading, ready);
        return true;
    }

    private async Task Load(Image picture, string path, int pixels, LibraryCommands backend,
        CancellationTokenSource request, Action? ready)
    {
        try
        {
            var result = await backend.Run(new("preview_animation", Paths: [path], Limit: pixels), reload: false, request.Token);
            if (result.Result.Animation is not { } response) return;
            var decoded = response.Native;
            if (decoded is null)
            {
                if (response.Frames is not { Length: > 0 } encoded) return;
                decoded = await Task.Run(() => PreviewFrames.Decode(encoded, response.Plays, request.Token), request.Token);
            }
            if (!ReferenceEquals(request, loading) || request.IsCancellationRequested) return;
            Attach(picture, decoded);
            ready?.Invoke();
        }
        catch (Exception error) when (error is OperationCanceledException or IOException or ArgumentException or
            FormatException or InvalidOperationException or NotSupportedException or UnauthorizedAccessException or ExternalException or
            System.ComponentModel.Win32Exception or System.Text.Json.JsonException) { }
        finally
        {
            if (ReferenceEquals(request, loading)) loading = null;
            request.Dispose();
        }
    }

    internal bool Play(Image picture, byte[] data, int pixels) =>
        PlayGif(picture, () => new MemoryStream(data, writable: false), pixels, null);

    internal void Play(Image picture, PreviewFrames decoded)
    {
        Stop();
        Attach(picture, decoded);
    }

    private void Attach(Image picture, PreviewFrames decoded)
    {
        frames = decoded;
        target = picture;
        still = picture.Source;
        picture.Source = decoded.Current;
        if (decoded.HasMotion)
        {
            due = Now + decoded.Delay.TotalMilliseconds;
            ticking = true;
            CompositionTarget.Rendering += Advance;
        }
    }

    private bool PlayGif(Image picture, Func<Stream> open, int pixels, Action? ready)
    {
        Stop();
        loading = new();
        _ = LoadGif(picture, open, pixels, loading, ready);
        return true;
    }

    private async Task LoadGif(Image picture, Func<Stream> open, int pixels, CancellationTokenSource request, Action? ready)
    {
        try
        {
            var decoded = await Task.Run(() => GifPreview.DecodeFrames(open(), pixels, request.Token), request.Token);
            if (decoded is null || !ReferenceEquals(request, loading) || request.IsCancellationRequested) return;
            Attach(picture, decoded);
            ready?.Invoke();
        }
        catch (Exception error) when (error is OperationCanceledException or IOException or ArgumentException or UnauthorizedAccessException
            or ExternalException or NotSupportedException or InvalidOperationException) { }
        finally
        {
            if (ReferenceEquals(request, loading)) loading = null;
            request.Dispose();
        }
    }

    private static double Now => Stopwatch.GetTimestamp() * (1000.0 / Stopwatch.Frequency);

    private void Advance(object? sender, EventArgs e)
    {
        if (frames is not { } decoded || target is null) return;
        var now = Now;
        if (now < due) return;
        // If a render was late, advance the media clock and present only the
        // latest due frame instead of adding dispatcher latency to every frame.
        for (int n = 0; n < 8 && now >= due; n++)
        {
            if (!decoded.Advance()) { Unsubscribe(); break; }
            due += decoded.Delay.TotalMilliseconds;
        }
        if (now >= due) due = now + decoded.Delay.TotalMilliseconds;
        target.Source = decoded.Current;
    }

    private void Unsubscribe()
    {
        if (!ticking) return;
        ticking = false;
        CompositionTarget.Rendering -= Advance;
    }

    internal void Stop(bool restoreStill = true)
    {
        Unsubscribe();
        var request = loading;
        loading = null;
        request?.Cancel();
        if (restoreStill && target is not null) target.Source = still;
        target = null;
        still = null;
        frames = null;
    }
}
