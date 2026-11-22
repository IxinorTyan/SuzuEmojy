using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace SuzuEmojy.Windows;

internal static class LanReconciliation
{
    private const int MaxMessage = 24 << 20;
    private static readonly JsonSerializerOptions Wire = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    internal sealed record Device(string Id);
    internal sealed record Request(string? Revision, SyncLibrary Library, string[]? Skipped = null);
    internal sealed record Reply(string? Error, string? Revision = null, SyncLibrary? Library = null,
        Dictionary<string, string>? Bindings = null, SyncAssets? Send = null, string[]? Hold = null);
    internal sealed record Validation(string[] Skipped, int Count = 0);

    internal static void Run(LanSyncWindowsLibrary library, Stream wire, Action<string> report, CancellationToken token,
        bool streaming = false)
    {
        report("正在读取两端图库…");
        var device = library.Device(token);
        Write(wire, new Device(device));
        var peer = Read<Device>(wire).Id;
        if (!LanSyncMerge.IsId(peer) || peer == device) throw new IOException("手机同步设备标识无效或与电脑重复。");
        using var captureStop = CancellationTokenSource.CreateLinkedTokenSource(token);
        var capture = Task.Run(() => library.Read(device, peer, captureStop.Token), captureStop.Token);
        Request incoming;
        try { incoming = Read<Request>(wire); }
        catch
        {
            captureStop.Cancel();
            try { capture.GetAwaiter().GetResult(); } catch { } // Preserve the connection failure.
            throw;
        }
        LanSyncWindowsLibrary.Capture before;
        SyncHistory history;
        SyncMerge merge;
        SyncAssets receive;
        SyncAssets send;
        var hold = new HashSet<string>(StringComparer.Ordinal);
        SyncCheckpoint? baseline;
        string? outbound = null, inbound = null;
        try
        {
            try
            {
                before = capture.GetAwaiter().GetResult();
                baseline = MatchBaseline(library.History(peer, token), incoming.Revision);
                LanSyncMerge.Validate(incoming.Library);
                var phoneSkipped = incoming.Skipped ?? [];
                if (phoneSkipped.Length > 35_000 || phoneSkipped.Any(key => !LanSyncMerge.IsKey(key)))
                    throw new InvalidDataException("手机的跳过清单无效。");
                if (!streaming && (before.Skipped.Length > 0 || phoneSkipped.Length > 0))
                    throw new IOException("图库含无法校验的表情。请更新两端后再同步，以便安全跳过。");
                hold.UnionWith(phoneSkipped);
                if (before.Skipped.Length > 0 && baseline is not null)
                    hold.UnionWith(baseline.Library.Resources.Select(r => r.Key).Except(before.Library.Resources.Select(r => r.Key)));
                var old = baseline?.Library;
                var win = Without(before.Library, hold);
                var phone = Without(incoming.Library, hold);
                // During a partial scan, defer category deletion as well:
                // an unreadable file may still be a member of that category.
                if (old is not null && (hold.Count != 0 || before.Skipped.Length != 0))
                    old = WithoutUnsafeFolderDeletes(old, win, phone);
                merge = LanSyncMerge.Reconcile(old is null ? null : Without(old, hold), win, phone, baseline?.Held?.ToHashSet());
                history = new(new(Guid.NewGuid().ToString("N"), merge.Library), baseline);
                receive = SyncAssets.Needed(before.Library, merge.Library);
                send = SyncAssets.Needed(incoming.Library, merge.Library);
                if (send.Any)
                {
                    report("正在打包要同步的资源…");
                    outbound = TempPath();
                    library.Export(outbound, send, before, token);
                    LanSyncHost.CheckArchiveLimits(outbound, token, streaming, receiving: false);
                }
            }
            catch (Exception error) when (!token.IsCancellationRequested && (error is IOException or JsonException or UnauthorizedAccessException))
            {
                Write(wire, new Reply(error.Message));
                throw;
            }
            Write(wire, new Reply(null, history.Latest.Revision, merge.Library, merge.Android, receive, hold.ToArray()));
            if (outbound is not null)
            {
                using var zip = File.OpenRead(outbound);
                var sha = LanSyncHost.HashFile(zip, token);
                zip.Position = 0;
                LanSyncHost.WritePackage(wire, zip, zip.Length, sha, token, pct => report($"正在发送：{pct}%"), streaming);
            }
            if (receive.Any)
            {
                inbound = TempPath();
                LanSyncHost.ReadPackage(wire, inbound, token, pct => report($"正在接收：{pct}%"), streaming);
                LanSyncHost.CheckArchiveLimits(inbound, token, streaming);
            }
            using var input = streaming && inbound is not null ? File.OpenRead(inbound) : null;
            using var zipIn = input is null ? null : new ZipArchive(input, ZipArchiveMode.Read);
            var rejected = new HashSet<string>(StringComparer.Ordinal);
            var verified = zipIn is null ? null : ExchangeV1Archive.Read(zipIn, token, null, streamingSync: true, skipped: rejected)
                ?? throw new IOException("收到不支持的资源包。");
            if (verified is not null && !verified.Resources.Select(r => r.Spec["sync_key"]!.GetValue<string>()).ToHashSet().SetEquals(receive.Keys))
                throw new IOException("收到的资源包与同步清单不符。");
            int skippedCount = before.Skipped.Length + (incoming.Skipped?.Length ?? 0);
            var target = merge.Library;
            if (streaming)
            {
                var invalid = Read<Validation>(wire).Skipped;
                if (invalid is null || invalid.Length > send.Keys.Length || !invalid.ToHashSet().IsSubsetOf(send.Keys))
                    throw new InvalidDataException("手机的资源校验结果无效。");
                rejected.UnionWith(invalid);
                skippedCount += rejected.Count;
                hold.UnionWith(rejected);
                target = Without(target, hold);
                Write(wire, new Validation(rejected.ToArray(), skippedCount));
                var retained = RetainBaseline(target, baseline?.Library, hold);
                history = history with { Latest = history.Latest with { Library = retained, Held = hold.ToArray() } };
            }
            else if (wire.ReadByte() != 1) throw new IOException("手机未准备好，本轮没有应用同步。");
            report("正在应用同步…");
            try { library.Apply(device, new(before, target, merge.Windows, peer, history, hold), inbound, token, verified); }
            catch { try { LanSyncHost.WriteStatus(wire, 0); } catch { } throw; }
            // Windows commits first and retains the common predecessor for retry.
            LanSyncHost.WriteStatus(wire, 1);
            if (wire.ReadByte() != 1) throw new IOException("电脑已应用，未收到手机确认；重新连接可继续同步。");
            report(skippedCount == 0 ? "两端同步完成。" : $"两端同步完成，跳过 {skippedCount} 个无法校验的表情。");
        }
        finally
        {
            if (outbound is not null) RuntimePrimitives.DiscardTemporaryFile(outbound);
            if (inbound is not null) RuntimePrimitives.DiscardTemporaryFile(inbound);
        }
    }

