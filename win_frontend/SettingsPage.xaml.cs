using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Threading;
using SuzuEmojy.Windows.Controls;
using SuzuEmojy.Windows.Desktop;

namespace SuzuEmojy.Windows;

public partial class SettingsPage : UserControl
{
    private const string HotkeyHint = "点击输入框并按下快捷键";
    private readonly Dictionary<string, object> pending = new();
    private readonly DispatcherTimer debounce = new() { Interval = TimeSpan.FromMilliseconds(250) };
    private readonly FluentMenuPalette menuPalette;
    private readonly List<(TextBox Number, Slider Rail)> ranges = new();
    private readonly List<SettingCard> wideCards = new();
    private readonly List<Action> commitInputs = new();
    private readonly Dictionary<string, TextBox> hotkeyFields = new();
    private readonly Button removeWallpaper;
    private bool discarded;
    internal StackPanel WallpaperOptions { get; } = new() { Visibility = Visibility.Collapsed };
    private StackPanel group = null!;
    internal event Action<Dictionary<string, object>>? ChangesRequested;
    internal event Action? ShortcutConflict;
    internal event Action<bool>? HotkeyRecordingChanged;
    internal event Action<bool>? WallpaperRequested;
    internal event Action<string, int>? WallpaperTonePreview;
    internal event Action<string>? StartupError;
    internal event Action<OperationProgress>? MaintenanceRequested;
    internal event Action? AboutRequested;
    internal bool IsRecordingHotkey => hotkeyFields.Values.Any(field => field.IsKeyboardFocusWithin);

    private const string Pin = "M3,10 L6,7 L6,3 L9,1 L15,7 L13,10 L9,10 L6,13 Z M4.5,11.5 L1,15";
    private const string Hide = "M1,3 L15,14 M2,7 Q8,1 14,8 Q12,12 8,12 Q4,12 1,8 Z";
    private const string Zoom = "M6,1 H2 V5 M10,1 H14 V5 M2,10 V14 H6 M14,10 V14 H10 M5,6 H11 V11 H5 Z";
    private const string Fit = "M1,3 H15 V13 H1 Z M6,5 H3 V8 M10,11 H13 V8";
    private const string Paint = "M2,2 H12 V6 H2 Z M12,4 H15 V8 H8 V10 M6,10 H10 V15 H6 Z";
    private const string PaletteIcon = "M8,1 C4,1 1,4 1,8 C1,13 5,15 8,15 C11,15 10,11 12,11 C15,11 16,8 14,5 C13,2 10,1 8,1 Z M5,6 H5.2 M8,4 H8.2 M11,6 H11.2";
    private const string Folder = "M1,5 V3 H6 L8,5 H15 V13 H1 Z";
    private const string Tile = "M2,2 H7 V7 H2 Z M9,2 H14 V7 H9 Z M2,9 H7 V14 H2 Z M9,9 H14 V14 H9 Z";
    private const string Picture = "M1,2 H15 V14 H1 Z M1,12 L5,8 8,11 11,6 15,11";
    private const string Send = "M1,7 L15,1 10,15 7,9 Z M7,9 L15,1";
    private const string Clock = "M8,1 A7,7 0 1 0 8.01,1 M8,4 V8 L11,9";
    private const string Terminal = "M1,2 H15 V14 H1 Z M4,5 L7,8 L4,11 M9,11 H12";
    private const string Font = "M1,14 L6,2 L11,14 M3,10 H9 M13,6 H16 M14.5,6 V14";

