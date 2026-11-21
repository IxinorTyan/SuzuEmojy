using System;
using System.Buffers.Binary;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using SuzuEmojy.Windows;

internal static class NativeApngTests
{
    // Same cropped/split-chunk case as legacy test_default_cover_blend_and_both_disposal_modes,
    // plus nonzero RGB under transparent SOURCE. Expected pixels come from the old compositor.
    internal const string Fixture =
        "iVBORw0KGgoAAAANSUhEUgAAAAUAAAABCAYAAAAW/mTzAAAACGFjVEwAAAAEAAAAAHzNZtAAAAAPSURBVHicY/jPwPAfHQMAaKwJ9+xCUe4AAAAaZmNUTAAAAAAAAAACAAAAAQAAAAEAAAAAAGQD6AABpqEKMgAAAAhmZEFUAAAAAXicY2Bm8S+yAAAADmZkQVQAAAAC0DixAIQBC5kDIXi8l4UAAAAaZmNUTAAAAAMAAAABAAAAAQAAAAIAAAAAAGQD6AIB2MaxJQAAAAhmZEFUAAAABHicYzjLoXkEAAAADWZkQVQAAAAFEcBwAAAE1QHZlIEFfwAAABpmY1RMAAAABgAAAAEAAAABAAAABAAAAAAAZAPoAQD3dlN0AAAACGZkQVQAAAAHeJxjYOmx2hIAAAANZmRBVAAAAAj4z/AfAAQBAf8M0el+AAAAGmZjVEwAAAAJAAAAAQAAAAEAAAABAAAAAABkA+gAAOk6Ff8AAAAIZmRBVAAAAAp4nGNgESEeowAAAA1mZEFUAAAAC2RiZgAAABUAB7qnNdYAAAAASUVORK5CYII=";

    internal static async Task Run(string output)
    {
        var data = Path.Combine(output, $"原生 APNG-{Guid.NewGuid():N}");
        var path = Path.Combine(Directory.CreateDirectory(Path.Combine(data, "images")).FullName, "兼容性.png");
        var input = Convert.FromBase64String(Fixture);
        File.WriteAllBytes(path, input);
        var commands = new LibraryCommands(Path.Combine(data, "no-backend"), data);
        var result = await commands.Run(new("preview_animation", Paths: [path], Limit: 80), reload: false);
        var apng = result.Result.Animation!;
        var player = apng.Native!;
        uint clear = 0, blue = 0xa00028c8, over = 0xe8a64922, green = 0xff00ff00;
        uint[][] expected =
        [
            [clear, blue, blue, clear, clear],
            [clear, blue, over, clear, clear],
            [clear, blue, blue, clear, green],
            [clear, 0x00010203, blue, clear, clear]
        ];
        Require(apng.Frames is null && apng.Plays == 0 && player.HasMotion, "APNG loop or native result was lost.");
        foreach (var frame in expected)
        {
            var picture = (BitmapSource)player.Current;
            Require(picture.Format == PixelFormats.Bgra32 && picture.IsFrozen && picture.PixelWidth == 5 &&
                picture.PixelHeight == 1 && player.Delay.TotalMilliseconds == 100, "APNG shape/timing/format changed.");
            var bytes = new byte[20];
            picture.CopyPixels(bytes, 20, 0);
            var actual = Enumerable.Range(0, 5).Select(i => BitConverter.ToUInt32(bytes, 4 * i)).ToArray();
            Require(actual.SequenceEqual(frame), "APNG cover, source-over, source, previous, or background differs: " +
                string.Join(" ", actual.Select(value => value.ToString("X8"))));
            Require(player.Advance(), "Infinite animation ended prematurely.");
        }
        var small = await commands.Run(new("preview_animation", Paths: [path], Limit: 2), reload: false);
        Require(((BitmapSource)small.Result.Animation!.Native!.Current).PixelWidth == 2,
            "The retained APNG preview was not bounded by the requested resolution.");
        Require(File.ReadAllBytes(path).SequenceEqual(input), "The APNG original changed during preview.");
        if (LegacyAnimatedGif.Available) await CheckTelegram(input);

        // Break the final CRC; no partial result should be published.
        input[^1] ^= 1;
        File.WriteAllBytes(path, input);
        try { await commands.Run(new("preview_animation", Paths: [path]), reload: false); }
        catch (InvalidDataException) { return; }
        throw new InvalidOperationException("Corrupt APNG produced a valid preview.");
    }