    private static SyncLibrary Without(SyncLibrary library, HashSet<string> held) =>
        library with { Resources = library.Resources.Where(r => !held.Contains(r.Key)).ToArray() };

    private static SyncLibrary WithoutUnsafeFolderDeletes(SyncLibrary baseline, SyncLibrary windows, SyncLibrary phone)
    {
        // Treat a folder still present on either side as retained, without
        // replacing its live metadata with the checkpoint's old version.
        var w = windows.Categories.Select(c => c.Id).ToHashSet();
        var a = phone.Categories.Select(c => c.Id).ToHashSet();
        var retained = baseline.Categories.Where(c => w.Contains(c.Id) == a.Contains(c.Id)).ToArray();
        var ids = retained.Select(c => c.Id).ToHashSet();
        return new(retained, baseline.Resources.Select(r => r with { Categories = r.Categories.Where(ids.Contains).ToArray() }).ToArray());
    }

    private static SyncLibrary RetainBaseline(SyncLibrary committed, SyncLibrary? baseline, HashSet<string> held)
    {
        if (baseline is null || held.Count == 0) return committed;
        var categories = committed.Categories.Select(c => c.Id).ToHashSet();
        var order = new LinkedList<SyncResource>(committed.Resources);
        var nodes = new Dictionary<string, LinkedListNode<SyncResource>>();
        for (var node = order.First; node is not null; node = node.Next) nodes.Add(node.Value.Key, node);
        LinkedListNode<SyncResource>? next = null;
        foreach (var row in baseline.Resources.Reverse())
        {
            if (nodes.TryGetValue(row.Key, out var found)) { next = found; continue; }
            if (!held.Contains(row.Key)) continue;
            var value = row with { Categories = row.Categories.Where(categories.Contains).ToArray() };
            next = next is null ? order.AddLast(value) : order.AddBefore(next, value);
        }
        return committed with { Resources = order.ToArray() };
    }

    internal static SyncCheckpoint? MatchBaseline(SyncHistory? saved, string? revision)
    {
        if (revision is null && saved?.Previous is null) return null;
        if (revision is not null && LanSyncMerge.IsId(revision))
        {
            if (saved?.Latest.Revision == revision) return saved.Latest;
            if (saved?.Previous?.Revision == revision) return saved.Previous;
        }
        throw new IOException("两端的同步记录不一致，已停止以避免覆盖或误删。");
    }

    private static string TempPath() => Path.Combine(Path.GetTempPath(), $"suzu-lan-{Guid.NewGuid():N}.zip");

    internal static T Read<T>(Stream stream)
    {
        int length = IPAddress.NetworkToHostOrder(BitConverter.ToInt32(LanSyncHost.ReadExactly(stream, 4), 0));
        if (length is <= 0 or > MaxMessage) throw new InvalidDataException("同步清单大小无效。");
        return JsonSerializer.Deserialize<T>(LanSyncHost.ReadExactly(stream, length), Wire)
            ?? throw new InvalidDataException("同步清单为空。");
    }

    internal static void Write<T>(Stream stream, T message)
    {
        var bytes = JsonSerializer.SerializeToUtf8Bytes(message, Wire);
        if (bytes.Length is <= 0 or > MaxMessage) throw new IOException("同步清单超过 24 MiB。");
        byte[] header = BitConverter.GetBytes(IPAddress.HostToNetworkOrder(bytes.Length));
        stream.Write(header, 0, header.Length);
        stream.Write(bytes, 0, bytes.Length);
        stream.Flush();
    }
}