    internal SettingsPage(LibrarySnapshot library, FluentMenuPalette menuPalette, LoginStartup? startup = null)
    {
        this.menuPalette = menuPalette;
        InitializeComponent();
        debounce.Tick += (_, _) => Flush();
        SizeChanged += (_, _) => AdjustLayout();

        Heading("窗口设置");
        Flag(library, "always_on_top", "主窗口始终置顶", "让表情包管理器始终显示在其他窗口之上", Pin, true);
        Flag(library, "hide_main_after_paste", "粘贴后自动隐藏主面板", "点击表情复制并尝试粘贴后隐藏主面板，仍可通过快捷键或托盘呼出", Hide, false);
        if (startup is not null) StartupSwitch(startup);
        Range(library, "preview_size", "预览浮窗大小", "设置悬停时弹出的大图的像素尺寸", Zoom, 320, 80, 800, 10);
        Range(library, "quick_panel_width", "快速面板宽度", "设置快速表情调用面板的宽度（像素）", Fit, 360, 250, 800, 10);
        Range(library, "quick_panel_height", "快速面板高度", "设置快速表情调用面板的高度（像素）", Fit, 480, 150, 1000, 10);

        Heading("个性化");
        var mode = Choice(library, "appearance_mode", library.String("theme_mode", "system"),
            [("system", "跟随系统"), ("light", "浅色模式"), ("dark", "深色模式")]);
        Card("外观模式", "更改软件的深浅色模式", Paint, mode);
        var themes = library.Themes.Select(item => (item.Key, item.Value.Name)).ToArray();
        var palettes = new WrapPanel { HorizontalAlignment = HorizontalAlignment.Right, MaxWidth = 305 };
        foreach (var (key, label) in new[] { ("light_theme_key", "浅色模式"), ("dark_theme_key", "深色模式") })
        {
            var choice = Choice(library, key, "white", themes);
            System.Windows.Automation.AutomationProperties.SetName(choice, label);
            var column = new StackPanel { Margin = new Thickness(6, 0, 0, 3) };
            column.Children.Add(new TextBlock { Text = label, Margin = new Thickness(0, 0, 0, 3), FontSize = 11 });
            column.Children.Add(choice);
            palettes.Children.Add(column);
        }
        Card("主题颜色", "为浅色和深色模式分别设置强调色和背景色", PaletteIcon, palettes, wide: true);
        Flag(library, "use_system_font", "使用系统默认字体", null, Font, false);
        Flag(library, "disable_animations", "关闭动画", null, Hide, false);
        Range(library, "thumbnail_size", "缩略图大小", null, Zoom, 120, 40, 300, 10);
        Range(library, "sidebar_icon_size", "列表模式下侧边栏图标大小", "设置左侧分类列表图标的尺寸", Folder, 20, 12, 40, 1);
        Flag(library, "show_setting_button", "显示设置入口", "在主面板右上角显示快速进入设置的按钮", Paint, true);
        Flag(library, "show_multi_select_button", "显示多选入口", "在主面板右上角显示多选（批量选择）按钮", Tile, true);
        var chooseWallpaper = new Button { Content = "选择图片…", Style = (Style)FindResource("FluentPushButton") };
        removeWallpaper = new Button { Content = "移除", Style = chooseWallpaper.Style, Margin = new Thickness(7, 0, 0, 0) };
        chooseWallpaper.Click += (_, _) => WallpaperRequested?.Invoke(true);
        removeWallpaper.Click += (_, _) => WallpaperRequested?.Invoke(false);
        removeWallpaper.IsEnabled = library.Wallpaper is not null;
        Card("背景图片", null, Picture, new StackPanel
        {
            Orientation = Orientation.Horizontal, Children = { chooseWallpaper, removeWallpaper }
        }, wide: true);
        group.Children.Add(WallpaperOptions);
        Range(library, WallpaperSurface.BlurKey, "背景模糊度", null, Picture, 0, 0, WallpaperSurface.MaxBlur, 1,
            level => WallpaperTonePreview?.Invoke(WallpaperSurface.BlurKey, level), WallpaperOptions);
        Range(library, WallpaperSurface.BrightnessKey, "背景亮度", "负值调暗，正值调亮", Picture,
            0, -WallpaperSurface.MaxBrightness, WallpaperSurface.MaxBrightness, 5,
            level => WallpaperTonePreview?.Invoke(WallpaperSurface.BrightnessKey, level), WallpaperOptions);
        Range(library, WallpaperSurface.OverlayOpacityKey, "遮罩不透明度", null, Picture,
            WallpaperSurface.DefaultOpacity, 0, WallpaperSurface.MaxOpacity, 5,
            level => WallpaperTonePreview?.Invoke(WallpaperSurface.OverlayOpacityKey, level), WallpaperOptions);

        Heading("高级设置");
        Flag(library, "show_sidebar_tooltip", "图标模式悬浮提示", "在侧边栏折叠为仅图标模式时，鼠标悬停显示分类名称", Folder, true);
        Flag(library, "convert_static_to_gif", "发送时将静态图转为GIF",
            "复制或发送 PNG/JPG/WEBP 等静态图时自动转为 1 帧 GIF 格式，避免在 QQ 等聊天软件中显示为超大原图", Send, true);
        Range(library, "preview_delay", "悬停预览延迟", "设置鼠标悬停多久后弹出大图预览", Clock, 500, 0, 5000, 50);
        Range(library, "recent_limit", "最近使用记录上限", "设置快速面板中显示的最近使用表情数量", Clock, 30, 1, 300, 1);
        Hotkey(library, "global_hotkey", "唤醒快捷键", "设置全局唤醒和隐藏主面板的快捷键", "ctrl+shift+e");
        Hotkey(library, "quick_panel_hotkey", "快速面板快捷键", "设置全局唤醒快速表情调用面板的快捷键", "alt+2");

        var maintenance = new Button { Content = "维护…", Style = (Style)FindResource("FluentPushButton") };
        var progress = new OperationProgress { Margin = new Thickness(0, 6, 0, 0) };
        maintenance.Click += (_, _) => MaintenanceRequested?.Invoke(progress);
        Card("旧图库维护", "备份旧数据、迁移到 SQLite、清理失效记录并补全索引", Folder, maintenance);
        group.Children.Add(progress);

        Heading("关于软件");
        var about = new Button { Content = "打开", Style = (Style)FindResource("FluentPushButton") };
        about.Click += (_, _) => AboutRequested?.Invoke();
        Card("关于软件", null, Terminal, about);
    }

