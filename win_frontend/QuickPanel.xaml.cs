using System;
using System.Linq;
using System.Windows;
using System.Windows.Input;
using System.Windows.Threading;
using SuzuEmojy.Windows.Controls;

namespace SuzuEmojy.Windows;

public partial class QuickPanel : Window
{
    internal const string ThumbnailSizeKey = "quick_panel_thumbnail_size";
    private readonly QuickPanelResults results;
    private readonly VirtualGallery gallery;
    private readonly PreviewPopup preview;
    private readonly DispatcherTimer searchTimer;
    private PanelAppearance appearance;
    internal VirtualGallery Gallery => gallery;
    internal Size RequestedSize { get; private set; }
    internal event Action<Sticker>? StickerActivated;
    internal event Action<int>? ThumbnailSizeChanged;

    internal QuickPanel(LibrarySnapshot library, QuickPanelResults results, bool offscreen = false, LibraryCommands? commands = null)
    {
        this.results = results;
        InitializeComponent();
        FluentTheme.Apply(this, library);
        ApplySize(library);
        appearance = PanelAppearance.Read(library);
        preview = new PreviewPopup(this, library, commands);
        RefreshWallpaper(library);
        gallery = new VirtualGallery(GalleryScroll, Cards, ReadThumbnailSize(library), GalleryLayout.Quick,
            item => StickerActivated?.Invoke(item), preview.Schedule, preview.Hide, offscreen);
        gallery.SizeChanged += preview.Hide;
        gallery.ZoomChanged += size => ThumbnailSizeChanged?.Invoke(size);
        searchTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(150) };
        searchTimer.Tick += (_, _) => RefreshResults();
        Search.TextChanged += (_, _) =>
        {
            SearchHint.Visibility = Search.Text.Length == 0 ? Visibility.Visible : Visibility.Collapsed;
            ClearSearch.Visibility = Search.Text.Length == 0 ? Visibility.Collapsed : Visibility.Visible;
            searchTimer.Stop();
            searchTimer.Start();
        };
        ClearSearch.Click += (_, _) => { ResetSearch(); FocusSearch(); };
        SearchAction.Click += (_, _) => { RefreshResults(); FocusSearch(); };
        PreviewKeyDown += (_, e) =>
        {
            if (e.Key == Key.Escape) { Hide(); e.Handled = true; }
            else if (e.Key == Key.Enter && Search.IsKeyboardFocusWithin) { RefreshResults(); e.Handled = true; }
        };
        IsVisibleChanged += (_, _) =>
        {
            if (IsVisible) return;
            searchTimer.Stop();
            preview.Hide();
        };
        Closed += (_, _) => { searchTimer.Stop(); gallery.Dispose(); preview.Dispose(); Wallpaper.Release(); };
        ResetSearch();
    }

    internal void ResetSearch()
    {
        Search.Clear();
        RefreshResults();
    }

    internal void FocusSearch() => Search.Focus();

    internal void RefreshAppearance(LibrarySnapshot library)
    {
        var next = PanelAppearance.Read(library);
        if (next == appearance) return;
        appearance = next;
        FluentTheme.Apply(this, library);
        preview.Refresh(library);
        RefreshWallpaper(library);
    }

    internal static int ReadThumbnailSize(LibrarySnapshot library) => library.Number(ThumbnailSizeKey, 72, 40, 200);

    internal void RefreshFromLibrary(LibrarySnapshot library, int? effectiveThumbnailSize = null)
    {
        ApplySize(library);
        RefreshAppearance(library);
        gallery.SetSize(effectiveThumbnailSize ?? ReadThumbnailSize(library));
        RefreshResults(preserveIfUnchanged: true);
    }

    private void ApplySize(LibrarySnapshot library)
    {
        var size = new Size(library.Number("quick_panel_width", 360, 250, 800),
            library.Number("quick_panel_height", 480, 150, 1000));
        if (size == RequestedSize) return;
        RequestedSize = size;
        Width = size.Width;
        Height = size.Height;
    }

    private readonly record struct PanelAppearance(bool Dark, Colors Theme, WallpaperAsset? Wallpaper,
        int Blur, int Brightness, int Opacity, int PreviewSize, int PreviewDelay, bool SystemFont, bool DisableAnimations)
    {
        internal static PanelAppearance Read(LibrarySnapshot snapshot)
        {
            bool dark = snapshot.Dark;
            return new(dark, snapshot.Theme(dark), snapshot.Wallpaper,
                snapshot.Number(WallpaperSurface.BlurKey, 0, 0, WallpaperSurface.MaxBlur),
                snapshot.Number(WallpaperSurface.BrightnessKey, 0, -WallpaperSurface.MaxBrightness, WallpaperSurface.MaxBrightness),
                snapshot.Number(WallpaperSurface.OverlayOpacityKey, WallpaperSurface.DefaultOpacity, 0, WallpaperSurface.MaxOpacity),
                snapshot.Number("preview_size", 320, 80, 800), snapshot.Number("preview_delay", 500, 0, 5000),
                snapshot.Flag("use_system_font", false), snapshot.Flag("disable_animations", false));
        }
    }

    private void RefreshWallpaper(LibrarySnapshot library)
    {
        Wallpaper.ApplyTone(library);
        Wallpaper.ShowImage(library.Wallpaper);
    }

    private void RefreshResults(bool preserveIfUnchanged = false)
    {
        searchTimer.Stop();
        var items = results.Find(Search.Text);
        if (preserveIfUnchanged && items.Length == gallery.Items.Count &&
            items.Zip(gallery.Items, (first, second) => ReferenceEquals(first, second) ||
                (first with { Categories = second.Categories }) == second &&
                first.Categories.SequenceEqual(second.Categories)).All(equal => equal))
        {
            // The delayed search may still have changed the empty-state label.
            UpdateEmpty(items.Length);
            return;
        }
        preview.Hide();
        gallery.SetItems(items);
        UpdateEmpty(items.Length);
    }

    private void UpdateEmpty(int count)
    {
        Empty.Text = string.IsNullOrWhiteSpace(Search.Text) ? "暂无最近使用记录" : "没有找到匹配的表情";
        Empty.Visibility = count == 0 ? Visibility.Visible : Visibility.Collapsed;
    }
}
