using System;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using SuzuEmojy.Windows;

internal static class LanSyncTests
{
    internal static async Task Run(string output)
    {
        byte[] fixturePin = Enumerable.Range(0, 32).Select(i => (byte)i).ToArray();
        byte[] fixtureClient = fixturePin.Take(16).ToArray(), fixtureServer = fixturePin.Skip(16).ToArray();
        Require(LanSyncHost.Verification(fixturePin, fixtureClient, fixtureServer) == "861136", "Verification vector differs.");
        Require(LanSyncHost.Hex(LanSyncHost.Commitment((byte)'C', fixturePin, fixtureClient)) ==
            "7ffe81c74fe822411040290bd5572399577bffec5474b33a7973945177358cc0", "Commitment vector differs.");
        CheckReconciliation();
        CheckDiskRecovery(output);

        var fixture = new Fixture(output);
        using var host = new LanSyncHost(loopbackForTests: true, testDiscovery: true);
        using var timeout = new CancellationTokenSource(30_000);
        using var registration = timeout.Token.Register(host.Cancel);
        var displayed = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        var approve = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        int offers = 0;
        var server = host.Run(async (code, token) =>
        {
            displayed.TrySetResult(code);
            using var onCancel = token.Register(() => approve.TrySetCanceled());
            return await approve.Task;
        }, fixture.Export, fixture.Import, _ => { }, (_, _) => Task.FromResult(++offers > 1));
        var port = int.Parse(host.Addresses.Single().Split(':')[1]);
        using (var discovery = new UdpClient())
        {
            discovery.Client.ReceiveTimeout = 4000;
            var request = Encoding.ASCII.GetBytes("SUZU-LAN-2?0123456789abcdef");
            discovery.Send(request, request.Length, new IPEndPoint(IPAddress.Loopback, host.BoundDiscoveryPort!.Value));
            var endpoint = new IPEndPoint(IPAddress.Any, 0);
            Require(Encoding.ASCII.GetString(discovery.Receive(ref endpoint)) == $"SUZU-LAN-2!0123456789abcdef:{port}",
                "Unexpected discovery response; it must contain only the discovery nonce and TCP port.");
        }
        byte[] pin = ParseHex(host.QrContent.Split('|')[2]);
        using (var wrongPin = Open(port, fixturePin))
        {
            bool rejected = false;
            try { wrongPin.Tls.AuthenticateAsClient("SuzuEmojy LAN", null, SslProtocols.Tls12, false); }
            catch (AuthenticationException) { rejected = true; }
            Require(rejected, "TLS did not reject the wrong QR fingerprint.");
        }
        using (var wrong = Open(port, pin))
        {
            wrong.Tls.AuthenticateAsClient("SuzuEmojy LAN", null, SslProtocols.Tls12, false);
            SendPair(wrong.Tls, host.Code == "000000" ? "999999" : "000000", pin, fixtureClient);
            Require(wrong.Tls.ReadByte() == 0 && !displayed.Task.IsCompleted, "An invalid code reached confirmation.");
        }

        using (var right = Open(port, pin))
        {
            var tls = right.Tls;
            tls.AuthenticateAsClient("SuzuEmojy LAN", null, SslProtocols.Tls12, false);
            SendPair(tls, host.Code, pin, fixtureClient);
            Require(tls.ReadByte() == 1, "Pairing was rejected.");
            byte[] commit = LanSyncHost.ReadExactly(tls, 32);
            tls.Write(fixtureClient, 0, fixtureClient.Length);
            tls.Flush();
            byte[] nonce = LanSyncHost.ReadExactly(tls, 16);
            Require(LanSyncHost.Commitment((byte)'S', pin, nonce).SequenceEqual(commit), "Unexpected server commitment.");
            Require(await displayed.Task == LanSyncHost.Verification(pin, fixtureClient, nonce), "Confirmation numbers differ.");
            LanSyncHost.WriteStatus(tls, 1);
            approve.SetResult(true);
            Require(tls.ReadByte() == 1, "Pairing was not approved.");

            Poll(tls, 'D', 'D', false);
            Require(tls.ReadByte() == 0, "Windows must be able to reject a phone request without transferring.");
            await Request(host, LanTransfer.SendToPhone, timeout.Token);
            Poll(tls, 'U', 'D', true);
            LanSyncHost.WriteStatus(tls, 0);
            await Request(host, LanTransfer.SendToPhone, timeout.Token);
            Poll(tls, 0, 'D', true);
            LanSyncHost.WriteStatus(tls, 1);
            Require(tls.ReadByte() == 1, "Windows failed to export.");
            var package = Path.Combine(fixture.Root, "download.zip");
            LanSyncHost.ReadPackage(tls, package, timeout.Token, _ => { });
            LanSyncHost.WriteStatus(tls, 1);
            using var upload = File.OpenRead(package);
            byte[] hash = LanSyncHost.HashFile(upload, timeout.Token);
            foreach (int attempt in new[] { 0, 1 })
            {
                upload.Position = 0;
                if (attempt == 0)
                {
                    await Request(host, LanTransfer.ReceiveFromPhone, timeout.Token);
                    Poll(tls, 0, 'U', true);
                    LanSyncHost.WriteStatus(tls, 1);
                }
                else
                {
                    Poll(tls, 'U', 'U', false);
                    Require(tls.ReadByte() == 1, "Windows did not accept the phone's upload offer.");
                }
                LanSyncHost.WritePackage(tls, upload, upload.Length, hash, timeout.Token, _ => { });
                Require(tls.ReadByte() == 1, $"Windows failed to import on attempt {attempt}.");
            }
            Require(fixture.Added == 1 && fixture.Duplicates == 1, "Duplicate transfer added an extra image.");

            upload.Position = 0;
            Poll(tls, 'U', 'U', false);
            Require(tls.ReadByte() == 1, "Windows did not accept the phone's upload offer.");
            hash[0] ^= 1;
            LanSyncHost.WritePackage(tls, upload, upload.Length, hash, timeout.Token, _ => { });
            Require(tls.ReadByte() == 0 && fixture.ImportCalls == 2, "Corrupt transfers must not reach import.");
        }
        try { await server; throw new Exception("The corrupt transfer was not rejected."); }
        catch (IOException e) when (e.Message.Contains("SHA-256")) { }
    }

