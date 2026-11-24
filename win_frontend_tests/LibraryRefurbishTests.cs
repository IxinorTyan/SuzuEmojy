using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using SuzuEmojy.Windows;

internal static class LibraryRefurbishTests
{
    internal static void Run(string output)
    {
        var work = Path.Combine(output, "rf-" + Guid.NewGuid().ToString("N").Substring(0, 8));
        var source = Path.Combine(work, "source");
        var destination = Path.Combine(work, "new");
        var images = Directory.CreateDirectory(Path.Combine(source, "images")).FullName;
        var wide = Path.Combine(images, "wide.png");
        var tall = Path.Combine(images, "tall.png");
        var single = Path.Combine(images, "tall.gif");
        var animated = Path.Combine(images, "anim.gif");
        var broken = Path.Combine(images, "broken.png");
        SavePicture(wide, 1200, 600);
        SavePicture(tall, 300, 1200);
        SavePicture(Path.Combine(images, "small.png"), 120, 60);
        var shapeA = Path.Combine(images, "shape-a.png");
        var shapeB = Path.Combine(images, "shape-b.png");
        SavePicture(shapeA, 3, 2, solid: true);
        SavePicture(shapeB, 2, 3, solid: true);
        Require(ResourceIdentityV1.TryRead(shapeA, CancellationToken.None) == ResourceIdentityV1.TryRead(shapeB, CancellationToken.None),
            "The different shapes must be candidates under the legacy pixel digest.");
        File.Copy(wide, Path.Combine(images, "duplicate.png"));
        File.Copy(StaticGifCache.TryPrepare(tall, Path.Combine(work, "gif"), CancellationToken.None)!, single);
        File.WriteAllBytes(animated, Convert.FromBase64String("R0lGODlhAQABAIAAAAAAAP///yH5BAQAAAAALAAAAAABAAEAAAICRAEAIfkEBAAAAAAsAAAAAAEAAQAAAgJMAQA7"));
        File.WriteAllBytes(Path.Combine(images, "offset.gif"),
            [71, 73, 70, 56, 57, 97, 3, 0, 2, 0, 128, 0, 0, 0, 0, 0, 255, 255, 255,
             33, 249, 4, 1, 0, 0, 1, 0, 44, 1, 0, 1, 0, 1, 0, 1, 0, 0, 2, 2, 68, 1, 0, 59]);
        File.WriteAllText(broken, "not a PNG");
        File.WriteAllBytes(Path.Combine(images, "apng.png"), Convert.FromBase64String(NativeApngTests.Fixture));
        File.WriteAllText(Path.Combine(source, "categories.json"), "{\"alpha\":[\"tall.gif\",\"wide.png\"]}");
        File.WriteAllText(Path.Combine(source, "category_icons.json"), "{\"alpha\":\"tall.gif\"}");
        File.WriteAllText(Path.Combine(source, "metadata.json"), "{\"tall.gif\":\"keep keywords\"}");
        File.WriteAllText(Path.Combine(source, "order.json"), "[\"tall.gif\",\"wide.png\"]");
        File.WriteAllText(Path.Combine(source, "recent.json"), "[\"tall.gif\",\"wide.png\"]");
        File.WriteAllText(Path.Combine(source, "config.json"), "{\"appearance_mode\":\"dark\"}");
        var before = LibrarySnapshotReader.Read(source, CancellationToken.None);
        var original = Directory.GetFiles(images).ToDictionary(path => Path.GetFileName(path)!, File.ReadAllBytes);

        var report = LibraryRefurbish.Run(source, destination, CancellationToken.None);
        var renamed = report.Renames["tall.gif"];
        Require(report is { Images: 11, Recleaned: 8, Resized: 4, PreservedAnimations: 2 } && renamed == "tall_2.png" &&
            report.UnchangedBecauseUnreadable.SequenceEqual(new[] { "broken.png" }) &&
            report.ExactDuplicateGroups.Length == 2 &&
            report.ExactDuplicateGroups.Any(group => group.Length == 2 && group.Contains("wide.png") && group.Contains("duplicate.png")) &&
            report.ExactDuplicateGroups.Any(group => group.Length == 2 && group.Contains("tall.png") && group.Contains(renamed)),
            "Report must distinguish resized, retained, renamed and duplicate files.");
        Require(original.All(item => item.Value.SequenceEqual(File.ReadAllBytes(Path.Combine(images, item.Key!)))) &&
            !File.Exists(Path.Combine(source, "features.db")), "The source files and indexes must remain untouched.");
        var after = LibrarySnapshotReader.Read(destination, CancellationToken.None);
        var byName = after.Images.ToDictionary(item => item.Name);
        Require(after.Images.Select(item => item.Name).SequenceEqual(before.Images.Select(item =>
                report.Renames.TryGetValue(item.Name, out var replacement) ? replacement : item.Name)) &&
            after.Recent.SequenceEqual(new[] { renamed, "wide.png" }) && after.Dark &&
            byName[renamed].Keywords == "keep keywords" && byName[renamed].Categories.SequenceEqual(new[] { "alpha" }) &&
            after.Categories.Single().IconPath == byName[renamed].Path,
            "The rebuilt library must retain the original order, keyword, category, icon, recents and settings.");
        Require(PixelSize(byName["wide.png"].Path) == (720, 360) && PixelSize(byName["tall.png"].Path) == (180, 720) &&
            PixelSize(byName[renamed].Path) == (180, 720) && PixelSize(byName["small.png"].Path) == (120, 60),
            "Static images must preserve aspect ratio, orientation and small dimensions.");
        using (var input = File.OpenRead(byName["offset.png"].Path))
        {
            var frame = ResourceIdentityV1.DecodePng(input, CancellationToken.None)!;
            var pixels = new byte[3 * 2 * 4];
            frame.CopyPixels(pixels, 12, 0);
            Require(frame.PixelWidth == 3 && frame.PixelHeight == 2 && pixels[3] == 0 &&
                pixels[4 * (3 + 1) + 3] == 255 && pixels[4 * (3 + 2) + 3] == 0,
                "A cropped single-frame GIF must retain its logical canvas, offset and transparency.");
        }
        Require(new[] { "anim.gif", "apng.png", "broken.png" }.All(name => original[name].SequenceEqual(File.ReadAllBytes(byName[name].Path))),
            "Animated and unreadable assets must be carried over without modifying their bytes.");
        using (var indexes = WindowsSqlite.OpenIfPresent(Path.Combine(destination, "features.db")))
        {
            string?[] row = indexes!.Read("SELECT sync_key, file_size FROM image_features WHERE image_path = ?", 2,
                CancellationToken.None, renamed).Single();
            Require(row[0] == ResourceIdentityV1.TryRead(byName[renamed].Path, CancellationToken.None) &&
                long.Parse(row[1]!) == new FileInfo(byName[renamed].Path).Length,
                "Renamed assets must have a fresh index for the actual output bytes.");
        }
        var imported = LocalImageImport.TryRun(destination, [wide], null, CancellationToken.None, null);
        Require(imported is { Count: 0, Skipped: 1 }, "Importing the unscaled original into the rebuilt library must deduplicate.");
        var fresh = Path.Combine(work, "fresh");
        Require(LocalImageImport.TryRun(fresh, [wide], null, CancellationToken.None, null) is { Count: 1 } &&
            PixelSize(LibrarySnapshotReader.Read(fresh, CancellationToken.None).Images.Single().Path) == (720, 360),
            "Regular imports into a fresh library must also use the same size limit.");
        Require(LocalImageImport.TryRun(fresh, [shapeA, shapeB, shapeB], null, CancellationToken.None, null)
                is { Count: 2, Skipped: 1 } &&
            LocalImageImport.TryRun(fresh, [shapeA, shapeB], null, CancellationToken.None, null)
                is { Count: 0, Skipped: 2 },
            "Local import must keep different shapes despite equal hashes and find either shape on reimport.");
        using var savedReport = JsonDocument.Parse(File.ReadAllText(Path.Combine(destination, "refurbish-report.json")));
        Require(savedReport.RootElement.GetProperty("Resized").GetInt32() == report.Resized,
            "The verified report must be present in the published output.");
        Require(File.Exists(Path.Combine(destination, StartupLibraryRefurbish.Receipt)), "A standalone output must not be refurbished again at startup.");
        CheckStartup(source, wide, work, original);
        CheckSyncedStartup(work);
    }

