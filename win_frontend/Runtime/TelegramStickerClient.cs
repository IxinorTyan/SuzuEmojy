using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace SuzuEmojy.Windows;

internal sealed class TelegramStickerClient : IDisposable
{
    internal const string Failure = "无法连接 Telegram 或处理贴纸，请检查网络、Bot Token 和贴纸包";
    private const string Official = "https://api.telegram.org";
    private static readonly Regex NamePattern = new(@"\A[A-Za-z0-9_]{1,128}\z", RegexOptions.CultureInvariant);
    private static readonly Regex IdPattern = new(@"\A[A-Za-z0-9_-]{1,1024}\z", RegexOptions.CultureInvariant);
    private static readonly Regex TokenPattern = new(@"\A[0-9]+:[A-Za-z0-9_-]+\z", RegexOptions.CultureInvariant);
    private static readonly Regex LinkPattern = new(
        @"\A(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/(?:addstickers|addemoji)/([A-Za-z0-9_]{1,128})(?:[/?#].*)?\z",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
    private static readonly Regex PathPart = new(@"\A[A-Za-z0-9_.-]{1,128}\z", RegexOptions.CultureInvariant);
    private readonly HttpClient http;
    private readonly string token;
    private readonly string firstBase;
    private readonly string proxy;
    private string? activeBase;

    internal sealed record Pack(string Name, string Title, string SourceUrl, ExternalSticker[] Items);
    internal sealed record Asset(byte[] Bytes, string Format);
    internal sealed class MissingSticker : Exception;

    private TelegramStickerClient(string token, string proxy, bool preferDirect, HttpMessageHandler handler)
    {
        this.token = token;
        this.proxy = proxy.TrimEnd('/');
        firstBase = preferDirect || this.proxy.Length == 0 ? Official : this.proxy;
        http = new(handler) { Timeout = Timeout.InfiniteTimeSpan };
    }

    internal static TelegramStickerClient? Create(LibraryCommand request, out string? error, HttpMessageHandler? handler = null,
        Func<string, string?>? readDefaults = null)
    {
        var (token, proxy) = TelegramConnectionDefaults.Resolve(request, readDefaults ?? Environment.GetEnvironmentVariable);
        bool preferDirect = string.IsNullOrWhiteSpace(request.ApiProxy);
        string network = request.NetworkProxy?.Trim() ?? "";
        error = null;
        if (!TokenPattern.IsMatch(token))
        {
            error = "请输入有效的 Bot Token";
            return null;
        }
        if (proxy.Length != 0 && (!ProxyUri(proxy, out var url) || url!.Scheme != "https" || url.UserInfo.Length != 0))
        {
            error = "API 反代地址必须为 HTTPS";
            return null;
        }
        Uri? transport = null;
        if (network.Length != 0 && (!ProxyUri(network, out transport) || transport!.AbsolutePath != "/" ||
            transport.Scheme is not ("http" or "https" or "socks5" or "socks5h") || !HasPort(transport)))
        {
            error = "本地代理需要有效的 HTTP、HTTPS 或 SOCKS5 地址和端口";
            return null;
        }
        if (handler is null)
        {
#if NETFRAMEWORK
            if (transport is not null && transport.Scheme != "http")
            {
                if (!File.Exists(WindowsTelegramProxy.Executable))
                {
                    error = "系统缺少 curl.exe，无法使用此代理协议。";
                    return null;
                }
                return new(token, proxy, preferDirect,
                    new WindowsTelegramProxy(WindowsTelegramProxy.Executable, transport));
            }
#else
            if (transport?.Scheme == "socks5h") transport = new UriBuilder(transport) { Scheme = "socks5" }.Uri;
#endif
            var networkHandler = new HttpClientHandler { AllowAutoRedirect = false, UseCookies = false,
                AutomaticDecompression = DecompressionMethods.GZip | DecompressionMethods.Deflate };
            if (transport is not null)
            {
                var proxyAddress = new UriBuilder(transport);
                string user = proxyAddress.UserName, password = proxyAddress.Password;
                proxyAddress.UserName = proxyAddress.Password = "";
                var web = new WebProxy(proxyAddress.Uri);
                if (user.Length > 0) web.Credentials = new NetworkCredential(Uri.UnescapeDataString(user), Uri.UnescapeDataString(password));
                networkHandler.Proxy = web;
            }
            handler = networkHandler;
        }
        return new(token, proxy, preferDirect, handler);
    }

    internal static string? ParseName(string? input)
    {
        var value = input?.Trim() ?? "";
        if (NamePattern.IsMatch(value)) return value;
        var match = LinkPattern.Match(value);
        if (match.Success) return match.Groups[1].Value;
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri) || uri.Scheme != "tg" || uri.UserInfo.Length > 0) return null;
        var fields = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var field in uri.Query.TrimStart('?').Split('&'))
        {
            int mark = field.IndexOf('=');
            if (mark <= 0) continue;
            var name = Uri.UnescapeDataString(field.Substring(0, mark).Replace('+', ' '));
            var fieldValue = Uri.UnescapeDataString(field.Substring(mark + 1).Replace('+', ' '));
            if (fieldValue.Length > 0 && !fields.ContainsKey(name)) fields.Add(name, fieldValue);
        }
        var route = uri.Host == "resolve" ? fields.GetValueOrDefault("domain")?.ToLowerInvariant() : uri.Host.ToLowerInvariant();
        var result = fields.GetValueOrDefault("set") ?? fields.GetValueOrDefault("start");
        return route is "addstickers" or "addemoji" && result is not null && NamePattern.IsMatch(result) ? result : null;
    }

    internal async Task Check(CancellationToken cancellation)
    {
        var result = await Api("getMe", null, cancellation).ConfigureAwait(false);
        if (!Flag(result, "is_bot", false)) throw new IOException(Failure);
    }

    internal async Task<Pack> ReadPack(string name, CancellationToken cancellation)
    {
        if (!NamePattern.IsMatch(name)) throw new ArgumentException("请输入 Telegram 贴纸包链接或包名");
        var root = await Api("getStickerSet", ("name", name), cancellation).ConfigureAwait(false);
        string actual = String(root, "name") ?? name;
        if (!NamePattern.IsMatch(actual)) throw new ArgumentException("Telegram 返回了无效的贴纸包名");
        var items = new List<ExternalSticker>();
        if (root.TryGetProperty("stickers", out var stickers) && stickers.ValueKind == JsonValueKind.Array)
        {
            int index = 0;
            foreach (var sticker in stickers.EnumerateArray())
            {
                var id = String(sticker, "file_id");
                int row = index++;
                if (id is null || !IdPattern.IsMatch(id)) continue;
                string? thumbnail = null;
                if (sticker.TryGetProperty("thumbnail", out var thumb) || sticker.TryGetProperty("thumb", out thumb))
                    thumbnail = String(thumb, "file_id");
                items.Add(new(id, row, Redact(String(sticker, "emoji") ?? ""),
                    thumbnail is not null && IdPattern.IsMatch(thumbnail) ? thumbnail : null,
                    Flag(sticker, "is_animated", Flag(root, "is_animated", false)) || Flag(sticker, "is_video", Flag(root, "is_video", false)),
                    Number(sticker, "width", 512), Number(sticker, "height", 512)));
            }
        }
        var route = String(root, "sticker_type") == "custom_emoji" ? "addemoji" : "addstickers";
        return new(actual, Redact(String(root, "title") ?? name), $"https://t.me/{route}/{actual}", items.ToArray());
    }

    internal async Task<Asset> Download(string id, CancellationToken cancellation)
    {
        if (!IdPattern.IsMatch(id)) throw new IOException(Failure);
        var file = await Api("getFile", ("file_id", id), cancellation).ConfigureAwait(false);
        string? path = String(file, "file_path");
        if (path is null || path.Length > 1024) throw new IOException(Failure);
        var parts = path.TrimStart('/').Split('/');
        if (parts.Any(part => part is "." or ".." || !PathPart.IsMatch(part))) throw new IOException(Failure);
        var extension = Path.GetExtension(parts.Last()).TrimStart('.').ToLowerInvariant();
        var suffix = string.Join("/", parts.Select(Uri.EscapeDataString));
        bool missing = false;
        foreach (var origin in Bases())
        {
            using var deadline = Deadline(cancellation);
            try
            {
                using var response = await Get($"{origin}/file/bot{token}/{suffix}", deadline.Token).ConfigureAwait(false);
                if (origin == Official && response.StatusCode is HttpStatusCode.NotFound or HttpStatusCode.Gone)
                { missing = true; continue; }
                if (!response.IsSuccessStatusCode) continue;
                var content = await Read(response, 64 * 1024 * 1024, deadline.Token).ConfigureAwait(false);
                if (content.Length == 0) continue;
                activeBase = origin;
                return new(content, extension);
            }
            catch (Exception error) when (!cancellation.IsCancellationRequested && NetworkFailure(error)) { }
        }
        cancellation.ThrowIfCancellationRequested();
        if (missing) throw new MissingSticker();
        throw new IOException(Failure);
    }

    private async Task<JsonElement> Api(string method, (string Name, string Value)? query, CancellationToken cancellation)
    {
        bool missing = false;
        foreach (var origin in Bases())
        {
            using var deadline = Deadline(cancellation);
            try
            {
                string args = query is { } pair ? $"?{pair.Name}={Uri.EscapeDataString(pair.Value)}" : "";
                using var response = await Get($"{origin}/bot{token}/{method}{args}", deadline.Token).ConfigureAwait(false);
                if ((int)response.StatusCode is >= 300 and < 400 || (int)response.StatusCode == 429) continue;
                var content = await Read(response, 4 * 1024 * 1024, deadline.Token).ConfigureAwait(false);
                using var doc = JsonDocument.Parse(content);
                var root = doc.RootElement;
                if (response.IsSuccessStatusCode && Flag(root, "ok", false) && root.TryGetProperty("result", out var result) &&
                    result.ValueKind == JsonValueKind.Object)
                {
                    activeBase = origin;
                    return result.Clone();
                }
                if (method == "getFile" && Number(root, "error_code", 0) is 400 or 404 or 410 &&
                    String(root, "description")?.ToLowerInvariant() is { } description &&
                    !description.Contains("temporar") && new[] { "file not found", "file_id not found", "file_id_invalid",
                        "invalid file_id", "file has been deleted" }.Any(description.Contains)) missing = true;
            }
            catch (Exception error) when (!cancellation.IsCancellationRequested && NetworkFailure(error)) { }
        }
        cancellation.ThrowIfCancellationRequested();
        if (missing) throw new MissingSticker();
        throw new IOException(Failure);
    }

    private Task<HttpResponseMessage> Get(string url, CancellationToken cancellation) =>
        http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, cancellation);

    private IEnumerable<string> Bases()
    {
        var first = activeBase ?? firstBase;
        yield return first;
        var second = first == Official ? proxy : Official;
        if (second.Length > 0 && second != first) yield return second;
    }

    private static CancellationTokenSource Deadline(CancellationToken token)
    {
        var deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
        deadline.CancelAfter(TimeSpan.FromSeconds(10));
        return deadline;
    }

    private static async Task<byte[]> Read(HttpResponseMessage response, int limit, CancellationToken cancellation)
    {
        if (response.Content.Headers.ContentLength > limit) throw new IOException(Failure);
        using var source = await response.Content.ReadAsStreamAsync().ConfigureAwait(false);
        using var abort = cancellation.Register(source.Dispose);
        using var output = new MemoryStream();
        var buffer = new byte[32 * 1024];
        for (int n; (n = await source.ReadAsync(buffer, 0, buffer.Length, cancellation).ConfigureAwait(false)) > 0;)
        {
            if (output.Length + n > limit) throw new IOException(Failure);
            output.Write(buffer, 0, n);
        }
        cancellation.ThrowIfCancellationRequested();
        if (response.Content.Headers.ContentLength is long declared && declared != output.Length) throw new IOException(Failure);
        return output.ToArray();
    }

    private static bool ProxyUri(string value, out Uri? uri)
    {
        uri = null;
        return value.Length <= 4096 && !value.Any(c => char.IsControl(c) || char.IsWhiteSpace(c) || c == '\\' || c == '#' || c == '?') &&
            Uri.TryCreate(value, UriKind.Absolute, out uri) && uri.Host.Length > 0 && uri.Port != 0;
    }

    private static bool HasPort(Uri uri)
    {
        // Uri.IsDefaultPort can't tell an explicit :80 from an absent port.
        var authority = uri.OriginalString.Split(new[] { "://" }, 2, StringSplitOptions.None)[1].Split('/')[0];
        authority = authority.Substring(authority.LastIndexOf('@') + 1);
        return Regex.IsMatch(authority, @":\d+\z", RegexOptions.CultureInvariant) && uri.Port is > 0 and <= 65535;
    }

    private static bool Flag(JsonElement element, string name, bool fallback) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(name, out var value)
            ? value.ValueKind == JsonValueKind.True : fallback;
    private static string? String(JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() : null;
    private static int Number(JsonElement element, string name, int fallback) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number &&
        value.TryGetInt32(out int number) ? number : fallback;
    private static bool NetworkFailure(Exception error) => error is IOException or HttpRequestException or
        JsonException or InvalidOperationException or OperationCanceledException;
    internal string Redact(string value)
    {
        value = value.Replace(token, "***");
        return proxy.Length == 0 ? value : value.Replace(proxy, "***");
    }
    public void Dispose() => http.Dispose();
}
