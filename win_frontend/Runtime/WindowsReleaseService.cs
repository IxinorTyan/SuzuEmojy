using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace SuzuEmojy.Windows;

internal sealed record AvailableRelease(string Version, string Notes, string? Name = null);

// Package discovery/download is independent of both the gallery and the future installer.
internal sealed class WindowsReleaseService(string data, HttpClient? transport = null)
{
    private const string Latest = "https://api.github.com/repos/IxinorTyan/SuzuEmojy/releases/latest";
    internal const string Releases = "https://github.com/IxinorTyan/SuzuEmojy/releases";
    private const long MaxDownload = 20_000_000;
    private static readonly HttpClient Client = new(new HttpClientHandler { AllowAutoRedirect = false });
    private readonly HttpClient http = transport ?? Client;
    private static readonly Regex StableTag = new(@"\Av?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\z",
        RegexOptions.CultureInvariant);
    private static readonly Regex Digest = new(@"\Asha256:[0-9a-f]{64}\z", RegexOptions.CultureInvariant);

    private sealed record Asset(AvailableRelease Release, Uri Url, long Length, string Hash);

    internal async Task<AvailableRelease?> Check(string installed, CancellationToken cancellation) =>
        (await Find(installed, cancellation).ConfigureAwait(false))?.Release;

