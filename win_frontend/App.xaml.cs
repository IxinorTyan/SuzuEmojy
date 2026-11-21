using System;
using System.IO;
using System.Windows;
using SuzuEmojy.Windows.Desktop;

namespace SuzuEmojy.Windows;

public partial class App : Application
{
    private DesktopSession? desktop;

    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        // The gallery bridge is a one-shot process, not a background server.
        try
        {
            var install = WindowsInstall.Locate(AppContext.BaseDirectory);
            var root = install.Backend;
            var data = install.Data;
            bool autostart = false;
            bool customData = false;
            for (var i = 0; i < e.Args.Length; i++)
            {
                if (e.Args[i] == "--autostart" && !autostart) autostart = true;
                else if (e.Args[i] == "--data" && !customData && i + 1 < e.Args.Length)
                {
                    data = Path.GetFullPath(e.Args[++i]);
                    customData = true;
                }
                else throw new ArgumentException("Expected [--autostart] [--data <directory>].");
            }
            var library = await LibrarySnapshot.ReadAsync(root, data);
            var commands = new LibraryCommands(root, data);
            var window = new MainWindow(library, commands: commands);
            MainWindow = window;
            desktop = new DesktopSession(window, library, root, data, commands);
            desktop.Start(autostart);
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "SuzuEmojy", MessageBoxButton.OK, MessageBoxImage.Error);
            Shutdown(1);
        }
    }

    protected override void OnExit(ExitEventArgs e)
    {
        desktop?.Dispose();
        base.OnExit(e);
    }
}
