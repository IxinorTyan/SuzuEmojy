using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows.Threading;

namespace SuzuEmojy.Windows.Desktop;

internal sealed class InboxMonitor : IDisposable
{
    private readonly string folder;
    private readonly Func<InboxFile[], Task<bool>> import;
    private readonly DispatcherTimer timer;
    private readonly InboxReadiness readiness = new();
    private readonly Stopwatch clock = Stopwatch.StartNew();
    private bool polling, disposed;

    internal InboxMonitor(string data, Dispatcher dispatcher, Func<InboxFile[], Task<bool>> import)
    {
        folder = Path.Combine(data, "inbox");
        this.import = import;
        timer = new(DispatcherPriority.Background, dispatcher) { Interval = TimeSpan.FromSeconds(2) };
        timer.Tick += Poll;
    }

    internal void Start()
    {
        timer.Start();
        Poll(this, EventArgs.Empty);
    }

    private async void Poll(object? sender, EventArgs e)
    {
        if (polling || disposed) return;
        polling = true;
        try
        {
            var files = await Task.Run(() => ReadCandidates(folder));
            if (disposed) return;
            var ready = readiness.Observe(files, clock.Elapsed);
            if (ready.Length > 0) ready = await Task.Run(() => KeepReadable(folder, ready));
            if (disposed) return;
            if (ready.Length > 0 && await import(ready))
                readiness.MarkAttempted(ready, clock.Elapsed);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        { Trace.TraceWarning("Cannot read the inbox: {0}", error.Message); }
        finally { polling = false; }
    }

    internal static InboxFile[] ReadCandidates(string folder)
    {
        Directory.CreateDirectory(folder);
        if (!PlainFolder(folder)) return [];
        var processing = Path.Combine(folder, ".processing");
        var staged = Directory.Exists(processing) && PlainFolder(processing)
            ? Read(processing, staged: true) : Enumerable.Empty<InboxFile>();
        return staged.Concat(Read(folder, staged: false)).ToArray();

        static bool PlainFolder(string path) => (File.GetAttributes(path) & FileAttributes.ReparsePoint) == 0;
        static IEnumerable<InboxFile> Read(string path, bool staged)
        {
            foreach (var file in new DirectoryInfo(path).EnumerateFiles())
            {
                if ((file.Attributes & FileAttributes.ReparsePoint) != 0 || !ImportFormats.Suffixes.Contains(file.Extension, StringComparer.OrdinalIgnoreCase))
                    continue;
                if (staged && !(file.Name.Length > 33 && file.Name[32] == '_' &&
                    file.Name.Take(32).All(c => c is >= '0' and <= '9' or >= 'a' and <= 'f'))) continue;
                long length, modified;
                try { length = file.Length; modified = (file.LastWriteTimeUtc - DateTime.UnixEpoch).Ticks; }
                catch (FileNotFoundException) { continue; }
                if (length > 0) yield return new(file.Name, length, modified, staged);
            }
        }
    }

    internal static InboxFile[] KeepReadable(string folder, InboxFile[] files) => files.Where(file =>
    {
        var path = Path.Combine(file.Staged ? Path.Combine(folder, ".processing") : folder, file.Name);
        try
        {
            // Refuse an active writer even if length and modification time have
            // remained constant (e.g. a preallocated browser download).
            using var probe = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            return probe.Length == file.Length &&
                (File.GetLastWriteTimeUtc(probe.SafeFileHandle) - DateTime.UnixEpoch).Ticks == file.Modified;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException) { return false; }
    }).ToArray();

    public void Dispose()
    {
        disposed = true;
        timer.Stop();
        timer.Tick -= Poll;
    }
}

// Same signature in consecutive polls, with a retry window for a locked file.
internal sealed class InboxReadiness
{
    private const int BatchLimit = 64;
    private sealed record Observation(InboxFile File, TimeSpan Since, TimeSpan RetryAt);
    private Dictionary<(bool Staged, string Name), Observation> observations = new();

    internal InboxFile[] Observe(InboxFile[] files, TimeSpan now)
    {
        var next = new Dictionary<(bool, string), Observation>();
        foreach (var file in files)
        {
            var key = (file.Staged, file.Name);
            next[key] = observations.TryGetValue(key, out var old) && old.File == file
                ? old : new(file, now, now);
        }
        observations = next;
        return observations.Values.Where(observation => now - observation.Since >= TimeSpan.FromSeconds(1) && now >= observation.RetryAt)
            .Take(BatchLimit).Select(observation => observation.File).ToArray();
    }

    internal void MarkAttempted(InboxFile[] files, TimeSpan now)
    {
        foreach (var file in files)
        {
            var key = (file.Staged, file.Name);
            if (observations.TryGetValue(key, out var observed) && observed.File == file)
                observations[key] = observed with { RetryAt = now + TimeSpan.FromSeconds(12) };
        }
    }
}
