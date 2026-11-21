using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using SuzuEmojy.Windows;

internal static class NativeSimilarityTests
{
    internal static async Task CheckFixtureGif(string fixture, string output)
    {
        var original = Path.Combine(fixture, "images", "0000.gif");
        var root = Path.Combine(output, $"动图感知哈希-{Guid.NewGuid():N}");
        var images = Directory.CreateDirectory(Path.Combine(root, "images")).FullName;
        var file = Path.Combine(images, "sample.gif");
        File.Copy(original, file);
        var result = (await new LibraryCommands(root, root).Run(new("similarity"), false)).Result;
        Require(result is { Error: null, Failed: 0 }, "Native animated similarity did not read the GIF.");
        using var db = WindowsSqlite.OpenIfPresent(Path.Combine(root, "cache", "similarity-v1.db"))!;
        using var payload = JsonDocument.Parse(db.Read("SELECT payload FROM similarity_v1 WHERE path = ?", 1,
            CancellationToken.None, file).Single()[0]!);
        var hashes = payload.RootElement.GetProperty("hashes").EnumerateArray().Select(hash => hash.GetInt64()).ToArray();
        // Reference: Pillow similarity-v1, a transparent two-frame GIF with disposal to background.
        long[] expected = [7621390282008926782, 7621390282008926782, 7621390282008926782, 7621390282008926782,
            7633071630899583502, 7633071630899583502, 7633071630899583502, 7633071630899583502];
        Require(payload.RootElement.GetProperty("duration").GetInt32() == 360 && hashes.SequenceEqual(expected),
            $"Native GIF timing or v1 samples differ from the historical reader: {string.Join(", ", hashes)}.");
    }

    internal static async Task Run(string output, string? legacy = null)
    {
        (int Width, int Height, long Hash)[] fixtures =
        [
            (3, 1, 35), (32, 32, 5221780853609094260), (13, 51, 896785238144728522),
            (314, 127, 3351359297684143782), (1, 77, 141287244169216), (41, 7, 3252815706903767063),
            (128, 128, 149610623284203258), (33, 31, 7560369637616049542), (1024, 255, 35957420381405226),
            (45, 32, 8976805180221357046)
        ];
        var root = Path.Combine(output, $"感知哈希-{Guid.NewGuid():N}");
        var images = Directory.CreateDirectory(Path.Combine(root, "images")).FullName;
        var token = CancellationToken.None;
        for (int test = 0; test < fixtures.Length; test++)
        {
            var (width, height, expected) = fixtures[test];
            var pixels = new byte[width * height * 4];
            for (int y = 0; y < height; y++)
                for (int x = 0; x < width; x++)
                {
                    int offset = (y * width + x) * 4;
                    pixels[offset] = (byte)(x * y * 11 + test * 17);
                    pixels[offset + 1] = (byte)(x * 3 + y * 33 + test * 5);
                    pixels[offset + 2] = (byte)(x * 37 + y * 13 + test * 61);
                    pixels[offset + 3] = (byte)(x * 67 + y * 23 + test * 11);
                }
            var source = BitmapSource.Create(width, height, 96, 96, PixelFormats.Bgra32, null, pixels, width * 4);
            long key = SimilarityV1Hash.Compute(source, token);
            Require(key == expected, $"Native similarity hash differs from legacy for {width}x{height}: {key} != {expected}.");
            var encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(source));
            using var file = File.Create(Path.Combine(images, $"{test}.png"));
            encoder.Save(file);
        }
        File.Copy(Path.Combine(images, "0.png"), Path.Combine(images, "0-copy.png"));
        var commands = new LibraryCommands(Path.Combine(root, "missing"), root);
        var groups = (await commands.Run(new("similarity", Threshold: 0), reload: false)).Result.SimilarityGroups!;
        Require(groups.Length == 1 && groups[0].Length == 2 &&
            groups[0].SequenceEqual(groups[0].OrderBy(image => image.Path, StringComparer.Ordinal)), "Native grouping did not keep anchor ordering.");
        LibrarySimilarity.Feature Item(string name, long hash) => new(name, 1, 2, 1, 1, [hash], 0);
        Require(LibrarySimilarity.Group([Item("A", 0), Item("B", 1), Item("C", 3)], 1, token)
            .Single().Select(item => item.Path).SequenceEqual(new[] { "A", "B" }), "Similarity is not a transitive group.");
        if (legacy is not null)
        {
            // Existing Python still consumes the same cache data; independent
            // golden keys above also cover varying source size and alpha.
            string result = await PythonBridge.Request(legacy, "services.gallery_commands",
                """{"action":"similarity","threshold":0}""", root, token, null);
            using var json = JsonDocument.Parse(result);
            Require(json.RootElement.GetProperty("similarity_groups").GetArrayLength() == 1 &&
                json.RootElement.GetProperty("failed").GetInt32() == 0, "Legacy similarity was unable to consume native cache records.");
        }
    }

    private static void Require(bool valid, string message)
    {
        if (!valid) throw new InvalidOperationException(message);
    }
}
