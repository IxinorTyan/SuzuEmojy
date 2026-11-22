using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;
using SuzuEmojy.Windows;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length < 4 || args[0] != "--install" || !int.TryParse(args[1], out int pid) ||
            !Guid.TryParseExact(args[2], "N", out var token) ||
            !Regex.IsMatch(args[3], @"\A[0-9a-fA-F]{64}\z") ||
            args.Length is not (5 or 7) || args.Length == 7 && args[5] != "--data") return 2;

        var payload = new DirectoryInfo(AppContext.BaseDirectory);
        var root = payload.Parent?.FullName;
        if (root is null || !payload.Name.Equals(PortableLayout.Payload, StringComparison.OrdinalIgnoreCase)) return 2;
        var bin = Path.Combine(root, PortableLayout.ProgramDirectory);
        var launcher = Path.Combine(root, PortableLayout.Launcher);
        var zip = Path.Combine(payload.FullName, PortableLayout.UpdateZip);
        var journal = Path.Combine(root, PortableLayout.Journal);
        var application = Path.Combine(bin, PortableLayout.Application);
        bool canRestart = false;
        int result = 0;
        try
        {
            using var gate = UpdateGate.TryAcquire() ?? throw new InvalidOperationException("另一个更新正在运行。");
            UpdateTransaction.AssertAncestors(payload.FullName);
            foreach (var path in new[] { bin, application, launcher, zip, journal, Path.Combine(bin, PortableLayout.Marker) })
                UpdateTransaction.AssertPlain(path);
            if (UpdateGate.HasJournal(root) || !File.Exists(zip) || !File.Exists(launcher)) return 2;
            using (var installed = File.OpenRead(Path.Combine(bin, PortableLayout.Marker)))
            {
                var marker = PackageLayout.ReadMarker(installed);
                if (!PackageLayout.SupportsInstall(marker) || !PackageLayout.NewerStable(args[4], marker.Version!)) return 2;
            }
            using var parent = Process.GetProcessById(pid);
            if (!string.Equals(parent.MainModule?.FileName, application, StringComparison.OrdinalIgnoreCase)) return 2;
            using (var ready = EventWaitHandle.OpenExisting(UpdateGate.ReadyName(token))) ready.Set();
            if (!parent.WaitForExit(30000) || WindowsInstance.IsRunning()) return 2;
            canRestart = true;
            new UpdateTransaction(root).Apply(zip, args[3], args[4], () =>
            {
                using var probe = Start(launcher, [PortableLayout.VerifyInstall]);
                if (!probe.WaitForExit(18000))
                {
                    probe.Kill();
                    probe.WaitForExit(2000);
                    throw new IOException("新版本启动检查超时。");
                }
                if (probe.ExitCode != 0) throw new IOException("新版本未通过启动检查。");
            });
            try { File.Delete(zip); }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
        catch (Exception error)
        {
            if (canRestart)
                MessageBox.Show(error.Message, "安装更新失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
            result = 1;
        }
        if (canRestart && !UpdateGate.HasJournal(root))
        {
            try { using var restarted = Start(launcher, args.Skip(5).ToArray()); }
            catch (Exception error)
            {
                MessageBox.Show(error.Message, "无法重启 SuzuEmojy", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
        }
        return result;
    }

    private static Process Start(string exe, string[] args) =>
        Process.Start(new ProcessStartInfo(exe, string.Join(" ", args.Select(WindowsCommandLine.QuoteArgument)))
        { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Path.GetDirectoryName(exe)! })
        ?? throw new IOException("无法启动程序。");
}
