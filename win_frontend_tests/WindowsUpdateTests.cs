using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Threading;
using SuzuEmojy.Windows;

internal static class WindowsUpdateTests
{
    internal static void Run(string output)
    {
        var root = Directory.CreateDirectory(Path.Combine(output, "更新测试-" + Guid.NewGuid().ToString("N"))).FullName;
        var bin = Directory.CreateDirectory(Path.Combine(root, "bin")).FullName;
        var library = Directory.CreateDirectory(Path.Combine(root, "data")).FullName;
        var original = Path.Combine(bin, PortableLayout.Application);
        var launcher = Path.Combine(root, PortableLayout.Launcher);
        var config = Path.Combine(library, "config.json");
        var extra = Path.Combine(bin, "保留文件.txt");
        var work = Path.Combine(root, PortableLayout.Journal);
        var zip = Path.Combine(root, "update.zip");
        File.WriteAllText(original, "old program");
        File.WriteAllText(launcher, "stable launcher");
        File.WriteAllText(config, "user config");
        File.WriteAllText(extra, "unmanaged");
        var transaction = new UpdateTransaction(root);

        foreach (var files in new[] { new[] { "data/config.json" }, new[] { "bin/../data/config.json" },
            new[] { "BIN/" + PortableLayout.Application.ToUpperInvariant() }, new[] { "bin/user.txt" } })
        {
            File.WriteAllBytes(zip, NativeServicesTests.CreatePackage(extra: files));
            Throws<InvalidDataException>(() => transaction.Apply(zip, Hash(zip), "2.0.0", UnexpectedProbe));
            AssertOriginal();
        }
        File.WriteAllBytes(zip, NativeServicesTests.CreatePackage("2.0.1",
            extra: ["bin/QRCoder.dll", "bin/QRCoder-LICENSE.txt"]));
        Throws<InvalidDataException>(() => transaction.Apply(zip, Hash(zip), "2.0.2", UnexpectedProbe));
        Throws<InvalidDataException>(() => transaction.Apply(zip, new string('0', 64), "2.0.1", UnexpectedProbe));
        AssertOriginal();

        Throws<IOException>(() => transaction.Apply(zip, Hash(zip), "2.0.1", () =>
        {
            Check(File.ReadAllBytes(original).SequenceEqual(new byte[] { 1 }), "The probe must see the new program.");
            throw new IOException("Simulated startup error");
        }));
        AssertOriginal();
        Check(!File.Exists(Path.Combine(bin, PortableLayout.Updater)), "Rollback must remove newly added managed files.");

        // Simulate power loss after two replacements, before the probe or commit.
        var backup = Directory.CreateDirectory(Path.Combine(work, "old", "bin")).FullName;
        File.Move(original, Path.Combine(backup, PortableLayout.Application));
        File.WriteAllText(original, "interrupted replacement");
        File.WriteAllText(Path.Combine(bin, PortableLayout.Updater), "new in interrupted update");
        File.WriteAllLines(Path.Combine(work, UpdateTransaction.PlanFile),
            ["+bin/" + PortableLayout.Application, "-bin/" + PortableLayout.Updater]);
        transaction.Recover();
        transaction.Recover();
        AssertOriginal();
        Check(!File.Exists(Path.Combine(bin, PortableLayout.Updater)), "Recovery must roll back an interrupted copy.");

        transaction.Apply(zip, Hash(zip), "2.0.1", () =>
            Check(File.ReadAllBytes(original).SequenceEqual(new byte[] { 1 }), "Probe did not see the update."));
        Check(!Directory.Exists(work) && File.ReadAllText(launcher) == "stable launcher" &&
            File.ReadAllText(config) == "user config" && File.ReadAllText(extra) == "unmanaged" &&
            File.Exists(Path.Combine(bin, PortableLayout.Updater)) && File.Exists(Path.Combine(bin, "QRCoder.dll")) &&
            File.Exists(Path.Combine(bin, "QRCoder-LICENSE.txt")), "Only managed bin files may change.");

        void AssertOriginal() => Check(!Directory.Exists(work) && File.ReadAllText(original) == "old program" &&
            File.ReadAllText(launcher) == "stable launcher" && File.ReadAllText(config) == "user config" &&
            File.ReadAllText(extra) == "unmanaged", "A failed package changed the previous installation or user files.");
        static void UnexpectedProbe() => throw new InvalidOperationException("An invalid package reached the probe.");
    }

