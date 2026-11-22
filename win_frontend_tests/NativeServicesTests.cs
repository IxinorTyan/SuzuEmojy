using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using SuzuEmojy.Windows;

internal static class NativeServicesTests
{
    internal static async Task Run(string output)
    {
        var workspace = Path.Combine(output, $"独立服务-{Guid.NewGuid():N}");
        var data = Path.Combine(workspace, "图库");
        var images = Directory.CreateDirectory(Path.Combine(data, "images")).FullName;
        var copies = Directory.CreateDirectory(Path.Combine(workspace, "导出")).FullName;
        var config = Path.Combine(data, "config.json");
        // A package marker with no private interpreter proves these paths do not invoke the bridge.
        var unavailable = Directory.CreateDirectory(Path.Combine(workspace, "broken-backend")).FullName;
        File.WriteAllText(Path.Combine(unavailable, "windows-preview.json"), "{}");
        var native = new LibraryCommands(unavailable, data);
        File.WriteAllText(config, "{\"qq_save_path\":\"旧路径\",\"legacy\":[1,\"旧值\",null]}", new UTF8Encoding(true));
        var written = await native.SaveSettings(new Dictionary<string, object> { ["appearance_mode"] = "dark" });
        Require(written["appearance_mode"].GetString() == "dark" && written["legacy"][1].GetString() == "旧值" &&
                written["qq_save_path"].GetString() == "旧路径", "Settings must preserve unrecognized legacy preferences and accept a UTF-8 BOM.");
        var baseline = File.ReadAllBytes(config);
        await Reject<ArgumentException>(() => native.SaveSettings(new Dictionary<string, object> { ["preview_size"] = 8000 }));
        Require(baseline.SequenceEqual(File.ReadAllBytes(config)), "Invalid settings must leave the saved config intact.");
        File.WriteAllText(config, "{broken");
        await Reject<JsonException>(() => native.SaveSettings(new Dictionary<string, object> { ["always_on_top"] = true }));
        Require(File.ReadAllText(config) == "{broken", "Malformed old settings must not be replaced silently.");

        var picture = Path.Combine(images, "旧图.gif");
        File.WriteAllBytes(picture, [1, 2, 3, 4]);
        File.WriteAllText(Path.Combine(copies, "旧图.gif"), "keep");
        var command = new LibraryCommand("export_files", [picture, picture], Value: copies);
        var exported = await native.Run(command, reload: false);
        Require(exported.Result.Count == 1 && File.ReadAllText(Path.Combine(copies, "旧图.gif")) == "keep" &&
                File.ReadAllBytes(Path.Combine(copies, "旧图_1.gif")).SequenceEqual(File.ReadAllBytes(picture)),
            "Direct export is a byte-for-byte copy with non-overwriting collision names.");
        await Reject<ArgumentException>(() => native.Run(command with { Value = images }, reload: false));
        await Reject<ArgumentException>(() => native.Run(command with { Paths = [config] }, reload: false));
        Require(!Directory.EnumerateFiles(copies, "*.part").Any(), "The export folder must not contain temporary files.");

        var package = CreatePackage();
        using var sha = SHA256.Create();
        var digest = BitConverter.ToString(sha.ComputeHash(package)).Replace("-", "").ToLowerInvariant();
        var packageName = "SuzuEmojy-Windows-2.0.0-win-x64-system-runtime.zip";
        var releaseUrl = $"{WindowsReleaseService.Releases}/download/v2.0.0/{packageName}";
        bool prerelease = false, hostileRedirect = false;
        string declared = digest;
        int requests = 0;
        using var http = new HttpClient(new Replies(request =>
        {
            requests++;
            if (request.RequestUri!.Host == "api.github.com")
            {
                return new(HttpStatusCode.OK) { Content = new StringContent(JsonSerializer.Serialize(new
                {
                    tag_name = "v2.0.0", body = "更新说明", prerelease,
                    assets = new[] { new { name = packageName, state = "uploaded", digest = "sha256:" + declared,
                        size = package.Length, browser_download_url = releaseUrl } }
                })) };
            }
            if (request.RequestUri.AbsoluteUri == releaseUrl)
                return new(HttpStatusCode.Found) { Headers = { Location = new Uri(hostileRedirect
                    ? "https://mirror.invalid/download" : "https://release-assets.githubusercontent.com/download") } };
            Require(request.RequestUri.Host == "release-assets.githubusercontent.com", "Unexpected update host was contacted.");
            return new(HttpStatusCode.OK) { Content = new ByteArrayContent(package) };
        }));
        var releases = new WindowsReleaseService(data, http);
        Require(await releases.Check("2.0.0-preview", CancellationToken.None) == new AvailableRelease("2.0.0", "更新说明", packageName) &&
                await releases.Check("2.0.0", CancellationToken.None) is null,
            "A matching stable version must update the preview but not an equal stable release.");
        prerelease = true;
        Require(await releases.Check("2.0.0-preview", CancellationToken.None) is null, "Prereleases must not enter the stable channel.");
        prerelease = false;
        var target = Path.Combine(copies, "更新.zip");
        File.WriteAllText(target, "keep old download");
        var progress = new Observer(_ => { });
        await Reject<ArgumentException>(() => releases.Save("2.0.0-preview", "2.0.0", Path.Combine(images, "更新.zip"), progress, CancellationToken.None));
        hostileRedirect = true;
        int before = requests;
        await Reject<InvalidDataException>(() => releases.Save("2.0.0-preview", "2.0.0", target, progress, CancellationToken.None));
        Require(requests == before + 2, "Update downloads must not follow a third-party redirect.");
        hostileRedirect = false;
        declared = new string('0', 64);
        await Reject<InvalidDataException>(() => releases.Save("2.0.0-preview", "2.0.0", target, progress, CancellationToken.None));
        declared = digest;
        using var cancellation = new CancellationTokenSource();
        await Reject<OperationCanceledException>(() => releases.Save("2.0.0-preview", "2.0.0", target,
            new Observer(_ => cancellation.Cancel()), cancellation.Token));
        Require(File.ReadAllText(target) == "keep old download" && !Directory.EnumerateFiles(copies, "*.part").Any(),
            "Failed or cancelled updates must not replace the prior ZIP or leave a partial ZIP.");
        await releases.Save("2.0.0-preview", "2.0.0", target, progress, CancellationToken.None);
        Require(File.ReadAllBytes(target).SequenceEqual(package), "Only the complete verified ZIP may be published.");
    }

