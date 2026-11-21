using System.Windows;
using System.Windows.Controls;
using SuzuEmojy.Windows.Controls;

namespace SuzuEmojy.Windows;

public partial class MainWindow
{
    internal bool OnGalleryPage => Pages.Page is null;

    private void OpenPage(PageRoute route, Button opener)
    {
        preview.Hide();
        if (Pages.Push(route, opener)) GalleryPage.Visibility = Visibility.Collapsed;
    }

    internal bool ShowGalleryPage()
    {
        if (!Pages.TryClose()) return false;
        GalleryPage.Visibility = Visibility.Visible;
        return true;
    }
}
