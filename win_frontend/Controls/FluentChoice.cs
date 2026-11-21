using System.Collections.Generic;
using System.Linq;
using System.Windows;
using System.Windows.Controls;

namespace SuzuEmojy.Windows.Controls;

internal sealed record FluentChoiceOption(string Key, string Label, Category? Category = null)
{
    internal static FluentChoiceOption ForCategory(Category category) => new(category.Name, category.Name, category);
}

// Shares the same popup and row styles as the gallery's filter menu.
internal sealed class FluentChoice : Button
{
    internal string Value { get; private set; }
    internal event System.Action? ValueChanged;
    private readonly FluentMenu menu;
    internal FluentChoice(FluentMenuPalette palette, Style style, string initial, IEnumerable<(string Key, string Label)> options)
        : this(palette, style, initial, options.Select(item => new FluentChoiceOption(item.Key, item.Label))) { }

    internal FluentChoice(FluentMenuPalette palette, Style style, string initial, IEnumerable<FluentChoiceOption> options)
    {
        Value = initial;
        Style = style;
        Width = 142;
        Height = 30;
        menu = new FluentMenu(palette) { PlacementTarget = this };
        ContextMenu = menu;
        Click += (_, _) => menu.IsOpen = true;
        SetOptions(initial, options);
    }

    internal void SetOptions(string initial, IEnumerable<FluentChoiceOption> options)
    {
        var choices = options.ToArray();
        var active = choices.FirstOrDefault(item => item.Key == initial) ?? choices[0];
        Value = active.Key;
        ShowValue(active);
        menu.ClearItems();
        var rows = new List<MenuItem>();
        foreach (var choice in choices)
        {
            var row = menu.AddItem(choice.Label, () =>
            {
                bool changed = Value != choice.Key;
                Value = choice.Key;
                ShowValue(choice);
                for (int index = 0; index < choices.Length; index++)
                    rows[index].IsChecked = choices[index].Key == Value;
                if (changed) ValueChanged?.Invoke();
            }, checkable: true, category: choice.Category);
            row.IsChecked = choice.Key == Value;
            rows.Add(row);
        }
    }

    private void ShowValue(FluentChoiceOption option) =>
        Content = option.Category is { } category
            ? CategoryIcon.Label(category, this, wrap: false, text: option.Label)
            : new TextBlock { Text = option.Label, VerticalAlignment = VerticalAlignment.Center,
                TextTrimming = TextTrimming.CharacterEllipsis };
}
