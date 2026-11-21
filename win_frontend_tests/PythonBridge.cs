using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace SuzuEmojy.Windows;

internal static class PythonBridge
{
    // Test oracle only; never compiled into the Windows app or added to its ZIP.
    internal static string PythonExecutable(string root) => File.Exists(Path.Combine(root, "windows-preview.json"))
        ? Path.Combine(root, "python", "python.exe") : Environment.GetEnvironmentVariable("SUZU_PYTHON") ?? "python.exe";

    internal static async Task<string> Run(string repository, string module, TimeSpan limit,
        CancellationToken cancellation, params string[] arguments) =>
        await RunCore(repository, module, limit, cancellation, null, arguments, null).ConfigureAwait(false);

    internal static Task<string> Request(string repository, string module, string input, string data,
        CancellationToken cancellation, Action<string>? progress) =>
        RunCore(repository, module, Timeout.InfiniteTimeSpan, cancellation, input, [data], progress);

    private static async Task<string> RunCore(string repository, string module, TimeSpan limit,
        CancellationToken cancellation, string? input, string[] arguments, Action<string>? progress)
    {
        cancellation.ThrowIfCancellationRequested();
        var info = new ProcessStartInfo(PythonExecutable(repository))
        {
            WorkingDirectory = repository, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true, RedirectStandardInput = input is not null,
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
        };
#if NETFRAMEWORK
        info.Arguments = string.Join(" ", new[] { "-B", "-m", module }.Concat(arguments).Select(WindowsCommandLine.QuoteArgument));
#else
        if (input is not null) info.StandardInputEncoding = new UTF8Encoding(false);
        foreach (var arg in new[] { "-B", "-m", module }) info.ArgumentList.Add(arg);
        foreach (var arg in arguments) info.ArgumentList.Add(arg);
#endif
        using var process = Process.Start(info) ?? throw new InvalidOperationException("无法启动 Python。");
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        timeout.CancelAfter(limit);
        var stdout = process.StandardOutput.ReadToEndAsync();
        var stderr = progress is null ? process.StandardError.ReadToEndAsync() : ReadProgress(process.StandardError, progress);
        try
        {
            if (input is not null)
            {
#if NETFRAMEWORK
                var utf8 = Encoding.UTF8.GetBytes(input);
                await process.StandardInput.BaseStream.WriteAsync(utf8, 0, utf8.Length, timeout.Token).ConfigureAwait(false);
#else
                await process.StandardInput.WriteAsync(input.AsMemory(), timeout.Token).ConfigureAwait(false);
#endif
                process.StandardInput.Close();
            }
            await process.WaitForExitAsync(timeout.Token).ConfigureAwait(false);
            var result = await stdout.ConfigureAwait(false);
            var diagnostic = await stderr.ConfigureAwait(false);
            if (process.ExitCode != 0)
                throw new InvalidOperationException(diagnostic);
            return result;
        }
        catch (OperationCanceledException)
        {
            try { if (!process.HasExited) process.Kill(); }
            catch (InvalidOperationException) { /* The helper exited concurrently. */ }
            await process.WaitForExitAsync().ConfigureAwait(false);
            await Task.WhenAll(stdout, stderr).ConfigureAwait(false);
            cancellation.ThrowIfCancellationRequested();
            throw new TimeoutException($"Python module {module} timed out.");
        }
    }

    private static async Task<string> ReadProgress(StreamReader reader, Action<string> progress)
    {
        const string prefix = "SUZU_PROGRESS ";
        var diagnostic = new StringBuilder();
        while (await reader.ReadLineAsync().ConfigureAwait(false) is { } line)
        {
            if (line.StartsWith(prefix, StringComparison.Ordinal)) progress(line.Substring(prefix.Length));
            else diagnostic.AppendLine(line);
        }
        return diagnostic.ToString();
    }
}
