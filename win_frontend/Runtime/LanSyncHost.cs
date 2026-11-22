using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace SuzuEmojy.Windows;

internal enum LanTransfer { SendToPhone = 'D', ReceiveFromPhone = 'U', Sync = 'S' }

internal sealed class LanSyncHost : IDisposable
{
    internal const int DiscoveryPort = 41731;
    internal const long MaxPackage = 2L * 1024 * 1024 * 1024;
    private const string Discovery = "SUZU-LAN-2";
    internal const int ProtocolVersion = 2;
    internal bool CanSync { get; private set; }
    internal bool StreamingSync { get; private set; }
    private const int PairTimeout = 6_000, ConfirmTimeout = 60_000, IdleTimeout = 600_000;
    private readonly record struct LocalNetwork(IPAddress Address, IPAddress Mask)
    {
        internal bool Contains(IPAddress other)
        {
            var a = Address.GetAddressBytes();
            var b = other.GetAddressBytes();
            var mask = Mask.GetAddressBytes();
            return b.Length == a.Length && a.Select((v, i) => (v & mask[i]) == (b[i] & mask[i])).All(v => v);
        }
    }

    private readonly LocalNetwork[] networks;
    private readonly TcpListener listener;
    private readonly X509Certificate2 certificate;
    private readonly UdpClient? discovery;
    private readonly CancellationTokenSource stop = new();
    private readonly CancellationToken lifetime;
    private readonly byte[] fingerprint;
    private readonly Timer pairingTimer;
    private readonly object transfers = new();
    private bool ready;
    private LanTransfer? requested;
    private int disposed;

    internal string Code { get; }
    internal string[] Addresses { get; }
    internal string QrContent => $"suzu-lan:{ProtocolVersion}|{string.Join(",", Addresses)}|{Hex(fingerprint)}|{Code}";
    internal int? BoundDiscoveryPort { get; }
    internal bool CanDiscover => BoundDiscoveryPort.HasValue;