    private void StartupSwitch(LoginStartup startup)
    {
        var input = new CheckBox { Style = (Style)FindResource("FluentSwitch") };
        static bool Expected(Exception e) => e is System.IO.IOException or UnauthorizedAccessException or System.Security.SecurityException;
        void Refresh()
        {
            try { input.IsChecked = startup.IsEnabled; input.IsEnabled = true; input.ToolTip = null; }
            catch (Exception error) when (Expected(error)) { input.IsEnabled = false; input.ToolTip = error.Message; }
        }
        Refresh();
        input.Loaded += (_, _) => Refresh();
        input.Click += (_, _) =>
        {
            try { startup.SetEnabled(input.IsChecked == true); }
            catch (Exception error) when (Expected(error)) { StartupError?.Invoke(error.Message); }
            Refresh();
        };
        Card("开机自启动", "登录 Windows 后自动启动到托盘；移动程序后请重新开启此选项", Clock, input);
    }

    internal void SetWallpaperState(bool hasAsset, bool visible)
    {
        removeWallpaper.IsEnabled = hasAsset;
        WallpaperOptions.Visibility = visible ? Visibility.Visible : Visibility.Collapsed;
    }

    internal void SetHotkeyStatus(string? main, string? quick)
    {
        SetError("global_hotkey", main);
        SetError("quick_panel_hotkey", quick);
        void SetError(string key, string? error)
        {
            var field = hotkeyFields[key];
            field.ToolTip = error ?? HotkeyHint;
            System.Windows.Automation.AutomationProperties.SetHelpText(field, error ?? HotkeyHint);
            field.Tag = error is not null ? "True" : null;
        }
    }

    internal void CommitAndFlush()
    {
        foreach (var commit in commitInputs) commit();
        Flush();
    }

