using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;

namespace SuzuEmojy.Windows;

internal static class LinkedImageImport
{
    private const long MaxBytes = 64 * 1024 * 1024;
    private static readonly LibraryResult Empty = new(0, 0, 0, null, null, null, null);
    private static readonly HttpClient Client = new(new HttpClientHandler { AllowAutoRedirect = false });

    internal static async Task<LibraryResult?> TryRun(string data, LibraryCommand request, CancellationToken cancellation,
        IProgress<LibraryProgress>? progress, HttpClient? http = null, string? ffmpeg = null)
    {
        if (!TryUrl(request.Value, out var address)) return Empty with { Error = "链接不是有效的 HTTP 或 HTTPS 图片地址。" };
        var directory = Path.Combine(Path.GetTempPath(), $"suzu-link-{Guid.NewGuid():N}");
        string? staged = null;
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        deadline.CancelAfter(TimeSpan.FromSeconds(45));
        var token = deadline.Token;
        try
        {
            progress?.Report(new("download", 0, 0, "正在从网络获取图片，请稍候…"));
            string format;
            try
            {
                using var response = await Get(http ?? Client, address!, token).ConfigureAwait(false);
                var mime = response.Content.Headers.ContentType?.MediaType;
                var final = response.RequestMessage?.RequestUri ?? address!;
                if (!LocalFolderImport.Supported(final.AbsolutePath) && !(mime?.StartsWith("image/", StringComparison.OrdinalIgnoreCase) ?? false) &&
                    !string.Equals(mime, "video/webm", StringComparison.OrdinalIgnoreCase))
                    return Empty with { Error = "该链接不是有效的图片资源。" };
                if (response.Content.Headers.ContentLength > MaxBytes) return Empty with { Error = "图片超过可下载的大小限制。" };
                Directory.CreateDirectory(directory);
                staged = Path.Combine(directory, "image.download");
                using (var input = await response.Content.ReadAsStreamAsync().ConfigureAwait(false))
                using (var abort = token.Register(input.Dispose))
                using (var output = File.Open(staged, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    var buffer = new byte[128 * 1024];
                    long total = 0;
                    int n;
                    while ((n = await input.ReadAsync(buffer, 0, buffer.Length, token).ConfigureAwait(false)) > 0)
                    {
                        total += n;
                        if (total > MaxBytes) return Empty with { Error = "图片超过可下载的大小限制。" };
                        await output.WriteAsync(buffer, 0, n, token).ConfigureAwait(false);
                    }
                    token.ThrowIfCancellationRequested();
                    if (total == 0 || response.Content.Headers.ContentLength is long advertised && total != advertised)
                        return Empty with { Error = "图片没有下载完整。" };
                }
                using (var input = File.OpenRead(staged))
                    format = StickerVideoConverter.IsWebm(input) ? "webm" : NativeLottie.LooksCompressed(input) ? "tgs" :
                        QqCacheScanner.SignatureAtStart(input) ?? "";
            }
            catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { return Empty with { Cancelled = 1 }; }
            catch (Exception error) when (error is HttpRequestException or IOException or OperationCanceledException or ObjectDisposedException
                or NotSupportedException or InvalidOperationException)
            {
                return cancellation.IsCancellationRequested ? Empty with { Cancelled = 1 }
                    : Empty with { Error = deadline.IsCancellationRequested ? "下载图片超时。" : "无法获取网络图片。" };
            }
            if (format.Length == 0) return Empty with { Error = "无法识别下载的图片格式。" };
            var named = Path.Combine(directory, $"image.{format}");
            File.Move(staged, named);
            staged = named;
            return LocalImageImport.TryRun(data, [staged], request.Category, cancellation, progress, ffmpeg);
        }
        finally
        {
            if (staged is not null) RuntimePrimitives.DiscardTemporaryFile(staged);
            try { if (Directory.Exists(directory)) Directory.Delete(directory); }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
        }
    }

    private static async Task<HttpResponseMessage> Get(HttpClient http, Uri first, CancellationToken token)
    {
        Uri current = first;
        for (int count = 0; count <= 10; count++)
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, current);
            request.Headers.UserAgent.ParseAdd("Mozilla/5.0 (Windows NT 10.0; Win64; x64)");
            var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, token).ConfigureAwait(false);
            if (response.StatusCode is HttpStatusCode.Moved or HttpStatusCode.Redirect or HttpStatusCode.RedirectMethod
                or HttpStatusCode.RedirectKeepVerb or (HttpStatusCode)308)
            {
                using (response)
                {
                    var location = response.Headers.Location;
                    if (location is null || !Uri.TryCreate(current, location, out var next) ||
                        !TryUrl(next.AbsoluteUri, out var valid)) throw new IOException("Invalid image redirect.");
                    current = valid!;
                }
                continue;
            }
            if (response.IsSuccessStatusCode) return response;
            response.Dispose();
            throw new IOException("Image request failed.");
        }
        throw new IOException("Too many image redirects.");
    }

    private static bool TryUrl(string? value, out Uri? uri)
    {
        uri = null;
        if (string.IsNullOrEmpty(value) || value!.Length > 8192) return false;
        foreach (char c in value) if (char.IsWhiteSpace(c) || char.IsControl(c) || c == '\\') return false;
        return Uri.TryCreate(value, UriKind.Absolute, out uri) && uri.Scheme is "http" or "https" &&
            uri.Host.Length > 0 && uri.UserInfo.Length == 0;
    }
}
