#if NETFRAMEWORK
using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace SuzuEmojy.Windows;

// Framework's HttpClient cannot negotiate HTTPS proxies or SOCKS5. Use the
// inbox Windows curl only for those explicit proxies, with system TLS checks.
internal sealed class WindowsTelegramProxy(string executable, Uri proxy) : HttpMessageHandler
{
    internal static string Executable => Path.Combine(Environment.SystemDirectory, "curl.exe");

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        if (request.Method != HttpMethod.Get || request.RequestUri is not { Scheme: "https" } uri)
            throw new IOException(TelegramStickerClient.Failure);
        // Credentials stay out of the process command line, and curl never
        // reads config from the user's profile or follows server redirects.
        var start = new ProcessStartInfo(executable, "-q --config -")
        {
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true
        };
        using var curl = Process.Start(start) ?? throw new IOException(TelegramStickerClient.Failure);
        using var abort = cancellationToken.Register(Stop);
        try
        {
            // Discard diagnostics instead of recording URLs, tokens, or proxy passwords.
            var stderr = curl.StandardError.BaseStream.CopyToAsync(Stream.Null);
            int limit = uri.AbsolutePath.StartsWith("/file/", StringComparison.Ordinal) ||
                uri.AbsolutePath.Contains("/file/bot") ? 64 * 1024 * 1024 : 4 * 1024 * 1024;
            var config = "silent\ngloboff\nnoproxy = \"\"\nproto = \"=https\"\n" +
                $"max-time = 10\nmax-filesize = {limit}\nwrite-out = \"%{{http_code}}\"\n" +
                $"url = {Quote(uri.AbsoluteUri)}\nproxy = {Quote(proxy.AbsoluteUri)}\n";
            var bytes = Encoding.UTF8.GetBytes(config);
            await curl.StandardInput.BaseStream.WriteAsync(bytes, 0, bytes.Length, cancellationToken).ConfigureAwait(false);
            curl.StandardInput.Close();
            using var output = new MemoryStream();
            var source = curl.StandardOutput.BaseStream;
            var buffer = new byte[32 * 1024];
            for (int count; (count = await source.ReadAsync(buffer, 0, buffer.Length, cancellationToken).ConfigureAwait(false)) > 0;)
            {
                if (output.Length + count > limit + 3L) throw new IOException(TelegramStickerClient.Failure);
                output.Write(buffer, 0, count);
            }
            await curl.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
            await stderr.ConfigureAwait(false);
            cancellationToken.ThrowIfCancellationRequested();
            int length = (int)output.Length - 3;
            if (curl.ExitCode != 0 || length < 0 ||
                !int.TryParse(Encoding.ASCII.GetString(output.GetBuffer(), length, 3), NumberStyles.None,
                    CultureInfo.InvariantCulture, out int status) || status is < 100 or > 599)
                throw new IOException(TelegramStickerClient.Failure);
            return new HttpResponseMessage((HttpStatusCode)status)
            {
                RequestMessage = request,
                Content = new ByteArrayContent(output.GetBuffer(), 0, length)
            };
        }
        catch (Exception error) when (error is IOException or InvalidOperationException)
        {
            cancellationToken.ThrowIfCancellationRequested();
            throw new IOException(TelegramStickerClient.Failure);
        }
        finally { Stop(); }

        void Stop()
        {
            try { if (!curl.HasExited) curl.Kill(); }
            catch (Exception error) when (error is InvalidOperationException or System.ComponentModel.Win32Exception) { }
        }
    }

    private static string Quote(string value) => "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
}
#endif
