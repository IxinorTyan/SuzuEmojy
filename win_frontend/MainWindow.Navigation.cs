using System.Windows;
using System.Windows.Controls;
using SuzuEmojy.Windows.Controls;

namespace SuzuEmojy.Windows;

public partial class MainWindow
{
    internal bool OnGalleryPage => Pages.Page is null;
    private PageMotion pageTransition = null!;

    private void SetupPageTransitions()
    {
        pageTransition = new(GalleryPage);
        Pages.RootClosed += () =>
        {
            GalleryPage.Visibility = Visibility.Visible;
            pageTransition.Show();
        };
    }

    private void OpenPage(PageRoute route, Button? opener = null)
    {
        preview.Hide();
        if (!Pages.Push(route, opener)) return;
        GalleryPage.Visibility = Visibility.Collapsed;
        pageTransition.Reset();
    }

    internal bool ShowGalleryPage()
    {
        if (!Pages.TryClose()) return false;
        GalleryPage.Visibility = Visibility.Visible;
        return true;
    }
}