    internal static async Task CheckTelegram(byte[]? fixture = null)
    {
        var input = fixture ?? Convert.FromBase64String(Fixture);
        foreach (var (rate, expected) in new[] { (60, new[] { 20, 10, 20, 20 }), (120, new[] { 20, 10 }) })
        {
            var raw = (byte[])input.Clone();
            var data = AtRate(raw, rate);
            var asset = new TelegramStickerClient.Asset(data, rate == 60 ? "apng" : "png");
            var original = await TelegramStickerOperations.ConvertAsset("", asset, "original", CancellationToken.None);
            Require(ReferenceEquals(asset, original), "TG must keep APNG bytes unchanged when original format is selected.");
            var converted = await TelegramStickerOperations.ConvertAsset("", asset, "gif", CancellationToken.None);
            Require(converted.Format == "gif" && ReadDelays(converted.Bytes).SequenceEqual(expected),
                "Telegram APNG must use cumulative GIF timing and preserve frames up to 60 FPS.");
            using var preview = new MemoryStream(converted.Bytes, writable: false);
            var frames = GifPreview.DecodeFrames(preview, 100, CancellationToken.None);
            Require(frames is { HasMotion: true } && frames.Delay.TotalMilliseconds == expected[0] &&
                frames.Advance() && frames.Delay.TotalMilliseconds == expected[1], "GIF preview must keep single-centisecond frames.");
            if (rate == 60)
            {
                Require(frames!.Advance() && AlphaAtRight((BitmapSource)frames.Current) == 255 && frames.Advance() &&
                    AlphaAtRight((BitmapSource)frames.Current) == 0,
                    "A transparent frame must remove earlier pixels, not leave an APNG-to-GIF trail.");
            }
        }
        var still = await TelegramStickerOperations.ConvertAsset("", new(input, "png"), "png", CancellationToken.None);
        using var bitmap = new MemoryStream(still.Bytes, writable: false);
        Require(still.Format == "png" && !ApngDecoder.HasAnimationControl(bitmap), "Explicit PNG output must extract a static frame.");

        static byte AlphaAtRight(BitmapSource picture)
        {
            var pixels = new byte[picture.PixelWidth * 4];
            picture.CopyPixels(pixels, pixels.Length, 0);
            return pixels[^1];
        }

        static byte[] AtRate(byte[] png, int rate)
        {
            for (int at = 8; at + 12 <= png.Length;)
            {
                int size = BinaryPrimitives.ReadInt32BigEndian(png.AsSpan(at, 4));
                var type = png.AsSpan(at + 4, 4);
                if (type.SequenceEqual("fcTL"u8))
                {
                    BinaryPrimitives.WriteUInt16BigEndian(png.AsSpan(at + 28, 2), 1);
                    BinaryPrimitives.WriteUInt16BigEndian(png.AsSpan(at + 30, 2), (ushort)rate);
                    BinaryPrimitives.WriteUInt32BigEndian(png.AsSpan(at + 8 + size, 4),
                        Crc32.Finish(Crc32.Update(Crc32.Start, png.AsSpan(at + 4, size + 4))));
                }
                at += size + 12;
            }
            return png;
        }

        static int[] ReadDelays(byte[] gif)
        {
            using var source = new MemoryStream(gif);
            var frames = new GifBitmapDecoder(source, BitmapCreateOptions.PreservePixelFormat,
                BitmapCacheOption.OnLoad).Frames;
            return frames.Select(frame => (ushort)((BitmapMetadata)frame.Metadata!).GetQuery("/grctlext/Delay")! * 10).ToArray();
        }
    }

    private static void Require(bool valid, string message)
    {
        if (!valid) throw new InvalidOperationException(message);
    }
}
