using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using SuzuEmojy.Windows;
using SuzuEmojy.Windows.Controls;
using SuzuEmojy.Windows.Desktop;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        if (args is ["--hold-instance", var instanceName]) return WindowsInstanceTests.Hold(instanceName);
        if (args is ["--package-smoke", var package, var smoke])
        {
            try
            {
                WindowsInstanceTests.Run();
                var install = CheckInstallLayout(package);
                Require(install.Launcher is not null, "The ZIP must have the layered portable layout.");
                var launcherInfo = new ProcessStartInfo(install.Launcher!, PortableLayout.VerifyInstall)
                { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Path.GetTempPath() };
                using (var launcher = Process.Start(launcherInfo)!)
                {
                    if (!launcher.WaitForExit(18000)) { launcher.Kill(); throw new TimeoutException("Launcher did not start the application."); }
                    Require(launcher.ExitCode == 0 && !Directory.Exists(install.Data) && !Directory.Exists(Path.Combine(install.Root, "data")),
                        "The launcher must work outside its directory without creating a gallery when probed.");
                }
                Require(NativeWebp.Available && NativeLottie.Available && LegacyAnimatedGif.Available,
                    "The package must contain all of its native image decoders.");
                var info = new ProcessStartInfo(StickerVideoConverter.Find(package)!, "-version")
                { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true };
                using var ffmpeg = Process.Start(info)!;
                var version = ffmpeg.StandardOutput.ReadToEndAsync();
                if (!ffmpeg.WaitForExit(5000)) { ffmpeg.Kill(); throw new TimeoutException("FFmpeg did not start."); }
                Require(ffmpeg.ExitCode == 0 && version.GetAwaiter().GetResult().StartsWith("ffmpeg version ", StringComparison.Ordinal),
                    "The converter failed to start without external dependencies.");
                NativeMigrationBoundaryTests.Run(smoke, install).GetAwaiter().GetResult();
                NativeExchangeTests.Run(smoke).GetAwaiter().GetResult();
                NativeApngTests.CheckTelegram().GetAwaiter().GetResult();
                Console.WriteLine("Native package smoke passed.");
                return 0;
            }
            catch (Exception error) { Console.Error.WriteLine(error); return 1; }
        }
        if (args is ["--native-edits", var output])
        {
            try
            {
                NativeEditTests.Run(output).GetAwaiter().GetResult();
                NativeApngTests.Run(output).GetAwaiter().GetResult();
                NativeIdentityTests.CheckWarmup(output).GetAwaiter().GetResult();
                NativeIdentityTests.Run(output);
                NativeExchangeTests.Run(output).GetAwaiter().GetResult();
                NativeMediaTests.Run(output).GetAwaiter().GetResult();
                NativeImportTests.Run(output).GetAwaiter().GetResult();
                NativeDeletionTests.Run(output).GetAwaiter().GetResult();
                NativeSimilarityTests.Run(output).GetAwaiter().GetResult();
                NativeQqTests.Run(output).GetAwaiter().GetResult();
                NativeMigrationBoundaryTests.Run(output).GetAwaiter().GetResult();
                return 0;
            }
            catch (Exception error) { Console.Error.WriteLine(error); return 1; }
        }
        if (args is ["--qq-fixtures", var qqOutput, var qqReference])
        {
            try { NativeQqTests.Run(qqOutput, qqReference).GetAwaiter().GetResult(); return 0; }
            catch (Exception error) { Console.Error.WriteLine(error); return 1; }
        }
        if (args.Length >= 5 && args[0] == "--webm-fixtures")
        {
            try { NativeMigrationBoundaryTests.Webm(args[1], args[2], args[3], args.Skip(4).ToArray()).GetAwaiter().GetResult(); return 0; }
            catch (Exception error) { Console.Error.WriteLine(error); return 1; }
        }
        if (args is ["--identity-fixtures", var directory])
        {
            try { NativeIdentityTests.CheckAndroid(directory); return 0; }
            catch (Exception error) { Console.Error.WriteLine(error); return 1; }
        }
        bool longGallery = args is [_, _, _, "--long-gallery"];
        if (args.Length != 3 && !longGallery)
        {
            Console.Error.WriteLine("Usage: SuzuEmojy.Windows.Tests <repo> <fixture-data> <output-directory> [--long-gallery]");
            return 2;
        }
        var application = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        application.Startup += async (_, _) =>
        {
            int code = 0;
            try
            {
                if (longGallery) await CheckLongGallery(args);
                else await Check(args);
            }
            catch (Exception error) { Console.Error.WriteLine(error); code = 1; }
            application.Shutdown(code);
        };
        // No Window.Show, global shortcuts, focus change, mouse or clipboard.
        return application.Run();
    }

    private static async Task Check(string[] args)
    {
        var started = Stopwatch.StartNew();
        WindowsInstanceTests.Run();
        DesktopTests.Run();
        DesktopTests.CheckLoginStartup();
        CheckMiddleScrollGesture();
        var reference = Environment.GetEnvironmentVariable("SUZU_LEGACY_REFERENCE");
        CheckInstallLayout(args[0]);
#if NETFRAMEWORK
        if (reference is not null) CheckFrameworkProcessArguments(reference);
#endif
        var bridgeData = Path.Combine(args[2], $"桥接验证-{Guid.NewGuid():N}");
        await new LibraryCommands(args[0], bridgeData).SaveSettings(new Dictionary<string, object> { ["appearance_mode"] = "dark" });
        Require((await LibrarySnapshot.ReadAsync(bridgeData)).Dark, "The native reader could not read saved settings.");
        await CheckOriginalAnimation(args[0], bridgeData);
        await CheckAboutPage();
        await NativeServicesTests.Run(args[2]);
        await NativeEditTests.Run(args[2]);
        await NativeApngTests.Run(args[2]);
        NativeIdentityTests.Run(args[2]);
        await NativeIdentityTests.CheckWarmup(args[2], reference);
        await NativeExchangeTests.Run(args[2], reference);
        await NativeMediaTests.Run(args[2]);
        await NativeImportTests.Run(args[2], reference);
        await NativeDeletionTests.Run(args[2]);
        await NativeSimilarityTests.Run(args[2], reference);
        await NativeQqTests.Run(args[2], reference);
        await NativeMigrationBoundaryTests.Run(args[2]);
        var library = await LibrarySnapshot.ReadAsync(args[1]);
        await NativeSimilarityTests.CheckFixtureGif(args[1], args[2]);
        await NativeGalleryTests.Run(reference, args[1], args[2]);
        CheckPreferenceState(library);
        await CheckPageLifecycle();
        var window = new MainWindow(library, offscreen: true, commands: new LibraryCommands(args[0], args[1]));
        try
        {
            Layout(window);
            await WaitForCards(window, expectedItems: library.Images.Length);
            Require(window.Gallery.ActiveCards < Math.Min(50, library.Images.Length),
                "Only the viewport plus overscan should create cards.");
            var topLeft = ((FrameworkElement)window.Cards.Children[0]).TranslatePoint(new Point(), window.Root);
            Require(Math.Abs(topLeft.X - window.SidebarWidth.ActualWidth - 20) <= 1 && Math.Abs(topLeft.Y - 88) <= 1,
                $"Unexpected default card grid origin: {topLeft} (sidebar={window.SidebarWidth.ActualWidth}, " +
                $"scroll={window.GalleryScroll.ActualWidth}, viewport={window.GalleryScroll.ViewportWidth}).");

            var readyMillis = started.ElapsedMilliseconds;
            var fresh = await SampleMemory();
            Directory.CreateDirectory(args[2]);
            await DesktopTests.CheckMainWindowPlacement(args[2]);
            SavePng(window.Root, 860, 640, Path.Combine(args[2], "wpf-gallery.png"));
            CheckStickerPreview(window, library, args[2]);
            CheckCaptionWhileBusy(library);

            var initialFirst = window.Cards.Children[0];
            window.GalleryScroll.ScrollToVerticalOffset(1300);
            await WaitForCards(window, library.Images.Length);
            Require(window.GalleryScroll.VerticalOffset == 1300 && !window.Cards.Children.Contains(initialFirst),
                "Scrolling should release offscreen cards.");
            Require(window.Gallery.ActiveCards <= 50, "Scrolling must not accumulate cards.");
            CheckSettingsAppearance(window, args[2]);
            CheckFluentDialogs(window, library, args[2]);
            CheckCategoryIcons(library, new LibraryCommands(args[0], args[1]), args[2]);
            await CheckCategorySubmenuCommands(library, args[0], args[2]);
            CheckExchangePage(window, args[2]);
            await WaitForCards(window, library.Images.Length);
            Require(window.GalleryScroll.VerticalOffset == 1300, "Returning from settings lost the gallery scroll position.");

            var filter = window.Toolbar.Children.OfType<Button>().Single(button => Equals(button.ToolTip, "筛选"));
            var menu = filter.ContextMenu;
            var noKeywords = menu.Items.OfType<MenuItem>().First(item => Equals(item.Header, "无关键词"));
            var clear = menu.Items.OfType<MenuItem>().Single(item => Equals(item.Header, "清除筛选"));
            var conditionalSeparator = menu.Items.OfType<Separator>().Last();
            Require(clear.Visibility == Visibility.Collapsed && conditionalSeparator.Visibility == Visibility.Collapsed,
                "Inactive menu should not show a dangling separator or clear action.");
            Require(menu.Items.Count == 8 && menu.Items[1] is Separator && menu.Items[3] is Separator &&
                menu.Items[4] is MenuItem && menu.Items[5] is MenuItem,
                "Filter row grouping differs from the legacy menu.");
            RenderMenu(menu, Path.Combine(args[2], "wpf-filter.png"), library.Dark, noKeywords);
            noKeywords.IsChecked = true;
            noKeywords.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            await WaitForCards(window, library.Images.Count(image => string.IsNullOrWhiteSpace(image.Keywords)));
            Require(window.Gallery.Items.All(image => string.IsNullOrWhiteSpace(image.Keywords)), "Keyword filter failed.");
            Require(clear.Visibility == Visibility.Visible && conditionalSeparator.Visibility == Visibility.Visible,
                "An active filter should expose its clear action and separator.");
            RenderMenu(menu, Path.Combine(args[2], "wpf-filter-active.png"), library.Dark, noKeywords);
            clear.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            Require(!noKeywords.IsChecked && clear.Visibility == Visibility.Collapsed &&
                conditionalSeparator.Visibility == Visibility.Collapsed, "Clearing did not restore the initial menu.");
            await WaitForCards(window, library.Images.Length);
            CheckTrayMenuThemes(window, library, menu, args[2]);
            await CheckSidebarScroll(library, args[2]);
            await CheckResponsiveGrids(library, args[2]);
            await CheckGalleryMotion(library);
            await CheckSidebarZoomMotion(library);

            // Raise only managed events on our non-displayed controls.
            var category = window.Categories.Children.OfType<Button>().Single(button => Equals(button.ToolTip, "动图"));
            category.ContextMenu.Items.OfType<MenuItem>().Single(item => Equals(item.Header, "批量选择"))
                .RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            int animatedCount = library.Images.Count(image => image.Categories.Contains("动图"));
            await WaitForCards(window, animatedCount);
            Require(window.SelectionBar.Visibility == Visibility.Visible && window.SelectionCount.Text == "已选择 0 项" &&
                window.Gallery.Items.All(image => image.Categories.Contains("动图") && image.Animated),
                "Category context selection must enter batch mode on that category without implicitly selecting files.");
            window.SelectAll.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Require(window.SelectionCount.Text == $"已选择 {animatedCount} 项", "Select All must be limited to that category.");
            window.LeaveSelection.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));

            window.Search.Text = "可爱";
            int found = library.Images.Count(image => image.Keywords.Contains("可爱") && image.Animated);
            await WaitForCards(window, found);
            Require(window.Gallery.Items.All(image => image.Keywords.Contains("可爱")), "Search leaks non-matching items.");
            window.Search.Text = "0000.gif";
            await WaitForCards(window, 1);
            Require(window.Gallery.Items.Single().Name == "0000.gif", "Filename search failed.");
            await WaitForCards(window, 1, width: 760);
            var single = window.Cards.Children.Cast<FrameworkElement>().Single();
            var singleOrigin = single.TranslatePoint(new Point(), window.Root);
            var viewportOrigin = window.GalleryScroll.TranslatePoint(new Point(), window.Root);
            Require(Math.Abs(singleOrigin.X - viewportOrigin.X - 16) < 1 &&
                Math.Abs(singleOrigin.Y - viewportOrigin.Y - 8) < 1,
                $"A single search result must stay anchored top-left after resize: {singleOrigin}, viewport {viewportOrigin}.");
            SavePng(window.Root, 760, 640, Path.Combine(args[2], "wpf-gallery-single.png"));
            Layout(window);

            var tiles = window.Toolbar.Children.OfType<Button>().Single(button => Equals(button.ToolTip, "批量选择"));
            tiles.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            window.SelectAll.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Require(window.SelectionCount.Text == "已选择 1 项", "Selection count is incorrect.");
            window.LeaveSelection.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Require(window.SelectionBar.Visibility == Visibility.Collapsed, "Selection bar stays visible after exit.");

            var gif = library.Images.Single(image => image.Name == "0000.gif");
            using var player = GifPreview.Open(gif.Path, 104);
            Require(player is { FrameCount: 2 }, "Could not read both GIF frames.");
            Require(player!.Delay == TimeSpan.FromMilliseconds(180), "GIF delay changed.");
            var firstPixels = Pixels(player.Bitmap);
            Require(player.Advance() && !firstPixels.SequenceEqual(Pixels(player.Bitmap)),
                "GIF preview did not advance to a visually different frame.");
            Require(player.Advance() && firstPixels.SequenceEqual(Pixels(player.Bitmap)),
                "GIF preview did not loop back to its first frame.");

            await CheckQuickPanel(library, args[2]);
            await CheckSendMedia(library, args[0], args[2], gif);
            await CheckLibraryCommands(library, args[0], args[2]);
            await CheckWallpaper(library, args[0], args[2]);
            await CheckFolderImport(library, args[0], args[2]);
            await CheckImportSources(library, args[0], args[2]);
            await CheckImportQueue(library, args[0], args[2]);
            await CheckExternalStickers(library, args[0], args[2]);
            await CheckTelegramBatch(library, args[2]);
            await CheckSimilarityPage(library, args[0], args[2]);
            Console.WriteLine(JsonSerializer.Serialize(new
            {
                frontend = "WPF", fixtureImages = library.Images.Length, readyMillis,
                samples = fresh, theme = library.Dark ? "dark" : "light",
                clientAreaAnimation = SystemParameters.ClientAreaAnimation,
                checks = new[] { "single-instance", "viewport", "scroll", "sidebar-scroll", "sidebar-icon-grid", "ctrl-wheel", "responsive-alignment",
                    "zoom-continuity", "category-exit-animation", "sidebar-zoom-motion",
                    "layout-persistence", "quick-panel-zoom-persistence", "middle-scroll-gesture", "filter", "tray-menu", "menu-theme",
                    "category", "keyword", "filename",
                    "selection", "gif", "hotkey-config", "hotkey-message-host", "clipboard-payload", "hotkey-to-paste-fakes",
                    "quick-panel-layout", "quick-recents", "quick-search", "monitor-placement", "main-window-placement",
                    "send-media", "library-command-bridge", "windows-install-layout",
                    "settings-navigation", "settings-narrow-layout", "settings-autosave", "wallpaper-cover", "wallpaper-tone",
                    "page-stack-lifecycle", "operation-cancellation", "settings-preview-isolation", "settings-write-rollback",
                    "quick-panel-refresh-in-place",
                    "wallpaper-overlay", "wallpaper-conditional-settings", "quick-panel-wallpaper", "wallpaper-replacement",
                    "fluent-dialogs", "category-picker-icons", "category-submenu-commands",
                    "inactive-hover-preview", "neutral-category-selection",
                    "sticker-preview-outline", "top-left-grid", "non-modal-import", "batch-progress", "folder-summary",
                    "clipboard-file-link-bitmap-priority", "clipboard-link-import", "stable-inbox-import",
                    "browser-drop-formats", "import-queue-cancellation",
                    "exchange-page", "qq-bridge", "telegram-selection", "telegram-single-package", "external-source-navigation", "external-waterfall",
                    "external-preview", "qq-scan-progress", "qq-recycle-confirmation", "qq-export-only", "qq-create-category-with-icon",
                    "telegram-batch-ui", "category-context-selection", "similarity-workspace",
                    "native-snapshot-json-sqlite-parity", "native-v1-hash-candidate" },
            }));
        }
        finally { window.Close(); }
    }

    private static WindowsInstall CheckInstallLayout(string backend)
    {
        backend = Path.GetFullPath(backend);
        bool packaged = File.Exists(Path.Combine(backend, "windows-preview.json"));
        var application = packaged ? backend : Path.Combine(backend, "win_frontend");
        var install = WindowsInstall.Locate(application);
        var portable = install.Launcher is null ? backend : Path.GetDirectoryName(install.Launcher)!;
        Require(install.Root == backend && install.Data == Path.Combine(portable, "data"),
            "Portable and source builds must locate the installation and its default gallery.");
        if (packaged)
            Require(!Directory.Exists(Path.Combine(backend, "backend")) && !Directory.EnumerateFiles(backend, "*", SearchOption.AllDirectories)
                .Any(path => Path.GetExtension(path) is ".py" or ".pyc" or ".pyd" || Path.GetFileName(path).StartsWith("python", StringComparison.OrdinalIgnoreCase)),
                "Packaged mode must not ship Python artifacts.");
        return install;
    }

    private static async Task CheckOriginalAnimation(string backend, string data)
    {
        const string apng = "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAACGFjVEwAAAACAAAAAYSKo+YAAAAaZmNUTAAAAAAAAAAQAAAAEAAAAAAAAAAAAAMACgAAGYacRQAAACVJREFUeJxj/M/A0MBAAWCiRPOoARDAxEAhYBo1gGE0DBgoDwMAVlsBn2iE6a8AAAAaZmNUTAAAAAEAAAAQAAAAEAAAAAAAAAAAAAIABQAAtMkYHAAAAClmZEFUAAAAAnicY2Rg+B/DQAFgokTzqAEQwMRAIWAaNYBhNAwYKA8DAMKeAXuxLv8jAAAAAElFTkSuQmCC";
        const string webp = "UklGRoQAAABXRUJQVlA4WAoAAAASAAAADwAADwAAQU5JTQYAAAAAAAAAAQBBTk1GKAAAAAAAAAAAAA8AAA8AACwBAAJWUDhMDwAAAC8PwAMQBxD9jwIGIqL/AQBBTk1GKAAAAAAAAAAAAA8AAA8AAJABAAJWUDhMDwAAAC8PwAMQBxDR/3IFIqL/AQA=";
        var images = Directory.CreateDirectory(Path.Combine(data, "images")).FullName;
        var commands = new LibraryCommands(backend, data);
        foreach (var (name, raw) in new[] { ("合成 动图.png", apng), ("透明 动图.webp", webp) })
        {
            var path = Path.Combine(images, name);
            File.WriteAllBytes(path, Convert.FromBase64String(raw));
            Require(ResourceIdentityV1.TryRead(path, CancellationToken.None) is null,
                "The native identity candidate must not mistake raw animated input for a static pixel key.");
            Require(PreviewFrames.CanDecode(path, hasStill: true), "Compressed frame detection missed animated content.");
            var response = await commands.Run(new("preview_animation", Paths: [path], Limit: 64), reload: false);
            var payload = response.Result.Animation!;
            var decoded = payload.Native ?? PreviewFrames.Decode(payload.Frames!, payload.Plays, CancellationToken.None);
            var alpha = new byte[4];
            new FormatConvertedBitmap((BitmapSource)decoded.Current, PixelFormats.Bgra32, null, 0)
                .CopyPixels(new Int32Rect(0, 0, 1, 1), alpha, 4, 0);
            Require(decoded.HasMotion && decoded.Delay.TotalMilliseconds == 300 && alpha[3] == 128 && decoded.Advance() &&
                decoded.Delay.TotalMilliseconds == 400 && !decoded.Advance(), "Original preview changed the alpha, timing or loop count.");
            var picture = new Image();
            var playback = new GifPlayback(commands);
            var ready = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            Require(playback.Play(picture, path, 64, () => ready.TrySetResult(true)), "Preview did not start the bundled decoder.");
            await ready.Task.WaitAsync(TimeSpan.FromSeconds(8));
            Require(picture.Source is BitmapSource, "Preview did not display the transported frame.");
            playback.Stop();
            Require(picture.Source is null, "Closing the preview must release its source.");

            bool lateFrame = false;
            Require(playback.Play(picture, path, 64, () => lateFrame = true), "Could not queue a second preview.");
            playback.Stop(restoreStill: false);
            await commands.Run(new("preview_animation", Paths: [path], Limit: 64), reload: false);
            await picture.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
            Require(!lateFrame && picture.Source is null, "A canceled hover decoder must not paint a stale preview.");
        }
        var still = Path.Combine(images, "静态 PNG.png");
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(BitmapSource.Create(1, 1, 96, 96, PixelFormats.Bgra32,
            null, new byte[] { 0, 0, 0, 255 }, 4)));
        using (var output = File.Create(still)) encoder.Save(output);
        Require(!PreviewFrames.CanDecode(still, hasStill: true), "Static PNG hover must not start an animation decoder.");
    }

    private static async Task CheckAboutPage()
    {
        int called = 0;
        string? opened = null, copied = null;
        using var completed = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        var reply = new TaskCompletionSource<AvailableRelease?>(TaskCreationOptions.RunContinuationsAsynchronously);
        var page = new AboutPage(_ => { called++; return reply.Task; }, url => opened = url, text => copied = text);
        Require(called == 0 && opened is null && copied is null && page.VersionText.Text.Contains(AboutPage.InstalledVersion),
            "About must show its version without network, shell or clipboard calls.");
        page.OpenProject.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        page.CopyGroup.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Require(opened == DesktopLinks.Project && copied == "834586488" && page.Credits.Children.Count == 3,
            "The legacy project, group or credits entry is missing.");
        page.CheckUpdate.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Require(called == 1 && !page.CheckUpdate.IsEnabled, "An update check must be explicit and not reentrant.");
        reply.SetResult(new("2.1.2", "更新说明"));
        while (!page.CheckUpdate.IsEnabled) await Task.Delay(10, completed.Token);
        Require(page.Notes.Text == "更新说明", "The About page dropped the update notes.");
        page.Cancel();
    }