    internal void DiscardEdits()
    {
        // A replaced editor may still receive LostKeyboardFocus as it is detached.
        discarded = true;
        debounce.Stop();
        pending.Clear();
    }

    private void Flush()
    {
        debounce.Stop();
        if (pending.Count == 0) return;
        var changes = new Dictionary<string, object>(pending);
        pending.Clear();
        ChangesRequested?.Invoke(changes);
    }

    private void Queue(string key, object value)
    {
        if (discarded) return;
        pending[key] = value;
        debounce.Stop();
        debounce.Start();
    }

    private void Heading(string title)
    {
        group = new StackPanel { Margin = new Thickness(0, Groups.Children.Count == 0 ? 0 : 32, 0, 0) };
        var header = new TextBlock { Text = title, FontSize = 20, Margin = new Thickness(0, 0, 0, 10) };
        header.SetResourceReference(TextBlock.ForegroundProperty, "Ink");
        group.Children.Add(header);
        Groups.Children.Add(group);
    }

    private void Card(string title, string? details, string icon, FrameworkElement input, bool wide = false, Panel? container = null)
    {
        var card = new SettingCard(title, details ?? "", icon, input);
        (container ?? group).Children.Add(card);
        if (wide) wideCards.Add(card);
    }

    private void Flag(LibrarySnapshot library, string key, string label, string? details, string icon, bool fallback)
    {
        var input = new CheckBox { Style = (Style)FindResource("FluentSwitch"), IsChecked = library.Flag(key, fallback) };
        input.Checked += (_, _) => Queue(key, true);
        input.Unchecked += (_, _) => Queue(key, false);
        Card(label, details, icon, input);
    }

    private FluentChoice Choice(LibrarySnapshot library, string key, string fallback, (string Key, string Label)[] values)
    {
        var input = new FluentChoice(menuPalette, (Style)FindResource("FluentChoiceButton"), library.String(key, fallback), values);
        input.ValueChanged += () => Queue(key, input.Value);
        return input;
    }

    private void Range(LibrarySnapshot library, string key, string label, string? details, string icon,
        int fallback, int low, int high, int step, Action<int>? preview = null, Panel? container = null)
    {
        var value = library.Number(key, fallback, low, high);
        var number = Field(value.ToString(CultureInfo.InvariantCulture), 200);
        number.Padding = new Thickness(11, 0, 67, 0);
        var rail = new Slider { Style = (Style)FindResource("FluentSlider"), Width = 246, Margin = new Thickness(11, 0, 10, 0),
            Minimum = low, Maximum = high, TickFrequency = 1, IsSnapToTickEnabled = true, Value = value,
            LargeChange = step * 2, SmallChange = step };
        System.Windows.Automation.AutomationProperties.SetName(rail, label);
        System.Windows.Automation.AutomationProperties.SetName(number, label);
        void Set(int next)
        {
            next = RuntimePrimitives.Clamp(next, low, high);
            number.Text = next.ToString(CultureInfo.InvariantCulture);
            if (next == value) return;
            value = next;
            rail.Value = next;
            Queue(key, next);
        }
        void CommitText() => Set(int.TryParse(number.Text, out int next) ? next : value);
        commitInputs.Add(CommitText);
        number.LostKeyboardFocus += (_, _) => CommitText();
        number.PreviewKeyDown += (_, e) =>
        {
            if (e.Key == Key.Enter) { CommitText(); e.Handled = true; }
            if (e.Key is Key.Up or Key.Down) { Set(value + (e.Key == Key.Up ? step : -step)); e.Handled = true; }
        };
        rail.ValueChanged += (_, _) =>
        {
            int next = (int)rail.Value;
            number.Text = next.ToString(CultureInfo.InvariantCulture);
            if (!discarded) preview?.Invoke(next);
            if (Mouse.LeftButton != MouseButtonState.Pressed) Set(next);
        };
        rail.PreviewMouseLeftButtonUp += (_, _) => Set((int)rail.Value);
        rail.LostMouseCapture += (_, _) => Set((int)rail.Value);

        var spin = new Grid();
        spin.Children.Add(number);
        var arrows = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        void Arrow(string data, int direction)
        {
            var button = new Button { Style = (Style)FindResource("ToolButton"), Width = 34, Height = 28, Focusable = false };
            var path = new System.Windows.Shapes.Path { Data = System.Windows.Media.Geometry.Parse(data), StrokeThickness = 1 };
            path.SetResourceReference(System.Windows.Shapes.Shape.StrokeProperty, "Hint");
            button.Content = path;
            button.Click += (_, _) => Set(value + direction * step);
            arrows.Children.Add(button);
        }
        Arrow("M0,5 L5,0 10,5", 1);
        Arrow("M0,0 L5,5 10,0", -1);
        spin.Children.Add(arrows);
        var inputs = new StackPanel { Orientation = Orientation.Horizontal };
        inputs.Children.Add(spin);
        inputs.Children.Add(rail);
        ranges.Add((number, rail));
        Card(label, details, icon, inputs, container: container);
    }