    private async Task<Asset?> Find(string installed, CancellationToken cancellation)
    {
        using var request = Request(new Uri(Latest));
        request.Headers.Accept.ParseAdd("application/vnd.github+json");
        using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellation).ConfigureAwait(false);
        if (response.StatusCode == HttpStatusCode.NotFound) return null;
        response.EnsureSuccessStatusCode(); // Do not read metadata through redirects or mirrors.
        using var stream = await response.Content.ReadAsStreamAsync().ConfigureAwait(false);
        using var abort = AbortRead(stream, cancellation);
        using var body = new MemoryStream();
        await Transfer(stream, body, 2 * 1024 * 1024, cancellation).ConfigureAwait(false);
        body.Position = 0;
        using var document = JsonDocument.Parse(body);
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object || !Read(root, "tag_name", out string? tag))
            throw new InvalidDataException("无法读取发布信息。");
        if (Flag(root, "draft") || Flag(root, "prerelease") || !ParseVersion(tag!, out var remote) || remote.Major != 2)
            return null;
        if (!PackageLayout.NewerStable(remote.ToString(), installed)) return null;

        var name = $"SuzuEmojy-Windows-{remote}-win-x64-system-runtime.zip";
        var expected = $"{Releases}/download/{tag}/{name}";
        if (!root.TryGetProperty("assets", out var assets) || assets.ValueKind != JsonValueKind.Array) return null;
        Asset? selected = null;
        int matching = 0;
        foreach (var asset in assets.EnumerateArray())
        {
            if (asset.ValueKind != JsonValueKind.Object || !Read(asset, "name", out var file) || file != name ||
                !Read(asset, "state", out var state) || state != "uploaded") continue;
            if (++matching > 1) return null;
            if (!Read(asset, "digest", out var digest) || !Digest.IsMatch(digest!) ||
                !Read(asset, "browser_download_url", out var url) || url != expected ||
                !asset.TryGetProperty("size", out var size) || size.ValueKind != JsonValueKind.Number ||
                !size.TryGetInt64(out long length) || length <= 0 || length > MaxDownload) continue;
            Read(root, "body", out var notes);
            if (notes is { Length: > 16000 }) notes = notes.Substring(0, 16000);
            selected = new(new(remote.ToString(), notes ?? "", name), new Uri(expected), length, digest!.Substring(7));
        }
        return selected;
    }

    internal async Task<string> Save(string installed, string expectedVersion, string destination, IProgress<LibraryProgress> progress,
        CancellationToken cancellation)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        deadline.CancelAfter(TimeSpan.FromMinutes(15));
        cancellation = deadline.Token;
        if (!RuntimePrimitives.IsAbsolutePath(destination) || !string.Equals(Path.GetExtension(destination), ".zip", StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("请选择 ZIP 保存位置");
        var directory = RuntimePrimitives.ExistingPath(Path.GetDirectoryName(Path.GetFullPath(destination))!);
        var root = Directory.Exists(data) ? RuntimePrimitives.ExistingPath(data) : Path.GetFullPath(data);
        if (RuntimePrimitives.IsWithin(root, directory)) throw new ArgumentException("请选择图库以外的位置");
        var target = Path.Combine(directory, Path.GetFileName(destination));
        bool overwrite = File.Exists(target);
        if (overwrite && (File.GetAttributes(target) & FileAttributes.ReparsePoint) != 0)
            throw new ArgumentException("请选择普通 ZIP 文件");

        var asset = await Find(installed, cancellation).ConfigureAwait(false);
        if (asset is null || asset.Release.Version != expectedVersion)
            throw new InvalidDataException("可用版本已经变化，请重新检查更新。");
        var temporary = Path.Combine(directory, $".suzu-update-{Guid.NewGuid():N}.part");
        try
        {
            using (var response = await OpenDownload(asset.Url, cancellation).ConfigureAwait(false))
            using (var input = await response.Content.ReadAsStreamAsync().ConfigureAwait(false))
            using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None, 81920, true))
            {
                using var abort = AbortRead(input, cancellation);
                using var sha = SHA256.Create();
                long length = await Transfer(input, output, asset.Length, cancellation, sha, progress).ConfigureAwait(false);
                sha.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                if (length != asset.Length || !string.Equals(BitConverter.ToString(sha.Hash!).Replace("-", ""), asset.Hash,
                        StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("更新包大小或 SHA-256 校验失败。");
                output.Flush(flushToDisk: true);
                output.Position = 0;
                using var zip = new ZipArchive(output, ZipArchiveMode.Read, leaveOpen: true);
                PackageLayout.Validate(zip, expectedVersion);
            }
            if (!RuntimePrimitives.CommitFile(temporary, target, overwrite, cancellation))
                throw new IOException("保存位置已有同名文件，未覆盖。");
            progress.Report(new("download", 100, 100, null));
            return asset.Hash;
        }
        catch (OperationCanceledException) when (!deadline.IsCancellationRequested && !cancellation.IsCancellationRequested)
        {
            throw new IOException("更新下载超时。");
        }
        finally { RuntimePrimitives.DiscardTemporaryFile(temporary); }
    }

    private async Task<HttpResponseMessage> OpenDownload(Uri url, CancellationToken cancellation)
    {
        for (int hops = 0; hops < 5; hops++)
        {
            using var request = Request(url);
            var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellation).ConfigureAwait(false);
            if ((int)response.StatusCode is >= 300 and < 400)
            {
                var location = response.Headers.Location;
                response.Dispose();
                if (location is null || !Uri.TryCreate(url, location, out var redirect) || redirect.Scheme != "https" ||
                    !redirect.IsDefaultPort || redirect.UserInfo.Length != 0 ||
                    redirect.Host is not ("github.com" or "release-assets.githubusercontent.com" or "objects.githubusercontent.com"))
                    throw new InvalidDataException("更新包重定向地址无效。");
                url = redirect;
                continue;
            }
            if (response.IsSuccessStatusCode) return response;
            using (response) response.EnsureSuccessStatusCode();
        }
        throw new InvalidDataException("更新包重定向次数过多。");
    }

    private static async Task<long> Transfer(Stream input, Stream output, long limit, CancellationToken cancellation,
        HashAlgorithm? digest = null, IProgress<LibraryProgress>? progress = null)
    {
        var buffer = new byte[81920];
        long length = 0;
        int previous = -1;
        while (true)
        {
            cancellation.ThrowIfCancellationRequested();
            int read;
            try { read = await input.ReadAsync(buffer, 0, buffer.Length, cancellation).ConfigureAwait(false); }
            catch (Exception error) when (cancellation.IsCancellationRequested &&
                error is IOException or ObjectDisposedException or HttpRequestException or WebException)
            { throw new OperationCanceledException(cancellation); }
            cancellation.ThrowIfCancellationRequested();
            if (read == 0) return length;
            length += read;
            if (length > limit) throw new InvalidDataException("更新数据超出声明大小。");
            digest?.TransformBlock(buffer, 0, read, null, 0);
            await output.WriteAsync(buffer, 0, read, cancellation).ConfigureAwait(false);
            int percent = (int)(length * 95 / limit);
            if (progress is not null && percent != previous)
            {
                previous = percent;
                progress.Report(new("download", percent, 100, null));
            }
        }
    }

    private static CancellationTokenRegistration AbortRead(Stream input, CancellationToken cancellation) => cancellation.Register(() =>
    {
        // Framework's HTTP stream can ignore the ReadAsync token once the read is pending.
        try { input.Dispose(); }
        catch (Exception error) when (error is IOException or ObjectDisposedException or WebException) { }
    });

    private static HttpRequestMessage Request(Uri url)
    {
        var request = new HttpRequestMessage(HttpMethod.Get, url);
        request.Headers.UserAgent.ParseAdd("SuzuEmojy-Windows-Updater/2");
        return request;
    }

    private static bool ParseVersion(string value, out Version version)
    {
        version = new Version(0, 0, 0);
        if (!StableTag.IsMatch(value) || !Version.TryParse(value.TrimStart('v'), out var parsed)) return false;
        version = parsed;
        return true;
    }

    private static bool Read(JsonElement element, string key, out string? value)
    {
        value = element.TryGetProperty(key, out var item) && item.ValueKind == JsonValueKind.String ? item.GetString() : null;
        return value is not null;
    }

    private static bool Flag(JsonElement element, string key) =>
        element.TryGetProperty(key, out var item) && item.ValueKind == JsonValueKind.True;
}