    private static void CheckStartup(string source, string wide, string work, Dictionary<string, byte[]> original)
    {
        var token = CancellationToken.None;
        var receipt = Path.Combine(source, StartupLibraryRefurbish.Receipt);
        var report = Path.Combine(source, LibraryRefurbish.ReportFile);
        var backups = Path.Combine(source, "maintenance-backups");
        var inbox = Directory.CreateDirectory(Path.Combine(source, "inbox")).FullName;
        var waiting = Path.Combine(inbox, "waiting.txt");
        File.WriteAllText(waiting, "leave inbox alone");
        File.WriteAllText(report, "old report");
        Require(!StartupLibraryRefurbish.RunOnce(source, token, _ => 0) && !File.Exists(receipt) && !Directory.Exists(backups) &&
            PixelSize(wide) == (1200, 600) && LocalImageImport.TryRun(source, [wide], null, token, null) is { Count: 0, Skipped: 1 },
            "A full disk must defer migration and imports of an original must still deduplicate.");
        var features = Path.Combine(source, "features.db");
        var featuresBefore = File.ReadAllBytes(features);
        // Block the last replacement after images and DBs have already moved.
        using (File.Open(report, FileMode.Open, FileAccess.Read, FileShare.Read))
            StartupLibraryRefurbish.RunOnce(source, token);
        Require(!File.Exists(receipt) && featuresBefore.SequenceEqual(File.ReadAllBytes(features)) && File.ReadAllText(report) == "old report" &&
            original.All(item => item.Value.SequenceEqual(File.ReadAllBytes(Path.Combine(source, "images", item.Key)))),
            "Failed publication must roll back the old files and leave no success marker.");

        var launch = new ProcessStartInfo(Path.ChangeExtension(System.Reflection.Assembly.GetEntryAssembly()!.Location, ".exe"),
            $"--crash-refurbish-fixture \"{source}\" images") { UseShellExecute = false, CreateNoWindow = true };
        using (var child = Process.Start(launch)!)
        {
            if (!child.WaitForExit(30_000)) { child.Kill(); throw new TimeoutException("Migration process did not reach publication."); }
            Require(child.ExitCode != 0 && File.Exists(Path.Combine(source, StartupLibraryRefurbish.Receipt + ".journal")) &&
                PixelSize(wide) == (720, 360), "The worker must be terminated after publishing images, before marking completion.");
        }
        StartupLibraryRefurbish.Recover(source);
        Require(!File.Exists(receipt) && featuresBefore.SequenceEqual(File.ReadAllBytes(features)) && File.ReadAllText(report) == "old report" &&
            original.All(item => item.Value.SequenceEqual(File.ReadAllBytes(Path.Combine(source, "images", item.Key)))),
            "On relaunch the journal must restore the original library after process termination.");
        StartupLibraryRefurbish.RunOnce(source, token);
        var backup = Path.Combine(Directory.GetDirectories(backups, "refurbish-v1-*").Single(), "original");
        Require(File.Exists(receipt) && original.All(item => item.Value.SequenceEqual(File.ReadAllBytes(Path.Combine(backup, "images", item.Key)))) &&
            File.ReadAllText(waiting) == "leave inbox alone" && PixelSize(wide) == (720, 360) &&
            LibrarySnapshotReader.Read(source, token).Images.Single(item => item.Name == "tall_2.png").Keywords == "keep keywords",
            "Startup must publish the rebuilt library in place, preserve the inbox, and keep the original version.");
        Require(LocalImageImport.TryRun(source, [Path.Combine(backup, "images", Path.GetFileName(wide))], null, token, null) is
            { Count: 0, Skipped: 1 }, "An incoming original-size file must match its already refurbished copy.");
        var unprocessed = Path.Combine(source, "images", "added-later.png");
        SavePicture(unprocessed, 1200, 300);
        StartupLibraryRefurbish.Recover(source);
        StartupLibraryRefurbish.RunOnce(source, token);
        Require(PixelSize(unprocessed) == (1200, 300) && Directory.GetDirectories(backups, "refurbish-v1-*").Length == 1,
            "A later launch must skip image processing and not make another backup.");

        var empty = Path.Combine(work, "empty-startup");
        StartupLibraryRefurbish.RunOnce(empty, token);
        Require(File.Exists(Path.Combine(empty, StartupLibraryRefurbish.Receipt)) &&
            !Directory.Exists(Path.Combine(empty, "maintenance-backups")),
            "A new gallery must be marked current without running the old-library migration.");
    }

