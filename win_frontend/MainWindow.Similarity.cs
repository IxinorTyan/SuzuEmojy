namespace SuzuEmojy.Windows;

public partial class MainWindow
{
    private void ShowSimilarity()
    {
        if (!OnGalleryPage || LibraryBusy || commands is null) return;
        var page = new SimilarityPage(library, MenuPalette, commands, snapshot => AcceptSnapshot(snapshot, null, updateSettings: false));
        page.HeadingChanged += (title, back) => { if (Pages.Page == page) Pages.SetCaption(title, back); };
        OpenPage(new("感知哈希去重", page, Leave: page.Leave,
            HandleBack: page.TryGoBack, Work: page.PageWork), similarityButton);
    }
}