    internal static byte[] CreatePackage(string version = "2.0.0", IEnumerable<string>? extra = null)
    {
        using var stream = new MemoryStream();
        using (var zip = new ZipArchive(stream, ZipArchiveMode.Create, leaveOpen: true))
            foreach (var path in PackageLayout.Required.Concat(extra ?? Array.Empty<string>()))
            {
                using var entry = zip.CreateEntry(path).Open();
                if (path == "bin/" + PortableLayout.Marker)
                {
                    using var output = new StreamWriter(entry, new UTF8Encoding(true));
                    output.Write(JsonSerializer.Serialize(new { version, layout = PortableLayout.LayoutId,
                        runtime = PortableLayout.SystemRuntime, architecture = "win-x64", updateProtocol = 1 }));
                }
                else entry.WriteByte(1);
            }
        return stream.ToArray();
    }

    private static async Task Reject<T>(Func<Task> call) where T : Exception
    {
        try { await call(); }
        catch (T) { return; }
        throw new InvalidOperationException($"Expected {typeof(T).Name}.");
    }

    private static void Require(bool valid, string message)
    {
        if (!valid) throw new InvalidOperationException(message);
    }

    private sealed class Replies(Func<HttpRequestMessage, HttpResponseMessage> reply) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            return Task.FromResult(reply(request));
        }
    }

    private sealed class Observer(Action<LibraryProgress> report) : IProgress<LibraryProgress>
    {
        public void Report(LibraryProgress value) => report(value);
    }
}
