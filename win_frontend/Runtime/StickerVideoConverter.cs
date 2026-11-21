using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace SuzuEmojy.Windows;

internal static class StickerVideoConverter
{
    internal static string? Find(string repository)
    {
        var path = Path.Combine(repository, "ffmpeg", "ffmpeg.exe");
        if (File.Exists(path)) return path;
        path = Path.Combine(repository, "dist", "ffmpeg-minimal", "ffmpeg.exe");
        return File.Exists(path) ? path : null;
    }

    internal static bool IsWebm(Stream source)
    {
        source.Position = 0;
        var header = new byte[4];
        int length = source.Read(header, 0, header.Length);
        source.Position = 0;
        return length == 4 && header.AsSpan().SequenceEqual(new byte[] { 0x1a, 0x45, 0xdf, 0xa3 });
    }

    internal static T Clean<T>(string ffmpeg, string original, Func<string, bool, T> prepare, CancellationToken token)
    {
        var folder = Path.Combine(Path.GetTempPath(), $"suzu-webm-clean-{Guid.NewGuid():N}");
        var output = Path.Combine(folder, "frames.gif");
        var still = Path.Combine(folder, "01.png");
        var next = Path.Combine(folder, "02.png");
        Directory.CreateDirectory(folder);
        try
        {
            if (SingleFrame(ffmpeg, original, folder, token).GetAwaiter().GetResult()) return prepare(still, false);
            Convert(ffmpeg, original, output, "gif", token, 320).GetAwaiter().GetResult();
            return prepare(output, true);
        }
        finally
        {
            foreach (var path in new[] { still, next, output }) RuntimePrimitives.DiscardTemporaryFile(path);
            try { Directory.Delete(folder); } catch (IOException) { }
        }
    }

    internal static async Task<TelegramStickerClient.Asset> Webm(string ffmpeg, byte[] bytes, string format,
        CancellationToken token, int maxSize = 512)
    {
        var folder = Path.Combine(Path.GetTempPath(), $"suzu-video-{Guid.NewGuid():N}");
        Directory.CreateDirectory(folder);
        var source = Path.Combine(folder, "source.webm");
        var target = Path.Combine(folder, $"converted.{format}");
        try
        {
            File.WriteAllBytes(source, bytes);
            await Convert(ffmpeg, source, target, format, token, maxSize).ConfigureAwait(false);
            var file = new FileInfo(target);
            if (file.Length <= 0 || file.Length > 64 * 1024 * 1024) throw new IOException("无效的贴纸转换结果");
            return new(File.ReadAllBytes(target), format);
        }
        finally
        {
            RuntimePrimitives.DiscardTemporaryFile(source);
            RuntimePrimitives.DiscardTemporaryFile(target);
            try { Directory.Delete(folder); } catch (IOException) { }
        }
    }

    internal static Task Convert(string ffmpeg, string source, string target, string format, CancellationToken token, int maxSize)
    {
        string[] args = format switch
        {
            "png" => ["-frames:v", "1", "-pix_fmt", "rgba", "-c:v", "png"],
            "gif" => ["-filter_complex", $"format=rgba,fps=15,scale={maxSize}:{maxSize}:force_original_aspect_ratio=decrease:flags=lanczos," +
                "split[frames][palette_source];[palette_source]palettegen=reserve_transparent=1:transparency_color=black[palette];" +
                "[frames][palette]paletteuse=alpha_threshold=128:diff_mode=rectangle", "-loop", "0", "-gifflags", "+transdiff", "-f", "gif"],
            _ => throw new ArgumentException("Unsupported output format.")
        };
        return Run(ffmpeg, source, target, args, token);
    }

    internal static async Task<bool> SingleFrame(string ffmpeg, string source, string folder, CancellationToken token)
    {
        await Run(ffmpeg, source, Path.Combine(folder, "%02d.png"),
            ["-frames:v", "2", "-fps_mode", "passthrough", "-pix_fmt", "rgba", "-c:v", "png"], token).ConfigureAwait(false);
        if (!File.Exists(Path.Combine(folder, "01.png"))) throw new IOException("无法解码 WebM");
        return !File.Exists(Path.Combine(folder, "02.png"));
    }

    private static async Task Run(string ffmpeg, string source, string target, string[] options, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        string[] arguments = ["-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-protocol_whitelist", "file,pipe", "-f", "matroska", "-c:v", "libvpx-vp9", "-i", source, "-an", .. options, target];
        var setup = new ProcessStartInfo(ffmpeg) { UseShellExecute = false, CreateNoWindow = true };
#if NETFRAMEWORK
        setup.Arguments = string.Join(" ", arguments.Select(WindowsCommandLine.QuoteArgument));
#else
        foreach (var argument in arguments) setup.ArgumentList.Add(argument);
#endif
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
        deadline.CancelAfter(TimeSpan.FromSeconds(45));
        using var process = Process.Start(setup) ?? throw new IOException("无法启动 FFmpeg");
        try { await process.WaitForExitAsync(deadline.Token).ConfigureAwait(false); }
        catch (OperationCanceledException)
        {
            try { if (!process.HasExited) process.Kill(); }
            catch (InvalidOperationException) { }
            await process.WaitForExitAsync().ConfigureAwait(false);
            token.ThrowIfCancellationRequested();
            throw new IOException("贴纸转换超时");
        }
        if (process.ExitCode != 0) throw new IOException("无法转换 WebM 贴纸");
    }
}
