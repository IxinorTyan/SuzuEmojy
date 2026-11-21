using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;

namespace SuzuEmojy.Windows.Controls;

// Each popup imports the same palette, including popups outside the main visual tree.
internal sealed class FluentMenu : ContextMenu
{
    public static readonly DependencyProperty HeadingProperty = DependencyProperty.Register(
        nameof(Heading), typeof(FrameworkElement), typeof(FluentMenu));

    public FrameworkElement? Heading
    {
        get => (FrameworkElement?)GetValue(HeadingProperty);
        set => SetValue(HeadingProperty, value);
    }

    private static readonly ResourceDictionary Styles = new()
    {
        Source = new Uri("pack://application:,,,/SuzuEmojy.Windows;component/Controls/FluentMenu.xaml")
    };
    private readonly ItemGroup rows;

    internal FluentMenu(FluentMenuPalette palette)
    {
        Resources.MergedDictionaries.Add(palette.Resources);
        rows = new ItemGroup(this, Resources);
        Style = (Style)Styles["FluentMenu"];
        Placement = PlacementMode.Custom;
        CustomPopupPlacementCallback = (popup, target, _) =>
        [
            new(new Point(-12, target.Height - 8), PopupPrimaryAxis.Horizontal),
            new(new Point(-12, -popup.Height + 20), PopupPrimaryAxis.Horizontal)
        ];
    }

    internal void ClearItems()
    {
        Items.Clear();
        rows.ResetColumns();
    }

    internal MenuItem AddItem(string label, Action invoke, bool checkable = false, Category? category = null) =>
        rows.AddItem(label, invoke, checkable, category);

    internal MenuItem AddSubmenu(string label, Action<ItemGroup> populate) => rows.AddSubmenu(label, populate);

    internal sealed class ItemGroup
    {
        private readonly ItemsControl owner;
        private readonly ResourceDictionary columns;

        internal ItemGroup(ItemsControl owner, ResourceDictionary? rootResources = null)
        {
            this.owner = owner;
            columns = rootResources ?? new ResourceDictionary();
            ResetColumns();
        }

        internal void ResetColumns()
        {
            columns["FluentMenuMarkColumn"] = new GridLength(0);
            columns["FluentMenuIconColumn"] = new GridLength(0);
        }

        internal MenuItem AddSubmenu(string label, Action<ItemGroup> populate)
        {
            var header = AddRow(label, false, null);
            header.Style = (Style)Styles["FluentSubmenuHeader"];
            populate(new ItemGroup(header));
            header.IsEnabled = header.HasItems;
            return header;
        }

        internal MenuItem AddItem(string label, Action invoke, bool checkable = false, Category? category = null)
        {
            var item = AddRow(label, checkable, category);
            // Click is routed; a child command must not trigger an ancestor command.
            item.Click += (_, e) => { if (ReferenceEquals(e.OriginalSource, item)) invoke(); };
            return item;
        }

        private MenuItem AddRow(string label, bool checkable, Category? category)
        {
            if (checkable) columns["FluentMenuMarkColumn"] = new GridLength(24);
            var item = new MenuItem { Header = label, IsCheckable = checkable, Style = (Style)Styles["FluentMenuItem"] };
            if (owner is MenuItem) item.Resources.MergedDictionaries.Add(columns);
            if (category is not null)
            {
                columns["FluentMenuIconColumn"] = new GridLength(24);
                item.Icon = CategoryIcon.Small(category, item);
                item.HeaderTemplate = (DataTemplate)Styles["FluentMenuLiteralLabel"];
            }
            owner.Items.Add(item);
            return item;
        }
    }

    internal Separator AddSeparator()
    {
        var line = new Separator { Style = (Style)Styles["FluentMenuSeparator"] };
        Items.Add(line);
        return line;
    }
}
