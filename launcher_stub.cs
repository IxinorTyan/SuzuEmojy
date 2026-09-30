using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class LauncherStub
{
    [STAThread]
    private static void Main()
    {
        string baseDirectory = AppDomain.CurrentDomain.BaseDirectory;
        string updateDirectory = Path.Combine(baseDirectory, ".update");
        if (File.Exists(Path.Combine(updateDirectory, "journal.json")) ||
            File.Exists(Path.Combine(updateDirectory, "installer-ready")))
        {
            string recovery = Path.Combine(updateDirectory, "SuzuEmojyUpdater.exe");
            try
            {
                Process.Start(new ProcessStartInfo {
                    FileName = recovery,
                    Arguments = "--recover --root \"" + baseDirectory.TrimEnd('\\') + "\"",
                    WorkingDirectory = updateDirectory,
                    UseShellExecute = false,
                    CreateNoWindow = true
                });
            }
            catch (Exception error)
            {
                MessageBox.Show("更新恢复失败，请保留 .update 目录并重试。\n" + error.Message, "SuzuEmojy");
            }
            return;
        }
        string targetPath = Path.Combine(baseDirectory, "bin", "SuzuEmojy.exe");

        if (!File.Exists(targetPath))
        {
            MessageBox.Show(
                "找不到核心程序：" + Environment.NewLine + targetPath + Environment.NewLine + Environment.NewLine +
                "请确保 bin 文件夹完整。",
                "SuzuEmojy",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return;
        }

        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = targetPath,
                WorkingDirectory = baseDirectory,
                UseShellExecute = false,
                CreateNoWindow = true
            });
        }
        catch (Exception error)
        {
            MessageBox.Show(
                "启动核心程序失败：" + Environment.NewLine + error.Message,
                "SuzuEmojy",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }
    }
}
