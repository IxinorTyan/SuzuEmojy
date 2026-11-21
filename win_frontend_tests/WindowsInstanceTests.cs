using System;
using System.Diagnostics;
using System.IO;
using SuzuEmojy.Windows;

internal static class WindowsInstanceTests
{
    internal static int Hold(string name)
    {
        using var instance = WindowsInstance.TryAcquire(name);
        Console.WriteLine(instance is null ? "duplicate" : "acquired");
        Console.Out.Flush();
        if (instance is not null) Console.ReadLine();
        return 0;
    }

    internal static void Run()
    {
        // Do not acquire, block, or close the user's real session.
        var name = $@"Local\SuzuEmojy.Windows.Tests.{Guid.NewGuid():N}";
        Require(!WindowsInstance.IsRunning(name), "A fresh session should not be reported as running.");

        using var first = Start(name);
        using var second = Start(name);
        try
        {
            var a = ReadSignal(first);
            var b = ReadSignal(second);
            Require(a == "acquired" && b == "duplicate" || b == "acquired" && a == "duplicate",
                $"Concurrent startups must produce exactly one running instance: {a}, {b}.");
            var owner = a == "acquired" ? first : second;
            var duplicate = a == "duplicate" ? first : second;
            Require(duplicate.WaitForExit(5000) && duplicate.ExitCode == 0, "A duplicate startup must exit promptly.");
            Require(WindowsInstance.IsRunning(name), "The launcher must see a running instance without a visible window.");
            using (var denied = WindowsInstance.TryAcquire(name))
                Require(denied is null, "A direct startup must not bypass the launcher guard.");
            owner.StandardInput.WriteLine("exit");
            Require(owner.WaitForExit(5000) && owner.ExitCode == 0 && !WindowsInstance.IsRunning(name),
                "A normal exit must allow a fresh start.");
        }
        finally
        {
            EnsureExited(first);
            EnsureExited(second);
        }

        using var crashed = Start(name);
        try
        {
            Require(ReadSignal(crashed) == "acquired", "The app could not restart after a normal exit.");
        }
        finally { EnsureExited(crashed); }
        Require(!WindowsInstance.IsRunning(name), "An interrupted process must not leave a stuck instance lock.");
        using var recovered = WindowsInstance.TryAcquire(name);
        Require(recovered is not null, "The app could not restart after an interrupted process.");
    }

    private static Process Start(string name)
    {
        var executable = Path.Combine(AppContext.BaseDirectory, "SuzuEmojy.Windows.Tests.exe");
        return Process.Start(new ProcessStartInfo(executable, "--hold-instance " + WindowsCommandLine.QuoteArgument(name))
        {
            UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardInput = true,
        }) ?? throw new InvalidOperationException("Could not launch an isolated instance test process.");
    }

    private static string? ReadSignal(Process child)
    {
        var signal = child.StandardOutput.ReadLineAsync();
        if (!signal.Wait(TimeSpan.FromSeconds(5))) throw new TimeoutException("Instance test process did not respond.");
        return signal.GetAwaiter().GetResult();
    }

    private static void EnsureExited(Process child)
    {
        if (child.HasExited) return;
        child.Kill();
        if (!child.WaitForExit(5000)) throw new TimeoutException("Instance test process did not stop.");
    }

    private static void Require(bool check, string message)
    {
        if (!check) throw new InvalidOperationException(message);
    }
}
