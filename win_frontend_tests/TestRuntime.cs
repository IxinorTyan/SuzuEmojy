using System;
using System.Net;
using System.Net.Sockets;
using System.IO;
using System.Threading;
using System.Threading.Tasks;

internal sealed class LoopbackListener() : TcpListener(IPAddress.Loopback, 0), IDisposable
{
    void IDisposable.Dispose() => Stop();
}

#if NETFRAMEWORK
// Only the test harness needs these bounded network/dispatcher waits.
internal static class TestRuntime
{
    internal static async Task WaitAsync(this Task task, TimeSpan timeout)
    {
        using var deadline = new CancellationTokenSource();
        if (await Task.WhenAny(task, Task.Delay(timeout, deadline.Token)) != task)
            throw new TimeoutException();
        deadline.Cancel();
        await task;
    }

    internal static async Task<T> WaitAsync<T>(this Task<T> task, TimeSpan timeout)
    {
        await ((Task)task).WaitAsync(timeout);
        return await task;
    }

    private static async Task<T> WithCancellation<T>(this Task<T> task, CancellationToken cancellation)
    {
        var cancelled = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        using (cancellation.Register(() => cancelled.TrySetResult(true)))
            if (await Task.WhenAny(task, cancelled.Task) != task) throw new OperationCanceledException(cancellation);
        return await task;
    }

    internal static Task<TcpClient> AcceptTcpClientAsync(this TcpListener server, CancellationToken cancellation) =>
        server.AcceptTcpClientAsync().WithCancellation(cancellation);

    internal static Task<string> ReadLineAsync(this StreamReader reader, CancellationToken cancellation) =>
        reader.ReadLineAsync().WithCancellation(cancellation);

    internal static Task WriteAsync(this Stream stream, byte[] buffer, CancellationToken cancellation) =>
        stream.WriteAsync(buffer, 0, buffer.Length, cancellation);
}
#endif
