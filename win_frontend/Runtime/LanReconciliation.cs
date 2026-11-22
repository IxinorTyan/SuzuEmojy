using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text.Json;
using System.Threading;

namespace SuzuEmojy.Windows;

internal static class LanReconciliation
{
    private const int MaxMessage = 24 << 20;
    private static readonly JsonSerializerOptions Wire = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    internal sealed record Device(string Id);
    internal sealed record Request(string? Revision, SyncLibrary Library);
    internal sealed record Reply(string? Error, string? Revision = null, SyncLibrary? Library = null,
        Dictionary<string, string>? Bindings = null, SyncAssets? Send = null);

    internal static void Run(LanSyncWindowsLibrary library, Stream wire, Action<string> report, CancellationToken token)
    {
        report("正在读取两端图库…");
        var device = library.Device(token);
        Write(wire, new Device(device));
        var peer = Read<Device>(wire).Id;
        if (!LanSyncMerge.IsId(peer) || peer == device) throw new IOException("手机同步设备标识无效或与电脑重复。");
        var incoming = Read<Request>(wire);
        LanSyncWindowsLibrary.Capture before;
        SyncHistory history;
        SyncMerge merge;
        SyncAssets receive;
        SyncAssets send;
        string? outbound = null, inbound = null;
        try
        {
            try
            {
                before = library.Read(device, peer, token);
                var baseline = MatchBaseline(library.History(peer, token), incoming.Revision);
                merge = LanSyncMerge.Reconcile(baseline?.Library, before.Library, incoming.Library);
                history = new(new(Guid.NewGuid().ToString("N"), merge.Library), baseline);
                receive = SyncAssets.Needed(before.Library, merge.Library);
                send = SyncAssets.Needed(incoming.Library, merge.Library);
                if (send.Any)
                {
                    report("正在打包要同步的资源…");
                    outbound = TempPath();
                    library.Export(outbound, send, token);
                    LanSyncHost.CheckArchiveLimits(outbound, token);
                }
            }
            catch (Exception error) when (!token.IsCancellationRequested && (error is IOException or JsonException or UnauthorizedAccessException))
            {
                Write(wire, new Reply(error.Message));
                throw;
            }
            Write(wire, new Reply(null, history.Latest.Revision, merge.Library, merge.Android, receive));
            if (outbound is not null)
            {
                using var zip = File.OpenRead(outbound);
                var sha = LanSyncHost.HashFile(zip, token);
                zip.Position = 0;
                LanSyncHost.WritePackage(wire, zip, zip.Length, sha, token, pct => report($"正在发送：{pct}%"));
            }
            if (receive.Any)
            {
                inbound = TempPath();
                LanSyncHost.ReadPackage(wire, inbound, token, pct => report($"正在接收：{pct}%"));
                LanSyncHost.CheckArchiveLimits(inbound, token);
            }
            if (wire.ReadByte() != 1) throw new IOException("手机未准备好，本轮没有应用同步。");
            report("正在应用同步…");
            try { library.Apply(device, new(before, merge.Library, merge.Windows, peer, history), inbound, token); }
            catch { try { LanSyncHost.WriteStatus(wire, 0); } catch { } throw; }
            // Windows commits first and retains the common predecessor for retry.
            LanSyncHost.WriteStatus(wire, 1);
            if (wire.ReadByte() != 1) throw new IOException("电脑已应用，未收到手机确认；重新连接可继续同步。");
            report("两端同步完成。");
        }
        finally
        {
            if (outbound is not null) RuntimePrimitives.DiscardTemporaryFile(outbound);
            if (inbound is not null) RuntimePrimitives.DiscardTemporaryFile(inbound);
        }
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
