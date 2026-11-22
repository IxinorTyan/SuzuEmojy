using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Windows.Forms;
using SuzuEmojy.Windows;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        bool probe = args.Length == 1 && args[0] == PortableLayout.VerifyInstall;
        try
        {
            if (!probe)
            {
                if (WindowsInstance.IsRunning() || UpdateGate.IsRunning()) return 0;
                if (UpdateGate.HasJournal(AppContext.BaseDirectory))
                {
                    using var gate = UpdateGate.TryAcquire();
                    if (gate is null || WindowsInstance.IsRunning()) return 0;
                    new UpdateTransaction(AppContext.BaseDirectory).Recover();
                }
            }
            var target = Path.Combine(AppContext.BaseDirectory, PortableLayout.ProgramDirectory, PortableLayout.Application);
            if (!File.Exists(target)) throw new FileNotFoundException("请从完整 ZIP 解压后运行。", target);
            using var child = Process.Start(new ProcessStartInfo(target,
                string.Join(" ", args.Select(WindowsCommandLine.QuoteArgument)))
                { UseShellExecute = false, CreateNoWindow = true }) ?? throw new InvalidOperationException("无法启动核心程序。");
            if (!probe) return 0;
            if (child.WaitForExit(15000)) return child.ExitCode;
            child.Kill();
            return 1;
        }
        catch (Exception error) when (error is IOException or Win32Exception or InvalidOperationException or UnauthorizedAccessException)
        {
            if (!probe) MessageBox.Show(error.Message, "SuzuEmojy", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