    private void AdjustLayout()
    {
        foreach (var card in wideCards) card.SetCompact(ActualWidth < 540);
        foreach (var (number, rail) in ranges)
        {
            number.Width = RuntimePrimitives.Clamp(ActualWidth - 540, 122, 200);
            rail.Width = RuntimePrimitives.Clamp(ActualWidth - 614, 95, 246);
            rail.Visibility = ActualWidth >= 680 ? Visibility.Visible : Visibility.Collapsed;
        }
    }

    private TextBox Field(string text, double width) => new()
    {
        Style = (Style)FindResource("FluentField"), Text = text, Width = width
    };

    private void Hotkey(LibrarySnapshot library, string key, string label, string details, string fallback)
    {
        string value = library.String(key, fallback);
        var field = Field(value, 130);
        field.IsReadOnly = true;
        System.Windows.Automation.AutomationProperties.SetName(field, label);
        hotkeyFields[key] = field;
        void Commit()
        {
            string next = field.Text.Trim().ToLowerInvariant();
            if (next == value) return;
            if (next.Length != 0)
            {
                if (!HotkeyChord.TryParse(next, out var chord)) { field.Text = value; return; }
                if (hotkeyFields.Any(pair => pair.Key != key &&
                    HotkeyChord.TryParse(pair.Value.Text, out var other) && other == chord))
                {
                    field.Text = value;
                    ShortcutConflict?.Invoke();
                    return;
                }
            }
            field.Text = value = next;
            Queue(key, next);
        }
        var clear = new Button { Style = (Style)FindResource("FluentPushButton"), Content = "清除", Margin = new Thickness(8, 0, 0, 0) };
        commitInputs.Add(Commit);
        clear.Click += (_, _) => { field.Clear(); Commit(); };
        field.GotKeyboardFocus += (_, _) => HotkeyRecordingChanged?.Invoke(true);
        field.LostKeyboardFocus += (_, e) => HotkeyRecordingChanged?.Invoke(hotkeyFields.Values.Any(other => ReferenceEquals(other, e.NewFocus)));
        field.PreviewKeyDown += (_, e) =>
        {
            Key pressed = e.Key == Key.System ? e.SystemKey : e.Key;
            if (pressed is Key.Tab or Key.Escape && Keyboard.Modifiers == ModifierKeys.None) return;
            e.Handled = true;
            // WPF's ModifierKeys values match MOD_ALT/CONTROL/SHIFT/WIN.
            if (!e.IsRepeat && HotkeyChord.TryFormat((uint)Keyboard.Modifiers, (uint)KeyInterop.VirtualKeyFromKey(pressed), out var chord))
            {
                field.Text = chord;
                Commit();
            }
        };
        var inputs = new StackPanel { Orientation = Orientation.Horizontal, Children = { field, clear } };
        Card(label, details, Terminal, inputs, wide: true);
    }
}
