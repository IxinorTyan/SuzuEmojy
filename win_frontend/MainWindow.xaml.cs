using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Shell;
using System.Windows.Threading;
using SuzuEmojy.Windows.Controls;

namespace SuzuEmojy.Windows;

public partial class MainWindow : Window
{
    private LibrarySnapshot library;
    private readonly PreferenceState preferences;
    private readonly LibraryCommands? commands;
    private readonly bool offscreen;
    private readonly Button filterButton, settingsButton, multiSelectButton, similarityButton, exchangeButton;
    private readonly VirtualGallery gallery;
    private readonly PreviewPopup preview;
    private readonly DispatcherTimer searchTimer;
    private readonly List<(Button Button, Category Category)> categoryButtons = new();
    private readonly HashSet<Sticker> selected = new();
    private readonly MenuItem[] filters;
    internal FluentMenuPalette MenuPalette { get; }
    internal bool AppliedDark { get; private set; }
    private Category? current;
    private string? anchor;
    private bool selecting, gridCategories;
    internal VirtualGallery Gallery => gallery;
    internal event Action<Sticker>? StickerActivated;

    internal MainWindow(LibrarySnapshot library, bool offscreen = false, LibraryCommands? commands = null)
    {
        this.library = library;
        preferences = new(library);
        MenuPalette = new FluentMenuPalette(library);
        AppliedDark = library.Dark;
        this.commands = commands;
        this.offscreen = offscreen;
        imports = new(RunImport, UpdateImportQueueCount);
        InitializeComponent();
        SetupPageTransitions();
        layoutSave.Tick += (_, _) => FlushLayoutSettings();
        FluentTheme.Apply(this, library);
        Wallpaper.ImageVisibilityChanged += UpdateWallpaperSettings;
        RefreshWallpaper();
        Topmost = library.Flag("always_on_top", true);
        gridCategories = library.Flag("sidebar_is_grid_mode", false);
        SetSidebarWidth();
        GalleryPage.SizeChanged += (_, e) => SidebarWidth.MaxWidth = RuntimePrimitives.Clamp(e.NewSize.Width - 104, 60, 400);
        LayoutUpdated += SidebarResized;
        SidebarScroll.PreviewMouseWheel += (_, e) =>
            e.Handled = ApplySidebarWheel(e.Delta, Keyboard.Modifiers, e.GetPosition(SidebarScroll));
        SidebarSplitter.DragCompleted += (_, e) => { if (!e.Canceled) SaveSidebarWidth(); };

        AddCaption("minimize", "最小化", () => WindowState = WindowState.Minimized);
        var max = AddCaption("maximize", "最大化", () =>
            WindowState = WindowState == WindowState.Maximized ? WindowState.Normal : WindowState.Maximized);
        var close = AddCaption("close", "关闭", Close);
        close.MouseEnter += (_, _) => { close.Foreground = Brushes.White; close.Background = FluentTheme.Brush("#C42B1C"); };
        close.MouseLeave += (_, _) => { close.SetResourceReference(ForegroundProperty, "Ink"); close.Background = Brushes.Transparent; };
        StateChanged += (_, _) => {
            max.Content = FluentGlyph.Create(WindowState == WindowState.Maximized ? "restore" : "maximize", 14, max);
        };

        multiSelectButton = AddTool("tiles", "批量选择", () => SetSelection(!selecting));
        multiSelectButton.Visibility =
            library.Flag("show_multi_select_button", true) ? Visibility.Visible : Visibility.Collapsed;
        filterButton = AddTool("filter", "筛选", null, active: true);
        similarityButton = AddTool("search", "感知哈希去重", commands is null ? null : ShowSimilarity);
        exchangeButton = AddTool("share", "导入导出", commands is null ? null : ShowExchange);
        settingsButton = AddTool("settings", "设置", commands is null ? null : ShowSettings);
        settingsButton.Visibility =
            library.Flag("show_setting_button", true) ? Visibility.Visible : Visibility.Collapsed;
        filters = CreateFilterMenu(filterButton);

        searchTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(300) };
        searchTimer.Tick += (_, _) => { searchTimer.Stop(); FilterGallery(); };
        SearchAction.Content = FluentGlyph.Create("search", 11, SearchAction);
        SearchAction.Click += (_, _) => {
            if (Search.Text.Length != 0) Search.Clear();
            else Search.Focus();
        };
        Search.TextChanged += (_, _) => {
            SearchHint.Visibility = Search.Text.Length == 0 ? Visibility.Visible : Visibility.Collapsed;
            SearchAction.Content = FluentGlyph.Create(Search.Text.Length == 0 ? "search" : "close", 11, SearchAction);
            searchTimer.Stop();
            searchTimer.Start();
        };
        Search.KeyDown += (_, e) => {
            if (e.Key == Key.Enter) { searchTimer.Stop(); FilterGallery(); e.Handled = true; }
        };
        preview = new PreviewPopup(this, library, commands);
        GalleryScroll.MiddleScrollStarted += preview.Hide;
        Caption.MouseEnter += (_, _) => GalleryScroll.StopMiddleScroll();
        gallery = new VirtualGallery(GalleryScroll, Cards, library.Number("thumbnail_size", 120, 40, 300), GalleryLayout.Main,
            ActivateSticker, (item, card) =>
            {
                if (!Refurbishing && !selecting) preview.Schedule(item, card);
            }, preview.Hide, offscreen, GalleryMotion);
        gallery.SizeChanged += preview.Hide;
        gallery.ZoomChanged += size => SetLayoutSetting("thumbnail_size", size);
        if (commands is not null) EnableLibraryActions();
        SelectAll.Click += (_, _) => { selected.UnionWith(gallery.Items); RefreshSelection(); };
        LeaveSelection.Click += (_, _) => SetSelection(false);
        PreviewKeyDown += (_, e) => {
            if (!OnGalleryPage) return;
            if (selecting && e.Key == Key.Escape) { SetSelection(false); e.Handled = true; }
            if (selecting && e.Key == Key.A && Keyboard.Modifiers == ModifierKeys.Control && !Search.IsKeyboardFocusWithin)
            {
                selected.UnionWith(gallery.Items);
                RefreshSelection();
                e.Handled = true;
            }
        };
        Closed += (_, _) =>
        {
            Pages.TryClose();
            pageTransition.Reset();
            searchTimer.Stop();
            layoutSave.Stop();
            GalleryNotice.Dismiss();
            preview.Dispose();
            gallery.Dispose();
            Wallpaper.Release();
        };
        BuildCategories();
        FilterGallery();
    }

    private MenuItem[] CreateFilterMenu(Button button)
    {
        var menu = new FluentMenu(MenuPalette) { PlacementTarget = button };
        var noKeywords = menu.AddItem("无关键词", FilterGallery, checkable: true);
        menu.AddSeparator();
        var unclassified = menu.AddItem("未分类", FilterGallery, checkable: true);
        menu.AddSeparator();
        var animated = menu.AddItem("动图", FilterGallery, checkable: true);
        var still = menu.AddItem("静态图片", FilterGallery, checkable: true);
        MenuItem[] options = [noKeywords, unclassified, animated, still];
        var clearSeparator = menu.AddSeparator();
        var clear = menu.AddItem("清除筛选", () =>
        {
            foreach (var item in options) item.IsChecked = false;
            FilterGallery();
        });
        void UpdateClear() => clear.Visibility = clearSeparator.Visibility =
            options.Any(item => item.IsChecked) ? Visibility.Visible : Visibility.Collapsed;
        foreach (var item in options)
        {
            item.Checked += (_, _) => UpdateClear();
            item.Unchecked += (_, _) => UpdateClear();
        }
        UpdateClear();
        button.ContextMenu = menu;
        button.Click += (_, _) => menu.IsOpen = true;
        return options;
    }

    private Button AddCaption(string icon, string label, Action callback)
    {
        var button = ButtonFor(icon, label, "CaptionButton", 14, callback);
        WindowChrome.SetIsHitTestVisibleInChrome(button, true);
        Caption.Children.Add(button);
        return button;
    }

    private Button AddTool(string icon, string label, Action? callback, bool active = false)
    {
        var button = ButtonFor(icon, label, "ToolButton", 16, callback);
        button.Margin = new Thickness(0, 0, 6, 0);
        button.IsEnabled = active || callback is not null;
        Toolbar.Children.Insert(Toolbar.Children.Count - 1, button);
        return button;
    }

    private Button ButtonFor(string icon, string label, string style, int size, Action? callback)
    {
        var button = new Button { Style = (Style)FindResource(style), ToolTip = label };
        System.Windows.Automation.AutomationProperties.SetName(button, label);
        button.Content = FluentGlyph.Create(icon, size, button);
        if (callback is not null) button.Click += (_, _) => callback();
        return button;
    }

    private string SidebarWidthKey => gridCategories ? "sidebar_width_grid" : "sidebar_width_list";
    private int PreferredSidebarWidth(LibrarySnapshot source) => source.Number(SidebarWidthKey, gridCategories ? 300 : 140, 60, 400);
    private static int CategoryGridSize(LibrarySnapshot source) => source.Number("category_grid_icon_size", 64, 20, 200);

    private void SetSidebarWidth()
    {
        SidebarWidth.Width = new GridLength(PreferredSidebarWidth(library));
        iconsOnly = gridCategories || SidebarWidth.Width.Value < 100;
    }

    private bool iconsOnly;
    private void SidebarResized(object? sender, EventArgs e)
    {
        var hide = gridCategories || SidebarWidth.ActualWidth is > 0 and < 100;
        if (hide != iconsOnly) { iconsOnly = hide; BuildCategories(); }
    }

    private void SaveSidebarWidth()
    {
        int width = RuntimePrimitives.Clamp((int)Math.Round(SidebarWidth.ActualWidth), 60, 400);
        if (!gridCategories && width < 140)
        {
            width = width < 100 ? 60 : 140;
            SidebarWidth.Width = new GridLength(width);
        }
        SetLayoutSetting(SidebarWidthKey, width);
    }

    internal bool ApplySidebarWheel(int delta, ModifierKeys modifiers, Point? pointer = null) => gridCategories &&
        WheelZoom.TryApply(delta, modifiers, CategoryGridSize(library), 20, 200, 10, size =>
        {
            SetLayoutSetting("category_grid_icon_size", size);
            ResizeCategoryIcons(size, pointer);
        });

    private void ResizeCategoryIcons(int size, Point? pointer)
    {
        if (Categories.Children.OfType<ResponsiveTilePanel>().SingleOrDefault() is not { } panel) return;
        panel.SetPreferredSize(size + 20, pointer);
        foreach (var (button, _) in categoryButtons)
        {
            if (button.Content is not Viewbox { Child: StackPanel row } box) continue;
            box.MaxWidth = box.MaxHeight = size;
            if (row.Children[0] is TextBlock emoji) emoji.FontSize = size - 2;
            else if (row.Children[0] is FrameworkElement glyph) glyph.Width = glyph.Height = size;
        }
    }

    private void ToggleCategoryGrid()
    {
        gridCategories = !gridCategories;
        SetLayoutSetting("sidebar_is_grid_mode", gridCategories);
        SetSidebarWidth();
        BuildCategories();
    }

    private void BuildCategories()
    {
        Categories.Children.Clear();
        categoryButtons.Clear();
        int size = gridCategories ? CategoryGridSize(library) : library.Number("sidebar_icon_size", 20, 12, 40);
        Panel target = Categories;
        if (gridCategories)
        {
            target = new ResponsiveTilePanel(size + 20, SidebarScroll);
            Categories.Children.Add(target);
        }
        foreach (var category in new[] { CategoryIcon.All }.Concat(library.Categories).Append(CategoryIcon.CreateCategory))
        {
            var button = new Button {
                Style = (Style)FindResource("FluentCategoryButton"), Height = gridCategories ? double.NaN : 52,
                Width = double.NaN, Margin = gridCategories ? new Thickness(3) : new Thickness(0, 2, 0, 2),
                HorizontalAlignment = HorizontalAlignment.Stretch, ToolTip = category.Name,
                IsEnabled = category.Name != "新建分类" || commands is not null
            };
            System.Windows.Automation.AutomationProperties.SetName(button, category.Name);
            ToolTipService.SetIsEnabled(button, iconsOnly && library.Flag("show_sidebar_tooltip", true));
            // Align rows to the left without interfering with the button hit region.
            var row = new StackPanel { Orientation = Orientation.Horizontal,
                HorizontalAlignment = iconsOnly ? HorizontalAlignment.Center : HorizontalAlignment.Left };
            if (!iconsOnly) row.Margin = new Thickness(11, 0, 0, 0);
            row.Children.Add(CategoryIcon.Create(category, size, button, gridCategories ? 200 : size));
            if (!iconsOnly)
            {
                var label = new TextBlock { Text = category.Name, FontSize = 12, Margin = new Thickness(6, 0, 0, 0),
                    VerticalAlignment = VerticalAlignment.Center, TextTrimming = TextTrimming.CharacterEllipsis };
                row.Children.Add(label);
                row.SetBinding(FrameworkElement.WidthProperty, new Binding(nameof(ActualWidth)) { Source = button });
            }
            button.Content = gridCategories ? new Viewbox
            {
                Child = row, StretchDirection = StretchDirection.DownOnly,
                MaxWidth = size, MaxHeight = size, Margin = new Thickness(5), IsHitTestVisible = false,
            } : row;
            button.Click += (_, _) => {
                if (category.Name == "新建分类") { CreateCategory(); return; }
                ShowCategory(category.Name == "全部表情" ? null : category, batch: false);
            };
            if (commands is not null && category.Name is not ("全部表情" or "新建分类"))
                ConfigureCategoryButton(button, category);
            if (category.Name == "全部表情")
                button.MouseDoubleClick += (_, e) =>
                {
                    if (e.ChangedButton != MouseButton.Left) return;
                    e.Handled = true;
                    ToggleCategoryGrid();
                };
            categoryButtons.Add((button, category));
            target.Children.Add(button);
        }
        MarkCategory();
    }

    private void RefreshCategoryToolTips()
    {
        bool enabled = iconsOnly && library.Flag("show_sidebar_tooltip", true);
        foreach (var (button, _) in categoryButtons) ToolTipService.SetIsEnabled(button, enabled);
    }

    private void MarkCategory()
    {
        foreach (var (button, category) in categoryButtons)
            Selector.SetIsSelected(button, (current?.Name ?? "全部表情") == category.Name);
    }

    private void ShowCategory(Category? category, bool batch)
    {
        bool changed = current?.Name != category?.Name;
        current = category;
        SetSelection(batch);
        MarkCategory();
        FilterGallery(categoryChange: changed);
    }

    private void FilterGallery() => FilterGallery(categoryChange: false);

    private void FilterGallery(bool categoryChange, bool preserveScroll = false)
    {
        var query = Search.Text.Trim();
        bool noTags = filters[0].IsChecked, unclassified = filters[1].IsChecked;
        bool animated = filters[2].IsChecked, still = filters[3].IsChecked;
        if (!preserveScroll)
        {
            GalleryScroll.StopMiddleScroll();
            preview.Hide();
        }
        gallery.SetItems(library.Images.Where(item =>
            (current is null || item.Categories.Contains(current.Name)) &&
            (query.Length == 0 || item.Name.IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0 ||
             item.Keywords.IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0) &&
            (!noTags || string.IsNullOrWhiteSpace(item.Keywords)) &&
            (!unclassified || item.Categories.Length == 0) &&
            (animated == still || item.Animated == animated)).ToArray(), categoryChange, preserveScroll);
        RefreshSelection();
    }

    private void SetSelection(bool value)
    {
        if (value) preview.Hide();
        selecting = value;
        selected.Clear();
        anchor = null;
        SelectionBar.Visibility = value ? Visibility.Visible : Visibility.Collapsed;
        RefreshSelection();
    }

    private void RefreshSelection()
    {
        SelectionCount.Text = $"已选择 {selected.Count} 项";
        SelectionActions.IsEnabled = selected.Count > 0;
        gallery.ApplySelection(selected);
    }

    private void ActivateSticker(Sticker item)
    {
        if (GalleryActionBusy || !OnGalleryPage) return;
        bool range = Keyboard.Modifiers.HasFlag(ModifierKeys.Shift);
        if (selecting || range || Keyboard.Modifiers.HasFlag(ModifierKeys.Control))
        {
            if (!selecting) SetSelection(true);
            var first = gallery.Items.FindIndex(image => image.Path == anchor);
            int last = gallery.Items.IndexOf(item);
            if (range && first >= 0 && last >= 0)
            {
                for (int i = Math.Min(first, last); i <= Math.Max(first, last); i++)
                    selected.Add(gallery.Items[i]);
            }
            else
            {
                if (!selected.Add(item)) selected.Remove(item);
                anchor = item.Path;
            }
            RefreshSelection();
            return;
        }
        StickerActivated?.Invoke(item);
    }
}
