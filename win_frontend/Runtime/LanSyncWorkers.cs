using System;
using System.Threading;
using System.Threading.Tasks;

namespace SuzuEmojy.Windows;

internal static class LanSyncWorkers
{
    private static readonly int Count = Math.Min(4, Math.Max(1, Environment.ProcessorCount / 2));

    // Keep ordered output and bound concurrent disk/decoder work. Each worker
    // owns its stream and decoder; archive writers and transactions stay serial.
    internal static T[] Map<T>(int length, Func<int, T> work, CancellationToken token)
    {
        var results = new T[length];
        if (length < 8 || Count == 1)
            for (int i = 0; i < length; i++) { token.ThrowIfCancellationRequested(); results[i] = work(i); }
        else
            Parallel.For(0, length, new ParallelOptions { CancellationToken = token, MaxDegreeOfParallelism = Count },
                i => results[i] = work(i));
        return results;
    }
}