    internal static void CheckZip(string path)
    {
        using var zip = ZipFile.OpenRead(path);
        var entry = zip.GetEntry("bin/" + PortableLayout.Marker) ?? throw new InvalidDataException("Missing package marker.");
        using var input = entry.Open();
        var marker = PackageLayout.ReadMarker(input);
        PackageLayout.Validate(zip, marker.Version!, stableOnly: false);
    }

    internal static void Upgrade(string installedZip, string newerZip, string output)
    {
        CheckZip(installedZip);
        CheckZip(newerZip);
        Directory.CreateDirectory(output);
        var target = Path.Combine(output, "更新演练-" + Guid.NewGuid().ToString("N"));
        ZipFile.ExtractToDirectory(installedZip, target);
        var launcher = Path.Combine(target, PortableLayout.Launcher);
        var marker = Path.Combine(target, "bin", PortableLayout.Marker);
        var original = File.ReadAllText(marker);
        var backupVersion = VersionAt(marker);
        var data = Directory.CreateDirectory(Path.Combine(target, "data")).FullName;
        File.WriteAllText(Path.Combine(data, "config.json"), "safe");
        using var archive = ZipFile.OpenRead(newerZip);
        using var entry = archive.GetEntry("bin/" + PortableLayout.Marker)!.Open();
        var expected = PackageLayout.ReadMarker(entry).Version!;
        Check(PackageLayout.NewerStable(expected, backupVersion), "The exercise requires two increasing release versions.");

        var transaction = new UpdateTransaction(target);
        var digest = Hash(newerZip);
        Throws<IOException>(() => transaction.Apply(newerZip, digest, expected, () => throw new IOException("Simulated failed probe")));
        Check(original == File.ReadAllText(marker), "Failed probe did not restore the packaged version.");
        Probe();
        transaction.Apply(newerZip, digest, expected, Probe);
        Check(VersionAt(marker) == expected && File.ReadAllText(Path.Combine(data, "config.json")) == "safe" &&
            !Directory.Exists(Path.Combine(target, PortableLayout.Journal)), "The verified package did not update cleanly.");

        static string VersionAt(string file)
        {
            using var stream = File.OpenRead(file);
            return PackageLayout.ReadMarker(stream).Version!;
        }
        void Probe()
        {
            using var process = Process.Start(new ProcessStartInfo(launcher, PortableLayout.VerifyInstall)
                { UseShellExecute = false, CreateNoWindow = true })!;
            if (!process.WaitForExit(20000)) { process.Kill(); throw new IOException("Launcher probe timed out."); }
            Check(process.ExitCode == 0, "Updated application failed the launcher probe.");
        }
    }

#if NETFRAMEWORK
    internal static void CheckWorker(string oldZip, string newZip, string output)
    {
        Check(!WindowsInstance.IsRunning(), "Close SuzuEmojy before the standalone updater rehearsal.");
        var root = Directory.CreateDirectory(Path.Combine(output, "进程交接-" + Guid.NewGuid().ToString("N"))).FullName;
        var portable = Path.Combine(root, "安装目录");
        ZipFile.ExtractToDirectory(oldZip, portable);
        var application = Path.Combine(portable, "bin", PortableLayout.Application);
        var launcher = Path.Combine(portable, PortableLayout.Launcher);
        var custom = Directory.CreateDirectory(Path.Combine(root, "自定义图库")).FullName;
        var config = Path.Combine(custom, "config.json");
        File.WriteAllText(config, "untouched");
        var restart = Path.Combine(custom, "restarted.txt");

        // A stand-in for the old and restarted process, without touching the
        // desktop, hotkeys or real gallery. The updater and launcher are real.
        var fixture = Path.Combine(root, "fixture.exe");
        using (var compiler = new Microsoft.CSharp.CSharpCodeProvider())
        {
            var build = compiler.CompileAssemblyFromSource(new System.CodeDom.Compiler.CompilerParameters
                { GenerateExecutable = true, OutputAssembly = fixture, CompilerOptions = "/target:winexe" },
                """
                using System.IO;
                using System.Threading;
                static class Program {
                    static int Main(string[] args) {
                        if (args.Length == 1 && args[0] == "--verify-install") return 0;
                        if (args.Length == 2 && args[0] == "--wait") {
                            using (var ready = EventWaitHandle.OpenExisting(args[1]))
                                return ready.WaitOne(30000) ? 0 : 1;
                        }
                        if (args.Length == 2 && args[0] == "--data") {
                            File.WriteAllText(Path.Combine(args[1], "restarted.txt"), "yes");
                            return 0;
                        }
                        return 2;
                    }
                }
                """);
            Check(!build.Errors.HasErrors, "Could not compile update process fixture: " +
                string.Join("; ", build.Errors.Cast<System.CodeDom.Compiler.CompilerError>()));
        }
        File.Copy(fixture, application, overwrite: true);
        var journal = Path.Combine(portable, PortableLayout.Journal);
        var backup = Directory.CreateDirectory(Path.Combine(journal, "old", "bin")).FullName;
        File.Move(application, Path.Combine(backup, PortableLayout.Application));
        File.WriteAllText(application, "interrupted replacement");
        File.WriteAllLines(Path.Combine(journal, UpdateTransaction.PlanFile), ["+bin/" + PortableLayout.Application]);
        using (var recovery = Start(launcher, "--data", custom))
            Check(recovery.WaitForExit(6000) && recovery.ExitCode == 0 && !Directory.Exists(journal) &&
                SpinWait.SpinUntil(() => File.Exists(restart) && File.ReadAllText(restart) == "yes", 5000),
                "The outer launcher must recover an interrupted install before starting the previous program.");
        File.Delete(restart);

        var payload = Directory.CreateDirectory(Path.Combine(portable, PortableLayout.Payload)).FullName;
        var stagedZip = Path.Combine(payload, PortableLayout.UpdateZip);
        var worker = Path.Combine(payload, PortableLayout.Updater);
        File.Copy(Path.Combine(portable, "bin", PortableLayout.Updater), worker);
        File.Copy(newZip, stagedZip);
        string version;
        using (var archive = ZipFile.Open(stagedZip, ZipArchiveMode.Update))
        {
            archive.GetEntry("bin/" + PortableLayout.Application)!.Delete();
            archive.CreateEntryFromFile(fixture, "bin/" + PortableLayout.Application);
            using var marker = archive.GetEntry("bin/" + PortableLayout.Marker)!.Open();
            version = PackageLayout.ReadMarker(marker).Version!;
        }
        var token = Guid.NewGuid();
        using var ready = new EventWaitHandle(false, EventResetMode.ManualReset, UpdateGate.ReadyName(token));
        string exitEvent = @"Local\SuzuEmojy.UpdateTest." + token.ToString("N");
        using var exit = new EventWaitHandle(false, EventResetMode.ManualReset, exitEvent);
        using var parent = Start(application, "--wait", exitEvent);
        using var helper = Start(worker, "--install", parent.Id.ToString(), token.ToString("N"), Hash(stagedZip),
            version, "--data", custom);
        try
        {
            Check(ready.WaitOne(8000) && !helper.HasExited && !parent.HasExited &&
                !Directory.Exists(Path.Combine(portable, PortableLayout.Journal)) && !File.Exists(restart),
                "The worker must acknowledge handoff and not touch the install before the old process exits.");
            exit.Set();
            Check(parent.WaitForExit(5000) && helper.WaitForExit(25000) && helper.ExitCode == 0, "Update worker did not finish.");
            Check(SpinWait.SpinUntil(() => File.Exists(restart) && File.ReadAllText(restart) == "yes", 5000) &&
                File.ReadAllText(config) == "untouched" && !File.Exists(stagedZip) &&
                !Directory.Exists(Path.Combine(portable, PortableLayout.Journal)),
                "The restart must use the original custom data path and leave no pending update.");
            using var marker = File.OpenRead(Path.Combine(portable, "bin", PortableLayout.Marker));
            Check(PackageLayout.ReadMarker(marker).Version == version, "The worker did not install the new version.");
        }
        finally
        {
            exit.Set();
            if (!parent.HasExited) parent.Kill();
            if (!helper.HasExited) helper.Kill();
        }

        static Process Start(string exe, params string[] args) =>
            Process.Start(new ProcessStartInfo(exe, string.Join(" ", args.Select(WindowsCommandLine.QuoteArgument)))
                { UseShellExecute = false, CreateNoWindow = true })!;
    }
#endif

    private static string Hash(string path)
    {
        using var sha = SHA256.Create();
        using var input = File.OpenRead(path);
        return BitConverter.ToString(sha.ComputeHash(input)).Replace("-", "");
    }

    private static void Throws<T>(Action action) where T : Exception
    {
        try { action(); }
        catch (T) { return; }
        throw new InvalidOperationException($"Expected {typeof(T).Name}.");
    }

    private static void Check(bool valid, string message)
    {
        if (!valid) throw new InvalidOperationException(message);
    }
}