    internal LanSyncHost(bool loopbackForTests = false, bool testDiscovery = false)
    {
        lifetime = stop.Token;
        networks = loopbackForTests ? [new(IPAddress.Loopback, IPAddress.Parse("255.0.0.0"))] :
            NetworkInterface.GetAllNetworkInterfaces()
                .Where(n => n.OperationalStatus == OperationalStatus.Up && n.NetworkInterfaceType != NetworkInterfaceType.Loopback)
                .Select(n => n.GetIPProperties())
                .OrderByDescending(n => n.GatewayAddresses.Any(g => g.Address.AddressFamily == AddressFamily.InterNetwork && !g.Address.Equals(IPAddress.Any)))
                .SelectMany(n => n.UnicastAddresses)
                .Where(n => n.Address.AddressFamily == AddressFamily.InterNetwork && !IPAddress.IsLoopback(n.Address))
                .Select(n => new LocalNetwork(n.Address, n.IPv4Mask)).Distinct().Take(12).ToArray();
        if (networks.Length == 0) throw new IOException("电脑未连接到 IPv4 局域网。");
        using var rsa = RSA.Create();
        rsa.KeySize = 2048;
        var request = new CertificateRequest("CN=SuzuEmojy LAN", rsa, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        var now = DateTimeOffset.UtcNow;
        using var selfSigned = request.CreateSelfSigned(now.AddHours(-1), now.AddDays(1));
        // Reimport using the platform key store: Schannel cannot use ephemeral private keys on all supported runtimes.
        var pfx = selfSigned.Export(X509ContentType.Pfx);
        try { certificate = new X509Certificate2(pfx, (string?)null, X509KeyStorageFlags.UserKeySet); }
        finally { Array.Clear(pfx, 0, pfx.Length); }
        fingerprint = Hash(certificate.RawData);
        using var rng = RandomNumberGenerator.Create();
        byte[] random = new byte[4];
        uint number;
        do { rng.GetBytes(random); number = ReadUInt32(random); } while (number >= 4_294_000_000u);
        Code = (number % 1_000_000).ToString("D6", CultureInfo.InvariantCulture);

        listener = new TcpListener(loopbackForTests ? IPAddress.Loopback : IPAddress.Any, 0);
        try { listener.Start(4); }
        catch { listener.Stop(); certificate.Dispose(); stop.Dispose(); throw; }
        int port = ((IPEndPoint)listener.LocalEndpoint).Port;
        Addresses = networks.Select(n => $"{n.Address}:{port}").Distinct().ToArray();
        if (!loopbackForTests || testDiscovery)
        {
            UdpClient? socket = null;
            try
            {
                socket = new(AddressFamily.InterNetwork);
                socket.Client.ExclusiveAddressUse = true;
                socket.Client.Bind(loopbackForTests ? new IPEndPoint(IPAddress.Loopback, 0) : new IPEndPoint(IPAddress.Any, DiscoveryPort));
                BoundDiscoveryPort = ((IPEndPoint)socket.Client.LocalEndPoint!).Port;
                discovery = socket;
            }
            catch (SocketException) { socket?.Dispose(); }
        }
        pairingTimer = new Timer(_ => Cancel(), null, 120_000, Timeout.Infinite);
    }

    internal async Task Run(Func<string, CancellationToken, Task<bool>> confirm,
        Func<string, CancellationToken, Task> export, Func<string, CancellationToken, Task<int>> import,
        Action<string> status, Func<LanTransfer, CancellationToken, Task<bool>> approveTransfer, Action<bool>? available = null,
        Func<Stream, Action<string>, CancellationToken, Task>? sync = null, bool streamingSync = false)
    {
        var discoveryTask = discovery is null ? Task.CompletedTask : Advertise(discovery);
        try { await Task.Run(() => Serve(confirm, export, import, status, approveTransfer, available, sync, streamingSync), lifetime).ConfigureAwait(false); }
        catch (Exception) when (lifetime.IsCancellationRequested) { }
        finally
        {
            lock (transfers) { ready = false; requested = null; }
            available?.Invoke(false);
            StopListening();
            await discoveryTask.ConfigureAwait(false);
        }
    }

    internal bool TryRequest(LanTransfer transfer)
    {
        lock (transfers)
        {
            if (!ready || requested is not null || lifetime.IsCancellationRequested || (transfer == LanTransfer.Sync && !CanSync)) return false;
            requested = transfer;
            return true;
        }
    }

    private (LanTransfer? Transfer, bool IsLocal) TakeOffer(int peerRequest)
    {
        LanTransfer? fromPhone = peerRequest switch
        {
            0 => null, 'D' => LanTransfer.SendToPhone, 'U' => LanTransfer.ReceiveFromPhone, 'S' when CanSync => LanTransfer.Sync,
            _ => throw new IOException("不支持的传输操作。")
        };
        lock (transfers)
        {
            if (requested is null && fromPhone is null) return (null, false);
            var result = (requested ?? fromPhone, requested is not null);
            requested = null;
            ready = false;
            return result;
        }
    }

    internal void Cancel()
    {
        if (Volatile.Read(ref disposed) != 0) return;
        try { stop.Cancel(); } catch (ObjectDisposedException) { }
        StopListening();
    }

    private void StopListening()
    {
        // Cancellation, expiry and the accepted connection may all reach this path.
        lock (listener)
        {
            listener.Stop();
            discovery?.Dispose();
        }
    }

    public void Dispose()
    {
        Cancel();
        if (Interlocked.Exchange(ref disposed, 1) != 0) return;
        pairingTimer.Dispose();
        certificate.Dispose();
        stop.Dispose();
    }

    private async Task Advertise(UdpClient socket)
    {
        try
        {
            string port = ((IPEndPoint)listener.LocalEndpoint).Port.ToString(CultureInfo.InvariantCulture);
            while (true)
            {
                var packet = await socket.ReceiveAsync().ConfigureAwait(false);
                if (packet.Buffer.Length != Discovery.Length + 1 + 16 ||
                    !networks.Any(n => n.Contains(packet.RemoteEndPoint.Address))) continue;
                var message = Encoding.ASCII.GetString(packet.Buffer);
                if (!message.StartsWith(Discovery + "?", StringComparison.Ordinal) ||
                    !message.Skip(Discovery.Length + 1).All(Uri.IsHexDigit)) continue;
                var reply = Encoding.ASCII.GetBytes(Discovery + "!" + message.Substring(Discovery.Length + 1) + ":" + port);
                await socket.SendAsync(reply, reply.Length, packet.RemoteEndPoint).ConfigureAwait(false);
            }
        }
        catch (Exception ex) when (ex is SocketException or ObjectDisposedException or InvalidOperationException) { }
    }

    private async Task Serve(Func<string, CancellationToken, Task<bool>> confirm,
        Func<string, CancellationToken, Task> export, Func<string, CancellationToken, Task<int>> import,
        Action<string> status, Func<LanTransfer, CancellationToken, Task<bool>> approveTransfer, Action<bool>? available,
        Func<Stream, Action<string>, CancellationToken, Task>? sync, bool streamingSync)
    {
        var expected = Encoding.ASCII.GetBytes(Code);
        int failures = 0;
        while (failures < 10)
        {
            lifetime.ThrowIfCancellationRequested();
            using var client = await listener.AcceptTcpClientAsync().ConfigureAwait(false);
            var remote = (IPEndPoint)client.Client.RemoteEndPoint!;
            var local = (IPEndPoint)client.Client.LocalEndPoint!;
            if (!networks.Any(n => n.Address.Equals(local.Address) && n.Contains(remote.Address))) continue;
            using var closeOnCancellation = lifetime.Register(client.Close);
            client.ReceiveTimeout = client.SendTimeout = PairTimeout;
            using var ssl = new SslStream(client.GetStream());
            byte[] clientNonce, serverNonce = new byte[16];
            try
            {
                ssl.AuthenticateAsServer(certificate, false, SslProtocols.Tls12, false);
                var hello = ReadExactly(ssl, 6);
                bool streaming = streamingSync && hello.SequenceEqual(Encoding.ASCII.GetBytes("SZLAN4"));
                bool modern = streaming || hello.SequenceEqual(Encoding.ASCII.GetBytes("SZLAN3"));
                if ((!modern && !hello.SequenceEqual(Encoding.ASCII.GetBytes("SZLAN2"))) || (modern && sync is null))
                { WriteStatus(ssl, 3); failures++; continue; }
                // 3 is the old host's unknown-greeting response, not a capability.
                WriteStatus(ssl, streaming ? 5 : modern ? 4 : ProtocolVersion);
                CanSync = modern;
                StreamingSync = streaming;
                var code = ReadExactly(ssl, 6);
                var clientCommit = ReadExactly(ssl, 32);
                if (!Equal(code, expected)) { WriteStatus(ssl, 0); failures++; continue; }
                using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(serverNonce);
                WriteStatus(ssl, 1);
                var serverCommit = Commitment((byte)'S', fingerprint, serverNonce);
                ssl.Write(serverCommit, 0, serverCommit.Length);
                ssl.Flush();
                clientNonce = ReadExactly(ssl, 16);
                if (!Equal(clientCommit, Commitment((byte)'C', fingerprint, clientNonce))) { failures++; continue; }
                ssl.Write(serverNonce, 0, serverNonce.Length);
                ssl.Flush();
            }
            catch (Exception ex) when (ex is IOException or AuthenticationException or SocketException)
            {
                failures++;
                continue;
            }

            pairingTimer.Change(Timeout.Infinite, Timeout.Infinite);
            StopListening();
            client.ReceiveTimeout = client.SendTimeout = ConfirmTimeout;
            using (var confirmation = CancellationTokenSource.CreateLinkedTokenSource(lifetime))
            {
                confirmation.CancelAfter(ConfirmTimeout);
                using var closeOnTimeout = confirmation.Token.Register(client.Close);
                var localApproval = confirm(Verification(fingerprint, clientNonce, serverNonce), confirmation.Token);
                bool approved;
                try { approved = await localApproval.ConfigureAwait(false) && ssl.ReadByte() == 1; }
                catch (OperationCanceledException) when (confirmation.IsCancellationRequested && !lifetime.IsCancellationRequested)
                { throw new IOException("确认连接已超时。"); }
                WriteStatus(ssl, approved ? 1 : 0);
                if (!approved) return;
            }
            client.ReceiveTimeout = client.SendTimeout = IdleTimeout;
            status("已连接");
            lock (transfers) ready = true;
            available?.Invoke(true);
            using var idle = new Timer(_ => client.Close(), null, IdleTimeout, Timeout.Infinite);
            while (!lifetime.IsCancellationRequested)
            {
                int frame = ssl.ReadByte();
                if (frame is -1 or 'Q') return;
                if (frame != 'P') throw new IOException("不支持的传输操作。");
                var (offer, initiatedHere) = TakeOffer(ssl.ReadByte());
                if (offer is not { } transfer) { WriteStatus(ssl, 0); continue; }
                idle.Change(Timeout.Infinite, Timeout.Infinite);
                available?.Invoke(false);
                ssl.WriteByte((byte)transfer);
                WriteStatus(ssl, initiatedHere ? 1 : 0);
                bool approved;
                using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(lifetime))
                {
                    deadline.CancelAfter(ConfirmTimeout);
                    using var closeOnOfferTimeout = deadline.Token.Register(client.Close);
                    if (initiatedHere)
                    {
                        status(transfer == LanTransfer.Sync ? "等待手机确认同步…" : "等待手机确认传输…");
                        approved = ReadDecision(ssl);
                    }
                    else
                    {
                        approved = await approveTransfer(transfer, deadline.Token).ConfigureAwait(false);
                        WriteStatus(ssl, approved ? 1 : 0);
                    }
                }
                if (!approved)
                {
                    status(initiatedHere ? "手机未同意传输。" : "已拒绝传输。");
                    lock (transfers) ready = true;
                    available?.Invoke(true);
                    idle.Change(IdleTimeout, Timeout.Infinite);
                    continue;
                }
                using var transferDeadline = CancellationTokenSource.CreateLinkedTokenSource(lifetime);
                if (transfer != LanTransfer.Sync || !StreamingSync)
                    transferDeadline.CancelAfter(TimeSpan.FromMinutes(30));
                using var closeOnTimeout = transferDeadline.Token.Register(client.Close);
                var token = transferDeadline.Token;
                var path = Path.Combine(Path.GetTempPath(), $"suzu-lan-{Guid.NewGuid():N}.zip");
                try
                {
                    if (transfer == LanTransfer.Sync)
                    {
                        await sync!(ssl, status, token).ConfigureAwait(false);
                    }
                    else if (transfer == LanTransfer.SendToPhone)
                    {
                        status("正在导出资源包…");
                        FileStream? package = null;
                        try
                        {
                            long length;
                            byte[] hash;
                            try
                            {
                                await export(path, token).ConfigureAwait(false);
                                package = File.OpenRead(path);
                                length = package.Length;
                                CheckLength(length, streamingSync: false);
                                hash = HashFile(package, token);
                                package.Position = 0;
                            }
                            catch when (!token.IsCancellationRequested) { try { WriteStatus(ssl, 0); } catch { } throw; }
                            WriteStatus(ssl, 1);
                            WritePackage(ssl, package, length, hash, token, pct => status($"正在发送：{pct}%"));
                        }
                        finally { package?.Dispose(); }
                        status("正在等待手机导入…");
                        status(ReadReceipt(ssl) switch { 1 => "手机已导入", 2 => "手机已导入，部分项目失败", _ => "手机导入失败" });
                    }
                    else
                    {
                        try
                        {
                            ReadPackage(ssl, path, token, pct => status($"正在接收：{pct}%"));
                            CheckArchiveLimits(path, token);
                            status("正在导入资源包…");
                            int result = await import(path, token).ConfigureAwait(false);
                            WriteStatus(ssl, result);
                        }
                        catch when (!token.IsCancellationRequested) { try { WriteStatus(ssl, 0); } catch { } throw; }
                    }
                }
                finally { try { File.Delete(path); } catch (IOException) { } catch (UnauthorizedAccessException) { } }
                lock (transfers) ready = true;
                available?.Invoke(true);
                idle.Change(IdleTimeout, Timeout.Infinite);
            }
            return;
        }
        throw new IOException("配对失败次数过多，请重新开启连接。");
    }

