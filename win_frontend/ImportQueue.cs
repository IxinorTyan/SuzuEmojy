using System;
using System.Collections.Generic;
using System.Threading.Tasks;

namespace SuzuEmojy.Windows;

internal sealed record ImportRequest(LibraryCommand Command, bool ContainsFolder = false)
{
    internal bool IsLink => Command.Action == "import_link";
    internal int FileCount => Command.Paths?.Length ?? 0;
    internal bool IsBatch => ContainsFolder || FileCount > 1;
}

// Owned by the dispatcher. Each caller can retain its source (including a temporary
// clipboard bitmap) until its own request, rather than the entire queue, settles.
internal sealed class ImportQueue(Func<ImportRequest, Task> run, Action<int> waitingChanged)
{
    private readonly Queue<(ImportRequest Request, TaskCompletionSource Completion)> pending = new();
    internal bool IsRunning { get; private set; }
    internal int WaitingCount => pending.Count;

    internal Task Enqueue(ImportRequest request)
    {
        var completion = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        pending.Enqueue((request, completion));
        if (IsRunning) waitingChanged(WaitingCount);
        else _ = Drain();
        return completion.Task;
    }

    private async Task Drain()
    {
        IsRunning = true;
        try
        {
            while (pending.TryDequeue(out var entry))
            {
                try
                {
                    waitingChanged(WaitingCount);
                    await run(entry.Request);
                    entry.Completion.TrySetResult();
                }
                catch (Exception error) { entry.Completion.TrySetException(error); }
            }
        }
        finally { IsRunning = false; }
    }
}
