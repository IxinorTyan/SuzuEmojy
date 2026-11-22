using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;

namespace SuzuEmojy.Windows;

internal sealed record StagedUpdate(string Version, string Digest);

internal sealed class UpdateInstallation
{
    private readonly string root, bin, data;
    private readonly bool customData;
    private string Payload => Path.Combine(root, PortableLayout.Payload);
    internal StagedUpdate? Staged { get; set; }

    private UpdateInstallation(string root, string bin, string data, bool customData)
    {
        this.root = root;
        this.bin = bin;
        this.data = data;
        this.customData = customData;
    }

    internal static UpdateInstallation? For(WindowsInstall install, string data, bool customData)
    {
        if (!install.Updatable || install.Launcher is null) return null;
        var root = Path.GetDirectoryName(install.Launcher)!;
        if (root.StartsWith(@"\\", StringComparison.Ordinal)) return null;
        try { data = Directory.Exists(data) ? RuntimePrimitives.ExistingPath(data) : Path.GetFullPath(data); }
        catch (IOException) { return null; }
        catch (UnauthorizedAccessException) { return null; }
        if (RuntimePrimitives.IsWithin(data, root) ||
            new[] { install.Root, Path.Combine(root, PortableLayout.Payload), Path.Combine(root, PortableLayout.Journal) }
                .Any(path => RuntimePrimitives.IsWithin(path, data)))
            return null;
        return new(root, install.Root, data, customData);
    }

    internal string PrepareDownload()
    {
        UpdateTransaction.AssertAncestors(Payload);
        UpdateTransaction.AssertPlain(Path.Combine(Payload, PortableLayout.UpdateZip));
        Directory.CreateDirectory(Payload);
        return Path.Combine(Payload, PortableLayout.UpdateZip);
    }

    internal Process Start(StagedUpdate update)
    {
        var zip = PrepareDownload();
        if (!File.Exists(zip)) throw new IOException("找不到下载的更新包，请重新下载。");
        var updater = Path.Combine(bin, PortableLayout.Updater);
        var executable = Path.Combine(Payload, PortableLayout.Updater);
        var temporary = executable + "." + Guid.NewGuid().ToString("N") + ".part";
        UpdateTransaction.AssertPlain(updater);
        UpdateTransaction.AssertPlain(executable);
        try
        {
            File.Copy(updater, temporary);
            RuntimePrimitives.CommitFile(temporary, executable, overwrite: true, CancellationToken.None);
        }
        finally { RuntimePrimitives.DiscardTemporaryFile(temporary); }

        var token = Guid.NewGuid();
        using var ready = new EventWaitHandle(false, EventResetMode.ManualReset, UpdateGate.ReadyName(token));
        using var parent = Process.GetCurrentProcess();
        string[] args = ["--install", parent.Id.ToString(System.Globalization.CultureInfo.InvariantCulture),
            token.ToString("N"), update.Digest, update.Version, .. customData ? new[] { "--data", data } : Array.Empty<string>()];
        var process = Process.Start(new ProcessStartInfo(executable,
            string.Join(" ", args.Select(WindowsCommandLine.QuoteArgument)))
            { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Payload })
            ?? throw new IOException("无法启动更新器。");
        try
        {
            for (int attempt = 0; attempt < 50; attempt++)
            {
                if (ready.WaitOne(100) && !process.HasExited) return process;
                if (process.HasExited) throw new IOException($"更新器未就绪（{process.ExitCode}）。");
            }
            throw new IOException("更新器启动超时，请重试。");
        }
        catch
        {
            Stop(process);
            process.Dispose();
            throw;
        }
    }

    internal static void Stop(Process process)
    {
        try { if (!process.HasExited) process.Kill(); }
        catch (Exception error) when (error is Win32Exception or InvalidOperationException)
        { Trace.TraceWarning("Cannot stop update helper: {0}", error.Message); }
    }
}
