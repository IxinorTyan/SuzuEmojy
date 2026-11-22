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
