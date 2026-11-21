using System;
using System.IO;
using System.Threading;
using System.Windows;
using SuzuEmojy.Windows.Desktop;

namespace SuzuEmojy.Windows;

public partial class App : Application
{
    private DesktopSession? desktop;
    private Mutex? instance;

    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        if (e.Args.Length == 1 && e.Args[0] == PortableLayout.VerifyInstall)
        {
            try
            {
                var package = WindowsInstall.Locate(AppContext.BaseDirectory);
                Shutdown(package.Launcher is not null && NativeWebp.Available && LegacyAnimatedGif.Available &&
                    NativeLottie.Available ? 0 : 1);
            }
            catch (Exception) { Shutdown(1); }
            return;
        }
        try
        {
            instance = WindowsInstance.TryAcquire();
            if (instance is null)
            {
                Shutdown(0);
                return;
            }
            var install = WindowsInstall.Locate(AppContext.BaseDirectory);
            var root = install.Root;
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
            var library = await LibrarySnapshot.ReadAsync(data);
            var commands = new LibraryCommands(root, data);
            var window = new MainWindow(library, commands: commands);
            window.StartupRegistration = LoginStartup.ForCurrentProcess(customData ? data : null, install.Launcher);
            MainWindow = window;
            desktop = new DesktopSession(window, library, data, commands);
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
        try
        {
            desktop?.Dispose();
            base.OnExit(e);
        }
        finally { instance?.Dispose(); }
    }
}
