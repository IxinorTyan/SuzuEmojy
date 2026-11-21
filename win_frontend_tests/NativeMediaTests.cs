using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using SuzuEmojy.Windows;
using SuzuEmojy.Windows.Desktop;

internal static class NativeMediaTests
{
    internal static async Task Run(string output)
    {
        var data = Directory.CreateDirectory(Path.Combine(output, $"原生媒体-{Guid.NewGuid():N}")).FullName;
        var command = new LibraryCommands(Path.Combine(data, "missing-backend"), data);
        var origin = Path.Combine(data, "当前图片.png");
        const int side = 256;
        var pixels = new byte[side * side * 4];
        var random = new Random(42);
        for (int i = 0; i < pixels.Length; i += 4)
        {
            // Enough nonrepeating pairs to cross all LZW widths and reset
            // the dictionary, but no quantization ambiguity for visible colors.
            pixels[i] = (byte)random.Next(200);
            pixels[i + 1] = 42;
            pixels[i + 2] = 98;
            pixels[i + 3] = i % 32 == 0 ? (byte)127 : (byte)128;
        }
        Save(origin, new PngBitmapEncoder(), side, side, pixels);
        var originalBytes = File.ReadAllBytes(origin);
        var sender = new StickerSendMedia(Path.Combine(data, "cache"), true);
        var prepared = await sender.Prepare(origin, CancellationToken.None);
        Require(prepared.Path.EndsWith(".gif", StringComparison.OrdinalIgnoreCase) && prepared.DibV5 is not null,
            "Static send must produce a single-frame GIF without Python.");
        using (var stream = File.OpenRead(prepared.Path))
        {
            var gif = new GifBitmapDecoder(stream, BitmapCreateOptions.PreservePixelFormat, BitmapCacheOption.OnLoad);
            Require(gif.Frames.Count == 1, "A static send must contain exactly one frame.");
            var actual = new byte[pixels.Length];
            new FormatConvertedBitmap(gif.Frames[0], PixelFormats.Bgra32, null, 0).CopyPixels(actual, side * 4, 0);
            for (int i = 0; i < actual.Length; i += 4)
            {
                bool alpha = pixels[i + 3] >= 128;
                Require(actual[i + 3] == (alpha ? 255 : 0) &&
                    (!alpha || actual.AsSpan(i, 3).SequenceEqual(pixels.AsSpan(i, 3))), "GIF alpha threshold or LZW changed a pixel.");
            }
        }
        Require((await sender.Prepare(origin, CancellationToken.None)).Path == prepared.Path &&
            File.ReadAllBytes(origin).SequenceEqual(originalBytes), "Sending changed the original or failed to reuse its GIF.");
        var apng = Path.Combine(data, "原始动画.png");
        File.WriteAllBytes(apng, Convert.FromBase64String(NativeApngTests.Fixture));
        Require((await sender.Prepare(apng, CancellationToken.None)).Path == apng, "Static send must not flatten an original APNG.");

        var wall = await command.Run(new("set_wallpaper", Value: origin), reload: true);
        Require(wall.Snapshot?.Wallpaper is { } && wall.Result.Error is null, "Static PNG wallpaper could not be copied without Python.");
        var asset = wall.Snapshot!.Wallpaper!;
        var pngCopy = File.ReadAllBytes(asset.Path);
        var newOrigin = Path.Combine(data, "替换.jpg");
        const int width = 9, height = 3;
        var jpegPixels = new byte[width * height * 4];
        for (int i = 0; i < jpegPixels.Length; i += 4)
        {
            jpegPixels[i + (i / 4 % width < 4 ? 2 : 0)] = 255;
            jpegPixels[i + 3] = 255;
        }
        var orientation = new BitmapMetadata("jpg");
        orientation.SetQuery("/app1/ifd/{ushort=274}", (ushort)6);
        Save(newOrigin, new JpegBitmapEncoder { QualityLevel = 100 }, width, height, jpegPixels, orientation);
        var replaced = await command.Run(new("set_wallpaper", Value: newOrigin), reload: true);
        var newAsset = replaced.Snapshot!.Wallpaper!;
        var bitmap = MediaThumbnail.Load(newAsset.Path, 64);
        Require(asset.Path == newAsset.Path && asset.Revision != newAsset.Revision && bitmap?.PixelWidth == height &&
            bitmap.PixelHeight == width && !File.ReadAllBytes(asset.Path).SequenceEqual(pngCopy) &&
            Directory.GetFiles(Path.GetDirectoryName(asset.Path)!).Length == 1, "Wallpaper replacement did not honor EXIF or remove the old copy.");
        var cleared = await command.Run(new("clear_wallpaper"), reload: true);
        Require(cleared.Snapshot!.Wallpaper is null && !File.Exists(asset.Path) && File.Exists(origin) && File.Exists(newOrigin),
            "Clearing a wallpaper must remove only the managed copy.");

    }

    private static void Save(string path, BitmapEncoder encoder, int width, int height, byte[] pixels, BitmapMetadata? metadata = null)
    {
        var picture = BitmapSource.Create(width, height, 96, 96, PixelFormats.Bgra32, null, pixels, width * 4);
        encoder.Frames.Add(BitmapFrame.Create(picture, null, metadata, null));
        using var file = File.Create(path);
        encoder.Save(file);
    }

    private static void Require(bool valid, string message)
    {
        if (!valid) throw new InvalidOperationException(message);
    }
}