    // Both peers commit before revealing either nonce, so a peer cannot choose its nonce to force a displayed code.
    internal static byte[] Commitment(byte role, byte[] pin, byte[] nonce) => Hash(new[] { role }.Concat(pin).Concat(nonce).ToArray());
    internal static string Verification(byte[] pin, byte[] clientNonce, byte[] serverNonce) =>
        (ReadUInt32(Hash(new[] { (byte)'V' }.Concat(pin).Concat(clientNonce).Concat(serverNonce).ToArray())) % 1_000_000)
            .ToString("D6", CultureInfo.InvariantCulture);

    private static uint ReadUInt32(byte[] bytes) => ((uint)bytes[0] << 24) | ((uint)bytes[1] << 16) | ((uint)bytes[2] << 8) | bytes[3];
    internal static byte[] Hash(byte[] bytes) { using var sha = SHA256.Create(); return sha.ComputeHash(bytes); }
    internal static string Hex(byte[] bytes) => BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();

    private static bool Equal(byte[] a, byte[] b)
    {
        if (a.Length != b.Length) return false;
        int difference = 0;
        for (int i = 0; i < a.Length; i++) difference |= a[i] ^ b[i];
        return difference == 0;
    }

    internal static byte[] ReadExactly(Stream stream, int count)
    {
        var bytes = new byte[count];
        for (int received = 0; received < count;)
        {
            int read = stream.Read(bytes, received, count - received);
            if (read == 0) throw new EndOfStreamException("连接已断开。");
            received += read;
        }
        return bytes;
    }