#if NETFRAMEWORK
    private static void CheckFrameworkProcessArguments(string backend)
    {
        string[] arguments = ["", "空 格", "C:\\path with spaces\\", "embedded\\\"quote"];
        string[] command = ["-I", "-c", "import sys,json; print(json.dumps(sys.argv[1:]))", .. arguments];
        var start = new ProcessStartInfo(PythonBridge.PythonExecutable(backend))
        {
            Arguments = string.Join(" ", command.Select(WindowsCommandLine.QuoteArgument)),
            UseShellExecute = false, RedirectStandardOutput = true, CreateNoWindow = true,
        };
        using var process = Process.Start(start)!;
        var output = process.StandardOutput.ReadToEndAsync();
        if (!process.WaitForExit(5000)) { process.Kill(); throw new TimeoutException("Argument probe timed out."); }
        Require(process.ExitCode == 0 && JsonSerializer.Deserialize<string[]>(output.GetAwaiter().GetResult())!
            .SequenceEqual(arguments), "The .NET Framework process adapter must preserve Windows arguments exactly.");
    }
#endif

    private static void CheckMiddleScrollGesture()
    {
        var gesture = new MiddleScrollGesture(4, 4);
        var origin = new Point(100, 100);
        gesture.Start(origin);
        Require(gesture.Active && gesture.Pressed && gesture.Advance(new Point(100, 200), .016) == 0 &&
            gesture.Drag(new Point(100, 103)) == 0 && gesture.Drag(new Point(100, 120)) == -20 &&
            gesture.Drag(new Point(100, 110)) == 10,
            "Holding middle button should grab and pan the content with the pointer, not autoscroll.");
        Require(!gesture.Release(new Point(100, 110)) && !gesture.Active, "A drag must end on release.");
        gesture.Start(origin);
        Require(gesture.Release(new Point(101, 101)) && gesture.Active && !gesture.Pressed &&
            gesture.Advance(new Point(500, 110), .05) == 0 &&
            gesture.Advance(new Point(100, 130), .05) == 12 &&
            gesture.Advance(new Point(100, 70), .05) == -12 &&
            gesture.Advance(new Point(100, 900), 1) == 120,
            "A click should latch directional autoscroll with a dead zone and a speed/elapsed cap.");
        gesture.Stop();
        Require(!gesture.Active && gesture.Advance(new Point(100, 900), .05) == 0,
            "Cancelling autoscroll must stop all movement.");
        gesture.Start(origin);
        Require(!gesture.Release(new Point(105, 100)), "A horizontal drag must not be mistaken for a click.");
    }

    private static async Task CheckLongGallery(string[] args)
    {
        var clock = Stopwatch.StartNew();
        var library = await LibrarySnapshot.ReadAsync(args[1]);
        var snapshotMs = clock.ElapsedMilliseconds;
        Require(library.Images.Length >= 10_000, "Use a fixture with at least 10,000 separate images.");
        var window = new MainWindow(library, offscreen: true);
        try
        {
            clock.Restart();
            await WaitForCards(window, library.Images.Length);
            var firstScreenMs = clock.ElapsedMilliseconds;
            using var process = Process.GetCurrentProcess();
            (long Working, long Private) Memory()
            {
                process.Refresh();
                return (process.WorkingSet64, process.PrivateMemorySize64);
            }
            var initial = Memory();
            var peak = initial;
            var firstButton = window.Cards.Children[0];
            int maxCards = window.Gallery.ActiveCards;
            double bottom = window.GalleryScroll.ScrollableHeight;
            Require(bottom > 100_000, $"The full waterfall must be scrollable (extent={bottom}).");
            async Task<double> Move(double target)
            {
                var start = Stopwatch.GetTimestamp();
                window.GalleryScroll.ScrollToVerticalOffset(target);
                Layout(window);
                await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
                Layout(window);
                var elapsed = (Stopwatch.GetTimestamp() - start) * 1000.0 / Stopwatch.Frequency;
                Require(Math.Abs(window.GalleryScroll.VerticalOffset - target) < 1 && window.Gallery.ActiveCards > 0,
                    $"Scroll did not settle at {target}, got {window.GalleryScroll.VerticalOffset}.");
                maxCards = Math.Max(maxCards, window.Gallery.ActiveCards);
                var current = Memory();
                peak = (Math.Max(peak.Working, current.Working), Math.Max(peak.Private, current.Private));
                return elapsed;
            }
            var moves = new double[240];
            for (int frame = 0; frame < moves.Length; frame++) moves[frame] = await Move((frame + 1) * 40);
            var jumps = new double[80];
            for (int i = 0; i < jumps.Length; i++)
                jumps[i] = await Move((i % 2 == 0 ? i / 2.0 : jumps.Length - (i - 1) / 2.0) * bottom / jumps.Length);
            await Move(bottom);
            clock.Restart();
            await WaitForCards(window, library.Images.Length);
            var tailThumbnailsMs = clock.ElapsedMilliseconds;
            Require(maxCards <= 50 && !window.Cards.Children.Contains(firstButton) &&
                window.Cards.Children.Cast<FrameworkElement>().Any(card =>
                    System.Windows.Automation.AutomationProperties.GetName(card) == library.Images[^1].Name),
                "The gallery must realize the final image and release the initial controls without growing with the extent.");
            double P95(double[] values) => Math.Round(values.OrderBy(value => value).ElementAt((int)Math.Ceiling(values.Length * .95) - 1), 2);
            double MiB(long bytes) => Math.Round(bytes / 1048576.0, 1);
            Console.WriteLine(JsonSerializer.Serialize(new
            {
                images = library.Images.Length, snapshotMs, firstScreenMs,
                extent = window.GalleryScroll.ExtentHeight, maxRealizedCards = maxCards,
                incrementalLayoutP95Ms = P95(moves), longJumpLayoutP95Ms = P95(jumps), tailThumbnailsMs,
                initialWorkingSetMiB = MiB(initial.Working), sampledPeakWorkingSetMiB = MiB(peak.Working),
                initialPrivateMiB = MiB(initial.Private), sampledPeakPrivateMiB = MiB(peak.Private),
            }));
        }
        finally { window.Close(); }
    }

    private static async Task CheckSimilarityPage(LibrarySnapshot fixture, string repo, string output)
    {
        var data = Path.Combine(output, $"similarity-fixture-{Guid.NewGuid():N}");
        var images = Directory.CreateDirectory(Path.Combine(data, "images")).FullName;
        var still = fixture.Images.First(item => item.Name.EndsWith(".png", StringComparison.OrdinalIgnoreCase)).Path;
        var animation = fixture.Images.First(item => item.Name.EndsWith(".gif", StringComparison.OrdinalIgnoreCase)).Path;
        File.WriteAllText(Path.Combine(data, "config.json"), fixture.Dark ? "{\"appearance_mode\":\"dark\"}" : "{\"appearance_mode\":\"light\"}");
        for (int i = 0; i < 36; i++) File.Copy(still, Path.Combine(images, $"still-{i:D2}.png"));
        for (int i = 0; i < 2; i++) File.Copy(animation, Path.Combine(images, $"motion-{i}.gif"));
        var commands = new LibraryCommands(repo, data);
        var library = await LibrarySnapshot.ReadAsync(data);
        var window = new MainWindow(library, offscreen: true, commands: commands);
        void Click(Button button) => button.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        async Task Until(Func<bool> ready)
        {
            var started = Stopwatch.StartNew();
            while (!ready() && started.Elapsed < TimeSpan.FromSeconds(12))
            {
                Layout(window);
                await Task.Delay(20);
            }
            Require(ready(), "Similarity workspace did not settle.");
        }
        try
        {
            var tool = window.Toolbar.Children.OfType<Button>().Single(button => Equals(button.ToolTip, "感知哈希去重"));
            Click(tool);
            Layout(window);
            Require(window.Pages.Page is SimilarityPage && !window.OnGalleryPage && window.Caption.Visibility == Visibility.Visible,
                "Similarity must occupy the same full-window host as settings.");
            var page = (SimilarityPage)window.Pages.Page!;
            Require(!page.IsScanning && !page.DeleteSelection.IsEnabled && page.Status.Text.StartsWith("选择范围"),
                "Opening the page must not scan or select any file.");
            Layout(window, 400);
            Require(page.Threshold.ActualWidth > 0 && page.Scan.ActualWidth > 0 && page.ResultScroll.ViewportHeight > 200,
                "Similarity options and results should remain usable in a narrow window.");
            Layout(window);
            SavePng(window.Root, 860, 640, Path.Combine(output, "wpf-similarity-idle.png"));
            Click(page.Scan);
            Require(page.IsScanning && page.CancelScan.IsEnabled, "Scanning should start explicitly and expose cancel.");
            Click(page.CancelScan);
            await Until(() => !page.IsScanning);
            Require(page.Status.Text.StartsWith("已取消") && Directory.GetFiles(images).Length == 38,
                "Cancel must return control without mutating the library.");
            await page.ScanResults(rematching: false);
            Layout(window);
            Require(page.Status.Text.Contains("2 组") && page.PageNumber.Text == "1 / 2" &&
                page.Results.Children.OfType<WrapPanel>().Single().Children.Count == 36 && !page.DeleteSelection.IsEnabled,
                $"Real scanner must show bounded pages, with nothing selected: {page.Status.Text}");
            await Until(() => Thumbnails(page).All(image => image.Source is not null));
            SavePng(window.Root, 860, 640, Path.Combine(output, "wpf-similarity-results.png"));

            var cards = page.Results.Children.OfType<WrapPanel>().Single();
            var last = (StackPanel)((Border)cards.Children[^1]).Child;
            Click((Button)last.Children[0]);
            Require(page.Inspector.Visibility == Visibility.Visible && window.Pages.Heading.Text == "相似图片预览" &&
                page.PictureDetails.Text.Contains("still-35.png"), "Clicking a card must open the in-page inspector.");
            Click(page.NextPicture);
            Require(page.PictureDetails.Text.Contains("相似组 2") && page.PictureDetails.Text.Contains("motion-0.gif") &&
                page.PictureDetails.Text.Contains("0.36 s"), "Inspector navigation must cross group and page boundaries with metadata.");
            await Until(() => page.LargePicture.Source is not null);
            SavePng(window.Root, 860, 640, Path.Combine(output, "wpf-similarity-inspector.png"));
            page.MarkPicture.IsChecked = true;
            Click(window.Pages.Back);
            Require(window.Pages.Page == page && page.PageNumber.Text == "2 / 2" && page.DeleteSelection.IsEnabled,
                "Back from the inspector must restore the current page and preserve its selection.");
            Click(page.PreviousPage);
            Require(page.DeleteSelection.IsEnabled && page.PageNumber.Text == "1 / 2", "Selection must survive changing result pages.");
            page.Threshold.Value = 5;
            await Until(() => !page.IsScanning && !page.DeleteSelection.IsEnabled && page.Status.Text.StartsWith("扫描完成"));
            Require(page.ThresholdValue.Text == "5" && page.PageNumber.Text == "1 / 2", "Changing the threshold must rematch in place.");

            Click(page.NextPage);
            var motion = page.Results.Children.OfType<WrapPanel>().Single();
            var motionCard = (StackPanel)((Border)motion.Children[0]).Child;
            ((CheckBox)motionCard.Children[2]).IsChecked = true;
            Click(page.DeleteSelection);
            Require(page.DeleteConfirmation.Visibility == Visibility.Visible && File.Exists(Path.Combine(images, "motion-0.gif")),
                "Opening confirmation alone must never delete an image.");
            Click(window.Pages.Back);
            Require(page.DeleteConfirmation.Visibility == Visibility.Collapsed && page.DeleteSelection.IsEnabled,
                "Back from confirmation should cancel it without clearing the selection.");
            Click(page.DeleteSelection);
            Click(page.ConfirmDelete);
            Require(page.IsDeleting && !window.ShowGalleryPage(), "Deletion must protect the page from being torn down mid-write.");
            await Until(() => !page.IsDeleting && !page.IsScanning && page.Status.Text.StartsWith("扫描完成"));
            Require(!File.Exists(Path.Combine(images, "motion-0.gif")) && File.Exists(Path.Combine(images, "motion-1.gif")) &&
                File.Exists(animation) && page.Status.Text.Contains("1 组") && window.Gallery.Items.Count == 37,
                "Confirmed deletion must remove only the checked copy and refresh both results and gallery.");
            var kind = page.Options.Children.OfType<FluentChoice>().Single(choice => choice.Value == "all");
            kind.ContextMenu.Items.OfType<MenuItem>().Single(item => Equals(item.Header, "仅动图"))
                .RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            Require(page.Status.Text.StartsWith("范围已变更") && !page.DeleteSelection.IsEnabled && !page.IsScanning,
                "Changing scope or image type must discard stale candidates and wait for an explicit scan.");
            Click(page.Scan);
            Click(window.Pages.Back);
            Require(window.OnGalleryPage && window.GalleryPage.Visibility == Visibility.Visible, "Back must return to the mounted gallery.");
            await Until(() => !page.IsScanning);
        }
        finally { window.Close(); }

        static System.Collections.Generic.IEnumerable<Image> Thumbnails(SimilarityPage page) => page.Results.Children.OfType<WrapPanel>()
            .SelectMany(wrap => wrap.Children.OfType<Border>()).Select(card => (StackPanel)card.Child)
            .Select(card => ((Grid)((Button)card.Children[0]).Content).Children.OfType<Image>().Single());
    }

    private static void CheckStickerPreview(MainWindow window, LibrarySnapshot library, string output)
    {
        using var hover = new PreviewPopup(window, library);
        Require(!window.IsActive && PreviewPopup.CanOpen(true, WindowState.Normal, true, false) &&
            !PreviewPopup.CanOpen(false, WindowState.Normal, true, false) &&
            !PreviewPopup.CanOpen(true, WindowState.Minimized, true, false) &&
            !PreviewPopup.CanOpen(true, WindowState.Normal, false, false) &&
            !PreviewPopup.CanOpen(true, WindowState.Normal, true, true),
            "Hover should depend on visibility, the pointer and mouse buttons, not foreground activation.");
        Require(hover.Surface is { Focusable: false, IsHitTestVisible: false, StaysOpen: true, AllowsTransparency: true,
            PopupAnimation: PopupAnimation.None }
            && ReferenceEquals(hover.Surface.PlacementTarget, window),
            "The hover window must be passive with no hit testing, focus or native animation.");
        var thumbnail = (Button)window.Cards.Children[0];
        var hoverSurface = (Border)thumbnail.Template.FindName("CardHover", thumbnail);
        Require(!hoverSurface.IsHitTestVisible && hoverSurface.Visibility == Visibility.Hidden,
            "The thumbnail's static hover layer must not take input or appear when unhovered.");
        var preview = new StickerPreview { FontFamily = window.FontFamily };
        preview.Configure(library, window.Resources);
        var sticker = library.Images.First(item => item.Name.EndsWith(".png", StringComparison.OrdinalIgnoreCase));
        var source = MediaThumbnail.Load(sticker.Path, (int)preview.PictureSide);
        Require(source is not null, "Could not decode the preview fixture PNG.");
        preview.ShowSticker(sticker, source!);
        int width = (int)preview.Width;
        preview.Measure(new Size(width, 1000));
        int height = (int)Math.Ceiling(preview.DesiredSize.Height);
        preview.Arrange(new Rect(0, 0, width, height));
        preview.UpdateLayout();
        var bitmap = RenderBitmap(preview, width, height);
        SaveBitmap(bitmap, Path.Combine(output, "wpf-sticker-preview.png"));
        var pixels = Pixels(bitmap);
        int At(int x, int y) => (y * width + x) * 4;
        int edge = At(width / 2, 0), inside = At(width / 2, 3), surface = At(12, 12);
        int contrast = library.Dark ? pixels[edge] - pixels[surface] : pixels[surface] - pixels[edge];
        Require(pixels[3] == 0 && pixels[edge + 3] > 220 && contrast is > 12 and < 40 &&
            pixels.AsSpan(inside, 4).SequenceEqual(pixels.AsSpan(surface, 4)),
            $"The preview must preserve transparent corners and a subtle outline only at the edge (contrast: {contrast}).");
        Require(pixels[At(width / 2, width / 2) + 3] == 255 && preview.Keywords.Text.EndsWith(
            string.IsNullOrWhiteSpace(sticker.Keywords) ? "无" : sticker.Keywords),
            "The preview must preserve image transparency and still render the image and metadata.");
    }

    private static void CheckCaptionWhileBusy(LibrarySnapshot library)
    {
        var window = new MainWindow(library, offscreen: true);
        try
        {
            var button = window.Caption.Children.OfType<Button>().Single(item => Equals(item.ToolTip, "关闭"));
            window.PageArea.IsEnabled = false;
            window.BusyProgress.Visibility = Visibility.Visible;
            Layout(window);
            var corner = button.TranslatePoint(new Point(button.ActualWidth / 2, 1), window.Root);
            // InputHitTest ignores the whole tree of an unshown Window. Walk the
            // arranged visuals and apply the input filter without showing it.
            DependencyObject? hit = null;
            VisualTreeHelper.HitTest(window.Root,
                visual => visual is UIElement { IsHitTestVisible: false } or UIElement { IsEnabled: false }
                    ? HitTestFilterBehavior.ContinueSkipSelfAndChildren : HitTestFilterBehavior.Continue,
                result => { hit = result.VisualHit; return HitTestResultBehavior.Stop; },
                new PointHitTestParameters(corner));
            bool hitsClose = false;
            while (hit is System.Windows.Media.Visual)
            {
                if (hit == button) { hitsClose = true; break; }
                hit = VisualTreeHelper.GetParent(hit);
            }
            Require(hitsClose && button.IsEnabled && !window.GalleryPage.IsEnabled &&
                System.Windows.Shell.WindowChrome.GetIsHitTestVisibleInChrome(button),
                "The window's busy indicator and disabled content must never block the close button.");
            Require(DesktopSession.DefersWindowClose(false, true, 0) &&
                DesktopSession.DefersWindowClose(false, false, 1) &&
                !DesktopSession.DefersWindowClose(false, false, 0) &&
                !DesktopSession.DefersWindowClose(true, true, 1),
                "Window close must wait only for pending writes, never veto an explicit shutdown.");
            bool closed = false;
            window.Closed += (_, _) => closed = true;
            button.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Require(closed, "The caption × did not route to Window.Close.");
        }
        finally { window.Close(); }
    }

    private static void CheckSettingsAppearance(MainWindow window, string output)
    {
        var tools = window.Toolbar.Children.OfType<Button>();
        var open = tools.Single(button => Equals(button.ToolTip, "设置"));
        open.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Layout(window);
        Require(window.OnSettingsPage && window.GalleryPage.Visibility == Visibility.Collapsed &&
            window.Pages.Page is SettingsPage && window.Caption.Visibility == Visibility.Visible,
            "Settings must replace the gallery body while retaining the main window chrome.");
        var page = (SettingsPage)window.Pages.Page!;
        var animations = page.Groups.Children.OfType<StackPanel>().SelectMany(group => group.Children.OfType<SettingCard>())
            .SelectMany(card => ((Grid)card.Child).Children.OfType<CheckBox>())
            .Single(input => System.Windows.Automation.AutomationProperties.GetName(input) == "关闭动画");
        Require(animations.IsChecked != true, "Motion must be enabled by default.");
        var top = window.Pages.Back.TranslatePoint(new Point(), window.Root);
        SavePng(window.Root, 860, 640, Path.Combine(output, "wpf-settings.png"));
        Require(Math.Abs(top.X - 36) < 1 && Math.Abs(top.Y - 42) < 1 && page.Scroll.ViewportHeight > 500,
            $"Settings header and content must match the full-window layout: {top}, viewport {page.Scroll.ViewportHeight}.");
        page.Scroll.ScrollToEnd();
        Layout(window);
        SavePng(window.Root, 860, 640, Path.Combine(output, "wpf-settings-advanced.png"));

        page.Scroll.ScrollToHome();
        Layout(window, 400);
        var personalization = page.Groups.Children.OfType<StackPanel>().ElementAt(1);
        page.Scroll.ScrollToVerticalOffset(personalization.TranslatePoint(new Point(), page.Groups).Y);
        Layout(window, 400);
        var paletteCard = personalization.Children.OfType<SettingCard>().ElementAt(1);
        var paletteGrid = (Grid)paletteCard.Child;
        var palettes = paletteGrid.Children.OfType<WrapPanel>().Single();
        var description = paletteGrid.Children.OfType<StackPanel>().Single();
        Require(Grid.GetRow(palettes) == 1 && description.ActualWidth > 200 && palettes.ActualWidth <= paletteGrid.ActualWidth,
            "The theme controls must wrap below the description at the minimum window width.");
        var thumbnailInput = personalization.Children.OfType<SettingCard>().SelectMany(card => ((Grid)card.Child).Children.OfType<StackPanel>())
            .Single(panel => System.Windows.Automation.AutomationProperties.GetName(panel) == "缩略图大小");
        var spin = (Grid)thumbnailInput.Children[0];
        var number = spin.Children.OfType<TextBox>().Single();
        var editor = (ScrollViewer)number.Template.FindName("PART_ContentHost", number);
        Require(editor.ScrollableWidth <= 1, "The numeric value must fit before the step buttons in a narrow window.");
        SavePng(window.Root, 400, 640, Path.Combine(output, "wpf-settings-narrow.png"));
        Layout(window);
        Require(Grid.GetRow(palettes) == 0, "Theme controls must return to the right when settings has enough room.");
        window.Pages.Back.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Require(!window.OnSettingsPage && window.GalleryPage.Visibility == Visibility.Visible,
            "Back did not restore the gallery.");
    }

    private static void CheckFluentDialogs(MainWindow window, LibrarySnapshot library, string output)
    {
        var prompt = FluentDialog.CreateTextInput(window, library, "新建分类", "请输入分类名称：", "", false,
            out var name, out var confirm);
        var rename = FluentDialog.CreateTextInput(window, library, "重命名", "请输入新的分类名称：", "动图", false,
            out var original, out var renameConfirm);
        var keywords = FluentDialog.CreateTextInput(window, library, "编辑关键词", "关键词", "示例", true,
            out var tag, out var tagConfirm);
        try
        {
            name.Text = "　 ";
            Require(!confirm.IsEnabled, "Empty category names must not be submitted.");
            name.Text = "  新分类  ";
            Require(confirm.IsEnabled && FluentDialog.TextValue(name) == "新分类", "Category names must be trimmed.");
            Require(renameConfirm.IsEnabled && original.Text == "动图", "Rename must start with the current name.");
            tag.Text = "";
            Require(tagConfirm.IsEnabled && FluentDialog.TextValue(tag) == "", "The shared dialog must allow clearing tags.");
            name.Text = "";
            Render(prompt, "wpf-new-category.png");
            Render(rename, "wpf-rename-category.png");
        }
        finally { prompt.Close(); rename.Close(); keywords.Close(); }

        void Render(FluentDialog dialog, string file)
        {
            Require(dialog is { WindowStyle: WindowStyle.None, AllowsTransparency: true, ShowInTaskbar: false,
                WindowStartupLocation: WindowStartupLocation.CenterOwner } &&
                dialog.Frame.CornerRadius == new CornerRadius(10) && dialog.Actions.Children.Count == 2 &&
                dialog.Actions.Children.OfType<Button>().Count(button => button.IsDefault) == 1 &&
                ColorOf(dialog.Frame.Background) == ColorOf((Brush)window.FindResource("PromptSurface")),
                "Text prompts should use the same themed, compact window chrome and actions.");
            var root = dialog.LayoutRoot;
            int width = (int)dialog.Width;
            root.Measure(new Size(width, 500));
            int height = (int)Math.Ceiling(root.DesiredSize.Height);
            Require(height is >= 155 and <= 205, $"Category dialog should stay compact: {height}");
            root.Arrange(new Rect(0, 0, width, height));
            root.UpdateLayout();
            SavePng(root, width, height, Path.Combine(output, file));
        }
    }

    private static void CheckCategoryIcons(LibrarySnapshot fixture, LibraryCommands commands, string output)
    {
        var missing = Path.Combine(output, "missing-category-icon.png");
        var categories = new Category[]
        {
            new("图片收藏夹", "🌿", fixture.Images.First(image => image.Animated).Path),
            new("Emoji_收藏夹", "🐈", missing),
            new("默认收藏夹", null, missing),
        };
        var library = fixture with { Categories = categories, Settings = new(fixture.Settings)
        {
            ["sidebar_is_grid_mode"] = JsonSerializer.SerializeToElement(false),
        } };
        var window = new MainWindow(library, offscreen: true, commands: commands);
        try
        {
            Layout(window);
            var buttons = window.Categories.Children.OfType<Button>().Where(button =>
                categories.Any(category => Equals(category.Name, button.ToolTip))).ToArray();
            Require(buttons.Length == categories.Length, "Synthetic folders should use the real sidebar.");
            var fallbackRow = buttons[2].ContextMenu;
            var dimensions = LayoutMenu(fallbackRow);
            var heading = (ContentPresenter)fallbackRow.Template.FindName("Heading", fallbackRow);
            Require(heading.Content is DockPanel headingRow && headingRow.Children[1] is TextBlock header &&
                header.Text == categories[2].Name && heading.ActualHeight >= 16 && !heading.IsHitTestVisible,
                "The folder menu must display its name and icon without adding a selectable action.");
            var headingIcon = IconCore(((DockPanel)heading.Content).Children[0]);
            Require(headingIcon is Viewbox, "An unreadable image without Emoji falls back to the folder glyph.");
            SavePng(fallbackRow, dimensions.Width, dimensions.Height, Path.Combine(output, "wpf-category-context.png"));

            buttons[0].RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            var context = window.Gallery.ContextMenuFor!(fixture.Images[0])!;
            Require(context.Items.OfType<MenuItem>().Where(row => ((string)row.Header).Contains(categories[0].Name))
                .Select(row => IconCore(row.Icon)).ToArray() is [Image { Source: not null }, Image { Source: not null }],
                "Sticker actions naming a folder should carry that folder's image.");
            var addTo = CategorySubmenu(context, "添加到分类");
            var moveTo = CategorySubmenu(context, "移动到分类");
            foreach (var branch in new[] { addTo, moveTo })
            {
                Require(branch.Role == MenuItemRole.SubmenuHeader && branch.Items.OfType<MenuItem>()
                    .Select(row => (string)row.Header).SequenceEqual(categories.Skip(1).Select(category => category.Name)),
                    "Add and Move must be native submenus with destinations in library order, excluding the source.");
                Require(branch.Items.OfType<MenuItem>().Select(row => IconCore(row.Icon)).ToArray()
                    is [TextBlock { Text: "🐈" }, Viewbox], "Nested destination rows must keep their folder icons.");
            }
            LayoutMenu(context);
            var popup = (Popup)addTo.Template.FindName("PART_Popup", addTo);
            Require(popup.Placement == PlacementMode.Right && !popup.IsOpen &&
                System.Windows.Data.BindingOperations.GetBinding(popup, Popup.IsOpenProperty)?.Path.Path == nameof(MenuItem.IsSubmenuOpen) &&
                addTo.Template.FindName("SubmenuArrow", addTo) is FrameworkElement { Visibility: Visibility.Visible },
                "The branch must use WPF's submenu state, side placement and chevron.");
            var popupRoot = (FrameworkElement)popup.Child;
            popupRoot.Measure(new Size(340, 260));
            var bounds = popupRoot.DesiredSize;
            popupRoot.Arrange(new Rect(new Point(), bounds));
            popupRoot.UpdateLayout();
            var card = (Border)addTo.Template.FindName("SubmenuCard", addTo);
            var leaf = (MenuItem)addTo.Items[0];
            var leafIcon = (FrameworkElement)leaf.Template.FindName("Icon", leaf);
            var leafLabel = (FrameworkElement)leaf.Template.FindName("Label", leaf);
            Require(card.ActualHeight > 56 && ColorOf(card.Background) == ColorOf((Brush)context.FindResource("FluentMenuSurface")) &&
                leafIcon.ActualWidth == 16 &&
                Math.Abs(leafLabel.TranslatePoint(new Point(), leaf).X - leafIcon.TranslatePoint(new Point(), leaf).X - 24) < 1,
                "The nested popup must render the current palette and its own aligned icon gutter.");
            SavePng(popupRoot, (int)Math.Ceiling(bounds.Width), (int)Math.Ceiling(bounds.Height),
                Path.Combine(output, "wpf-category-submenu.png"));

            var tool = window.Toolbar.Children.OfType<Button>().Single(button => Equals(button.ToolTip, "感知哈希去重"));
            tool.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Layout(window);
            var page = (SimilarityPage)window.Pages.Page!;
            var scope = page.Options.Children.OfType<FluentChoice>().Single(choice => choice.Value == "");
            var menu = scope.ContextMenu;
            var rows = menu.Items.OfType<MenuItem>().ToArray();
            Require(rows.Select(row => (string)row.Header).SequenceEqual(new[] { "全部资源" }.Concat(categories.Select(x => x.Name))),
                "Folder icons must not change option names or their order.");
            Require(IconCore(rows[1].Icon) is Image { Source: BitmapSource { PixelWidth: <= 16, PixelHeight: <= 16 } } &&
                IconCore(rows[2].Icon) is TextBlock { Text: "🐈" } && IconCore(rows[3].Icon) is Viewbox,
                "Choice rows must show an image, the custom Emoji, or the default glyph.");
            rows[2].RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            Require(scope.Value == categories[1].Name && rows.Count(row => row.IsChecked) == 1 && rows[2].IsChecked &&
                scope.Content is DockPanel current && IconCore(current.Children[0]) is TextBlock { Text: "🐈" },
                "Choosing an icon-bearing folder must update the key, checkmark and closed control.");
            dimensions = LayoutMenu(menu);
            var check = (FrameworkElement)rows[2].Template.FindName("Mark", rows[2]);
            var icon = (FrameworkElement)rows[2].Template.FindName("Icon", rows[2]);
            var label = (FrameworkElement)rows[2].Template.FindName("Label", rows[2]);
            Require(check.Visibility == Visibility.Visible && icon.ActualWidth == 16 &&
                icon.TranslatePoint(new Point(), rows[2]).X - check.TranslatePoint(new Point(), rows[2]).X == 24 &&
                label.TranslatePoint(new Point(), rows[2]).X - icon.TranslatePoint(new Point(), rows[2]).X == 24,
                "Checkmarks, icons and labels must have separate aligned columns.");
            SavePng(menu, dimensions.Width, dimensions.Height, Path.Combine(output, "wpf-category-choice.png"));
            var glyph = ((Canvas)((Viewbox)IconCore(rows[3].Icon)!).Child).Children.OfType<System.Windows.Shapes.Path>().Single();
            Require(ColorOf(glyph.Stroke) == (library.Dark ? System.Windows.Media.Colors.White : System.Windows.Media.Colors.Black),
                "Default glyphs must match the menu ink.");
            var settings = new Dictionary<string, JsonElement>(library.Settings)
            {
                ["appearance_mode"] = JsonSerializer.SerializeToElement(library.Dark ? "light" : "dark"),
            };
            window.MenuPalette.Apply(library with { Settings = settings });
            LayoutMenu(menu);
            Require(ColorOf(glyph.Stroke) == (library.Dark ? System.Windows.Media.Colors.Black : System.Windows.Media.Colors.White),
                "Already-built popup icons must follow live theme updates.");

            foreach (bool multiple in new[] { false, true })
            {
                var picker = FluentDialog.CreateCategoryPicker(window, library, multiple ? "导出指定分类" : "选择分类",
                    categories, multiple, out var selected);
                try
                {
                    var options = picker.Body.Children.OfType<ToggleButton>().ToArray();
                    var ok = picker.Actions.Children.OfType<Button>().Single(button => button.IsDefault);
                    Require(!ok.IsEnabled && options.Select(option => System.Windows.Automation.AutomationProperties.GetName(option))
                        .SequenceEqual(categories.Select(category => category.Name)) &&
                        options.Select(option => IconCore(((DockPanel)option.Content).Children[0])).ToArray()
                            is [Image, TextBlock { Text: "🐈" }, Viewbox],
                        "Both picker types must retain accessible folder names and display the shared icons.");
                    options[0].IsChecked = true;
                    options[1].IsChecked = true;
                    Require(ok.IsEnabled && selected().SequenceEqual(
                        categories.Skip(multiple ? 0 : 1).Take(multiple ? 2 : 1).Select(category => category.Name)),
                        "Rendering icons must not affect the dialog's single/multiple selection or returned names.");
                    if (multiple)
                    {
                        var root = picker.LayoutRoot;
                        root.Measure(new Size(420, 500));
                        int height = (int)Math.Ceiling(root.DesiredSize.Height);
                        root.Arrange(new Rect(0, 0, 420, height));
                        root.UpdateLayout();
                        SavePng(root, 420, height, Path.Combine(output, "wpf-category-export-picker.png"));
                    }
                }
                finally { picker.Close(); }
            }
        }
        finally { window.Close(); }
    }

    private static UIElement? IconCore(object? icon) => (icon as Viewbox)?.Child;

    private static MenuItem CategorySubmenu(ContextMenu menu, string name) =>
        menu.Items.OfType<MenuItem>().Single(row => Equals(row.Header, name));

    private static async Task CheckCategorySubmenuCommands(LibrarySnapshot fixture, string repo, string output)
    {
        var data = Path.Combine(output, $"category-submenu-{Guid.NewGuid():N}");
        var images = Directory.CreateDirectory(Path.Combine(data, "images")).FullName;
        var names = fixture.Images.Where(image => !image.Animated).Take(2).Select(image =>
        {
            File.Copy(image.Path, Path.Combine(images, image.Name));
            return image.Name;
        }).ToArray();
        var source = "来源";
        var keep = "保留";
        var target = "目标";
        File.WriteAllText(Path.Combine(data, "categories.json"), JsonSerializer.Serialize(new Dictionary<string, string[]>
        {
            [source] = names, [keep] = [], [target] = [],
        }));
        var commands = new LibraryCommands(repo, data);
        var library = await LibrarySnapshot.ReadAsync(data);
        var window = new MainWindow(library, offscreen: true, commands: commands);
        try
        {
            Layout(window);
            var all = window.Gallery.ContextMenuFor!(library.Images[0])!;
            Require(CategorySubmenu(all, "添加到分类").Items.Count == 3 &&
                !all.Items.OfType<MenuItem>().Any(row => Equals(row.Header, "移动到分类")),
                "All stickers has all add destinations and no move source.");
            var sourceButton = window.Categories.Children.OfType<Button>().Single(button => Equals(button.ToolTip, source));
            sourceButton.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            var added = await Choose(window.Gallery.ContextMenuFor(library.Images[0])!, "添加到分类", keep);
            Require(added.Images[0].Categories.ToHashSet().SetEquals([source, keep]) &&
                added.Images[1].Categories.SequenceEqual([source]),
                "Clicking a one-image Add destination must write directly and keep its source membership.");

            sourceButton.ContextMenu.Items.OfType<MenuItem>().Single(row => Equals(row.Header, "批量选择"))
                .RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            window.SelectAll.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Require(window.SelectionCount.Text == "已选择 2 项", "Batch submenu must receive both selected images.");
            var moved = await Choose(window.Gallery.ContextMenuFor(added.Images[0])!, "移动到分类", target);
            Require(moved.Images[0].Categories.ToHashSet().SetEquals([keep, target]) &&
                moved.Images[1].Categories.SequenceEqual([target]),
                "Clicking the batch Move destination must move both files and not remove other memberships.");

            window.AcceptSnapshot(moved with { Categories = [moved.Categories.Single(category => category.Name == source)] },
                source, updateSettings: false);
            var none = window.Gallery.ContextMenuFor(moved.Images[0])!;
            Require(!CategorySubmenu(none, "添加到分类").IsEnabled && !CategorySubmenu(none, "移动到分类").IsEnabled,
                "Branches with no eligible destinations must be disabled.");
        }
        finally { window.Close(); }

        async Task<LibrarySnapshot> Choose(ContextMenu menu, string branch, string destination)
        {
            var changed = new TaskCompletionSource<LibrarySnapshot>(TaskCreationOptions.RunContinuationsAsynchronously);
            void OnChange(LibrarySnapshot updated) => changed.TrySetResult(updated);
            window.LibraryChanged += OnChange;
            try
            {
                var header = CategorySubmenu(menu, branch);
                header.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
                Require(!window.LibraryBusy && !changed.Task.IsCompleted, "The submenu header itself must not write.");
                header.Items.OfType<MenuItem>().Single(row => Equals(row.Header, destination))
                    .RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
                return await changed.Task.WaitAsync(TimeSpan.FromSeconds(8));
            }
            finally { window.LibraryChanged -= OnChange; }
        }
    }

    private static async Task CheckWallpaper(LibrarySnapshot fixture, string repo, string output)
    {
        var data = Path.Combine(output, $"wallpaper-fixture-{Guid.NewGuid():N}");
        Directory.CreateDirectory(data);
        var source = Path.Combine(data, "source.png");
        const int sourceWidth = 600, sourceHeight = 200;
        var pixels = new byte[sourceWidth * sourceHeight * 4];
        for (int y = 0; y < sourceHeight; y++)
            for (int x = 0; x < sourceWidth; x++)
            {
                int offset = (y * sourceWidth + x) * 4;
                pixels[offset] = x >= 450 ? (byte)230 : (byte)0;
                pixels[offset + 1] = x is >= 150 and < 450 ? (byte)128 : (byte)0;
                pixels[offset + 2] = x < 150 ? (byte)230 : (byte)0;
                pixels[offset + 3] = 255;
            }
        SaveBitmap(BitmapSource.Create(sourceWidth, sourceHeight, 96, 96, PixelFormats.Bgra32, null, pixels, sourceWidth * 4), source);
        var commands = new LibraryCommands(repo, data);
        await commands.SaveSettings(new Dictionary<string, object> { ["appearance_mode"] = fixture.Dark ? "dark" : "light" });
        var window = new MainWindow(await LibrarySnapshot.ReadAsync(data), offscreen: true, commands: commands);
        try
        {
            window.ShowSettings();
            Layout(window);
            var page = (SettingsPage)window.Pages.Page!;
            var settings = page.Groups.Children.OfType<StackPanel>().ElementAt(1);
            var remove = settings.Children.OfType<SettingCard>().SelectMany(card => ((Grid)card.Child).Children.OfType<StackPanel>())
                .SelectMany(panel => panel.Children.OfType<Button>()).Single(button => Equals(button.Content, "移除"));
            Require(!remove.IsEnabled && window.Wallpaper.Visibility == Visibility.Collapsed &&
                page.WallpaperOptions.Visibility == Visibility.Collapsed, "No picture should mean no wallpaper adjustment controls.");
            Require(await window.ChangeWallpaper(source), "The real bridge should save a selected wallpaper.");
            await WaitForWallpaper(window, true);
            File.Move(source, Path.Combine(data, "moved.png"));
            Require(remove.IsEnabled && !window.Wallpaper.IsHitTestVisible && page.WallpaperOptions.Visibility == Visibility.Visible &&
                window.Wallpaper.Overlay.Opacity == .2, "A loaded wallpaper should reveal its adjustments and default overlay.");
            Require(window.Caption.Background is null && window.SidebarChrome.Background is null && window.Pages.Background is null &&
                Equals(window.SearchFrame.Background, window.FindResource("SearchSurface")),
                "Wallpaper must not add separate tinted blocks to window chrome, sidebar, pages or search.");
            Slider Rail(string label) => page.WallpaperOptions.Children.OfType<SettingCard>()
                .SelectMany(card => ((Grid)card.Child).Children.OfType<StackPanel>())
                .SelectMany(panel => panel.Children.OfType<Slider>())
                .Single(rail => System.Windows.Automation.AutomationProperties.GetName(rail) == label);
            var opacity = Rail("遮罩不透明度");
            opacity.Value = 0;
            Layout(window);
            Require(window.Wallpaper.Overlay.Visibility == Visibility.Collapsed, "Zero opacity must remove the overlay.");
            var brush = (ImageBrush)window.Wallpaper.Texture.Background;
            Require(brush.Stretch == Stretch.UniformToFill &&
                ((BitmapSource)brush.ImageSource).PixelWidth == sourceWidth, "Wallpaper must keep the original ratio.");
            var pixel = Sample(window.Wallpaper, 15, 100);
            Require(pixel.G is >= 124 and <= 132 && pixel.R < 5 && pixel.B < 5 &&
                Sample(window.Wallpaper, 845, 100).G >= 124, "A wide wallpaper should be center-cropped, not squeezed or letterboxed.");

            opacity.Value = 100;
            Layout(window);
            var theme = ColorOf((Brush)window.FindResource("CanvasColor"));
            Require(Sample(window.Root, 15, 5) == theme && Sample(window.Root, 845, 620) == theme,
                "The full-opacity overlay must cover both the caption and the bottom of the main window.");
            opacity.Value = 35;
            Layout(window);
            var tinted = Sample(window.Root, 15, 5);
            Require(tinted == Sample(window.Root, 845, 620) &&
                Math.Abs(tinted.G - (pixel.G * .65 + theme.G * .35)) <= 2,
                "A partial opacity must blend exactly one theme-colored layer over the entire wallpaper.");
            opacity.Value = 0;
            var brightness = Rail("背景亮度");
            brightness.Value = -50;
            Layout(window);
            var darker = Sample(window.Wallpaper, 25, 100);
            Require(darker.G < pixel.G - 35, $"Negative brightness must darken the wallpaper immediately ({pixel.G} -> {darker.G}).");
            brightness.Value = 50;
            Layout(window);
            var lighter = Sample(window.Wallpaper, 25, 100);
            Require(lighter.G > pixel.G + 35 && lighter.R > 100, "Positive brightness must brighten the wallpaper immediately.");
            opacity.Value = 35;
            Rail("背景模糊度").Value = 15;
            Require(window.Wallpaper.Texture.Effect is System.Windows.Media.Effects.BlurEffect { Radius: 15 } &&
                window.Root.Effect is null && window.Pages.Effect is null, "Only the wallpaper layer may be blurred.");
            var saved = new TaskCompletionSource<LibrarySnapshot>(TaskCreationOptions.RunContinuationsAsynchronously);
            window.LibraryChanged += snapshot =>
            {
                if (snapshot.Number("wallpaper_blur", 0, 0, 40) == 15) saved.TrySetResult(snapshot);
            };
            window.Pages.Back.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            window.FlushSettings();
            await saved.Task.WaitAsync(TimeSpan.FromSeconds(8));
            var persisted = await LibrarySnapshot.ReadAsync(data);
            Require(persisted.Wallpaper is not null && persisted.Number("wallpaper_brightness", 0, -100, 100) == 50 &&
                persisted.Number("wallpaper_overlay_opacity", 0, 0, 100) == 35,
                "Wallpaper and its adjustment values should survive reopening the app.");
            var reopened = new MainWindow(persisted, offscreen: true, commands: commands);
            var quick = new QuickPanel(persisted, new QuickPanelResults(persisted), offscreen: true);
            try
            {
                await WaitForWallpaper(reopened, true);
                quick.Root.Measure(quick.RequestedSize);
                quick.Root.Arrange(new Rect(quick.RequestedSize));
                quick.Root.UpdateLayout();
                Require(quick.Wallpaper.Visibility == Visibility.Visible && !quick.Wallpaper.IsHitTestVisible &&
                    quick.Wallpaper.Texture.Background is ImageBrush { Stretch: Stretch.UniformToFill } quickBrush &&
                    ReferenceEquals(quickBrush.ImageSource, ((ImageBrush)reopened.Wallpaper.Texture.Background).ImageSource),
                    "The quick and main panels must share one decoded wallpaper and aspect-preserving cover.");
                SavePng(quick.Root, (int)quick.Width, (int)quick.Height, Path.Combine(output, "wpf-quick-wallpaper.png"));
                byte cornerAlpha = Sample(quick.Wallpaper, 0, 0).A, interiorAlpha = Sample(quick.Wallpaper, 15, 5).A;
                Require(quick.Wallpaper.Clip is RectangleGeometry { RadiusX: 7 } clip &&
                    Math.Abs(clip.Rect.Width - (quick.Width - 2)) < 1.5 &&
                    Math.Abs(clip.Rect.Height - (quick.Height - 2)) < 1.5 &&
                    quick.Wallpaper.Overlay.RenderSize == quick.Wallpaper.RenderSize &&
                    cornerAlpha < 16 && interiorAlpha >= 245,
                    $"The quick panel wallpaper must cover its inner bounds and respect the rounded frame. " +
                    $"Clip: {quick.Wallpaper.Clip?.Bounds}; wallpaper: {quick.Wallpaper.RenderSize}; " +
                    $"overlay: {quick.Wallpaper.Overlay.RenderSize}; window: {quick.RequestedSize}; alpha: {cornerAlpha}/{interiorAlpha}.");
                Require(quick.Wallpaper.Texture.Effect is System.Windows.Media.Effects.BlurEffect { Radius: 15 } &&
                    quick.Wallpaper.BrightnessVeil.Opacity == .5 && quick.Wallpaper.Overlay.Opacity == .35 &&
                    quick.Root.Effect is null && quick.Search.Effect is null,
                    "Quick panel tone and overlay must match the main window without blurring the controls.");
                reopened.ShowSettings();
                var reopenedPage = (SettingsPage)reopened.Pages.Page!;
                Require(reopened.Wallpaper.Texture.Effect is System.Windows.Media.Effects.BlurEffect { Radius: 15 } &&
                    reopened.Wallpaper.BrightnessVeil.Opacity == .5 && reopened.Wallpaper.Overlay.Opacity == .35 &&
                    reopenedPage.WallpaperOptions.Visibility == Visibility.Visible, "Restart did not restore the wallpaper and its controls.");
                var oldImage = ((ImageBrush)quick.Wallpaper.Texture.Background).ImageSource;
                var replacement = Path.Combine(data, "replacement.png");
                var replacementPixels = Enumerable.Repeat(new byte[] { 150, 10, 225, 255 }, 96).SelectMany(px => px).ToArray();
                SaveBitmap(BitmapSource.Create(12, 8, 96, 96, PixelFormats.Bgra32, null, replacementPixels, 48), replacement);
                Require(await reopened.ChangeWallpaper(replacement), "Replacing a wallpaper must succeed after the old one is loaded.");
                var latest = await LibrarySnapshot.ReadAsync(data);
                quick.RefreshAppearance(latest);
                for (int pass = 0; pass < 100 && ReferenceEquals(((ImageBrush)quick.Wallpaper.Texture.Background).ImageSource, oldImage); pass++)
                    await Task.Delay(25);
                var newImage = ((ImageBrush)quick.Wallpaper.Texture.Background).ImageSource;
                Require(!ReferenceEquals(oldImage, newImage) &&
                    ReferenceEquals(newImage, ((ImageBrush)reopened.Wallpaper.Texture.Background).ImageSource) &&
                    latest.Wallpaper is { } asset && asset.Path == persisted.Wallpaper!.Path &&
                    asset.Revision != persisted.Wallpaper.Revision && Directory.GetFiles(Path.GetDirectoryName(asset.Path)!).Length == 1,
                    "Both panels must refresh to the new revision at the same managed path without retaining the old copy.");
                Require(await reopened.ChangeWallpaper(null), "The user must be able to remove the application copy.");
                await WaitForWallpaper(reopened, false);
                quick.RefreshAppearance(await LibrarySnapshot.ReadAsync(data));
                Require(!File.Exists(persisted.Wallpaper!.Path) && File.Exists(Path.Combine(data, "moved.png")) && File.Exists(replacement) &&
                    reopenedPage.WallpaperOptions.Visibility == Visibility.Collapsed &&
                    quick.Wallpaper.Visibility == Visibility.Collapsed && !quick.Wallpaper.HasImage,
                    "Removing the wallpaper must preserve the source, hide its controls and restore both panels.");
            }
            finally { quick.Close(); reopened.Close(); }
        }
        finally { window.Close(); }

        static async Task WaitForWallpaper(MainWindow window, bool present)
        {
            for (int pass = 0; pass < 100; pass++)
            {
                Layout(window);
                if ((window.Wallpaper.Visibility == Visibility.Visible) == present) return;
                await Task.Delay(25);
            }
            throw new InvalidOperationException("Wallpaper layer did not finish loading.");
        }

        static Color Sample(Visual wallpaper, int x, int y)
        {
            var bytes = new byte[4];
            RenderBitmap(wallpaper, 860, 640).CopyPixels(new Int32Rect(x, y, 1, 1), bytes, 4, 0);
            return Color.FromArgb(bytes[3], bytes[2], bytes[1], bytes[0]);
        }
    }

    private static async Task CheckImportSources(LibrarySnapshot fixture, string repo, string output)
    {
        Require(ClipboardImageSource.FindLink("""
            <!-- <img src="https://bad.example/comment"> -->
            <IMG DATA-SRC="https://bad.example/lazy" ALT='attr src="https://bad.example/alt"'
                 SRC="https://test.example/image?a=1&amp;b=2">
            """, "https://bad.example/text")?.AbsoluteUri == "https://test.example/image?a=1&b=2",
            "Clipboard HTML must use the real img src and decode its query, before falling back to text.");
        Require(ClipboardImageSource.FindLink("<img src='file:///C:/not-a-url.png'>", "  https://test.example/image  ")?.Host ==
            "test.example" && ClipboardImageSource.FindLink(null, "https://u:p@test.example/x") is null &&
            ClipboardImageSource.FindLink(null, "https://test.example/x\nsecond line") is null,
            "Only one complete HTTP(S) image URL without embedded credentials may be used.");

        var data = Path.Combine(output, $"intake-{Guid.NewGuid():N}");
        var commands = new LibraryCommands(repo, data);
        var setup = await commands.Run(new("create_category", Value: "网页图片"), reload: true);
        var window = new MainWindow(setup.Snapshot!, offscreen: true, commands: commands);
        try
        {
            var png = fixture.Images.First(item => !item.Animated).Path;
            var gif = fixture.Images.First(item => item.Animated).Path;
            var files = new DataObject();
            files.SetData(DataFormats.FileDrop, new[] { png });
            files.SetText("https://must-not-download.invalid/image.png");
            await window.ImportClipboardData(files, () => throw new InvalidOperationException("File drop must take priority."));
            Require(window.Gallery.Items.Count == 1 && window.GalleryNotice.Heading.Text == "导入完成" && File.Exists(png),
                "File drops on the clipboard must still import without consuming the source.");
            var category = window.Categories.Children.OfType<Button>().Single(item => Equals(item.ToolTip, "网页图片"));
            category.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));

            using var server = new LoopbackListener();
            server.Start();
            var port = ((IPEndPoint)server.LocalEndpoint).Port;
            var link = new DataObject();
            link.SetData(DataFormats.Html, $"<img src='http://127.0.0.1:{port}/animated'>");
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(8));
            var served = ServeImage();
            await window.ImportClipboardData(link, () => throw new InvalidOperationException("Image URLs must beat bitmap fallback."));
            await served;
            Require(window.Gallery.Items is [{ Animated: true, Categories: ["网页图片"] }] &&
                window.ActivityProgress.Visibility == Visibility.Collapsed && window.GalleryNotice.Heading.Text == "导入完成",
                "A pasted link must import to the category captured when it was pasted, preserving animation.");

            var empty = new DataObject(DataFormats.UnicodeText, "not a link");
            var bitmap = BitmapSource.Create(12, 12, 96, 96, PixelFormats.Bgra32, null,
                Enumerable.Repeat(new byte[] { 0, 255, 127, 255 }, 144).SelectMany(value => value).ToArray(), 48);
            await window.ImportClipboardData(empty, () => bitmap);
            Require(window.Gallery.Items.Count == 2 && window.Gallery.Items.Any(item => !item.Animated),
                "An ordinary clipboard bitmap must still be a fallback.");

            var inbox = Path.Combine(data, "inbox");
            var filename = Path.Combine(inbox, "verified.png");
            File.Copy(png, filename);
            File.WriteAllBytes(Path.Combine(inbox, "still-downloading.png"), []);
            File.WriteAllText(Path.Combine(inbox, "ignore.txt"), "ignored");
            var observed = InboxMonitor.ReadCandidates(inbox);
            Require(observed is [{ Name: "verified.png" }], "Inbox must ignore empty or unsupported files.");
            var tracker = new InboxReadiness();
            Require(tracker.Observe(observed, TimeSpan.Zero).Length == 0 &&
                tracker.Observe([observed[0] with { Modified = observed[0].Modified - 1 }], TimeSpan.FromSeconds(2)).Length == 0 &&
                tracker.Observe(observed, TimeSpan.FromSeconds(4)).Length == 0 &&
                tracker.Observe(observed, TimeSpan.FromSeconds(6)).SequenceEqual(observed),
                "Only a file unchanged for two polls may become a candidate.");
            using (File.Open(filename, FileMode.Open, FileAccess.ReadWrite, FileShare.ReadWrite))
                Require(InboxMonitor.KeepReadable(inbox, observed).Length == 0, "Refuse preallocated files still open for writing.");
            Require(InboxMonitor.KeepReadable(inbox, observed).SequenceEqual(observed), "A settled file should be admitted.");
            // The duplicate is removed from the inbox, never from the original folder.
            var pending = window.ImportInbox(observed);
            Require(window.Root.IsEnabled && !window.LibraryBusy && window.HasPendingWrite,
                "The inbox must run as a background write, leaving the gallery interactive and protecting exit.");
            Require(await pending && !File.Exists(filename) && File.Exists(png) && !window.HasPendingWrite,
                "The accepted inbox file should be consumed and the background lease cleared.");
            Require(window.Gallery.Items.Count == 2, "Inbox intake must not switch the current category.");

            // Start the actual timer while the main window is hidden. Nothing touches the shell.
            var nextSource = fixture.Images.First(item => !item.Animated && item.Path != png).Path;
            var automatic = Path.Combine(inbox, "automatic.png");
            File.Copy(nextSource, automatic);
            var polled = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            using (var monitor = new InboxMonitor(data, window.Dispatcher, async ready =>
            {
                var admitted = await window.ImportInbox(ready);
                polled.TrySetResult(admitted);
                return admitted;
            }))
            {
                monitor.Start();
                Require(File.Exists(automatic), "The inbox must not consume a file before the stability check.");
                Require(await polled.Task.WaitAsync(TimeSpan.FromSeconds(8)) && !File.Exists(automatic) &&
                    window.GalleryNotice.Heading.Text == "收件箱自动导入完成" && window.Gallery.Items.Count == 2,
                    "The running monitor must import from the hidden window without changing the current category.");
            }
            var persisted = await LibrarySnapshot.ReadAsync(data);
            Require(persisted.Images.Count(item => item.Categories.Length == 0) == 2, "Automatic inbox intake should be unclassified.");

            async Task ServeImage()
            {
                using var client = await server.AcceptTcpClientAsync(timeout.Token);
                using var stream = client.GetStream();
                using var request = new StreamReader(stream, Encoding.ASCII, false, 1024, leaveOpen: true);
                var first = await request.ReadLineAsync(timeout.Token);
                Require(first?.StartsWith("GET /animated ") == true, "The link import made the wrong HTTP request.");
                while (await request.ReadLineAsync(timeout.Token) is { Length: > 0 }) { }
                var bytes = File.ReadAllBytes(gif);
                await stream.WriteAsync(Encoding.ASCII.GetBytes($"HTTP/1.1 200 OK\r\nContent-Type: image/gif\r\nContent-Length: {bytes.Length}\r\nConnection: close\r\n\r\n"), timeout.Token);
                await stream.WriteAsync(bytes, timeout.Token);
            }
        }
        finally { window.Close(); }
    }

    private static async Task CheckImportQueue(LibrarySnapshot fixture, string repo, string output)
    {
        var html = new DataObject();
        html.SetData(DataFormats.Html, "<a href='https://example.test/page'><img src='https://example.test/animation.gif'></a>");
        html.SetData("UniformResourceLocatorW", "https://example.test/page");
        Require(ClipboardImageSource.MayContainDropLink(html) &&
            ClipboardImageSource.FindDropLinks(html) is [{ AbsolutePath: "/animation.gif" }],
            "A dragged browser image must prefer img src to the wrapping page address.");
        var list = new DataObject("text/uri-list", "# browser\r\nhttps://example.test/a.png\r\nfile:///C:/outside\r\nhttps://example.test/b.gif\r\nhttps://example.test/a.png");
        Require(ClipboardImageSource.FindDropLinks(list).Select(link => link.AbsolutePath).SequenceEqual(["/a.png", "/b.gif"]),
            "Browser URI lists must preserve input order, ignore unsupported schemes and skip repeated addresses.");
        var native = new DataObject("UniformResourceLocatorW", new MemoryStream(Encoding.Unicode.GetBytes("https://example.test/drag.webp\0")));
        Require(ClipboardImageSource.FindDropLinks(native) is [{ AbsolutePath: "/drag.webp" }],
            "The native Windows URL transfer format should be accepted.");

        var data = Path.Combine(output, $"queue-{Guid.NewGuid():N}");
        var commands = new LibraryCommands(repo, data);
        var setup = await commands.Run(new("create_category", Value: "第一批"), reload: true);
        var window = new MainWindow(setup.Snapshot!, offscreen: true, commands: commands);
        try
        {
            using var server = new LoopbackListener();
            server.Start();
            using var timer = new CancellationTokenSource(TimeSpan.FromSeconds(12));
            var endpoint = (IPEndPoint)server.LocalEndpoint;
            var web = new DataObject("UniformResourceLocatorW", $"http://127.0.0.1:{endpoint.Port}/cancel.gif");
            var download = window.ImportDrop([], ClipboardImageSource.FindDropLinks(web), "第一批");
            using var client = await server.AcceptTcpClientAsync(timer.Token);
            using var stream = client.GetStream();
            using var reader = new StreamReader(stream, Encoding.ASCII, false, 1024, leaveOpen: true);
            var request = await reader.ReadLineAsync(timer.Token);
            Require(request?.StartsWith("GET /cancel.gif ") == true, "Dropped image must reach the shared downloader.");
            while (await reader.ReadLineAsync(timer.Token) is { Length: > 0 }) { }

            var source = fixture.Images.First(item => !item.Animated).Path;
            var second = window.ImportFiles([source], "第二批");
            var third = new DataObject(DataFormats.FileDrop, new[] { source });
            var clipboardQueued = window.ImportClipboardData(third, () => throw new InvalidOperationException("File drop wins."));
            Require(window.Root.IsEnabled && !second.IsCompleted && !clipboardQueued.IsCompleted &&
                window.ActivityProgress.Waiting.Text.Contains("2") && window.ActivityProgress.CancelButton.IsEnabled,
                "Dragging and pasting after an active download must queue in order without disabling the gallery.");
            window.ActivityProgress.CancelButton.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Require(!window.ActivityProgress.CancelButton.IsEnabled && window.ActivityProgress.Detail.Text == "正在取消…" && window.HasPendingWrite,
                "Cancelling must keep waiting for the worker and protect pending writes.");

            var gif = File.ReadAllBytes(fixture.Images.First(item => item.Animated).Path);
            try
            {
                await stream.WriteAsync(Encoding.ASCII.GetBytes($"HTTP/1.1 200 OK\r\nContent-Type: image/gif\r\nContent-Length: {gif.Length}\r\nConnection: close\r\n\r\n"), timer.Token);
                await stream.WriteAsync(gif, timer.Token);
            }
            catch (IOException) { /* Native cancellation may close the connection before the reply. */ }
            await Task.WhenAll(download, second, clipboardQueued).WaitAsync(TimeSpan.FromSeconds(12));
            var snapshot = await LibrarySnapshot.ReadAsync(data);
            Require(snapshot.Images is [{ Animated: false, Categories: ["第二批"] }] && !window.HasPendingWrite &&
                window.ActivityProgress.Visibility == Visibility.Collapsed && window.ActivityProgress.Waiting.Text == "" && File.Exists(source),
                "The cancelled transfer must not import; the queued tasks still finish in order in their own categories.");
        }
        finally { window.Close(); }
    }

    private static async Task CheckLibraryCommands(LibrarySnapshot fixture, string repo, string output)
    {
        var data = Path.Combine(output, $"command-fixture-{Guid.NewGuid():N}");
        var commands = new LibraryCommands(repo, data);
        await commands.SaveSettings(new Dictionary<string, object> { ["appearance_mode"] = fixture.Dark ? "dark" : "light" });
        var library = await LibrarySnapshot.ReadAsync(data);
        var window = new MainWindow(library, offscreen: true, commands: commands);
        try
        {
            Layout(window);
            Require(window.Toolbar.Children.OfType<Button>().Where(button => Equals(button.ToolTip, "设置") ||
                Equals(button.ToolTip, "导入导出") || Equals(button.ToolTip, "感知哈希去重")).Count(button => button.IsEnabled) == 3,
                "Library settings, exchange and similarity should have enabled entry points.");
            var original = fixture.Images[0].Path;
            var single = window.ImportFiles([original], "中文分类");
            Require(window.ActivityProgress.Visibility == Visibility.Collapsed && window.BusyProgress.Visibility == Visibility.Collapsed,
                "A single file should not open a progress panel.");
            await single;
            Require(window.Gallery.Items is [{ } added] && added.Categories.SequenceEqual(new[] { "中文分类" }) &&
                window.GalleryNotice.Visibility == Visibility.Visible && window.GalleryNotice.Heading.Text == "导入完成" &&
                window.GalleryNotice.Detail.Text == "成功添加了 1 个表情包" && window.GalleryNotice.Symbol.Text == "✓" &&
                window.GalleryNotice.Parent is Grid { ClipToBounds: true, IsHitTestVisible: false } &&
                window.GalleryNotice.NoticeCard.RenderTransform is TranslateTransform &&
                !window.GalleryNotice.Focusable && !window.GalleryNotice.IsHitTestVisible && !window.LibraryBusy && window.Root.IsEnabled,
                "A single file should show a sliding success notice clipped to the window and pass through input.");
            await WaitForCards(window, 1);
            SavePng(window.Root, 860, 640, Path.Combine(output, "wpf-import-success.png"));
            await Task.Delay(3300);
            Require(window.GalleryNotice.Visibility == Visibility.Collapsed, "The success notice must dismiss itself.");
            var savedImage = window.Gallery.Items.Single();
            var path = savedImage.Path;
            await window.ImportFiles([original], "中文分类");
            Require(window.Gallery.Items.Count == 1 && window.GalleryNotice.Visibility == Visibility.Visible &&
                window.GalleryNotice.Detail.Text.Contains("已跳过保存"), "Duplicate image feedback should stay inside the gallery.");
            var bad = Path.Combine(data, "invalid-test-image.png");
            File.WriteAllText(bad, "not an image");
            await window.ImportFiles([bad], "中文分类");
            Require(window.Gallery.Items.Count == 1 && window.GalleryNotice.Visibility == Visibility.Visible &&
                window.GalleryNotice.Heading.Text == "部分文件处理失败" && File.Exists(bad) &&
                window.GalleryNotice.NoticeCard.RenderTransform == Transform.Identity,
                "A failed single-file import must give an inline warning and leave the source intact.");
            var batch = window.ImportFiles([original, original], "中文分类");
            Require(window.ActivityProgress.Visibility == Visibility.Visible && window.ActivityProgress.Bar.IsIndeterminate &&
                window.BusyProgress.Visibility == Visibility.Collapsed, "A batch should open its progress panel before loading.");
            await batch;
            Require(window.ActivityProgress.Visibility == Visibility.Collapsed, "Finishing a batch must dismiss its progress.");
            Require(window.Gallery.Items.Count == 1 && window.GalleryNotice.Visibility == Visibility.Visible &&
                window.GalleryNotice.Detail.Text.Contains("跳过 2 张"), "A file batch should show a non-modal summary.");
            await WaitForCards(window, 1);
            SavePng(window.Root, 860, 640, Path.Combine(output, "wpf-import-summary.png"));
            var second = fixture.Images.First(item => item.Path != original && Path.GetExtension(item.Path) == ".png");
            await window.ImportFiles([bad, second.Path], "中文分类");
            Require(window.Gallery.Items.Count == 2 && window.GalleryNotice.Visibility == Visibility.Visible &&
                window.GalleryNotice.Heading.Text == "部分文件处理失败" && window.GalleryNotice.Detail.Text.Contains("成功导入 1 张") &&
                window.GalleryNotice.Detail.Text.Contains("失败 1 张"), "Mixed imports should show both success and failure in one notice.");
            var secondCopy = window.Gallery.Items.Single(item => item.Path != path);
            var cleanup = await commands.Run(new("delete", [secondCopy.Path]), reload: true);
            window.AcceptSnapshot(cleanup.Snapshot!, null, updateSettings: false);
            window.GalleryNotice.Dismiss();
            var renamed = await commands.Run(new("rename_category", Category: "中文分类", Value: "收藏"), reload: true);
            window.AcceptSnapshot(renamed.Snapshot!, "收藏", updateSettings: false);
            Require(window.Gallery.Items.Single().Categories.SequenceEqual(new[] { "收藏" }),
                "A mutation should refresh the current category from the persisted snapshot.");
            await window.RecordRecent(savedImage, 30);
            await CheckLayoutPersistence(window, repo, data);
            window.ShowSettings();
            Layout(window);
            var page = (SettingsPage)window.Pages.Page!;
            var settingsSaved = new TaskCompletionSource<LibrarySnapshot>();
            window.LibraryChanged += snapshot => settingsSaved.TrySetResult(snapshot);
            var controls = page.Groups.Children.OfType<StackPanel>().SelectMany(group => group.Children.OfType<SettingCard>())
                .Select(card => ((Grid)card.Child).Children.Cast<UIElement>().Last()).ToArray();
            var top = controls.OfType<CheckBox>().First();
            var entry = controls.OfType<CheckBox>().Single(control => System.Windows.Automation.AutomationProperties.GetName(control) == "显示设置入口");
            var appearance = controls.OfType<FluentChoice>().Single(control => System.Windows.Automation.AutomationProperties.GetName(control) == "外观模式");
            top.IsChecked = false;
            entry.IsChecked = false;
            appearance.ContextMenu.Items.OfType<MenuItem>().Single(item => Equals(item.Header, "深色模式"))
                .RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            window.Pages.Back.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            var saved = await settingsSaved.Task.WaitAsync(TimeSpan.FromSeconds(5));
            Require(!window.OnSettingsPage && !window.Topmost && saved.Dark && saved.Recent is [var recent] && recent == savedImage.Name &&
                window.Toolbar.Children.OfType<Button>().Single(button => Equals(button.ToolTip, "设置")).Visibility == Visibility.Collapsed,
                "Settings must save on return, update the window immediately and keep recent history.");
            var deleted = await commands.Run(new("delete", [path]), reload: true);
            window.AcceptSnapshot(deleted.Snapshot!, null, updateSettings: false);
            Require(deleted.Result.Count == 1 && window.Gallery.Items.Count == 0 && File.Exists(fixture.Images[0].Path),
                "Deleting the imported copy must leave the fixture source intact.");
        }
        finally { window.Close(); }
    }

    private static void CheckPreferenceState(LibrarySnapshot library)
    {
        var state = new PreferenceState(library);
        state.StageLayout("thumbnail_size", 140);
        state.Preview(WallpaperSurface.BlurKey, 12);
        Require(state.TakeWrite() is null && state.Saved == library &&
            state.Effective.Number("thumbnail_size", 0, 0, 300) == 140 &&
            state.Effective.Number(WallpaperSurface.BlurKey, 0, 0, 100) == 12,
            "Uncommitted layout and wallpaper drafts must affect only the local view.");
        state.FlushLayout();
        var write = state.TakeWrite()!;
        Require(write.Count == 1 && Equals(write["thumbnail_size"], 140),
            "Flushing layout must not commit separate wallpaper drafts.");
        state.StageLayout("thumbnail_size", 160);
        var committed = library with { Settings = new(library.Settings) };
        committed.Settings["thumbnail_size"] = JsonSerializer.SerializeToElement(140);
        state.Acknowledge(write);
        state.Accept(committed);
        Require(state.Saved == committed && state.Effective.Number("thumbnail_size", 0, 0, 300) == 160,
            "An older save result must not roll back a newer wheel step.");
        state.FlushLayout();
        Require(Equals(state.TakeWrite()!["thumbnail_size"], 160), "A newer layout step must still be written.");
        state.Queue(new System.Collections.Generic.Dictionary<string, object> { [WallpaperSurface.BlurKey] = 12 });
        state.Revert();
        Require(state.Effective == committed && state.TakeWrite() is null &&
            state.Effective.Number("thumbnail_size", 0, 0, 300) == 140,
            "A failed write must discard drafts and queued edits and restore the last persisted snapshot.");
    }

    private static async Task CheckPageLifecycle()
    {
        var host = new FluentPageHost();
        var root = new Grid();
        var middle = new Grid();
        var leaf = new Grid();
        var rootWork = new OperationScope();
        var leafWork = new OperationScope();
        int suspended = 0, resumed = 0, left = 0, closed = 0, received = 0, cancelCallbacks = 0;
        bool backConsumed = false;
        host.RootClosed += () => closed++;
        var inherited = new Grid { Children = { host } };
        inherited.SetValue(FluentMotion.DisabledProperty, true);
        Require(!FluentMotion.Enabled(host), "Reduced motion must propagate into detached and nested pages.");
        var revealer = new Border();
        var reveal = new MotionReveal(host, revealer);
        bool visible = false;
        reveal.Show(() => visible = true);
        Require(visible && revealer.Opacity == 1, "Reduced motion must show the detail immediately.");
        reveal.Hide(() => visible = false);
        Require(!visible && revealer.Opacity == 0, "Reduced motion must close the detail immediately.");
        var notice = new InlineNotice { MaxWidth = 420, Margin = new Thickness(16, 56, 16, 0) };
        inherited.Children.Add(notice);
        notice.Show("导入完成", "成功添加了 1 个表情包", persistent: true, slideFromRight: true);
        Require(notice.Visibility == Visibility.Visible && notice.NoticeCard.Opacity == 1 &&
            notice.NoticeCard.RenderTransform is TranslateTransform { X: 0, Y: 0 },
            "With motion disabled the right-edge notice must appear immediately at its final position.");
        notice.Dismiss();
        Require(notice.Visibility == Visibility.Collapsed && notice.NoticeCard.RenderTransform is TranslateTransform offset &&
            offset.X >= notice.NoticeCard.DesiredSize.Width + notice.Margin.Right && offset.Y == 0,
            "The offscreen position must clear the right edge without vertical movement.");
        Require(host.Push(new("root", root, Suspend: () => suspended++, Resume: () => resumed++,
            Leave: () => left++, Work: rootWork)), "Could not open the root page.");
        Require(host.Push(new("middle", middle, Leave: () => left++,
            HandleBack: () => backConsumed)) && !host.Push(new("duplicate", root)),
            "A live view must not appear twice in the page stack.");
        backConsumed = true;
        host.RequestBack();
        Require(host.Page == middle && left == 0, "A route must be able to consume Back for its own detail view.");
        backConsumed = false;
        host.SetCaption("middle detail", "back to middle");
        Require(host.Push(new("leaf", leaf, Leave: () => left++, Work: leafWork)), "Could not open the leaf page.");

        using (var backgroundWrite = rootWork.Begin(LeavePolicy.FinishBeforeLeaving))
        {
            Require(!host.TryClose() && host.Page == leaf && left == 0 && closed == 0 &&
                !rootWork.RequestCancel() && !backgroundWrite.Token.CanBeCanceled,
                "Full-stack exit must preflight all routes and never partially pop during a write.");
        }
        using (var batch = leafWork.Begin(LeavePolicy.CancelAndWait, () => cancelCallbacks++))
        {
            host.RequestBack();
            host.RequestBack();
            Require(batch.Token.IsCancellationRequested && batch.AcceptsResult && cancelCallbacks == 1 &&
                host.Page == leaf && !host.TryClose() && left == 0,
                "Cooperative cancellation must be issued once and keep the route live until completion.");
        }
        using (var scan = leafWork.Begin(LeavePolicy.CancelOnLeave))
        {
            var progress = scan.Progress(_ => received++);
            progress.Report(new("scan", 1, 3, null));
            await host.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
            Require(received == 1, "A live scan should receive its progress.");
            progress.Report(new("scan", 2, 3, null));
            host.RequestBack();
            await host.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
            Require(scan.Token.IsCancellationRequested && !scan.AcceptsResult && received == 1 &&
                host.Page == middle && host.Heading.Text == "middle detail" && left == 1,
                "Leaving a read-only page must cancel it, ignore queued updates and restore its parent instance.");
        }
        host.RequestBack();
        Require(host.Page == root && suspended == 1 && resumed == 2 && left == 2 && closed == 0,
            "Returning to the root must resume its existing view exactly once.");
        Require(host.TryClose() && host.Page is null && left == 3 && closed == 1,
            "Closing the final route must notify the gallery exactly once.");
    }

    private static void CheckExchangePage(MainWindow window, string output)
    {
        var button = window.Toolbar.Children.OfType<Button>().Single(control => Equals(control.ToolTip, "导入导出"));
        button.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Layout(window);
        Require(window.Pages.Page is ExchangePage && window.Pages.Heading.Text == "导入导出" &&
            window.GalleryPage.Visibility == Visibility.Collapsed, "Exchange must open inside the same shared page host as Settings.");
        Require(window.Pages.PageTools.Content is Button { ToolTip: "前往资源站" }, "The exchange page lost its resource site action.");
        var page = (ExchangePage)window.Pages.Page!;
        var cards = new[] { page.Packages, page.LocalFiles, page.ExternalSources }
            .SelectMany(group => group.Children.OfType<SettingCard>()).ToArray();
        var actions = cards.Select(card => ((Grid)card.Child).Children.OfType<Button>().Single()).ToArray();
        Require(actions.All(action => action.IsEnabled) && actions.Select(System.Windows.Automation.AutomationProperties.GetName)
            .SequenceEqual(new[] { "导入资源包", "导出全部资源包", "导出指定分类", "资源包切割分卷", "导入图片", "导入文件夹",
                "扫描 QQ 聊天表情", "下载 TG 贴纸" }),
            "The exchange page lost a package, local file, or external source entry point.");
        SavePng(window.Root, 860, 640, Path.Combine(output, "wpf-exchange-page.png"));
        Layout(window, width: 400);
        Layout(window, width: 400);
        Require(actions.All(action => Grid.GetRow(action) == 1), "Narrow exchange cards should wrap the action below the title.");
        foreach (var (action, title) in new[] { (actions[^2], "扫描 QQ 聊天表情"), (actions[^1], "下载 TG 贴纸") })
        {
            action.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Layout(window);
            Require(window.Pages.Page is ExternalStickerPage && window.Pages.Heading.Text == title,
                "External source must open as a subpage using the shared page host.");
            SavePng(window.Root, 860, 640, Path.Combine(output, title.Contains("QQ") ? "wpf-qq-page.png" : "wpf-tg-page.png"));
            if (title == "下载 TG 贴纸")
            {
                var source = (ExternalStickerPage)window.Pages.Page!;
                source.Options.Children.OfType<Button>().Single(control => Equals(control.Content, "批量打包"))
                    .RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                Layout(window);
                Require(window.Pages.Page is TelegramBatchPage && window.Pages.Heading.Text == "批量打包 Telegram 贴纸",
                    "The Telegram batch entry must open in the shared page host.");
                window.Pages.Back.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                Require(ReferenceEquals(window.Pages.Page, source), "Batch page Back should restore the same Telegram source page.");
            }
            window.Pages.Back.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Require(ReferenceEquals(window.Pages.Page, page) && window.Pages.Heading.Text == "导入导出",
                "External source Back should return to the same exchange page.");
        }
        window.Pages.Back.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Layout(window);
        Require(window.OnGalleryPage && window.GalleryPage.Visibility == Visibility.Visible,
            "Returning from exchange should restore the mounted gallery.");
    }

    private static async Task CheckExternalStickers(LibrarySnapshot fixture, string repo, string output)
    {
        var sandbox = Path.Combine(output, $"external-{Guid.NewGuid():N}");
        var qqFolder = Path.Combine(sandbox, "qq");
        var ori = Path.Combine(qqFolder, "12345", "nt_qq", "nt_data", "Emoji", "personal_emoji", "Ori");
        Directory.CreateDirectory(ori);
        var stillPath = fixture.Images.First(image => !image.Animated).Path;
        var gifPath = fixture.Images.First(image => image.Animated).Path;
        File.Copy(gifPath, Path.Combine(ori, "snap.dat"));
        var data = Path.Combine(sandbox, "data");
        var exported = Path.Combine(sandbox, "导出");
        Directory.CreateDirectory(exported);
        LibrarySnapshot? imported = null;
        var qq = new ExternalStickerPage(false, new FluentMenuPalette(fixture), new LibraryCommands(repo, data),
            (result, _) => imported = result, _ => exported, [], null, askCategory: () => "QQ 新分类");
        ApplyPageTheme(qq);
        await qq.Discover(qqFolder);
        await qq.Scan();
        await Render(qq, () => qq.Tiles.Children.OfType<StickerResultTile>().FirstOrDefault()?.Picture.Source is not null);
        Require(qq.Tiles.Children.Count == 1 && !Directory.Exists(data), "QQ analysis must be read-only.");
        var exportSelected = qq.Actions.Children.OfType<Button>().Single(button => Equals(button.Content, "导出所选"));
        Require(!exportSelected.IsEnabled, "QQ export selected must require a selection.");
        ((StickerResultTile)qq.Tiles.Children[0]).Open.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        await Render(qq, () => qq.DetailImage.Source is BitmapSource { IsFrozen: true } && !qq.PreviewInfo.Text.Contains("加载"));
        Require(qq.PreviewInfo.Text.Contains("snap.dat"), "QQ detail should show the animated source and its filename.");
        exportSelected.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        await Render(qq, () => !qq.IsWriting && qq.Status.Text.Contains("导出 1 张"));
        Require(Directory.GetFiles(exported) is [var copy] && Path.GetFileName(copy) == "snap.dat.gif" &&
            File.ReadAllBytes(copy).SequenceEqual(File.ReadAllBytes(gifPath)) && imported is null && !Directory.Exists(data) &&
            qq.Summary.Text.Contains("已选 1 张"), "QQ export must keep its source, selection and library unchanged.");
        qq.Actions.Children.OfType<Button>().Single(button => Equals(button.Content, "新建分类"))
            .RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        await Render(qq, () => imported?.Categories.Any(item => item.Name == "QQ 新分类") == true && !qq.IsWriting);
        var qqDestination = qq.Actions.Children.OfType<StackPanel>().SelectMany(panel => panel.Children.OfType<FluentChoice>()).Single();
        Require(qqDestination.Value == "QQ 新分类" && qq.Summary.Text.Contains("已选 1 张"),
            "Creating a category from QQ must select the destination and keep the scanned sticker selection.");
        await qq.Save(all: false, import: true);
        Require(imported?.Images is [{ Categories: ["QQ 新分类"] } image] && imported.Categories.Single().IconPath == image.Path &&
            File.Exists(Path.Combine(ori, "snap.dat")) && qq.Status.Text.Contains("导入 1 张"),
            "Importing selected QQ stickers must assign the destination and its first icon without touching the original.");
        var destinationRow = qqDestination.ContextMenu.Items.OfType<MenuItem>().Single(row => Equals(row.Header, "QQ 新分类"));
        Require(destinationRow.IsChecked && IconCore(destinationRow.Icon) is Image { Source: not null } &&
            qqDestination.Content is DockPanel caption && IconCore(caption.Children[0]) is Image { Source: not null },
            "QQ import must refresh the selected folder and its menu row when a first picture gives it an icon.");
        var host = new FluentPageHost();
        Require(host.Push(new("QQ", qq, Leave: qq.Leave, Work: qq.PageWork)) && host.TryClose(),
            "Completed QQ import should release the page.");

        var ok = new LibraryResult(0, 0, 0, null, null, null, null);
        var scanFinished = new TaskCompletionSource<(LibraryResult, LibrarySnapshot?)>();
        var exportFinished = new TaskCompletionSource<(LibraryResult, LibrarySnapshot?)>();
        var recycleFinished = new TaskCompletionSource<(LibraryResult, LibrarySnapshot?)>();
        LibraryCommand? recycle = null;
        IProgress<LibraryProgress>? scannerProgress = null;
        IProgress<LibraryProgress>? exportProgress = null;
        CancellationToken exportToken = default;
        var encoded = Convert.ToBase64String(File.ReadAllBytes(stillPath));
        var cleanupPage = new ExternalStickerPage(false, new FluentMenuPalette(fixture), (command, reload, cancellation, progress) =>
        {
            switch (command.Action)
            {
                case "qq_discover":
                    return Task.FromResult<(LibraryResult, LibrarySnapshot?)>((ok with {
                        Destination = qqFolder, Sources = [new("12345", [new("personal_emoji", "个人表情")])] }, null));
                case "qq_scan":
                    scannerProgress = progress;
                    return scanFinished.Task;
                case "qq_previews":
                case "qq_detail":
                    return Task.FromResult<(LibraryResult, LibrarySnapshot?)>((ok with {
                        Previews = command.Items!.Select(item => new ExternalPreview(item.Id, encoded)).ToArray() }, null));
                case "qq_recycle":
                    Require(!reload && !cancellation.IsCancellationRequested, "QQ cleanup must not write or reload the gallery.");
                    recycle = command;
                    return recycleFinished.Task;
                case "qq_export":
                    Require(!reload && cancellation.CanBeCanceled && command.Items?.Length == 3 && command.Destination == exported,
                        "QQ export all must send all scanned items, the destination and a cooperative token, without reloading.");
                    exportProgress = progress;
                    exportToken = cancellation;
                    return exportFinished.Task;
                default: throw new InvalidOperationException(command.Action);
            }
        }, (_, _) => throw new InvalidOperationException("QQ cache operations changed the library"), _ => exported, [], null);
        ApplyPageTheme(cleanupPage);
        Require(host.Push(new("QQ", cleanupPage, Leave: cleanupPage.Leave, Work: cleanupPage.PageWork)),
            "QQ cleanup test must attach to the shared host.");
        await cleanupPage.Discover(qqFolder);
        var scanning = cleanupPage.Scan();
        Require(cleanupPage.Work.Visibility == Visibility.Visible && cleanupPage.Work.Bar.IsIndeterminate && !cleanupPage.IsWriting,
            "QQ scan must immediately show an indeterminate progress bar while the candidate total is unknown.");
        scannerProgress!.Report(new("qq_walk", 3, 0, "已遍历 3 个文件夹，发现 18 个候选文件"));
        await Render(cleanupPage, () => cleanupPage.Work.Detail.Text.Contains("18 个候选文件"));
        scannerProgress.Report(new("qq_check", 5, 18, "正在检查文件：5 / 18"));
        await Render(cleanupPage, () => !cleanupPage.Work.Bar.IsIndeterminate && cleanupPage.Work.Bar.Value == 5);
        Require(cleanupPage.Work.Bar.Maximum == 18, "QQ scan must display the real candidate total.");
        scanFinished.SetResult((ok with { Items = [
            new("one", 0, "所选.png", FileIdentity: "original-one"),
            new("two", 1, "占用.png", FileIdentity: "original-two"),
            new("three", 2, "未选.png", FileIdentity: "original-three")
        ] }, null));
        await scanning;
        await Render(cleanupPage, () => cleanupPage.Tiles.Children.Count == 3);
        cleanupPage.Actions.Children.OfType<Button>().Single(button => Equals(button.Content, "导出全部"))
            .RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        exportProgress!.Report(new("qq_export", 1, 3, null));
        await Render(cleanupPage, () => !cleanupPage.Work.Bar.IsIndeterminate && cleanupPage.Work.Bar.Value == 1);
        Require(cleanupPage.Work.Bar.Maximum == 3 && cleanupPage.Work.CancelButton.IsEnabled && !host.TryClose(),
            "QQ export should show item progress and wait for safe completion.");
        host.RequestBack();
        Require(cleanupPage.IsWriting && exportToken.IsCancellationRequested && !cleanupPage.Work.CancelButton.IsEnabled &&
            ReferenceEquals(host.Page, cleanupPage), "Leaving a running QQ export must cancel and wait.");
        exportFinished.SetResult((ok with { Count = 1, Cancelled = 1, Destination = exported }, null));
        await Render(cleanupPage, () => !cleanupPage.IsWriting && cleanupPage.Status.Text.Contains("导出已取消"));
        Require(cleanupPage.Status.Text.Contains("导出 1 张") && cleanupPage.Tiles.Children.Count == 3,
            "Cancelling export must show the completed count and retain the scan results.");
        var delete = cleanupPage.Actions.Children.OfType<Button>().Single(button => Equals(button.Content, "删除所选缓存…"));
        Require(!delete.IsEnabled && cleanupPage.Work.Visibility == Visibility.Collapsed, "No source may be cleaned without a selection.");
        foreach (var tile in cleanupPage.Tiles.Children.OfType<StickerResultTile>().Where(tile => tile.Item.Index < 2))
            tile.Toggle.IsChecked = true;
        delete.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Require(cleanupPage.CleanupConfirmation.Visibility == Visibility.Visible &&
            cleanupPage.CleanupWarning.Text.Contains("2 张") && recycle is null, "Delete must show the count and wait for confirmation.");
        Require(cleanupPage.TryGoBack() && cleanupPage.CleanupConfirmation.Visibility == Visibility.Collapsed &&
            recycle is null, "Back/Esc must cancel confirmation without changing source files.");
        delete.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        cleanupPage.ConfirmCleanup.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Require(cleanupPage.IsWriting && !host.TryClose() && ReferenceEquals(host.Page, cleanupPage) &&
            recycle is { Items: [{ Id: "one", FileIdentity: "original-one" }, { Id: "two", FileIdentity: "original-two" }] },
            "Cleanup must only send the explicitly confirmed scan records and prevent leaving while recycling.");
        recycleFinished.SetResult((ok with { Count = 1, Failed = 1, RemovedIds = ["one"] }, null));
        await Render(cleanupPage, () => !cleanupPage.IsWriting && cleanupPage.Tiles.Children.Count == 2);
        Require(cleanupPage.Tiles.Children.OfType<StickerResultTile>().All(tile => tile.Item.Id != "one") &&
            cleanupPage.Summary.Text.Contains("已选 1 张") && cleanupPage.Status.Text.Contains("未移除 1 张"),
            "Only successfully recycled cache files disappear; failed selections and untouched files must remain.");
        Require(host.TryClose(), "Completed cleanup must release its navigation guard.");

        var stickers = Enumerable.Range(0, 1200).Select(i => new ExternalSticker($"id_{i}", i, $"图 {i}",
            Width: 128, Height: (i % 3) switch { 0 => 64, 1 => 128, _ => 224 })).ToArray();
        var thumbBatches = new System.Collections.Generic.List<int>();
        var firstDetail = new TaskCompletionSource<(LibraryResult, LibrarySnapshot?)>();
        var download = new TaskCompletionSource<(LibraryResult, LibrarySnapshot?)>();
        var packExport = new TaskCompletionSource<(LibraryResult, LibrarySnapshot?)>();
        IProgress<LibraryProgress>? packageProgress = null;
        var packZip = Path.Combine(sandbox, "新包.zip");
        string? packageId = null;
        string? packageCategory = null;
        bool canOverwrite = false;
        bool? packageSuccess = null;
        string? packageSummary = null;
        int packageFolderPicks = 0, downloadFolderPicks = 0, overwritePrompts = 0, packageRequests = 0;
        string? savedFolder = null, log = null;
        LibraryCommand? sent = null;
        var tg = new ExternalStickerPage(true, new FluentMenuPalette(fixture), (command, reload, cancellation, progress) =>
        {
            switch (command.Action)
            {
                case "tg_test_connection":
                    Require(command.NetworkProxy == "socks5h://127.0.0.1:1080" && command.BotToken == "1234:synthetic",
                        "Connectivity must use the same explicit proxy and Token as downloads.");
                    return Task.FromResult<(LibraryResult, LibrarySnapshot?)>((ok, null));
                case "tg_parse":
                    Require(command.BotToken == "1234:synthetic" && !reload, "Telegram parse must use the supplied token.");
                    return Task.FromResult<(LibraryResult, LibrarySnapshot?)>((ok with {
                        SourceName = "test_pack", SourceTitle = "Test Pack", Items = stickers,
                        SourceUrl = "https://t.me/addstickers/test_pack" }, null));
                case "tg_previews":
                    Require(command.Items is { Length: > 0 and <= 6 }, "Telegram thumbnails must use bounded viewport batches.");
                    thumbBatches.AddRange(command.Items!.Select(item => item.Index));
                    return Task.FromResult<(LibraryResult, LibrarySnapshot?)>((ok with {
                        Previews = command.Items!.Select(item => new ExternalPreview(item.Id, encoded)).ToArray() }, null));
                case "tg_detail":
                    var item = command.Items!.Single();
                    return item.Index == 1 ? firstDetail.Task : Task.FromResult<(LibraryResult, LibrarySnapshot?)>((ok with {
                        Previews = [new(item.Id, encoded)] }, null));
                case "tg_download":
                    sent = command;
                    Require(!reload && !cancellation.IsCancellationRequested && progress is not null,
                        "Downloads to a folder should report progress without refreshing the library.");
                    return download.Task;
                case "tg_single_package":
                    packageRequests++;
                    sent = command;
                    packageProgress = progress;
                    Require(reload && !cancellation.IsCancellationRequested && progress is not null,
                        "Single resource package export must report progress and refresh the library.");
                    return packExport.Task;
                default: throw new InvalidOperationException(command.Action);
            }
        }, (_, category) => packageCategory = category, title =>
        {
            if (title == "选择资源包保存目录") packageFolderPicks++;
            if (title == "选择下载文件夹") downloadFolderPicks++;
            return sandbox;
        }, [], null, packagePrompts: new(title =>
        {
            Require(title == "Test Pack", "The package title should be the default ID.");
            return packageId;
        }, path =>
        {
            Require(path == packZip, "Overwrite consent must identify the exact ZIP.");
            overwritePrompts++;
            return canOverwrite;
        }, (ok, summary) =>
        {
            packageSuccess = ok;
            packageSummary = summary;
        }), folders: new("", sandbox, _ => { }, path => savedFolder = path), details: new(_ => { }, (text, _) => log = text));
        ApplyPageTheme(tg);
        Require(host.Push(new("TG", tg, Leave: tg.Leave, Work: tg.PageWork)),
            "Telegram download test must attach to the shared host.");
        Require(tg.ConnectionOptions.Visibility == Visibility.Collapsed, "Telegram connection overrides must start collapsed.");
        tg.Options.Children.OfType<Button>().Single(button => Equals(button.Content, "高级配置"))
            .RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        var inputs = tg.Options.Children.OfType<StackPanel>().Concat(tg.ConnectionOptions.Children.OfType<StackPanel>())
            .SelectMany(panel => panel.Children.OfType<Control>()).ToArray();
        var password = inputs.OfType<PasswordBox>().Single();
        Require(password.Password.Length == 0 && inputs.OfType<TextBox>().Single(input =>
            System.Windows.Automation.AutomationProperties.GetName(input).StartsWith("API 反代", StringComparison.Ordinal)).Text.Length == 0,
            "Saved defaults must never populate override fields.");
        password.Password = "1234:synthetic";
        inputs.OfType<TextBox>().First().Text = "https://t.me/addstickers/test_pack";
        inputs.OfType<TextBox>().Single(input => System.Windows.Automation.AutomationProperties.GetName(input) == "本地代理")
            .Text = "socks5h://127.0.0.1:1080";
        await tg.TestConnection();
        tg.Options.Children.OfType<Button>().Single(button => Equals(button.Content, "操作日志"))
            .RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Require(log is not null && log.Contains("连接成功") && !log.Contains(password.Password) && !log.Contains("socks5h"),
            "Operation log must report the outcome without exposing Telegram credentials or proxies.");
        tg.Options.Children.OfType<Button>().Single(button => Equals(button.Content, "浏览…"))
            .RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Require(downloadFolderPicks == 1 && savedFolder == sandbox, "Choosing a download folder must persist it.");
        await tg.ExportTelegramPackage();
        Require(packageFolderPicks == 0 && packageRequests == 0, "Export must require a parsed pack.");
        await tg.Parse();
        await Render(tg, () => tg.Tiles.Children.OfType<StickerResultTile>().Any(tile => tile.Picture.Source is not null));
        var initial = tg.Tiles.Children.OfType<StickerResultTile>().ToArray();
        Require(initial.Length is > 3 and < 60 && thumbBatches.Count < 60, "Only viewport tiles and images should be loaded.");
        Require(initial.Select(tile => tile.Height).Distinct().Count() > 1 &&
            initial.Where(tile => Canvas.GetTop(tile) > 0).Select(Canvas.GetTop).Distinct().Count() > 1,
            "Images of varied aspect ratios must not be arranged into a uniform grid.");
        initial.Single(tile => tile.Item.Index == 1).Open.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        initial.Single(tile => tile.Item.Index == 0).Open.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        firstDetail.SetResult((ok with { Previews = [new("id_1", encoded)] }, null));
        await Render(tg, () => tg.DetailImage.Source is not null && !tg.PreviewInfo.Text.Contains("加载"));
        Require(tg.PreviewInfo.Text.Contains("图 0") && tg.PreviewInfo.Text.Contains("128 × 64") &&
            tg.Summary.Text.Contains("已选 1 张"), "A stale response must not replace the active detail or selection.");
        tg.InvertSelection();
        Require(tg.Summary.Text.Contains("已选 1199 张"), "Invert must include all unselected rows, also outside the viewport.");
        tg.InvertSelection();
        SavePng(tg, 860, 590, Path.Combine(output, "wpf-telegram-waterfall.png"));
        tg.Scroller.ScrollToEnd();
        await Render(tg, () => tg.Tiles.Children.OfType<StickerResultTile>().Any(tile => tile.Item.Index > 1180 && tile.Picture.Source is not null));
        Require(tg.Tiles.Children.Count < 60 && tg.Tiles.Children.OfType<StickerResultTile>().All(tile => tile.Item.Index != 0)
            && tg.Summary.Text.Contains("已选 1 张"), "Continuous scrolling should recycle tiles without losing selection.");
        await Render(tg, () => DockPanel.GetDock(tg.Inspector) == Dock.Top && tg.Scroller.ViewportHeight > 60, 440);

        var exportButton = tg.Actions.Children.OfType<Button>().Single(button => Equals(button.Content, "导出为资源包"));
        exportButton.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Require(packageFolderPicks == 0 && packageRequests == 0, "Cancelling the ID dialog must not open a folder picker.");
        packageId = "NUL.txt";
        await tg.ExportTelegramPackage();
        Require(packageFolderPicks == 0 && packageRequests == 0, "Unsafe IDs must not reach the folder picker.");
        packageId = "新包";
        File.WriteAllText(packZip, "keep");
        await tg.ExportTelegramPackage();
        Require(overwritePrompts == 1 && packageRequests == 0 && File.ReadAllText(packZip) == "keep",
            "Declining the overwrite must not send a command or change the existing ZIP.");
        canOverwrite = true;
        var package = tg.ExportTelegramPackage();
        Require(tg.IsWriting && !host.TryClose() && overwritePrompts == 2 && packageRequests == 1 &&
            sent is { Action: "tg_single_package", PackageId: "新包", OverwritePackage: true,
                SourceUrl: "https://t.me/addstickers/test_pack", Items.Length: 1200, Destination: var destination } &&
            destination == sandbox, "Package export must submit all parsed rows and block navigation until it finishes.");
        packageProgress!.Report(new("download", 3, stickers.Length, null));
        await Render(tg, () => tg.Work.Detail.Text.Contains("正在下载") && tg.Work.Bar.Value == 3);
        packageProgress.Report(new("export", 1, 2, null));
        await Render(tg, () => tg.Work.Detail.Text.Contains("正在打包") && tg.Work.Bar.Maximum == 2);
        packExport.SetResult((ok with { Error = "入库失败", LibraryChanged = true }, fixture));
        await package;
        Require(packageSuccess == false && packageSummary!.Contains("入库失败") && packageCategory == "新包",
            "A partial import must still refresh the library, but must not report export success.");
        packExport = new();
        package = tg.ExportTelegramPackage();
        packExport.SetResult((ok with { Count = 2, Failed = 1, Destination = packZip,
            Warnings = ["贴纸 #3 获取失败"] }, fixture));
        await package;
        Require(packageSuccess == true && packageSummary!.Contains(packZip) && packageSummary.Contains("贴纸 #3 获取失败"),
            "Successful exports must show the ZIP and any source failures.");

        var save = tg.Save(all: false, import: false);
        Require(tg.IsWriting && !host.TryClose() && sent is { Items: [{ Id: "id_0" }], ZipOutput: true, Destination: var downloadTo } &&
            downloadTo == sandbox && downloadFolderPicks == 1, "Download must use the selected IDs and saved path and block leaving.");
        download.SetResult((ok with { Count = 1, Destination = sandbox }, null));
        await save;
        Require(tg.Status.Text.Contains("下载 1 张") && host.TryClose() && password.Password == "",
            "A completed Telegram download must release the page and clear the token on exit.");

        void ApplyPageTheme(ExternalStickerPage page)
        {
            var palette = new Window();
            FluentTheme.Apply(palette, fixture);
            foreach (var key in palette.Resources.Keys) page.Resources[key] = palette.Resources[key];
            page.Background = (Brush)page.Resources["CanvasColor"];
            palette.Close();
        }

        static async Task Render(ExternalStickerPage page, Func<bool> ready, int width = 860)
        {
            for (int pass = 0; pass < 200; pass++)
            {
                page.Measure(new Size(width, 590));
                page.Arrange(new Rect(0, 0, width, 590));
                page.UpdateLayout();
                await page.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
                if (ready()) return;
                await Task.Delay(15);
            }
            throw new InvalidOperationException("External sticker page was not laid out or did not finish loading the preview.");
        }
    }

    private static async Task CheckFolderImport(LibrarySnapshot fixture, string repo, string output)
    {
        var sandbox = Path.Combine(output, $"folder-import-{Guid.NewGuid():N}");
        var folder = Path.Combine(sandbox, "合成目录");
        var child = Path.Combine(folder, "子目录");
        Directory.CreateDirectory(child);
        var original = fixture.Images.First(image => !image.Animated).Path;
        File.Copy(original, Path.Combine(folder, "一.png"));
        File.Copy(original, Path.Combine(child, "相同.png"));
        File.WriteAllText(Path.Combine(child, "坏图.png"), "not an image");
        File.WriteAllText(Path.Combine(child, "说明.txt"), "not an image");
        var data = Path.Combine(sandbox, "data");
        var commands = new LibraryCommands(repo, data);
        var updates = new System.Collections.Generic.List<LibraryProgress>();
        var response = await commands.Run(new("import", [folder]), reload: true,
            progress: new Progress<LibraryProgress>(updates.Add));
        var result = response.Result;
        var stats = result.FolderStats ?? throw new InvalidOperationException("Missing folder scan result.");
        var originalUpdate = updates.First(update => update.Phase == "folder_scan");
        var finalUpdate = updates.Last(update => update.Phase == "import");
        Require(originalUpdate.Total == 0 && originalUpdate.Detail!.Contains("扫描") &&
            finalUpdate is { Current: 3, Total: 3 } && updates.Any(update => update is { Phase: "import", Current: 0, Total: 3 }),
            "The real bridge must report scanning, start of the known total and a complete count.");
        var summary = MainWindow.FormatFolderImportResult(result, stats);
        foreach (var line in new[] { "文件夹：合成目录", "扫描文件总数：4", "其中图片文件：3", "成功导入：1",
            "重复跳过：1", "解析失败：1", "忽略非图片文件：1", "扫描子文件夹：1", "坏图.png" })
            Require(summary.Contains(line), $"Missing folder summary: {line}.");
        Require(response.Snapshot!.Images is [{ }] && stats.IssueCount == 1,
            "Only one image should be saved and the original error should appear in the summary.");

        var window = new MainWindow(fixture with { Images = [] }, offscreen: true, commands: commands);
        try
        {
            Layout(window);
            var import = window.ImportFiles([folder], null);
            var progress = window.ActivityProgress;
            Require(progress.Visibility == Visibility.Visible && progress.Bar.IsIndeterminate && progress.Detail.Text.Contains("扫描"),
                "A folder should open the scanning indicator before the file count is known.");
            await import;
            Require(progress.Visibility == Visibility.Collapsed && window.GalleryNotice.Visibility == Visibility.Collapsed &&
                window.Gallery.Items.Count == 1 && window.Gallery.Items[0].Categories.Contains("子目录"),
                "Folder import should refresh the gallery and use its own summary instead of an inline success notice.");
            progress.Begin("正在导入", "正在扫描文件夹…");
            progress.Report(originalUpdate);
            Require(progress.Bar.IsIndeterminate, "The folder discovery phase must not display a fake percentage.");
            progress.Report(finalUpdate with { Current = 2 });
            Require(!progress.Bar.IsIndeterminate && progress.Bar.Maximum == 3 && progress.Bar.Value == 2 &&
                progress.Detail.Text.Contains("2 / 3"), "The progress control must show the actual worker count.");
            Layout(window);
            SavePng(window.Root, 860, 640, Path.Combine(output, "wpf-import-progress.png"));
            progress.Dismiss();
        }
        finally { window.Close(); }
    }

    private static async Task CheckTelegramBatch(LibrarySnapshot library, string output)
    {
        var response = new TaskCompletionSource<(LibraryResult, LibrarySnapshot?)>(TaskCreationOptions.RunContinuationsAsynchronously);
        LibraryCommand? sent = null;
        IProgress<LibraryProgress>? updates = null;
        CancellationToken stop = default;
        int refreshed = 0;
        var page = new TelegramBatchPage(new("tg_batch_package", BotToken: "1234:synthetic"),
            (request, reload, cancellation, progress) =>
            {
                sent = request;
                Require(reload, "The Telegram batch page must reload the library after partial and full runs.");
                stop = cancellation;
                updates = progress;
                return response.Task;
            }, _ => output, _ => refreshed++, _ => { });
        var palette = new Window();
        FluentTheme.Apply(palette, library);
        foreach (var key in palette.Resources.Keys) page.Resources[key] = palette.Resources[key];
        page.Background = (Brush)page.Resources["CanvasColor"];
        palette.Close();
        Require(!page.Start.IsEnabled, "Batch start must require input and a destination.");
        page.Links.Text = " https://t.me/addstickers/first  \r\n\r\n second ";
        page.Browse.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        var host = new FluentPageHost();
        var source = new Grid();
        host.Push(new("TG", source));
        host.Push(new("批量打包", page, Work: page.PageWork));
        var batch = page.RunBatch();
        Require(page.IsRunning && !page.Inputs.IsEnabled && page.Cancel.Visibility == Visibility.Visible &&
            sent is { Links: ["https://t.me/addstickers/first", "second"], BotToken: "1234:synthetic" } &&
            sent.Destination == output, "Batch packaging must preserve the order of nonblank lines and use the same session credentials.");
        updates!.Report(new("parsed", 0, 4, null, 0, "第一个包"));
        updates.Report(new("download", 2, 4, null, 0));
        updates.Report(new("parse", 0, 0, null, 1));
        await page.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
        var rows = page.Packages.Items.OfType<TelegramBatchPage.BatchRow>().ToArray();
        Require(rows[0] is { Title: "第一个包", Stage: "下载中", Progress: "2/4" } && rows[1].Stage == "解析中",
            "Concurrent Telegram stages must update the correct pack rows.");
        updates.Report(new("done", 1, 1, "路径", 0));
        await page.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
        Require(page.Work.Bar.Value == 1 && page.Work.Bar.Maximum == 2, "Batch progress must reflect completed packs.");
        host.RequestBack();
        Require(stop.IsCancellationRequested && page.IsRunning && !page.Cancel.IsEnabled && ReferenceEquals(host.Page, page),
            "Cancellation should wait for the helper to finish cleaning up.");
        response.SetResult((new LibraryResult(1, 0, 0, null, null, null, null, Cancelled: 1,
            BatchRows: [new(0, "done", "已导出"), new(1, "cancelled", "")]), library));
        await batch;
        Require(refreshed == 1 && !page.IsRunning && rows[0].Stage == "已完成" && rows[1].Stage == "已取消" &&
            page.Status.Text.Contains("成功 1 包") && page.Start.IsEnabled,
            "Completed and cancelled rows must survive cancellation, and inputs must become available again.");
        page.Measure(new Size(860, 570));
        page.Arrange(new Rect(0, 0, 860, 570));
        page.UpdateLayout();
        SavePng(page, 860, 570, Path.Combine(output, "wpf-telegram-batch.png"));
        host.RequestBack();
        Require(ReferenceEquals(host.Page, source), "After cancellation settles, Back must restore the same source page.");
        Require(host.TryClose(), "The settled batch must release its route.");
    }

    private static async Task CheckQuickPanel(LibrarySnapshot library, string output)
    {
        var results = new QuickPanelResults(library);
        Require(results.Find("").Select(i => i.Name).SequenceEqual(library.Recent ?? []),
            "Recent history order differs from the snapshot.");
        var first = library.Images[0];
        if (results.Find("").Length == 0) results.Record(first);
        var panel = new QuickPanel(library, results, offscreen: true);
        try
        {
            async Task WaitForResults(int count)
            {
                var size = panel.RequestedSize;
                var limit = Stopwatch.StartNew();
                do
                {
                    panel.Root.Measure(size);
                    panel.Root.Arrange(new Rect(size));
                    panel.Root.UpdateLayout();
                    await panel.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
                    if (panel.Gallery.Items.Count == count && panel.Gallery.LoadedThumbnails == panel.Gallery.ActiveCards &&
                        (count == 0 || panel.Gallery.ActiveCards > 0)) return;
                    await Task.Delay(25);
                } while (limit.Elapsed < TimeSpan.FromSeconds(5));
                throw new InvalidOperationException($"Quick panel did not settle on {count} items.");
            }
            await WaitForResults(results.Find("").Length);
            var origin = ((FrameworkElement)panel.Cards.Children[0]).TranslatePoint(new Point(), panel.Root);
            Require(Math.Abs(origin.Y - 62) < 2, $"Quick panel should start at the top of the gallery: {origin}");
            SavePng(panel.Root, (int)panel.Width, (int)panel.Height, Path.Combine(output, "wpf-quick-panel.png"));
            results.Record(first);
            results.Record(first);
            Require(results.Find("").First() == first && results.Find("").Count(item => item == first) == 1,
                "A send should move the original entry to the top without duplication.");
            panel.Search.Text = "  HAPPY ";
            await WaitForResults(30);
            Require(panel.Gallery.Items.All(item => item.Keywords.Contains("happy")), "Quick search must match keywords.");
            var matched = panel.Gallery.Items;
            var updated = library with { Settings = new(library.Settings) };
            updated.Settings["quick_panel_width"] = JsonSerializer.SerializeToElement((int)panel.RequestedSize.Width == 390 ? 400 : 390);
            var newWidth = updated.Settings["quick_panel_width"].GetInt32();
            panel.RefreshFromLibrary(updated);
            Require(ReferenceEquals(matched, panel.Gallery.Items) && panel.Search.Text == "  HAPPY " &&
                panel.RequestedSize.Width == newWidth && panel.Width == newWidth,
                "Applying a saved preference must resize the existing quick panel without resetting its search results.");
            await WaitForResults(30);
            Sticker? chosen = null;
            panel.StickerActivated += item => chosen = item;
            ((Button)panel.Cards.Children[0]).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Require(chosen == panel.Gallery.Items[0], "Quick panel did not publish the selected sticker.");
            panel.Search.Text = "0000.gif";
            await WaitForResults(0);
            Require(panel.Empty.Visibility == Visibility.Visible && panel.Empty.Text == "没有找到匹配的表情",
                "Quick search should not match by filename and should display the old empty-state text.");
            panel.ResetSearch();
            await WaitForResults(results.Find("").Length);
            Require(panel.Gallery.Items[0] == first && panel.Empty.Visibility == Visibility.Collapsed,
                "Waking the quick panel must clear search and bring back recents.");
        }
        finally { panel.Close(); }
    }

    private static async Task CheckSendMedia(LibrarySnapshot library, string repository, string output, Sticker gif)
    {
        var cache = Path.Combine(output, "send-cache");
        var png = library.Images.First(item => item.Name.EndsWith(".png", StringComparison.OrdinalIgnoreCase));
        var sourceBytes = File.ReadAllBytes(png.Path);
        var converter = new StickerSendMedia(cache, true);
        var prepared = await converter.Prepare(png.Path, CancellationToken.None);
        Require(prepared.Path.EndsWith(".gif") && File.Exists(prepared.Path) && prepared.DibV5 is { Length: > 124 },
            "Static send should prepare a cached GIF and a bitmap.");
        Require(File.ReadAllBytes(png.Path).SequenceEqual(sourceBytes), "Send conversion changed the source image.");
        using (var file = File.OpenRead(prepared.Path))
        {
            var converted = new GifBitmapDecoder(file, BitmapCreateOptions.None, BitmapCacheOption.OnLoad);
            Require(converted.Frames.Count == 1, "Converted file is not a one-frame GIF.");
        }
        var original = await converter.Prepare(gif.Path, CancellationToken.None);
        Require(original.Path == gif.Path && original.DibV5 is not null, "Animated GIF should be offered as its original file.");
        var disabled = await new StickerSendMedia(cache, false).Prepare(png.Path, CancellationToken.None);
        Require(disabled.Path == png.Path && disabled.DibV5 is not null, "Disabled conversion should keep PNG and bitmap.");
        using var canceled = new CancellationTokenSource();
        canceled.Cancel();
        try
        {
            await converter.Prepare(png.Path, canceled.Token);
            throw new InvalidOperationException("A canceled send was still prepared.");
        }
        catch (OperationCanceledException) { }
    }

    private static void Layout(MainWindow window, int width = 860)
    {
        window.Root.Measure(new Size(width, 640));
        window.Root.Arrange(new Rect(0, 0, width, 640));
        window.Root.UpdateLayout();
    }

    private static async Task WaitForCards(MainWindow window, int expectedItems, int width = 860)
    {
        var limit = Stopwatch.StartNew();
        do
        {
            Layout(window, width);
            await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
            if (window.Gallery.Items.Count == expectedItems && window.Gallery.ActiveCards > 0 &&
                window.Gallery.ActiveCards == window.Gallery.LoadedThumbnails)
                return;
            await Task.Delay(40);
        } while (limit.Elapsed < TimeSpan.FromSeconds(5));
        throw new InvalidOperationException($"Gallery did not settle. Items={window.Gallery.Items.Count}, " +
            $"cards={window.Gallery.ActiveCards}, loaded={window.Gallery.LoadedThumbnails}.");
    }

    private static async Task<long[][]> SampleMemory()
    {
        var samples = new long[5][];
        using var process = Process.GetCurrentProcess();
        for (int i = 0; i < samples.Length; i++)
        {
            await Task.Delay(1000);
            process.Refresh();
            samples[i] = new[] { process.WorkingSet64, process.PrivateMemorySize64 };
        }
        return samples;
    }

    private static void CheckTrayMenuThemes(MainWindow window, LibrarySnapshot library, ContextMenu filter, string output)
    {
        var invoked = new System.Collections.Generic.List<string>();
        var tray = TrayIcon.CreateMenu(window.MenuPalette,
            () => invoked.Add("show"), () => invoked.Add("settings"), () => invoked.Add("exit"));
        Require(tray.Placement == PlacementMode.MousePoint && tray.Items.Count == 4 && tray.Items[2] is Separator,
            "Tray menu placement, length or separator differs from Qt.");
        var actions = tray.Items.OfType<MenuItem>().ToArray();
        Require(actions.Select(item => item.Header).SequenceEqual(new object[] { "显示主面板", "设置", "退出" }),
            "Tray menu labels or order differ from Qt.");
        var size = LayoutMenu(tray);
        var categoryMenu = window.Categories.Children.OfType<Button>()
            .Single(button => Equals(button.ToolTip, "动图")).ContextMenu;
        var stickerMenu = window.Gallery.ContextMenuFor!(library.Images[0])!;
        LayoutMenu(categoryMenu);
        LayoutMenu(stickerMenu);
        var menus = new[] { tray, filter, categoryMenu, stickerMenu };
        var cards = menus.Select(menu => (Border)menu.Template.FindName("MenuCard", menu)).ToArray();
        var trayCard = cards[0];
        Require(tray.Style == filter.Style && trayCard.CornerRadius == new CornerRadius(9) &&
            actions.All(item => item.ActualHeight == 28) &&
            (GridLength)tray.FindResource("FluentMenuMarkColumn") == new GridLength(0),
            "Tray should share the Fluent menu without an empty checkmark gutter.");
        var neutral = ColorOf(trayCard.Background);
        SavePng(tray, size.Width, size.Height, Path.Combine(output, "wpf-tray.png"));
        foreach (var item in actions) item.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
        Require(invoked.SequenceEqual(new[] { "show", "settings", "exit" }), "Tray actions are not wired in order.");

        LibrarySnapshot WithTheme(bool dark, string key) => library with
        {
            Settings = new(library.Settings)
            {
                ["appearance_mode"] = JsonSerializer.SerializeToElement(dark ? "dark" : "light"),
                [dark ? "dark_theme_key" : "light_theme_key"] = JsonSerializer.SerializeToElement(key),
            }
        };
        void SetTheme(bool dark, string key) => window.AcceptSnapshot(WithTheme(dark, key), null, updateSettings: true);
        Color CheckColors(bool dark)
        {
            var surface = ColorOf(trayCard.Background);
            var ink = ColorOf(dark ? Brushes.White : Brushes.Black);
            var hover = ColorOf((Brush)tray.FindResource("FluentMenuHover"));
            Require(surface != neutral && cards.All(card => ColorOf(card.Background) == surface) &&
                hover != surface && menus.All(menu => ColorOf((Brush)menu.FindResource("FluentMenuHover")) == hover) &&
                menus.SelectMany(menu => menu.Items.OfType<MenuItem>()).All(item => ColorOf(item.Foreground) == ink),
                "Existing tray, filter, category and sticker menus must immediately share the new themed palette.");
            return surface;
        }

        try
        {
            SetTheme(library.Dark, "rouge");
            var rouge = CheckColors(library.Dark);
            SavePng(tray, size.Width, size.Height, Path.Combine(output, "wpf-tray-rouge.png"));
            SetTheme(library.Dark, "sky");
            Require(CheckColors(library.Dark) != rouge, "Switching between two palette colors did not update the card.");
            SetTheme(!library.Dark, "sky");
            CheckColors(!library.Dark);
        }
        finally { window.AcceptSnapshot(library, null, updateSettings: true); }
        Require(cards.All(card => ColorOf(card.Background) == neutral),
            "Returning to the neutral palette should update all existing menus.");
    }

    private static Color ColorOf(Brush brush) => ((SolidColorBrush)brush).Color;

    private static async Task CheckSidebarScroll(LibrarySnapshot library, string output)
    {
        const string lastCategory = "最后一个分类";
        var onlySticker = library.Images[0] with { Categories = [lastCategory] };
        var large = library with
        {
            Categories = Enumerable.Range(1, 23).Select(i => new Category($"分类 {i}", null, null))
                .Append(new Category(lastCategory, null, null)).ToArray(),
            Images = [onlySticker, library.Images[1]],
        };
        var window = new MainWindow(large, offscreen: true);
        try
        {
            await WaitForCards(window, large.Images.Length);
            var scroll = window.SidebarScroll;
            var bar = (ScrollBar)scroll.Template.FindName("PART_VerticalScrollBar", scroll);
            var track = (Track)bar.Template.FindName("PART_Track", bar);
            var thumb = track.Thumb;
            var fill = (Border)thumb.Template.FindName("ThumbFill", thumb);
            Require(scroll.Template == window.GalleryScroll.Template && bar.Visibility == Visibility.Visible &&
                Math.Abs(bar.ActualWidth - 6) < .75 && thumb.ActualWidth == bar.ActualWidth &&
                Math.Abs(fill.ActualWidth - 3) < .75 && thumb.ActualHeight >= 30 && scroll.ScrollableHeight > 0,
                $"Overflowing categories must use the same thin thumb as the gallery: " +
                $"bar={bar.Visibility}/{bar.ActualWidth}, thumb={thumb.ActualWidth}×{thumb.ActualHeight}, " +
                $"fill={fill.ActualWidth}, scrollable={scroll.ScrollableHeight}.");
            SavePng(window.Root, 860, 640, Path.Combine(output, "wpf-sidebar.png"));

            thumb.RaiseEvent(new DragDeltaEventArgs(0, 120) { RoutedEvent = Thumb.DragDeltaEvent });
            Layout(window);
            Require(scroll.VerticalOffset > 0, "The slim sidebar thumb should still drag to scroll.");
            scroll.ScrollToBottom();
            Layout(window);
            var category = window.Categories.Children.OfType<Button>().Single(button => Equals(button.ToolTip, lastCategory));
            var position = category.TranslatePoint(new Point(), scroll);
            Require(scroll.VerticalOffset == scroll.ScrollableHeight && position.Y >= 0 &&
                position.Y + category.ActualHeight <= scroll.ViewportHeight,
                "The sidebar must scroll far enough to reach the last category.");
            category.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Require(window.Gallery.Items.SequenceEqual(new[] { onlySticker }), "Selection from the scrolled categories failed.");
            window.AcceptSnapshot(large with { Categories = [] }, null, updateSettings: false);
            Layout(window);
            Require(bar.Visibility == Visibility.Collapsed && scroll.ScrollableHeight == 0,
                "The sidebar thumb should disappear when all categories fit.");
        }
        finally { window.Close(); }
    }

    private static void DoubleClick(Button button, MouseButton mouseButton = MouseButton.Left) =>
        button.RaiseEvent(new MouseButtonEventArgs(Mouse.PrimaryDevice, 0, mouseButton)
        {
            RoutedEvent = Control.MouseDoubleClickEvent,
        });

    private static async Task CheckResponsiveGrids(LibrarySnapshot library, string output)
    {
        const int size = 120, gap = 10;
        Require(TileColumns.Fit(3 * size + 2 * gap, size, gap, true) == new TileColumns(3, size + gap) &&
            TileColumns.Fit(3 * size + 2 * gap - 1, size, gap, true) == new TileColumns(2, 2 * size + 2 * gap - 1) &&
            TileColumns.Fit(size - 1, size, gap, true).Count == 1,
            "Column count must include exact fits and drop a column just below the threshold.");
        var snapshot = library with { Images = library.Images.Take(7).ToArray() };
        var window = new MainWindow(snapshot, offscreen: true);
        try
        {
            await WaitForCards(window, 7);
            Button CategoryButton(string label) => window.Categories.Children.OfType<Panel>().SingleOrDefault()
                ?.Children.OfType<Button>().Single(button => Equals(button.ToolTip, label))
                ?? window.Categories.Children.OfType<Button>().Single(button => Equals(button.ToolTip, label));
            FrameworkElement[] Cards() => window.Cards.Children.Cast<FrameworkElement>()
                .OrderBy(Canvas.GetTop).ThenBy(Canvas.GetLeft).ToArray();
            void CheckGallery(int expectedColumns)
            {
                var cards = Cards();
                double firstTop = Canvas.GetTop(cards[0]), left = Canvas.GetLeft(cards[0]);
                var row = cards.TakeWhile(card => Canvas.GetTop(card) == firstTop).ToArray();
                Require(row.Length == expectedColumns && left == 16 &&
                    Math.Abs(Canvas.GetLeft(row[^1]) + window.Gallery.CardSize + left - window.GalleryScroll.ViewportWidth) < 1,
                    "Gallery must pack all fitting columns and spread them between symmetric insets.");
                double stride = Canvas.GetLeft(row[1]) - left;
                Require(row.Select((card, i) => Math.Abs(Canvas.GetLeft(card) - left - i * stride) < 1).All(aligned => aligned)
                    && cards.Skip(expectedColumns).Select((card, i) => Math.Abs(Canvas.GetLeft(card) - left - i * stride) < 1)
                        .All(aligned => aligned),
                    "Both complete and partial rows must align to the same evenly spaced columns.");
            }
            await WaitForCards(window, 7, width: 802);
            CheckGallery(4);
            await WaitForCards(window, 7, width: 816);
            CheckGallery(5);
            Require(!window.Gallery.ApplyWheel(120, ModifierKeys.None) && window.Gallery.CardSize == size,
                "Plain gallery wheel must stay a scroll gesture.");
            Require(window.Gallery.ApplyWheel(120, ModifierKeys.Control) && window.Gallery.CardSize == 130,
                "Gallery Ctrl-wheel did not zoom by one Qt-sized step.");
            await WaitForCards(window, 7, width: 816);
            CheckGallery(4);
            Require(window.Gallery.ApplyWheel(-120, ModifierKeys.Control) && window.Gallery.CardSize == size,
                "Reverse gallery Ctrl-wheel did not restore the card size.");

            Require(!window.ApplySidebarWheel(120, ModifierKeys.Control), "Ctrl-wheel should scroll normally in sidebar list mode.");
            CategoryButton("动图").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            CheckSelection();
            DoubleClick(CategoryButton("动图"));
            DoubleClick(CategoryButton("全部表情"), MouseButton.Right);
            Require(window.Categories.Children.OfType<ResponsiveTilePanel>().Count() == 0,
                "Only left double-click on All Stickers should toggle icon mode.");
            DoubleClick(CategoryButton("全部表情"));
            Layout(window);
            var panel = window.Categories.Children.OfType<ResponsiveTilePanel>().Single();
            Require(panel.Children.Count >= 4 && panel.Children.OfType<Button>().All(button => button.Content is Viewbox),
                "Icon mode must wrap the categories without showing their labels.");
            var first = CategoryButton("全部表情");
            Point Position(int index) => panel.Children[index].TranslatePoint(new Point(), panel);
            var third = (FrameworkElement)panel.Children[2];
            Require(Position(0).Y == Position(2).Y && Position(3).Y > Position(2).Y &&
                Math.Abs(Position(2).X + third.ActualWidth + Position(0).X - panel.ActualWidth) < 1.5 &&
                Math.Abs((Position(1).X - Position(0).X) - (Position(2).X - Position(1).X)) < 1.5 &&
                Position(3).X == Position(0).X,
                $"Category icons must distribute spare space evenly without stretching the incomplete row: " +
                $"points={Position(0)}/{Position(1)}/{Position(2)}/{Position(3)}, width={third.ActualWidth}/{panel.ActualWidth}.");
            Require(!window.ApplySidebarWheel(120, ModifierKeys.None), "Plain icon-mode wheel must stay a scroll gesture.");
            CategoryButton("动图").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            var selected = window.Gallery.Items.ToArray();
            Require(window.ApplySidebarWheel(120, ModifierKeys.Control), "Icon-mode Ctrl-wheel should zoom.");
            Layout(window);
            Require(ReferenceEquals(first, CategoryButton("全部表情")) && ((Viewbox)first.Content).MaxWidth == 74 &&
                window.Gallery.Items.SequenceEqual(selected),
                "Zooming category icons must resize the actual icon and preserve the active category.");
            CheckSelection();
            for (int i = 0; i < 20; i++) window.ApplySidebarWheel(120, ModifierKeys.Control);
            Layout(window);
            Require(((Viewbox)CategoryButton("全部表情").Content).MaxWidth == 200 &&
                CategoryButton("全部表情").ActualWidth > 180, "Category tiles must support the full Qt zoom range.");
            window.SidebarWidth.Width = new GridLength(80);
            Layout(window);
            var narrowed = CategoryButton("全部表情");
            Require(narrowed.ActualWidth > 0 && ((Viewbox)narrowed.Content).ActualWidth <= narrowed.ActualWidth,
                "A large icon should fit a narrow sidebar instead of clipping.");
            window.SidebarWidth.Width = new GridLength(300);
            for (int i = 0; i < 13; i++) window.ApplySidebarWheel(-120, ModifierKeys.Control);
            CategoryButton("全部表情").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            await WaitForCards(window, 7);
            SavePng(window.Root, 860, 640, Path.Combine(output, "wpf-aligned-icon-grid.png"));
            DoubleClick(CategoryButton("全部表情"));
            Layout(window);
            Require(window.Categories.Children.OfType<Button>().Any() && window.SidebarWidth.ActualWidth == 140,
                "Returning from icon mode should restore the separate list width.");

            window.AcceptSnapshot(library with { Images = library.Images.Take(19).ToArray() }, null, updateSettings: false);
            await WaitForCards(window, 19, width: 802);
            Require(window.GalleryScroll.ScrollableHeight > 0, "Four columns should overflow vertically for this fixture.");
            await WaitForCards(window, 19, width: 816);
            Require(window.GalleryScroll.ScrollableHeight == 0 &&
                Cards().Count(card => Canvas.GetTop(card) == 8) == 5,
                "A visible scrollbar must not prevent a fifth column from fitting exactly and removing the overflow.");

            void CheckSelection()
            {
                var button = CategoryButton("动图");
                var all = CategoryButton("全部表情");
                button.ApplyTemplate();
                all.ApplyTemplate();
                var surface = (Border)button.Template.FindName("Surface", button);
                var hoverSurface = (Border)button.Template.FindName("HoverSurface", button);
                var fill = ((SolidColorBrush)surface.Background).Color;
                Require(Selector.GetIsSelected(button) && !Selector.GetIsSelected(all) &&
                    Equals(surface.Background, window.FindResource("CategorySelectedSurface")) &&
                    fill.R == fill.G && fill.G == fill.B && fill.A is > 0 and < 48 &&
                    surface.BorderThickness == new Thickness(0) && Equals(button.Foreground, window.FindResource("Ink")) &&
                    !hoverSurface.IsHitTestVisible && hoverSurface.Visibility == Visibility.Hidden &&
                    Equals(((Border)all.Template.FindName("Surface", all)).Background, Brushes.Transparent),
                    "Only the active category should have a subtle neutral fill, with no blue or dark outline.");
            }
        }
        finally { window.Close(); }
    }

    private static async Task CheckGalleryMotion(LibrarySnapshot library)
    {
        var fixtures = library.Images.Take(120).Select((item, i) => item with
        {
            Categories = i % 3 == 0 ? ["甲"] : i % 3 == 1 ? ["甲", "乙"] : ["乙"]
        }).ToArray();
        var window = new MainWindow(library with
        {
            Images = fixtures, Categories = [new("甲", null, null), new("乙", null, null), new("空分类", null, null)]
        }, offscreen: true);
        try
        {
            static Image Thumbnail(Button card) => ((Grid)((Border)card.Content).Child).Children.OfType<Image>().Single();
            Button[] Cards() => window.Cards.Children.OfType<Button>().ToArray();
            string Name(Button card) => System.Windows.Automation.AutomationProperties.GetName(card);
            void Switch(string category) => window.Categories.Children.OfType<Button>()
                .Single(button => Equals(button.ToolTip, category)).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            async Task Frame()
            {
                Layout(window);
                await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
                Layout(window);
            }

            await WaitForCards(window, fixtures.Length);
            window.GalleryScroll.ScrollToVerticalOffset(680);
            await Frame();
            await WaitForCards(window, fixtures.Length);
            double offset = window.GalleryScroll.VerticalOffset;
            var anchor = Cards().Where(card => Canvas.GetTop(card) >= offset + 10).OrderBy(Canvas.GetTop).First();
            var original = Thumbnail(anchor).Source;
            var pointer = new Point(Canvas.GetLeft(anchor) + anchor.Width / 2, Canvas.GetTop(anchor) + anchor.Height / 2 - offset);
            Require(window.Gallery.ApplyWheel(120, ModifierKeys.Control, pointer) &&
                Cards().Contains(anchor) && ReferenceEquals(Thumbnail(anchor).Source, original),
                "Zoom must keep previously drawn cards and bitmaps until their next layout.");
            await Frame();
            Require(Cards().Contains(anchor) && Thumbnail(anchor).Source is not null &&
                Math.Abs(Canvas.GetTop(anchor) + anchor.Height / 2 - window.GalleryScroll.VerticalOffset - pointer.Y) < 1,
                "Zoom reflow must retain the sticker vertically under the pointer, even when the column count changes.");
            Require(!SystemParameters.ClientAreaAnimation || anchor.RenderTransform is TransformGroup zoom &&
                zoom.Children.OfType<ScaleTransform>().Any(scale => scale.HasAnimatedProperties),
                "The retained card should ease between sizes when Windows animations are enabled.");
            for (int i = 0; i < 5; i++)
            {
                window.Gallery.ApplyWheel(i % 2 == 0 ? -120 : 120, ModifierKeys.Control, pointer);
                await Frame();
                Require(window.Gallery.LoadedThumbnails > 0 && Cards().Contains(anchor) && Thumbnail(anchor).Source is not null,
                    "Rapid wheel changes must not clear visible images or recycle the anchored card.");
            }

            Switch("甲");
            await WaitForCards(window, 80);
            if (SystemParameters.ClientAreaAnimation) await Task.Delay(200);
            var allA = Cards();
            var bNames = fixtures.Where(item => item.Categories.Contains("乙")).Select(item => item.Name).ToHashSet();
            var common = allA.First(card => bNames.Contains(Name(card)));
            var sharedBitmap = Thumbnail(common).Source;
            var excluded = allA.Where(card => !bNames.Contains(Name(card))).Select(card => Thumbnail(card).Source).ToHashSet();
            Switch("乙");
            Require(Cards().Contains(common), "Category switching must not clear the old scene synchronously.");
            await Frame();
            Require(Cards().Contains(common) && ReferenceEquals(Thumbnail(common).Source, sharedBitmap) &&
                Cards().All(card => bNames.Contains(Name(card))), "Category reflow must reuse the shared card and show only matching interactive cards.");
            if (SystemParameters.ClientAreaAnimation)
            {
                var ghosts = window.GalleryMotion.Children.OfType<Border>().ToArray();
                Require(ghosts.Length > 0 && !window.GalleryMotion.IsHitTestVisible &&
                    ghosts.All(ghost => ghost.Child is Image image && excluded.Contains(image.Source) &&
                        ghost.RenderTransform is ScaleTransform { HasAnimatedProperties: true }),
                    "Only visible nonmatching images should leave via a non-interactive animation.");
                await Task.Delay(240);
                Require(window.GalleryMotion.Children.Count == 0, "Exit animation must release its temporary images.");
            }
            await WaitForCards(window, 80);

            Switch("空分类");
            await Frame();
            Require(window.Gallery.Items.Count == 0 && window.Gallery.ActiveCards == 0 &&
                (!SystemParameters.ClientAreaAnimation || window.GalleryMotion.Children.Count > 0),
                "Switching to a genuinely empty category should still play the exit animation.");
            Switch("甲");
            Switch("乙");
            Switch("甲");
            await WaitForCards(window, 80);
            await Task.Delay(200);
            Require(Cards().All(card => fixtures.Single(item => item.Name == Name(card)).Categories.Contains("甲")) &&
                window.GalleryMotion.Children.Count == 0, "An earlier category transition must not leave stale visuals.");
        }
        finally { window.Close(); }
    }

    private static async Task CheckSidebarZoomMotion(LibrarySnapshot library)
    {
        var sizeSettings = new System.Collections.Generic.Dictionary<string, JsonElement>(library.Settings)
        {
            ["sidebar_is_grid_mode"] = JsonSerializer.SerializeToElement(true),
            ["sidebar_width_grid"] = JsonSerializer.SerializeToElement(290),
            ["category_grid_icon_size"] = JsonSerializer.SerializeToElement(64)
        };
        var icon = library.Images.First(item => !item.Animated).Path;
        var categories = Enumerable.Range(0, 96).Select(index => new Category($"分类 {index}", null, index == 31 ? icon : null)).ToArray();
        var window = new MainWindow(library with { Settings = sizeSettings, Categories = categories, Images = [] }, offscreen: true);
        try
        {
            await Frame();
            var panel = window.Categories.Children.OfType<ResponsiveTilePanel>().Single();
            var scroll = window.SidebarScroll;
            var buttons = panel.Children.OfType<Button>().ToArray();
            var anchor = buttons.Single(button => Equals(button.ToolTip, "分类 31"));
            var thumbnail = (Image)((StackPanel)((Viewbox)anchor.Content).Child).Children[0];
            var source = thumbnail.Source;
            Require(source is not null && buttons.Take(3).All(button => LayoutBounds(button).Y == LayoutBounds(buttons[0]).Y),
                "The sidebar motion fixture must begin with three columns and a real thumbnail.");
            scroll.ScrollToVerticalOffset(700);
            await Frame();
            var pointer = anchor.TranslatePoint(new Point(anchor.ActualWidth / 2, anchor.ActualHeight / 2), scroll);
            Require(pointer.Y > 0 && pointer.Y < scroll.ViewportHeight, "The motion fixture must point at a visible category.");
            var start = LayoutBounds(anchor);
            Require(window.ApplySidebarWheel(120, ModifierKeys.Control, pointer), "Ctrl-wheel must zoom the category grid.");
            await Frame();
            var larger = LayoutBounds(anchor);
            Require(larger.Y != start.Y && larger.Width > start.Width && InPointerRow(pointer),
                "When a column disappears, the anchor must stay vertically under the pointer. " +
                $"Before: {start}; after: {larger}; cursor: {pointer}; offset: {scroll.VerticalOffset}.");
            Require(!SystemParameters.ClientAreaAnimation || anchor.RenderTransform is TransformGroup pose &&
                pose.Children.OfType<ScaleTransform>().Any(scale => scale.HasAnimatedProperties) &&
                pose.Children.OfType<TranslateTransform>().Any(translation => translation.HasAnimatedProperties),
                "Sidebar reflow should ease both position and size using the shared motion.");
            for (int tick = 0; tick < 5; tick++)
            {
                window.ApplySidebarWheel(tick % 2 == 0 ? -120 : 120, ModifierKeys.Control, pointer);
                await Frame();
                Require(InPointerRow(pointer) && ReferenceEquals(thumbnail.Source, source) &&
                    buttons.SequenceEqual(panel.Children.OfType<Button>()),
                    "Rapid zoom must preserve the anchor, existing buttons and decoded icon.");
            }
            await Task.Delay(200);
            await Frame();
            Require(InPointerRow(pointer) && anchor.RenderTransform.Value.IsIdentity, "Sidebar motion must settle on its final layout.");
            Require(!window.ApplySidebarWheel(120, ModifierKeys.None, pointer), "Plain wheel must remain a scroll gesture.");

            Rect LayoutBounds(UIElement button)
            {
                var offset = VisualTreeHelper.GetOffset(button);
                return new Rect(new Point(offset.X, offset.Y), button.RenderSize);
            }
            bool InPointerRow(Point at)
            {
                var bounds = LayoutBounds(anchor);
                return Math.Abs(panel.TranslatePoint(new Point(0, bounds.Y + bounds.Height / 2), scroll).Y - at.Y) < 1.5;
            }
            async Task Frame()
            {
                Layout(window);
                await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
                Layout(window);
            }
        }
        finally { window.Close(); }
    }

    private static async Task CheckLayoutPersistence(MainWindow window, string repo, string data)
    {
        var initial = await LibrarySnapshot.ReadAsync(data);
        var quick = new QuickPanel(initial, new QuickPanelResults(initial), offscreen: true);
        quick.ThumbnailSizeChanged += window.SetQuickThumbnailSize;
        quick.Search.Text = "stay";
        window.LibraryChanged += SyncQuick;
        try { await CheckZoomAndRollback(window, quick, repo, data); }
        finally
        {
            window.LibraryChanged -= SyncQuick;
            quick.Close();
        }

        void SyncQuick(LibrarySnapshot snapshot) => quick.RefreshFromLibrary(snapshot, window.QuickThumbnailSize);
    }

    private static async Task CheckZoomAndRollback(MainWindow window, QuickPanel quick, string repo, string data)
    {
        DoubleClick(window.Categories.Children.OfType<Button>().Single(button => Equals(button.ToolTip, "全部表情")));
        Require(window.ApplySidebarWheel(120, ModifierKeys.Control) &&
            window.Gallery.ApplyWheel(120, ModifierKeys.Control) && quick.Gallery.ApplyWheel(120, ModifierKeys.Control),
            "All three layout changes should be handled.");
        var saved = new TaskCompletionSource<LibrarySnapshot>(TaskCreationOptions.RunContinuationsAsynchronously);
        var seen = new System.Collections.Generic.List<int>();
        var committed = new System.Collections.Generic.List<int>();
        var quickSteps = new System.Collections.Generic.List<(int Displayed, int Saved)>();
        void OnChanged(LibrarySnapshot snapshot)
        {
            seen.Add(window.Gallery.CardSize);
            committed.Add(snapshot.Number("thumbnail_size", 0, 40, 300));
            quickSteps.Add((quick.Gallery.CardSize, QuickPanel.ReadThumbnailSize(snapshot)));
            if (seen.Count >= 2) saved.TrySetResult(snapshot);
        }
        window.LibraryChanged += OnChanged;
        try
        {
            window.FlushSettings(); // Begin saving the 130px step.
            window.Gallery.ApplyWheel(120, ModifierKeys.Control);
            quick.Gallery.ApplyWheel(120, ModifierKeys.Control);
            window.FlushSettings(); // Queue the newer step before the first reply.
            await saved.Task.WaitAsync(TimeSpan.FromSeconds(8));
            var persisted = await LibrarySnapshot.ReadAsync(data);
            Require(seen.Count >= 2 && seen.All(size => size == 140) && committed.SequenceEqual(new[] { 130, 140 }) &&
                quickSteps.SequenceEqual(new[] { (84, 78), (84, 84) }) && quick.Search.Text == "stay" &&
                persisted.Flag("sidebar_is_grid_mode", false) &&
                persisted.Number("category_grid_icon_size", 0, 20, 200) == 74 &&
                persisted.Number("thumbnail_size", 0, 40, 300) == 140,
                "All zooms and icon mode must persist without resetting search or rolling back rapid input.");
            var reopened = new QuickPanel(persisted, new QuickPanelResults(persisted), offscreen: true);
            try { Require(reopened.Gallery.CardSize == 84, "Reopening from disk must restore the quick panel's zoom."); }
            finally { reopened.Close(); }
        }
        finally { window.LibraryChanged -= OnChanged; }

        var reverted = new TaskCompletionSource<LibrarySnapshot>(TaskCreationOptions.RunContinuationsAsynchronously);
        void OnReverted(LibrarySnapshot snapshot) => reverted.TrySetResult(snapshot);
        // The isolated fixture stays readable, but Windows denies replacing the locked config.
        using var config = File.Open(Path.Combine(data, "config.json"), FileMode.Open, FileAccess.Read, FileShare.Read);
        window.LibraryChanged += OnReverted;
        try
        {
            Require(window.Gallery.ApplyWheel(120, ModifierKeys.Control) && window.Gallery.CardSize == 150 &&
                quick.Gallery.ApplyWheel(120, ModifierKeys.Control) && quick.Gallery.CardSize == 90,
                "The next step must preview before attempting a write.");
            window.FlushSettings();
            var persisted = await reverted.Task.WaitAsync(TimeSpan.FromSeconds(8));
            Require(window.Gallery.CardSize == 140 && persisted.Number("thumbnail_size", 0, 40, 300) == 140 &&
                quick.Gallery.CardSize == 84 && !window.LibraryBusy,
                "A failed layout write must roll back both live grids to their saved sizes.");
        }
        finally { window.LibraryChanged -= OnReverted; }
    }

    private static (int Width, int Height) LayoutMenu(ContextMenu menu)
    {
        // The real template, laid out without opening the popup HWND.
        menu.ApplyTemplate();
        menu.Measure(new Size(1000, 1000));
        int width = (int)Math.Ceiling(menu.DesiredSize.Width), height = (int)Math.Ceiling(menu.DesiredSize.Height);
        menu.Arrange(new Rect(0, 0, width, height));
        menu.UpdateLayout();
        return (width, height);
    }

    private static void RenderMenu(ContextMenu menu, string path, bool dark, MenuItem first)
    {
        var (width, height) = LayoutMenu(menu);
        var card = (Border)menu.Template.FindName("MenuCard", menu);
        Require(card.CornerRadius == new CornerRadius(9) && card.ActualWidth >= 132 &&
            ((SolidColorBrush)card.Background).Color == (Color)ColorConverter.ConvertFromString(dark ? "#2B2B2B" : "#F9F9F9"),
            "Unexpected Fluent card radius or palette.");
        var mark = (FrameworkElement)first.Template.FindName("Mark", first);
        Require(first.ActualHeight == 28 && mark.Visibility == (first.IsChecked ? Visibility.Visible : Visibility.Hidden),
            "Filter checkmark or row height is incorrect.");
        var firstLabel = (FrameworkElement)first.Template.FindName("Label", first);
        double labelX = firstLabel.TranslatePoint(new Point(), card).X;
        Require(Math.Abs(labelX - 43) < 1, "Unexpected check column width.");
        foreach (var item in menu.Items.OfType<MenuItem>().Where(x => x.Visibility == Visibility.Visible))
        {
            var label = (FrameworkElement)item.Template.FindName("Label", item);
            Require(Math.Abs(label.TranslatePoint(new Point(), card).X - labelX) < 1,
                "Menu action labels must share the same gutter.");
        }
        SavePng(menu, width, height, path);
    }

    private static void SavePng(Visual view, int width, int height, string path) =>
        SaveBitmap(RenderBitmap(view, width, height), path);

    private static BitmapSource RenderBitmap(Visual view, int width, int height)
    {
        var bitmap = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(view);
        return bitmap;
    }

    private static void SaveBitmap(BitmapSource bitmap, string path)
    {
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using var file = File.Create(path);
        encoder.Save(file);
    }

    private static byte[] Pixels(BitmapSource bitmap)
    {
        var stride = bitmap.PixelWidth * 4;
        var data = new byte[stride * bitmap.PixelHeight];
        bitmap.CopyPixels(data, stride, 0);
        return data;
    }

    private static void Require(bool check, string message)
    {
        if (!check) throw new InvalidOperationException(message);
    }
}
