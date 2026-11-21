using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;

namespace SuzuEmojy.Windows.Controls;

internal sealed partial class FluentDialog : Window
{
    private readonly Window parent;
    private UIElement? initialFocus;

    internal FluentDialog(Window owner, LibrarySnapshot library, string title, int width = 420, int maxHeight = 640)
    {
        InitializeComponent();
        parent = owner;
        Title = title;
        Width = width;
        MaxHeight = maxHeight;
        FontFamily = owner.FontFamily;
        FluentTheme.Apply(this, library);
        Dismiss.Click += (_, _) => Close();
        Loaded += (_, _) =>
        {
            initialFocus?.Focus();
            if (initialFocus is TextBox field) field.SelectAll();
        };
        PreviewKeyDown += (_, e) =>
        {
            if (e.Key != Key.Escape) return;
            e.Handled = true;
            Close();
        };
    }

    internal bool? ShowModal()
    {
        Owner = parent;
        return ShowDialog();
    }

    internal void Add(UIElement element) => Body.Children.Add(element);

    internal CheckBox AddCheckBox(string label, Thickness margin = default)
    {
        var box = new CheckBox { Content = new TextBlock { Text = label, TextWrapping = TextWrapping.Wrap },
            Style = (Style)FindResource("FluentCheckBox"), Margin = margin };
        Add(box);
        return box;
    }

    internal Button Button(string label, Action action, bool primary = false)
    {
        var button = new Button
        {
            Content = label, MinWidth = 82, Margin = new Thickness(Actions.Children.Count == 0 ? 0 : 8, 0, 0, 0),
            Style = (Style)FindResource(primary ? "FluentPrimaryButton" : "FluentPushButton"), IsDefault = primary
        };
        button.Click += (_, _) => action();
        Actions.Children.Add(button);
        if (primary) initialFocus ??= button;
        return button;
    }

    internal static FluentDialog CreateTextInput(Window owner, LibrarySnapshot library, string title, string label,
        string text, bool allowEmpty, out TextBox field, out Button confirm)
    {
        var dialog = new FluentDialog(owner, library, title, width: 340);
        var hint = new TextBlock { Text = label, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 6) };
        var input = new TextBox { Text = text, Style = (Style)dialog.FindResource("FluentField") };
        System.Windows.Automation.AutomationProperties.SetLabeledBy(input, hint);
        dialog.Add(hint);
        dialog.Add(input);
        dialog.initialFocus = input;
        var ok = dialog.Button("确定", () => dialog.DialogResult = true, primary: true);
        void Validate() => ok.IsEnabled = allowEmpty || TextValue(input).Length != 0;
        input.TextChanged += (_, _) => Validate();
        Validate();
        dialog.Button("取消", dialog.Close);
        field = input;
        confirm = ok;
        return dialog;
    }

    internal static string TextValue(TextBox field) => field.Text.Trim();

    internal static string? AskText(Window owner, LibrarySnapshot library, string title, string label,
        string text = "", bool allowEmpty = false)
    {
        var dialog = CreateTextInput(owner, library, title, label, text, allowEmpty, out var input, out _);
        return dialog.ShowModal() == true ? TextValue(input) : null;
    }

    internal static string[]? ChooseCategories(Window owner, LibrarySnapshot library, string title,
        IEnumerable<Category> categories, bool multiple)
    {
        var dialog = CreateCategoryPicker(owner, library, title, categories, multiple, out var selected);
        return dialog.ShowModal() == true ? selected() : null;
    }

    internal static FluentDialog CreateCategoryPicker(Window owner, LibrarySnapshot library, string title,
        IEnumerable<Category> categories, bool multiple, out Func<string[]> selected)
    {
        var dialog = new FluentDialog(owner, library, title);
        var options = new List<(Category Category, ToggleButton Toggle)>();
        foreach (var category in categories)
        {
            ToggleButton option = multiple ? new CheckBox() : new RadioButton { GroupName = "selection" };
            option.Style = (Style)dialog.FindResource(multiple ? "FluentCheckBox" : "FluentRadioButton");
            option.Margin = new Thickness(0, 3, 0, 3);
            option.Content = CategoryIcon.Label(category, option, wrap: true);
            AutomationProperties.SetName(option, category.Name);
            dialog.Add(option);
            options.Add((category, option));
        }
        var confirm = dialog.Button("确定", () => dialog.DialogResult = true, primary: true);
        void Validate(object? sender, RoutedEventArgs e) => confirm.IsEnabled = options.Any(option => option.Toggle.IsChecked == true);
        confirm.IsEnabled = false;
        foreach (var (_, option) in options) { option.Checked += Validate; option.Unchecked += Validate; }
        var cancel = dialog.Button("取消", dialog.Close);
        dialog.initialFocus = (UIElement?)options.FirstOrDefault().Toggle ?? cancel;
        selected = () => options.Where(option => option.Toggle.IsChecked == true).Select(option => option.Category.Name).ToArray();
        return dialog;
    }

    internal void AddMessage(string message, MessageBoxImage severity = MessageBoxImage.None)
    {
        var text = new TextBlock { Text = message, TextWrapping = TextWrapping.Wrap };
        if (severity is not (MessageBoxImage.Warning or MessageBoxImage.Error)) { Add(text); return; }
        var line = new DockPanel();
        var mark = new TextBlock { Text = severity == MessageBoxImage.Warning ? "⚠" : "⊗",
            FontSize = 17, Margin = new Thickness(0, 0, 10, 0), VerticalAlignment = VerticalAlignment.Top };
        mark.SetResourceReference(TextBlock.ForegroundProperty, "NoticeWarning");
        DockPanel.SetDock(mark, Dock.Left);
        line.Children.Add(mark);
        line.Children.Add(text);
        Add(line);
    }

    internal static void ShowMessage(Window owner, LibrarySnapshot library, string title, string message,
        MessageBoxImage severity = MessageBoxImage.None)
    {
        var dialog = new FluentDialog(owner, library, title);
        dialog.AddMessage(message, severity);
        dialog.Button("确定", dialog.Close, primary: true);
        dialog.ShowModal();
    }

    internal static bool Confirm(Window owner, LibrarySnapshot library, string title, string message)
    {
        var dialog = new FluentDialog(owner, library, title);
        dialog.AddMessage(message, MessageBoxImage.Warning);
        dialog.Button("是", () => dialog.DialogResult = true);
        dialog.Button("否", dialog.Close, primary: true);
        return dialog.ShowModal() == true;
    }

    internal static void ShowDetails(Window owner, LibrarySnapshot library, string title, string details)
    {
        var dialog = new FluentDialog(owner, library, title, width: 540);
        dialog.Add(new TextBox
        {
            Text = details, IsReadOnly = true, TextWrapping = TextWrapping.Wrap, Height = 220,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalContentAlignment = VerticalAlignment.Stretch,
            Style = (Style)dialog.FindResource("FluentField")
        });
        dialog.Button("关闭", dialog.Close, primary: true);
        dialog.ShowModal();
    }
}