    internal static void WriteStatus(Stream stream, int status) { stream.WriteByte((byte)status); stream.Flush(); }
    private static bool ReadDecision(Stream stream) => stream.ReadByte() switch
    {
        0 => false, 1 => true, _ => throw new IOException("未收到对方的确认。")
    };
    private static int ReadReceipt(Stream stream) => stream.ReadByte() switch
    {
        0 => 0, 1 => 1, 2 => 2, _ => throw new IOException("未收到对方的导入结果。")
    };

    private static void CheckLength(long length, bool streamingSync)
    {
        if (length <= 0 || (!streamingSync && length > MaxPackage))
            throw new IOException(streamingSync ? "同步资源包大小不受支持。" : "资源包大小不受支持（旧版上限 2 GiB）。");
    }

    internal static void CheckArchiveLimits(string path, CancellationToken token, bool streamingSync = false, bool receiving = true)
    {
        using var file = File.OpenRead(path);
        using var zip = new System.IO.Compression.ZipArchive(file, System.IO.Compression.ZipArchiveMode.Read);
        if (zip.Entries.Count is < 2 or > 40_000) throw new IOException("资源包的条目数量超出限制。");
        long total = 0;
        long available = !streamingSync ? MaxPackage : receiving ? new DriveInfo(Path.GetPathRoot(path)!).AvailableFreeSpace : long.MaxValue;
        foreach (var item in zip.Entries)
        {
            token.ThrowIfCancellationRequested();
            var limit = item.FullName is "manifest.json" or "catalog.json" ? 16L << 20 :
                streamingSync && item.FullName.StartsWith("assets/", StringComparison.Ordinal) ? long.MaxValue : 48L << 20;
            if (item.Length > limit || item.Length < 0 || item.Length > available - total)
                throw new IOException(streamingSync ? "同步资源包索引过大或暂存空间不足。" : "资源包展开后的大小超出限制。");
            total += item.Length;
        }
    }

