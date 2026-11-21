using System;
using System.Threading;

namespace SuzuEmojy.Windows;

internal static class WindowsInstance
{
    // One Windows 2.0 instance per interactive session, independent of the
    // executable location, startup arguments, or whether the window is visible.
    private const string Name = @"Local\SuzuEmojy.Windows.App.Instance";

    internal static Mutex? TryAcquire(string name = Name)
    {
        try
        {
            // Use the kernel object's lifetime as the lease, not thread
            // ownership. Creation is atomic, and process exit closes the handle.
            var lease = new Mutex(initiallyOwned: false, name, out bool created);
            if (created) return lease;
            lease.Dispose();
        }
        catch (UnauthorizedAccessException) { } // Already held across a launch privilege boundary.
        return null;
    }

    // The launcher uses this only to avoid starting a redundant process.
    // The application still arbitrates concurrent/direct launches atomically.
    internal static bool IsRunning(string name = Name)
    {
        try
        {
            if (!Mutex.TryOpenExisting(name, out var lease)) return false;
            lease.Dispose();
            return true;
        }
        catch (UnauthorizedAccessException) { return true; }
    }
}
