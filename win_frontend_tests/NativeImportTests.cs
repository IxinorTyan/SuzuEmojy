using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using SuzuEmojy.Windows;

internal static class NativeImportTests
{
    internal static async Task Run(string output, string? legacy = null)
    {
        var folder = Directory.CreateDirectory(Path.Combine(output, $"本地清洗-{Guid.NewGuid():N}")).FullName;
        var png = Path.Combine(folder, "透明.png");
        var gif = Path.Combine(folder, "动图.gif");
        byte[] p = Convert.FromBase64String(NativeIdentityTests.Rgba);
        byte[] g = Convert.FromBase64String("R0lGODlhBgAFAIEAAMg8tAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQACQAAACwAAAAABgAFAAAIDAABCBxIsKDBgwIDAgAh+QQBDwABACwAAAAABgAFAIEoZCgAAAAAAAAAAAAIDAABCBxIsKDBgwIDAgA7");
        File.WriteAllBytes(png, p);
        File.WriteAllBytes(gif, g);
        var data = Path.Combine(folder, "data");
        var unavailableBackend = Path.Combine(folder, "missing-backend");
        var commands = new LibraryCommands(unavailableBackend, data);
        var imported = await commands.Run(new("import", [png, gif, png], Category: "原生分类"), reload: true);
        var images = imported.Snapshot!.Images;
        Require(imported.Result is { Count: 2, Skipped: 1, Failed: 0 } && images.Length == 2 &&
            ResourceIdentityV1.TryRead(images[0].Path, CancellationToken.None) == "p:6fd2f58323d227445b3d1b0af54f47a0" &&
            imported.Snapshot.Categories.Single().IconPath == images[0].Path && File.ReadAllBytes(images[1].Path).SequenceEqual(g) &&
            File.ReadAllBytes(png).SequenceEqual(p) && File.ReadAllBytes(gif).SequenceEqual(g),
            "Native cleaning changed identity, raw GIF, order, originals or auto icon.");
        var promoted = await commands.Run(new("import", [gif]), reload: true);
        Require(promoted.Snapshot!.Images[0].Path == images[1].Path && promoted.Result.Skipped == 1, "Repeated imports must promote the duplicate.");
        var singleGif = Path.Combine(folder, "一帧.gif");
        File.WriteAllBytes(singleGif, Convert.FromBase64String("R0lGODlhBAABAIEAAAsWIS06W3mW0QAAACH5BAEAAAEALAAAAAAEAAEAAAgHAAMAEBAgIAA7"));
        var singleData = Path.Combine(folder, "单帧导入");
        var single = await new LibraryCommands(unavailableBackend, singleData).Run(new("import", [singleGif]), reload: true);
        Require(single.Result.Count == 1 && single.Snapshot?.Images.Single().Path is { } singlePng &&
            Path.GetExtension(singlePng) == ".png" &&
            ResourceIdentityV1.TryRead(singlePng, CancellationToken.None) == "p:8ff80f03355baca1491ef85d14b528fe", "Single-frame GIF RGBA identity.");
        var cancelledRoot = Path.Combine(folder, "取消部分导入");
        using (var cancel = new CancellationTokenSource())
        {
            var interrupted = await new LibraryCommands(unavailableBackend, cancelledRoot)
                .Run(new("import", [png, gif]), reload: true, cancel.Token, new CancelAfterFirst(cancel));
            Require(interrupted.Result is { Count: 1, Cancelled: 1 } && interrupted.Snapshot!.Images.Length == 1,
                "Cancellation must preserve only complete local items.");
        }
        if (legacy is not null)
        {
            var request = JsonSerializer.Serialize(new { action = "import", paths = new[] { png, gif } });
            using var result = JsonDocument.Parse(await PythonBridge.Request(legacy, "services.gallery_commands", request, data,
                CancellationToken.None, null));
            Require(result.RootElement.GetProperty("skipped").GetInt32() == 2 &&
                (await LibrarySnapshot.ReadAsync(data)).Images.Length == 2, "Legacy cleaner did not recognize native cleaned identities.");
            request = JsonSerializer.Serialize(new { action = "import", paths = new[] { singleGif } });
            using var singleResult = JsonDocument.Parse(await PythonBridge.Request(legacy, "services.gallery_commands", request, singleData,
                CancellationToken.None, null));
            Require(singleResult.RootElement.GetProperty("skipped").GetInt32() == 1, "Legacy single-frame GIF identity.");
        }

        var hierarchy = Path.Combine(folder, "表情目录");
        Directory.CreateDirectory(Path.Combine(hierarchy, "子类", "更深"));
        File.WriteAllBytes(Path.Combine(hierarchy, "图10.PNG"), p);
        File.WriteAllBytes(Path.Combine(hierarchy, "图2.gif"), g);
        File.WriteAllBytes(Path.Combine(hierarchy, "子类", "更深", "同一张.png"), p);
        File.WriteAllText(Path.Combine(hierarchy, "说明.txt"), "ignored");
        var folderData = Path.Combine(folder, "文件夹导入");
        var folderCommands = new LibraryCommands(unavailableBackend, folderData);
        var capture = new TrackProgress();
        var batch = await folderCommands.Run(new("import", [hierarchy], Category: "默认"), reload: true, progress: capture);
        Require(batch.Result is { Count: 2, Skipped: 1, Failed: 0, FolderStats: { FolderName: "表情目录",
            TotalFiles: 4, ImageCount: 3, NonImages: 1, Subdirs: 2, IssueCount: 0 } } &&
            batch.Snapshot is { } found && found.Images.Length == 2 && Path.GetExtension(found.Images[0].Path) == ".gif" &&
            found.Categories.Single(group => group.Name == "表情目录").IconPath == found.Images[0].Path &&
            found.Categories.Single(group => group.Name == "子类").IconPath == found.Images[1].Path &&
            capture.Phases.Contains("folder_scan") && capture.Phases.Contains("import"), "Folder structure, natural order, summary and progress");
        if (legacy is not null)
        {
            var baseline = Path.Combine(folder, "旧版文件夹导入");
            var request = JsonSerializer.Serialize(new { action = "import", paths = new[] { hierarchy }, category = "默认" });
            using var original = JsonDocument.Parse(await PythonBridge.Request(legacy, "services.gallery_commands", request, baseline,
                CancellationToken.None, null));
            Require(original.RootElement.GetProperty("count").GetInt32() == batch.Result.Count &&
                original.RootElement.GetProperty("skipped").GetInt32() == batch.Result.Skipped &&
                (await LibrarySnapshot.ReadAsync(baseline)).Images.Select(item => Path.GetExtension(item.Path))
                    .SequenceEqual(batch.Snapshot!.Images.Select(item => Path.GetExtension(item.Path))), "Folder import legacy parity");
        }
    }

    private sealed class TrackProgress : IProgress<LibraryProgress>
    {
        internal System.Collections.Generic.HashSet<string> Phases { get; } = [];
        public void Report(LibraryProgress value) => Phases.Add(value.Phase);
    }

    private sealed class CancelAfterFirst(CancellationTokenSource cancellation) : IProgress<LibraryProgress>
    {
        public void Report(LibraryProgress value)
        {
            if (value is { Phase: "import", Current: 1 }) cancellation.Cancel();
        }
    }

    private static void Require(bool valid, string message)
    {
        if (!valid) throw new InvalidOperationException(message);
    }
}