    internal static byte[] HashFile(Stream input, CancellationToken token)
    {
        using var digest = SHA256.Create();
        byte[] buffer = new byte[65_536];
        int n;
        while ((n = input.Read(buffer, 0, buffer.Length)) != 0)
        {
            token.ThrowIfCancellationRequested();
            digest.TransformBlock(buffer, 0, n, buffer, 0);
        }
        digest.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
        return digest.Hash!;
    }

    internal static void WritePackage(Stream stream, Stream source, long length, byte[] hash, CancellationToken token, Action<int> progress,
        bool streamingSync = false)
    {
        CheckLength(length, streamingSync);
        var head = BitConverter.GetBytes(IPAddress.HostToNetworkOrder(length));
        stream.Write(head, 0, head.Length);
        stream.Write(hash, 0, hash.Length);
        CopyExact(source, stream, length, token, progress);
        stream.Flush();
    }

    internal static void ReadPackage(Stream stream, string path, CancellationToken token, Action<int> progress, bool streamingSync = false)
    {
        long size = IPAddress.NetworkToHostOrder(BitConverter.ToInt64(ReadExactly(stream, 8), 0));
        CheckLength(size, streamingSync);
        if (new DriveInfo(Path.GetPathRoot(path)!).AvailableFreeSpace < size) throw new IOException("电脑暂存空间不足。");
        byte[] expected = ReadExactly(stream, 32);
        using var output = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        using var digest = SHA256.Create();
        CopyExact(stream, output, size, token, progress, digest);
        digest.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
        if (!Equal(expected, digest.Hash!)) throw new IOException("资源包 SHA-256 校验失败。");
    }

    private static void CopyExact(Stream from, Stream to, long length, CancellationToken token, Action<int> progress, HashAlgorithm? digest = null)
    {
        var buffer = new byte[65_536];
        int last = -1;
        for (long remaining = length; remaining > 0;)
        {
            token.ThrowIfCancellationRequested();
            int count = from.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
            if (count == 0) throw new EndOfStreamException("资源包传输中断。");
            digest?.TransformBlock(buffer, 0, count, buffer, 0);
            to.Write(buffer, 0, count);
            remaining -= count;
            int percent = (int)((length - remaining) / (double)length * 100);
            if (percent != last) { last = percent; progress(percent); }
        }
    }
}
