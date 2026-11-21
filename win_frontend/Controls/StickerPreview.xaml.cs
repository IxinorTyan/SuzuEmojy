using System.Windows.Controls;
using System.Windows.Media;

namespace SuzuEmojy.Windows.Controls;

public partial class StickerPreview : UserControl
{
    private const int FrameInset = 16;
    internal double PictureSide => Picture.Width;

    internal StickerPreview() => InitializeComponent();

    internal void Configure(LibrarySnapshot library, System.Windows.ResourceDictionary palette)
    {
        Width = library.Number("preview_size", 320, 80, 800);
        Picture.Width = Picture.Height = Width - FrameInset;
        // Popups render in a separate window; copy only the brushes this view uses.
        foreach (string key in new[] { "PreviewSurface", "PreviewEdge", "Ink" })
            Resources[key] = palette[key];
    }

    internal void ShowSticker(Sticker item, ImageSource source)
    {
        Categories.Text = "🗂️ 分类: " + (item.Categories.Length == 0 ? "无" : string.Join("、", item.Categories));
        Keywords.Text = "🏷️ 关键词: " + (string.IsNullOrWhiteSpace(item.Keywords) ? "无" : item.Keywords);
        Picture.Source = source;
    }
}
