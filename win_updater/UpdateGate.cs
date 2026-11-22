using System;
using System.IO;
using System.Threading;

namespace SuzuEmojy.Windows;

internal static class UpdateGate
{
    private const string Name = @"Local\SuzuEmojy.Windows.Update";
    internal static string ReadyName(Guid token) => Name + "." + token.ToString("N");

    internal static Mutex? TryAcquire()
    {
        var gate = new Mutex(false, Name, out bool created);
        if (created) return gate;
        gate.Dispose();
        return null;
    }

    internal static bool IsRunning()
    {
        if (!Mutex.TryOpenExisting(Name, out var gate)) return false;
        gate.Dispose();
        return true;
    }

    internal static bool HasJournal(string root)
    {
        var journal = Path.Combine(root, PortableLayout.Journal);
        return Directory.Exists(journal) || File.Exists(journal);
    }
}
