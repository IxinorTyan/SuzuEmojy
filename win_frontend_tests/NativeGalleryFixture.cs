using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Windows.Media;
using System.Windows.Media.Imaging;

internal static class NativeGalleryFixture
{
    // Preserve the historical transparent two-frame GIF and its 180 ms delays
    // for the fixed preview and similarity regression vectors.
    private const string GoldenGif = "R0lGODlhgACAAIEAAMl8hgAAACIiIgAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQJEgABACwAAAAAgACAAAAI/wADCBxIsKDBgwgTKlzIsKHDhxAjSpxIsaLFixgzatzIsaPHjyBDihxJsqTJkyhTqlzJsqXLlzBjypxJs6bNmxMB6NzJs6fPn0CDCh1KlOfKokiTKl1a9CTTp1CjEiUptarVqyGvat3K9CPXr2CHegxLtqzOjmbTht2otu1XjW7jYs0ot25Uunbzdr2ot29SjH4Di+UruLBPwIYTnyWs2DDixoUfQw4seXLfypbzYs5cdzPnuJ4/tw0tOi3p0mVPo17LeLVb1a65wo6tdTZtq7ZvS82tGyrv3nstAjf7e/jf1mAFKFd+dTlz4si5Ol9edfpzssWLWqcOdft1sNmHev/nvnT8d9nRm5t/al4A9vRW27Nfz1p4cvrl8b+FX11/Uvn1VUTWeFER+J59YW3X33TQIZigc+qRd6CAxgVIUYUW5oThfg5uOFeHHlYVXoiH8UfiUiOeuFOKKgLAooovnhgjiTOGWKOHN/50nlQ7ZihRWz0yFSR4Jm41pFJHogfig+q9VqSR7vEY5WhPaiVhflOqlWNQSQrVpZIUuvWlT2NutaWXZV5J5ZJpqaljmWBeaBeDZEKoWZUDtpelXWciaV1keLYYVJ/DEQqcob0hqpuitzFKm6OxQeqapKtRipqlpWEqmqafccqZp5mBapmok5EKGV6CBhdoqkahyupxrr44Ohhbss5Ka62DjoXrT1nt2qpIvjpV61Et4mTsscgmq+yyzDbr7LPQRivttNRWa+212Gar7bYSBQQAIfkECRIAAAAsAAAAAIAAgACBAAAAyXyGIiIiAAAACP8AAQgcSLCgwYMIEypcyLChw4cQI0qcSLGixYsYM2rcyLGjx48gQ4ocSbKkyZMoU6pcybKly5cwY8qcSbOmzZs4c+rcybOnz59AgwodSrSo0aNIkypVGqCp06dQo0qdSrWq1J9Ws2rdytXpzq5gw4rFKbas2a01z6pde1Um27dw3cKdezYm3btmX+LdO7Yl379cXQIebNUv4cNTWSJeDFUx48eOHy+OLPkw5cqDL2P+q3nz3s6e74IOPXc06bemT69NrbruytafX8MWLfusgNu32eLOvbp22d241QLn7Vql7eHEwyJP3tf47+XMt0KP3pU11ekCxGIvntLsduXTuaP/9B4ePHTxJ8mfN78cvcnjyNUPV2u9anz4u3s7x599LXC29c1WnW8CAkhggfQdiGBeCi7YXHcO6gdhhO6VRGGC+1344HgalhVgh401CGJhIo5I1YcmBoDidf3p1qKEHLooY1wlgkUdeKXV2NWNNr4IY3pw8Sidjz++F6SQVgWXY4YzakdkkRbSheRUU24IpJRVNqUkbUwe+WRUW3I54V3/gZkfXyv2+B1nOr51H2Fpghhnh3NqWOeFd1KYZ4R7Otjngn8iaFiKWQlGaKGDHnqioYq2pVejIcIEaaSSTqoiTZOSRehXJmKl51KghirqqKSWauqpqKaq6qqsturqq7DGEirrrLTWauutuOaq66689qpTQAA7";
    private const int Side = 128;

    internal static void Create(string path, int count, bool dark)
    {
        if (count is < 1 or > 100_000) throw new ArgumentOutOfRangeException(nameof(count));
        var root = Path.GetFullPath(path);
        if (Directory.Exists(root) || File.Exists(root)) throw new IOException("Choose a new fixture folder.");
        var data = Path.Combine(root, "data");
        var images = Directory.CreateDirectory(Path.Combine(data, "images")).FullName;
        var groups = new Dictionary<string, List<string>> { ["收藏"] = [], ["静态"] = [], ["动图"] = [] };
        var tags = new Dictionary<string, string>();
        var names = new string[count];
        byte[] gif = Convert.FromBase64String(GoldenGif);
        for (int index = 0; index < count; index++)
        {
            bool animated = index % 5 == 0;
            var name = names[index] = index.ToString("D4", CultureInfo.InvariantCulture) + (animated ? ".gif" : ".png");
            using var output = File.Create(Path.Combine(images, name));
            if (animated)
            {
                // A GIF comment makes each fixture file distinct without changing its frames.
                output.Write(gif, 0, gif.Length - 1);
                if (index != 0)
                {
                    byte[] label = Encoding.ASCII.GetBytes(name);
                    output.WriteByte(0x21);
                    output.WriteByte(0xFE);
                    output.WriteByte((byte)label.Length);
                    output.Write(label, 0, label.Length);
                    output.WriteByte(0);
                }
                output.WriteByte(0x3B);
            }
            else WritePng(output, index);
            groups[animated ? "动图" : "静态"].Add(name);
            if (index % 3 == 0) groups["收藏"].Add(name);
            if (index % 2 == 0) tags.Add(name, "可爱 happy");
        }
        Write("categories", groups);
        Write("metadata", tags);
        Write("recent", names.Take(8).ToArray());
        Write("config", new { appearance_mode = dark ? "dark" : "light", always_on_top = false });
        Console.WriteLine(data);

        void Write<T>(string name, T value) =>
            File.WriteAllText(Path.Combine(data, name + ".json"), JsonSerializer.Serialize(value));
    }

    private static void WritePng(Stream output, int index)
    {
        var pixels = new byte[Side * Side * 4];
        int inset = 12 + 4 * (index % 4);
        for (int y = inset; y < Side - inset; y++)
        for (int x = inset; x < Side - inset; x++)
        {
            int pixel = 4 * (y * Side + x);
            pixels[pixel] = (byte)index;
            pixels[pixel + 1] = (byte)(index >> 8);
            pixels[pixel + 2] = (byte)(100 + (index >> 16));
            pixels[pixel + 3] = 255;
        }
        var bitmap = BitmapSource.Create(Side, Side, 96, 96, PixelFormats.Bgra32, null, pixels, Side * 4);
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(bitmap));
        encoder.Save(output);
    }
}
