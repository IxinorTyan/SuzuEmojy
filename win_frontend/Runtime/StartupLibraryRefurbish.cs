using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;

namespace SuzuEmojy.Windows;

// Startup serializes library writes while the original snapshot remains on screen.
// The receipt is published only after the images and indexes have switched.
internal static class StartupLibraryRefurbish
{
    internal const string Receipt = ".windows-library-refurbish-v1";
    private const string Journal = Receipt + ".journal";
    private static readonly string[] Payload =
        [.. LibraryRefurbish.Databases, .. LibraryRefurbish.Mirrors, "images", LibraryRefurbish.ReportFile];
    private static readonly string[] Slots =
        [.. LibraryRefurbish.Databases.SelectMany(name => new[] { name + "-wal", name + "-shm", name + "-journal" }), .. Payload];
    private sealed record Pending(Guid Id, string[] Install);

    internal static bool NeedsRun(string data) => !File.Exists(Path.Combine(data, Receipt));

    internal static void Recover(string data)
    {
        if (!File.Exists(Path.Combine(data, Journal))) return;
        var root = RuntimePrimitives.ExistingPath(Path.GetFullPath(data));
        var journal = Path.Combine(root, Journal);
        RuntimePrimitives.EnsureNotReparse(journal);
        var pending = JsonSerializer.Deserialize<Pending>(File.ReadAllText(journal));
        if (pending is null || pending.Id == Guid.Empty || pending.Install is null ||
            pending.Install.Any(name => !Payload.Contains(name, StringComparer.Ordinal)))
            throw new InvalidDataException("无法恢复图库翻新事务。");
        var work = Work(root, pending.Id);
        var next = Path.Combine(work, "next");
        var original = Path.Combine(work, "original");
        RuntimePrimitives.EnsureNotReparse(next);
        RuntimePrimitives.EnsureNotReparse(original);
        var receipt = Path.Combine(root, Receipt);
        RuntimePrimitives.EnsureNotReparse(receipt);
        bool committed = File.Exists(receipt);
        if (!committed)
        {
            foreach (var name in Slots.Reverse())
            {
                var prepared = Path.Combine(next, name);
                var saved = Path.Combine(original, name);
                var live = Path.Combine(root, name);
                if (pending.Install.Contains(name, StringComparer.Ordinal) && !Exists(prepared))
                    Move(live, prepared);
                if (Exists(saved)) Move(saved, live);
            }
        }
        File.Delete(journal);
        Discard(committed ? next : work);
    }

    internal static bool RunOnce(string data, CancellationToken token, Func<string, ulong>? availableBytes = null,
        Action<string>? afterInstall = null, Action<string, LibraryRefurbish.Report, Action>? publish = null)
    {
        if (!NeedsRun(data)) return false;
        bool journaled = false;
        try
        {
            token.ThrowIfCancellationRequested();
            var root = RuntimePrimitives.ExistingPath(Directory.CreateDirectory(data).FullName);
            var images = Path.Combine(root, "images");
            RuntimePrimitives.EnsureNotReparse(images);
            if (!Directory.Exists(images) || !LibrarySnapshotReader.ImageFiles(root, token).Any())
            {
                WriteReceipt(root, token);
                return false;
            }

            if ((availableBytes ?? RuntimePrimitives.AvailableBytes)(root) < LibraryRefurbish.RequiredStagingBytes(root, token))
            {
                Trace.TraceWarning("Not enough free space to refurbish the Windows library; migration deferred.");
                return false;
            }

            var id = Guid.NewGuid();
            var work = Work(root, id);
            WriteState(root, Journal, output => JsonSerializer.Serialize(output, new Pending(id, [])), token);
            journaled = true;
            var next = Directory.CreateDirectory(Path.Combine(work, "next")).FullName;
            var original = Directory.CreateDirectory(Path.Combine(work, "original")).FullName;
            var report = LibraryRefurbish.Build(root, next, token, standalone: false);
            var pending = new Pending(id, Payload.Where(name => Exists(Path.Combine(next, name))).ToArray());
            foreach (var name in Slots) RuntimePrimitives.EnsureNotReparse(Path.Combine(root, name));
            if (publish is null) Install();
            else publish(Path.Combine(original, "images"), report, Install);

            void Install()
            {
                WriteState(root, Journal, output => JsonSerializer.Serialize(output, pending), token, overwrite: true);
                try
                {
                    foreach (var name in Slots)
                    {
                        var live = Path.Combine(root, name);
                        if (Exists(live)) Move(live, Path.Combine(original, name));
                        if (pending.Install.Contains(name, StringComparer.Ordinal)) Move(Path.Combine(next, name), live);
                        afterInstall?.Invoke(name);
                    }
                    // No cancellation between journal publication and the commit/rollback.
                    Move(Path.Combine(next, Receipt), Path.Combine(root, Receipt));
                }
                catch
                {
                    Recover(root);
                    throw;
                }
            }
        }
        catch (Exception error) when (error is IOException or InvalidDataException or UnauthorizedAccessException or
            ArgumentException or NotSupportedException or LibrarySqliteException or JsonException or OperationCanceledException)
        {
            if (journaled) Recover(data);
            if (error is OperationCanceledException) throw;
            Trace.TraceWarning("Could not refurbish the Windows library: {0}", error.Message);
            return false;
        }
        if (journaled) Recover(data);
        return true;
    }

    internal static void WriteReceipt(string root, CancellationToken token) =>
        WriteState(root, Receipt, output => output.WriteByte((byte)'1'), token);

    private static void WriteState(string root, string name, Action<FileStream> write, CancellationToken token, bool overwrite = false)
    {
        var path = Path.Combine(root, name);
        RuntimePrimitives.EnsureNotReparse(path);
        var temp = path + "." + Guid.NewGuid().ToString("N") + ".part";
        try
        {
            using (var file = new FileStream(temp, FileMode.CreateNew, FileAccess.Write))
            {
                write(file);
                file.Flush(flushToDisk: true);
            }
            if (!RuntimePrimitives.CommitFile(temp, path, overwrite, token))
                throw new IOException("图库翻新状态已经变化。");
        }
        finally { RuntimePrimitives.DiscardTemporaryFile(temp); }
    }

    private static string Work(string root, Guid id)
    {
        var parent = Path.Combine(root, "maintenance-backups");
        RuntimePrimitives.EnsureNotReparse(parent);
        var work = Path.Combine(parent, "refurbish-v1-" + id.ToString("N"));
        RuntimePrimitives.EnsureNotReparse(work);
        return work;
    }

    private static bool Exists(string path) => File.Exists(path) || Directory.Exists(path);

    private static void Move(string from, string to)
    {
        RuntimePrimitives.EnsureNotReparse(from);
        RuntimePrimitives.EnsureNotReparse(to);
        if (Directory.Exists(from)) Directory.Move(from, to);
        else File.Move(from, to);
    }

    private static void Discard(string folder)
    {
        try { if (Directory.Exists(folder)) Directory.Delete(folder, recursive: true); }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        { Trace.TraceWarning("Could not remove a completed refurbish staging directory: {0}", error.Message); }
    }
}
