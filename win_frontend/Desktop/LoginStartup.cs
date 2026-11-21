using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using Microsoft.Win32;

namespace SuzuEmojy.Windows.Desktop;

internal interface ILoginStartupStore
{
    string? ReadCommand();
    void WriteCommand(string? command);
    bool IsDisabledByWindows { get; }
    void ClearDisabled();
}

internal sealed class LoginStartup(ILoginStartupStore store, string command)
{
    internal static LoginStartup? ForCurrentProcess(string? customData, string? launcher = null)
    {
        using var process = Process.GetCurrentProcess();
        var executable = process.MainModule!.FileName;
        if (Path.GetFileName(executable).Equals("dotnet.exe", StringComparison.OrdinalIgnoreCase)) return null;
        return new(new RegistryStore(), BuildCommand(launcher ?? executable, customData));
    }

    internal static string BuildCommand(string executable, string? customData) =>
        WindowsCommandLine.QuoteArgument(executable) + " --autostart" +
        (customData is null ? "" : " --data " + WindowsCommandLine.QuoteArgument(customData));

    private bool IsOurs => string.Equals(store.ReadCommand(), command, StringComparison.OrdinalIgnoreCase);
    internal bool IsEnabled => IsOurs && !store.IsDisabledByWindows;

    internal void SetEnabled(bool enabled)
    {
        if (!enabled)
        {
            if (IsOurs) store.WriteCommand(null);
            return;
        }
        // Only an explicit enable replaces the old installation's command or clears a Windows disable record.
        if (command.Length > 260) throw new IOException("自启动路径过长，请将程序移到较短的路径后重试。");
        store.WriteCommand(command);
        store.ClearDisabled();
        if (!IsEnabled) throw new IOException("Windows 未能启用自启动，请检查系统的启动应用设置。");
    }

    private sealed class RegistryStore : ILoginStartupStore
    {
        private const string Run = @"Software\Microsoft\Windows\CurrentVersion\Run";
        private const string Name = "SuzuEmojy";
        private static readonly string[] Approval = [
            @"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run",
            @"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32",
        ];

        public string? ReadCommand()
        {
            using var key = Registry.CurrentUser.OpenSubKey(Run);
            return key?.GetValue(Name, null, RegistryValueOptions.DoNotExpandEnvironmentNames) as string;
        }

        public void WriteCommand(string? value)
        {
            if (value is null)
            {
                using var key = Registry.CurrentUser.OpenSubKey(Run, writable: true);
                key?.DeleteValue(Name, throwOnMissingValue: false);
            }
            else
            {
                using var key = Registry.CurrentUser.CreateSubKey(Run, writable: true);
                key.SetValue(Name, value, RegistryValueKind.String);
            }
        }

        private static bool Disabled(RegistryKey? key) => key?.GetValue(Name) is byte[] bytes &&
            bytes.Length > 0 && bytes[0] is 3 or 7;

        public bool IsDisabledByWindows => Approval.Any(path =>
        {
            using var key = Registry.CurrentUser.OpenSubKey(path);
            return Disabled(key);
        });

        public void ClearDisabled()
        {
            foreach (string path in Approval)
            {
                using var read = Registry.CurrentUser.OpenSubKey(path);
                if (!Disabled(read)) continue;
                using var key = Registry.CurrentUser.OpenSubKey(path, writable: true);
                if (Disabled(key)) key?.DeleteValue(Name, throwOnMissingValue: false);
            }
        }
    }
}
