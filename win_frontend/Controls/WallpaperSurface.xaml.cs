using System;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Effects;
using System.Windows.Media.Imaging;

namespace SuzuEmojy.Windows.Controls;

public partial class WallpaperSurface : UserControl
{
    internal const string BlurKey = "wallpaper_blur";
    internal const string BrightnessKey = "wallpaper_brightness";
    internal const string OverlayOpacityKey = "wallpaper_overlay_opacity";
    internal const int MaxBlur = 40, MaxBrightness = 100, MaxOpacity = 100, DefaultOpacity = 20;
    private static readonly object loadGate = new();
    private static (WallpaperAsset Asset, WeakReference<Task<BitmapSource?>> Load)? sharedLoad;
    private WallpaperAsset? requested;
    private Task<BitmapSource?>? imageRequest;
    private int generation, displayedBlur, displayedBrightness, displayedOpacity;
    private bool released;
    private readonly BlurEffect blurEffect = new() { RenderingBias = RenderingBias.Performance };
    private readonly BitmapCache layerCache = new();
    internal event Action<bool>? ImageVisibilityChanged;
    internal bool HasImage => Texture.Background is not null;

    public static readonly DependencyProperty ClipRadiusProperty = DependencyProperty.Register(
        nameof(ClipRadius), typeof(double), typeof(WallpaperSurface),
        new PropertyMetadata(0d, (surface, _) => ((WallpaperSurface)surface).UpdateClip()));

    public double ClipRadius
    {
        get => (double)GetValue(ClipRadiusProperty);
        set => SetValue(ClipRadiusProperty, value);
    }

    public WallpaperSurface()
    {
        InitializeComponent();
        SizeChanged += (_, _) => UpdateClip();
    }

    private void UpdateClip() => Clip = ClipRadius > 0
        ? new RectangleGeometry(new Rect(RenderSize), ClipRadius, ClipRadius)
        : null;

    internal void ShowImage(WallpaperAsset? asset)
    {
        if (released || requested == asset) return;
        requested = asset;
        int version = ++generation;
        imageRequest = asset is null ? null : GetImage(asset);
        if (imageRequest is null) SetImage(null);
        else _ = FinishLoad(imageRequest, version);
    }

    // Keep a single decoded bitmap shared by the two surfaces, and let it go when neither uses it.
    private static Task<BitmapSource?> GetImage(WallpaperAsset asset)
    {
        lock (loadGate)
        {
            if (sharedLoad is { } cached && cached.Asset == asset && cached.Load.TryGetTarget(out var existing) &&
                !(existing.Status == TaskStatus.RanToCompletion && existing.Result is null)) return existing;
            var task = Task.Run(() => MediaThumbnail.Load(asset.Path, 2560));
            sharedLoad = (asset, new(task));
            return task;
        }
    }

    private async Task FinishLoad(Task<BitmapSource?> pending, int version)
    {
        var source = await pending;
        if (!released && version == generation) SetImage(source);
    }

    internal void ApplyTone(LibrarySnapshot library) => ApplyTone(
        library.Number(BlurKey, 0, 0, MaxBlur), library.Number(BrightnessKey, 0, -MaxBrightness, MaxBrightness),
        library.Number(OverlayOpacityKey, DefaultOpacity, 0, MaxOpacity));

    private void SetImage(ImageSource? source)
    {
        if (source is null) Texture.Background = null;
        else
        {
            var brush = new ImageBrush(source) { Stretch = Stretch.UniformToFill };
            brush.Freeze();
            Texture.Background = brush;
        }
        Visibility = source is null ? Visibility.Collapsed : Visibility.Visible;
        ImageVisibilityChanged?.Invoke(source is not null);
    }

    internal void ApplyTone(int blur, int brightness, int opacity)
    {
        blur = RuntimePrimitives.Clamp(blur, 0, MaxBlur);
        brightness = RuntimePrimitives.Clamp(brightness, -MaxBrightness, MaxBrightness);
        opacity = RuntimePrimitives.Clamp(opacity, 0, MaxOpacity);
        if (displayedBlur != blur)
        {
            displayedBlur = blur;
            blurEffect.Radius = blur;
            // Overscan just the wallpaper to keep the blur kernel off the visible edges.
            Texture.Margin = new Thickness(-blur);
            Texture.Effect = blur == 0 ? null : blurEffect;
            Texture.CacheMode = blur == 0 ? null : layerCache;
        }
        if (displayedBrightness != brightness)
        {
            displayedBrightness = brightness;
            BrightnessVeil.Background = brightness < 0 ? Brushes.Black : Brushes.White;
            BrightnessVeil.Opacity = Math.Abs(brightness) / (double)MaxBrightness;
            BrightnessVeil.Visibility = brightness == 0 ? Visibility.Collapsed : Visibility.Visible;
        }
        if (displayedOpacity != opacity)
        {
            displayedOpacity = opacity;
            Overlay.Opacity = opacity / (double)MaxOpacity;
            Overlay.Visibility = opacity == 0 ? Visibility.Collapsed : Visibility.Visible;
        }
    }

    internal void Release()
    {
        released = true;
        imageRequest = null;
        SetImage(null);
    }
}