    // Test executable only. Android emulator instrumentation uses 10.0.2.2 to reach this fixture.
    internal static async Task ServeAndroidFixture(string output)
    {
        if (Environment.GetEnvironmentVariable("SUZU_LAN_RECONCILIATION_TEST") == "1")
        {
            await ServeReconciliationFixture(output);
            return;
        }
        var fixture = new Fixture(output);
        using var host = new LanSyncHost(loopbackForTests: true);
        int round = 0;
        string qr = host.QrContent.Replace("127.0.0.1:", "10.0.2.2:");
        Console.WriteLine(qr);
        await host.Run((code, _) => { Console.WriteLine($"Verification: {code}"); return Task.FromResult(true); },
            fixture.Export, fixture.Import, Console.WriteLine, (_, _) => Task.FromResult(true), ready =>
            {
                if (!ready) return;
                if (++round == 1) Require(host.TryRequest(LanTransfer.SendToPhone), "Could not queue Windows export.");
                if (round == 3) Require(host.TryRequest(LanTransfer.ReceiveFromPhone), "Could not queue Windows import.");
            });
        Require(fixture.Added == 1 && fixture.Duplicates == 1, "Android did not upload (and reupload) the expected resource.");
    }

    private static void CheckReconciliation()
    {
        var w = new string('1', 32);
        var a = new string('2', 32);
        var aKey = "p:" + new string('a', 32);
        var bKey = "p:" + new string('b', 32);
        var cKey = "p:" + new string('c', 32);
        var groupWin = new SyncCategory(w, "同名", null);
        var groupAndroid = new SyncCategory(a, "同名", new("text", "✅"));
        var soloWindows = new SyncResource(aKey, ["a", "x"], [w]);
        var soloAndroid = new SyncResource(bKey, [], [a]);
        var first = LanSyncMerge.Reconcile(null, new([groupWin], [soloWindows]), new([groupAndroid], [soloAndroid]));
        Require(first.Library.Resources.Length == 2 && first.Library.Categories.Single().Icon == groupAndroid.Icon &&
            first.Android[w] == a, "First sync did not merge complementary libraries or preserve category aliases.");
        var @base = first.Library;
        var removed = LanSyncMerge.Reconcile(@base,
            new([groupWin with { Name = "改名", Icon = groupAndroid.Icon }], [new(aKey, ["a", "电脑"], [w]), new(cKey, [], [])]),
            new(@base.Categories, [new(aKey, ["x", "手机"], []), @base.Resources[1]]));
        var merged = removed.Library.Resources.Single(r => r.Key == aKey);
        Require(removed.Library.Resources.All(r => r.Key != bKey) && merged.Categories.Length == 0 &&
            merged.Keywords.OrderBy(x => x).SequenceEqual(new[] { "手机", "电脑" }.OrderBy(x => x)) &&
            removed.Library.Categories.Single().Name == "改名", "Deletes, independent keyword removal, or rename did not reconcile.");
        Require(LanSyncMerge.Reconcile(removed.Library, removed.Library, removed.Library).Library.Resources.Length == 2,
            "A repeated sync resurrected a deletion.");

        var ordered = new SyncLibrary([], [new(aKey, [], []), new(bKey, [], []), new(cKey, [], [])]);
        var flipped = ordered with { Resources = ordered.Resources.Reverse().ToArray() };
        Require(LanSyncMerge.Reconcile(ordered, ordered, flipped).Library.Resources[0].Key == cKey,
            "An Android-only gallery reorder was not propagated.");
        try
        {
            LanSyncMerge.Reconcile(ordered, flipped, ordered with { Resources = [ordered.Resources[1], ordered.Resources[0], ordered.Resources[2]] });
            throw new InvalidOperationException("Incompatible order edits must not be silently overwritten.");
        }
        catch (IOException e) when (e.Message.Contains("排序")) { }
        try
        {
            LanSyncMerge.Reconcile(ordered, ordered with { Resources = [ordered.Resources[0], ordered.Resources[1]] },
                ordered with { Resources = [ordered.Resources[0], ordered.Resources[1], new(cKey, ["new"], [])] });
            throw new InvalidOperationException("Delete-vs-edit must not be silently overwritten.");
        }
        catch (IOException e) when (e.Message.Contains("冲突")) { }
        var predecessor = new SyncCheckpoint(w, ordered);
        var latest = new SyncCheckpoint(a, flipped);
        Require(LanReconciliation.MatchBaseline(new(latest, predecessor), w) == predecessor &&
            LanReconciliation.MatchBaseline(new(latest, predecessor), a) == latest &&
            LanReconciliation.MatchBaseline(new(latest, null), null) is null, "Interrupted sync cannot resume from the common checkpoint.");
    }

