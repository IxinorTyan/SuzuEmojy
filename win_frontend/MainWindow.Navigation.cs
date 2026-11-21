using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using SuzuEmojy.Windows.Controls;

namespace SuzuEmojy.Windows;

public partial class MainWindow
{
    internal bool OnGalleryPage => Pages.Page is null;
    private PageMotion pageTransition = null!;

    private void SetupPageTransitions()
    {
        pageTransition = new(GalleryPage.Parent as FrameworkElement ?? this, PreviousPage);
        ImageSource? outgoing = null;
        FluentMotion.Observe(this, pageTransition.Reset);
        Pages.RootClosing += () => outgoing = pageTransition.Capture(Pages);
        Pages.RootClosed += () =>
        {
            GalleryPage.Visibility = Visibility.Visible;
            pageTransition.Swap(outgoing, GalleryPage);
            outgoing = null;
        };
    }

    private void OpenPage(PageRoute route, Button? opener = null)
    {
        preview.Hide();
        bool entering = OnGalleryPage;
        var outgoing = entering ? pageTransition.Capture(GalleryPage) : null;
        if (!Pages.Push(route, opener)) return;
        GalleryPage.Visibility = Visibility.Collapsed;
        if (entering) pageTransition.Swap(outgoing, Pages);
    }

    internal bool ShowGalleryPage()
    {
        if (!Pages.TryClose()) return false;
        GalleryPage.Visibility = Visibility.Visible;
        return true;
    }
}