    private static void CheckSyncedStartup(string work)
    {
        var source = Path.Combine(work, "synced-startup");
        var images = Directory.CreateDirectory(Path.Combine(source, "images")).FullName;
        var synced = Path.Combine(images, "synced.png");
        var local = Path.Combine(images, "local.png");
        SavePicture(synced, 1200, 600);
        SavePicture(local, 600, 1200);
        var token = CancellationToken.None;
        var key = ResourceIdentityV1.TryRead(synced, token)!;
        var device = Guid.NewGuid().ToString("N");
        var peer = Guid.NewGuid().ToString("N");
        var published = new SyncLibrary([], [new(key, [], [])]);
        using (var db = LibraryDatabases.TryOpen(source, recent: false, token, out _)!)
        {
            db.Execute("CREATE TABLE lan_sync_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)", token);
            db.Execute("INSERT INTO lan_sync_state VALUES (?, ?)", token, "revision:" + peer,
                JsonSerializer.Serialize(new SyncHistory(new(Guid.NewGuid().ToString("N"), published), null)));
            LibraryEdits.Commit(db, source, token);
        }
        Require(StartupLibraryRefurbish.RunOnce(source, token) && PixelSize(synced) == (1200, 600) && PixelSize(local) == (360, 720),
            "The automatic migration must keep the previously published key and still resize unrelated images.");
        var after = new LanSyncWindowsLibrary(source).Read(device, peer, token).Library;
        var mixed = LanSyncMerge.Reconcile(published, after, published).Library;
        Require(mixed.Resources.Length == 2 && mixed.Resources.Count(item => item.Key == key) == 1 &&
            LocalImageImport.TryRun(source, [synced], null, token, null) is { Count: 0, Skipped: 1 },
            "An old peer must see the same resource without replacement or duplication, and local reimports must find it.");
    }

    internal static void SavePicture(string path, int width, int height, bool solid = false)
    {
        var pixels = new byte[width * height * 4];
        for (int y = 0; y < height; y++)
            for (int x = 0; x < width; x++)
            {
                int i = (y * width + x) * 4;
                pixels[i + 2] = 230;
                if (solid || (x >= width / 8 && y >= height / 8)) pixels[i + 3] = 255;
            }
        var image = BitmapSource.Create(width, height, 96, 96, PixelFormats.Bgra32, null, pixels, width * 4);
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(image));
        using var file = File.Create(path);
        encoder.Save(file);
    }

    internal static (int, int) PixelSize(string path)
    {
        using var file = File.OpenRead(path);
        var frame = BitmapDecoder.Create(file, BitmapCreateOptions.None, BitmapCacheOption.OnDemand).Frames[0];
        return (frame.PixelWidth, frame.PixelHeight);
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }
}