    private static async Task ServeReconciliationFixture(string output)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(3));
        var root = Directory.CreateDirectory(Path.Combine(output, "lan-reconcile-" + Guid.NewGuid().ToString("N"))).FullName;
        var images = Directory.CreateDirectory(Path.Combine(root, "images")).FullName;
        var gone = Png("Gone.png", 70);
        var tagged = Png("Tagged.png", 110);
        var stable = Png("Stable.png", 175);
        string icon = Png("Icon.png", 230);
        Directory.CreateDirectory(Path.Combine(root, "category_icons"));
        File.Move(icon, Path.Combine(root, "category_icons", "Icon.png"));
        File.WriteAllText(Path.Combine(root, "categories.json"), """{"电脑初始":["Gone.png","Tagged.png"],"图标收藏夹":[]}""");
        File.WriteAllText(Path.Combine(root, "category_icons.json"), """{"图标收藏夹":"category_icons/Icon.png"}""");
        File.WriteAllText(Path.Combine(root, "metadata.json"), """{"Tagged.png":"keep remove"}""");
        var commands = new LibraryCommands(root, root);
        var taggedKey = ResourceIdentityV1.TryRead(tagged, timeout.Token);
        var stableKey = ResourceIdentityV1.TryRead(stable, timeout.Token);
        int finished = 0;
        using var host = new LanSyncHost(loopbackForTests: true);
        using var retry = new LanSyncHost(loopbackForTests: true);
        using var close = timeout.Token.Register(() => { host.Cancel(); retry.Cancel(); });
        Console.WriteLine(host.QrContent.Replace("127.0.0.1:", "10.0.2.2:"));
        Console.WriteLine(retry.QrContent.Replace("127.0.0.1:", "10.0.2.2:"));
        var session = host.Run(Confirm, NoExport, NoImport, Console.WriteLine, (_, _) => Task.FromResult(true),
            ready => { if (ready && finished == 0) Require(host.TryRequest(LanTransfer.Sync), "Windows sync button did not queue the offer."); },
            async (stream, report, token) =>
            {
                // In round 3 deliberately lose the connection after commit, before the phone applies.
                await commands.Synchronize(finished == 2 ? new DropAfterCommit(stream) : stream, report, token);
                var state = await commands.ReadForLanSync();
                Require(state.Images.Length == 4 && state.Categories.Length == 3, "Synced counts differ.");
                if (++finished == 1)
                {
                    var order = state.Images.Select(s => s.Path).Where(p => p != gone && p.EndsWith(".png", StringComparison.OrdinalIgnoreCase)).Reverse().ToArray();
                    Require(order.Length == 2, "Missing PNGs.");
                    await Edit(new("order", Paths: order), token);
                    await Edit(new("tags", Paths: [tagged], Value: "keep remove desktop"), token);
                    await Edit(new("rename_category", Category: "电脑初始", Value: "电脑改名"), token);
                    await Edit(new("delete", Paths: [gone]), token);
                    Png("New.png", 210);
                }
                else
                {
                    var item = state.Images.Single(s => s.Path == tagged);
                    Require(item.Keywords == "desktop keep phone" && item.Categories.Length == 0 &&
                        state.Categories.Any(c => c.Name == "电脑改名") && !File.Exists(gone) &&
                        state.Images.Count(s => s.Path.EndsWith(".gif", StringComparison.OrdinalIgnoreCase)) == 1,
                        "Windows did not apply Android removals/additions or preserve the category rename.");
                    Console.WriteLine($"Surviving PNGs: {stableKey}, {taggedKey}");
                    // This edit needs to propagate on retry even though Windows
                    // advances its revision in the deliberately interrupted round.
                    await Edit(new("tags", Paths: [stable], Value: "retry"), token);
                }
            });
        try { await session; throw new InvalidOperationException("Interrupt fixture was not reached."); }
        catch (IOException e) when (e.Message.Contains("fixture disconnected")) { Require(finished == 2, "Interrupted early."); }
        await retry.Run(Confirm, NoExport, NoImport, Console.WriteLine, (_, _) => Task.FromResult(true),
            sync: async (stream, report, token) => { await commands.Synchronize(stream, report, token); finished++; });
        Require(finished == 4, "The retry and following no-change sync did not finish.");

        Task<bool> Confirm(string code, CancellationToken _) { Console.WriteLine($"Verification: {code}"); return Task.FromResult(true); }
        static Task NoExport(string _, CancellationToken __) => throw new InvalidOperationException("Expected true sync, not export.");
        static Task<int> NoImport(string _, CancellationToken __) => throw new InvalidOperationException("Expected true sync, not import.");
        async Task Edit(LibraryCommand command, CancellationToken token)
        {
            var (result, _) = await commands.Run(command, false, token);
            Require(result.Error is null && result.Failed == 0, $"Fixture edit {command.Action} failed: {result.Error}");
        }
        string Png(string name, byte red)
        {
            var bitmap = System.Windows.Media.Imaging.BitmapSource.Create(1, 1, 96, 96,
                System.Windows.Media.PixelFormats.Bgra32, null, new byte[] { 15, 40, red, 255 }, 4);
            var encoder = new System.Windows.Media.Imaging.PngBitmapEncoder();
            encoder.Frames.Add(System.Windows.Media.Imaging.BitmapFrame.Create(bitmap));
            var file = Path.Combine(images, name);
            using var outputPng = File.Create(file);
            encoder.Save(outputPng);
            return file;
        }
    }

    private static void CheckDiskRecovery(string output)
    {
        var id = Guid.NewGuid().ToString("N");
        var root = Path.Combine(output, "lan-recovery-" + id);
        var stage = Path.Combine(root, ".lan-sync-stage", id);
        var icons = Directory.CreateDirectory(Path.Combine(stage, "index")).FullName;
        Directory.CreateDirectory(Path.Combine(stage, "add"));
        var trash = Directory.CreateDirectory(Path.Combine(root, "lan-sync-trash", id)).FullName;
        var images = Directory.CreateDirectory(Path.Combine(root, "images")).FullName;
        File.WriteAllText(Path.Combine(trash, "old.png"), "original");
        File.WriteAllText(Path.Combine(images, "new.png"), "uncommitted");
        File.WriteAllText(Path.Combine(root, "metadata.json"), "{}");
        File.WriteAllText(Path.Combine(icons, "metadata.json"), """{"old.png":"keyword"}""");
        File.WriteAllText(Path.Combine(stage, "changes.json"), System.Text.Json.JsonSerializer.Serialize(
            new LanSyncWindowsLibrary.FileChanges(Guid.NewGuid().ToString("N"), id, ["new.png"], ["old.png"], ["metadata.json"])));
        LanSyncWindowsLibrary.RecoverPending(root, CancellationToken.None);
        Require(!Directory.Exists(stage) && !File.Exists(Path.Combine(images, "new.png")) &&
            File.ReadAllText(Path.Combine(images, "old.png")) == "original" &&
            File.ReadAllText(Path.Combine(root, "metadata.json")).Contains("keyword"), "Startup failed to roll back a partial local commit.");
    }

    private sealed class DropAfterCommit(Stream source) : Stream
    {
        private bool status;
        public override bool CanRead => true;
        public override bool CanWrite => true;
        public override bool CanSeek => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
        public override int Read(byte[] buffer, int offset, int count) => source.Read(buffer, offset, count);
        public override void Write(byte[] buffer, int offset, int count) => source.Write(buffer, offset, count);
        public override void WriteByte(byte value) => status = true;
        public override void Flush() { if (status) throw new IOException("fixture disconnected after Windows committed"); source.Flush(); }
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
    }

    private static void SendPair(SslStream ssl, string code, byte[] fingerprint, byte[] nonce)
    {
        var hello = Encoding.ASCII.GetBytes("SZLAN2");
        ssl.Write(hello, 0, hello.Length);
        ssl.Flush();
        Require(ssl.ReadByte() == LanSyncHost.ProtocolVersion, "Unexpected protocol version.");
        var bytes = Encoding.ASCII.GetBytes(code).Concat(LanSyncHost.Commitment((byte)'C', fingerprint, nonce)).ToArray();
        ssl.Write(bytes, 0, bytes.Length);
        ssl.Flush();
    }

    private static async Task Request(LanSyncHost host, LanTransfer direction, CancellationToken token)
    {
        while (!host.TryRequest(direction)) await Task.Delay(10, token);
        Require(!host.TryRequest(direction), "Two simultaneous local requests must not be queued.");
    }

    private static void Poll(SslStream tls, int request, int expected, bool fromWindows)
    {
        tls.WriteByte((byte)'P');
        LanSyncHost.WriteStatus(tls, request);
        Require(tls.ReadByte() == expected && tls.ReadByte() == (fromWindows ? 1 : 0), "Unexpected selected transfer or initiator.");
    }

    private static Peer Open(int port, byte[] pin) => new(port, pin);
    private sealed class Peer : IDisposable
    {
        private readonly TcpClient client = new() { ReceiveTimeout = 8000, SendTimeout = 8000 };
        internal SslStream Tls { get; }

        internal Peer(int port, byte[] pin)
        {
            client.Connect("127.0.0.1", port);
            Tls = new SslStream(client.GetStream(), false, (_, cert, _, _) =>
                cert is not null && LanSyncHost.Hash(cert.GetRawCertData()).SequenceEqual(pin));
        }
        public void Dispose() { Tls.Dispose(); client.Dispose(); }
    }

    private sealed class Fixture
    {
        internal string Root { get; }
        internal int Added { get; private set; }
        internal int Duplicates { get; private set; }
        internal int ImportCalls { get; private set; }
        private readonly LibraryCommands source, receiver;

        internal Fixture(string output)
        {
            Root = Directory.CreateDirectory(Path.Combine(output, "lan-" + Guid.NewGuid().ToString("N"))).FullName;
            var from = Path.Combine(Root, "from");
            var images = Directory.CreateDirectory(Path.Combine(from, "images"));
            File.WriteAllBytes(Path.Combine(images.FullName, "Lan.png"), Convert.FromBase64String(NativeIdentityTests.Rgba));
            File.WriteAllText(Path.Combine(from, "categories.json"), """{"局域网":["Lan.png"],"空收藏夹":[]} """);
            File.WriteAllText(Path.Combine(from, "category_icons.json"), """{"空收藏夹":"⭐"} """);
            File.WriteAllText(Path.Combine(from, "metadata.json"), """{"Lan.png":"同步 标签"} """);
            source = new LibraryCommands(Root, from);
            receiver = new LibraryCommands(Root, Path.Combine(Root, "received"));
        }

        internal async Task Export(string path, CancellationToken token)
        {
            var (result, _) = await source.Run(new("export_zip", Value: path), false, token);
            Require(result.Error is null && result.Count == 1, result.Error ?? "Fixture export failed.");
        }

        internal async Task<int> Import(string path, CancellationToken token)
        {
            ImportCalls++;
            var (result, snapshot) = await receiver.Run(new("import_zip", Value: path), true, token);
            Require(result.Error is null && result.Failed == 0 && snapshot is not null, result.Error ?? "Fixture import failed.");
            Added += result.Count;
            Duplicates += result.Skipped;
            Require(snapshot!.Images.Single().Keywords == "同步 标签" && snapshot.Categories.Any(c => c.Name == "空收藏夹" && c.Icon == "⭐"),
                "Keywords or empty category icon did not round-trip.");
            return 1;
        }
    }

    private static byte[] ParseHex(string hex) => Enumerable.Range(0, hex.Length / 2).Select(i => Convert.ToByte(hex.Substring(i * 2, 2), 16)).ToArray();
    private static void Require(bool valid, string message) { if (!valid) throw new InvalidOperationException(message); }
}
