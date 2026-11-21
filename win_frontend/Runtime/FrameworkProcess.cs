#if NETFRAMEWORK
using System;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;

namespace SuzuEmojy.Windows;

internal static class FrameworkProcess
{
    internal static async Task WaitForExitAsync(this Process process, CancellationToken cancellation = default)
    {
        if (process.HasExited) return;
        var completed = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        void OnExit(object? sender, EventArgs e) => completed.TrySetResult(true);
        process.Exited += OnExit;
        try
        {
            process.EnableRaisingEvents = true;
            if (process.HasExited) return;
            using (cancellation.Register(() => completed.TrySetCanceled()))
                await completed.Task.ConfigureAwait(false);
        }
        finally { process.Exited -= OnExit; }
    }
}
#endif
