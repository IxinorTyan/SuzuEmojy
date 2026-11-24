using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using SuzuEmojy.Windows.Controls;

namespace SuzuEmojy.Windows;

public partial class SimilarityPage : UserControl
{
    private const int PageSize = 36;
    private const string PageTitle = "感知哈希去重";
    private readonly LibraryCommands commands;
    private readonly Action<LibrarySnapshot> publishLibrary;
    private readonly FluentChoice scope, kind;
    private readonly DispatcherTimer rematch = new() { Interval = TimeSpan.FromMilliseconds(180) };
    private readonly GifPlayback hoverPlayback, inspectorPlayback;
    private readonly HashSet<string> selected = new(StringComparer.OrdinalIgnoreCase);
    private readonly OperationScope work = new();
    internal OperationScope PageWork => work;
    private CancellationTokenSource? thumbnailCancellation, pictureCancellation;
    private Image? hovered;
    private Dictionary<string, Sticker> stickers;
    private SimilarityImage[][] groups = [];
    private Entry[] entries = [];
    private int page, pictureIndex;
    private bool hasScanned, left, settingMark;
    internal bool IsDeleting => work.BlocksLeave;
    internal bool IsScanning => work.IsBusy && !work.BlocksLeave;
    internal event Action<string, string>? HeadingChanged;

    private readonly record struct Entry(int Group, int Index, SimilarityImage Image);
    private int PageCount => Math.Max(1, (entries.Length + PageSize - 1) / PageSize);
    private bool Reviewing => Inspector.Visibility == Visibility.Visible;
    private bool Confirming => DeleteConfirmation.Visibility == Visibility.Visible;
    private bool Busy => IsScanning || IsDeleting;

    internal SimilarityPage(LibrarySnapshot library, FluentMenuPalette menuPalette, LibraryCommands commands, Action<LibrarySnapshot> publishLibrary)
    {
        this.commands = commands;
        hoverPlayback = new(commands);
        inspectorPlayback = new(commands);
        this.publishLibrary = publishLibrary;
        stickers = ByPath(library);
        InitializeComponent();
        var choiceStyle = (Style)FindResource("FluentChoiceButton");
        scope = new FluentChoice(menuPalette, choiceStyle, "", new[] { new FluentChoiceOption("", "全部资源", CategoryIcon.All) }
            .Concat(library.Categories.Select(FluentChoiceOption.ForCategory)));
        kind = new FluentChoice(menuPalette, choiceStyle, "all", [("all", "全部图片"), ("static", "仅静态图"), ("animated", "仅动图")]);
        AddChoice(scope, "扫描范围");
        AddChoice(kind, "图片类型");
        void AddChoice(FluentChoice choice, string name)
        {
            choice.Margin = new Thickness(0, 0, 6, 5);
            System.Windows.Automation.AutomationProperties.SetName(choice, name);
            Options.Children.Insert(Options.Children.Count - 2, choice);
            choice.ValueChanged += InvalidateScan;
        }
        Scan.Click += async (_, _) => await ScanResults(rematching: false);
        CancelScan.Click += (_, _) => CancelActiveScan();
        rematch.Tick += async (_, _) => await ScanResults(rematching: true);
        Threshold.ValueChanged += (_, _) =>
        {
            ThresholdValue.Text = $"{Threshold.Value:0}";
            rematch.Stop();
            if (hasScanned && Mouse.LeftButton != MouseButtonState.Pressed) rematch.Start();
        };
        Threshold.PreviewMouseLeftButtonUp += (_, _) => { if (hasScanned) rematch.Start(); };
        PreviousPage.Click += (_, _) => MovePage(-1);
        NextPage.Click += (_, _) => MovePage(1);
        ClearSelection.Click += (_, _) => { selected.Clear(); RenderPage(resetScroll: false); };
        DeleteSelection.Click += (_, _) => ShowConfirmation();
        CancelDelete.Click += (_, _) => SetConfirmation(false);
        ConfirmDelete.Click += async (_, _) => await RemoveSelected();
        PreviousPicture.Click += (_, _) => MovePicture(-1);
        NextPicture.Click += (_, _) => MovePicture(1);
        MarkPicture.Checked += (_, _) => SelectPicture(true);
        MarkPicture.Unchecked += (_, _) => SelectPicture(false);
        PreviewKeyDown += InspectorKeys;
        IsVisibleChanged += (_, _) =>
        {
            if (IsVisible && Reviewing) PlayLargePicture();
            if (!IsVisible) { StopHover(); inspectorPlayback.Stop(); }
        };
        RenderPage();
    }

    private static Dictionary<string, Sticker> ByPath(LibrarySnapshot library) =>
        library.Images.ToDictionary(item => item.Path, StringComparer.OrdinalIgnoreCase);

    internal bool TryGoBack()
    {
        if (Confirming) { SetConfirmation(false); return true; }
        if (!Reviewing) return false;
        CancelPicture();
        Inspector.Visibility = Visibility.Collapsed;
        Overview.Visibility = Visibility.Visible;
        HeadingChanged?.Invoke(PageTitle, "返回主面板");
        page = pictureIndex / PageSize;
        RenderPage(reveal: pictureIndex);
        return true;
    }

    internal void Leave()
    {
        left = true;
        rematch.Stop();
        CancelThumbnails();
        CancelPicture();
    }

    private void InvalidateScan()
    {
        rematch.Stop();
        hasScanned = false;
        Scan.Content = "开始扫描";
        Status.ToolTip = null;
        Status.Text = "范围已变更，请点击开始扫描。";
        selected.Clear();
        ReplaceGroups([]);
    }

    private void CancelActiveScan()
    {
        if (!work.RequestCancel()) return;
        CancelScan.IsEnabled = false;
        Status.Text = "正在取消…";
    }

    internal async Task ScanResults(bool rematching)
    {
        rematch.Stop();
        if (Busy || left || Confirming || Reviewing || (rematching && !hasScanned)) return;
        using var operation = work.Begin(LeavePolicy.CancelOnLeave);
        hasScanned = false;
        selected.Clear();
        Status.ToolTip = null;
        Status.Text = rematching ? "正在重新匹配…" : "正在扫描…";
        Progress.Value = 0;
        ReplaceGroups([]);
        try
        {
            var response = await commands.Run(new("similarity", Category: scope.Value, Kind: kind.Value, Threshold: (int)Threshold.Value),
                reload: false, cancellation: operation.Token, progress: operation.Progress(SetProgress));
            operation.Token.ThrowIfCancellationRequested();
            if (left) return;
            var result = response.Result;
            ReplaceGroups(result.SimilarityGroups ?? []);
            hasScanned = true;
            Scan.Content = "重新扫描";
            Status.Text = $"扫描完成：{groups.Length} 组疑似相似图片 · {entries.Length} 张图片"
                + (result.Failed > 0 ? $" · {result.Failed} 个文件无法读取，已跳过" : "");
            Status.ToolTip = result.Warnings is { Length: > 0 } warnings ? string.Join(Environment.NewLine, warnings) : null;
        }
        catch (OperationCanceledException) when (operation.Token.IsCancellationRequested)
        {
            if (!left) Status.Text = "已取消，已完成的特征缓存可在下次扫描时复用。";
        }
        catch (Exception error)
        {
            if (!left) Status.Text = "扫描失败：" + DescribeError(error);
        }
        finally
        {
            operation.Dispose();
            if (!left) UpdateControls();
        }
    }

    private void SetProgress(LibraryProgress update)
    {
        var phase = update.Phase switch { "scan" => "扫描", "match" => "匹配", _ => "正在删除所选图片…" };
        Status.Text = $"{phase}：{update.Current} / {update.Total}";
        Progress.Value = update.Total > 0 ? 100.0 * update.Current / update.Total : 0;
    }

    private void ReplaceGroups(SimilarityImage[][] results)
    {
        groups = results;
        entries = groups.SelectMany((items, group) => items.Select((image, index) => new Entry(group, index, image))).ToArray();
        page = 0;
        RenderPage();
    }

    private void MovePage(int step)
    {
        if (Busy || Confirming) return;
        page = RuntimePrimitives.Clamp(page + step, 0, PageCount - 1);
        RenderPage();
    }

    private void UpdateControls()
    {
        scope.IsEnabled = kind.IsEnabled = Threshold.IsEnabled = Scan.IsEnabled = !Busy && !Confirming;
        ResultScroll.IsEnabled = !Busy && !Confirming;
        CancelScan.IsEnabled = work.CanCancel;
        Progress.Visibility = Busy ? Visibility.Visible : Visibility.Collapsed;
        PreviousPage.IsEnabled = !Busy && !Confirming && page > 0;
        NextPage.IsEnabled = !Busy && !Confirming && page + 1 < PageCount;
        ClearSelection.IsEnabled = DeleteSelection.IsEnabled = !Busy && !Confirming && selected.Count > 0;
        DeleteSelection.Content = $"删除所选（{selected.Count}）";
        PageNumber.Text = $"{page + 1} / {PageCount}";
    }

    private void ShowConfirmation()
    {
        if (Busy || selected.Count == 0) return;
        DeleteWarning.Text = "将从资源库及所有相关分类中永久删除所选图片，此操作不可恢复。"
            + $"\n已选择 {selected.Count} 张图片\n疑似相似不代表内容相同，请确认已逐张比较。";
        SetConfirmation(true);
    }

    private void SetConfirmation(bool open)
    {
        rematch.Stop();
        StopHover();
        DeleteConfirmation.Visibility = open ? Visibility.Visible : Visibility.Collapsed;
        UpdateControls();
        FocusIfActive(open ? CancelDelete : DeleteSelection);
    }

    private async Task RemoveSelected()
    {
        if (Busy || !Confirming || selected.Count == 0) return;
        var paths = selected.OrderBy(path => path, StringComparer.OrdinalIgnoreCase).ToArray();
        using var operation = work.Begin(LeavePolicy.FinishBeforeLeaving);
        SetConfirmation(false);
        Progress.Value = 0;
        Status.ToolTip = null;
        Status.Text = "正在删除所选图片…";
        bool refreshMatches = false;
        try
        {
            var response = await commands.Run(new("delete", paths), reload: true, progress: operation.Progress(SetProgress));
            if (response.Snapshot is { } snapshot)
            {
                stickers = ByPath(snapshot);
                publishLibrary(snapshot);
            }
            if (response.Result.Failed == 0) refreshMatches = true;
            else
            {
                RemoveMissingResults();
                Status.Text = $"删除失败：{response.Result.Failed} 张图片";
                if (response.Result.Warnings is { Length: > 0 } warnings) Status.ToolTip = string.Join(Environment.NewLine, warnings);
            }
        }
        catch (Exception error)
        {
            RemoveMissingResults();
            Status.Text = "删除失败：" + DescribeError(error);
        }
        finally
        {
            operation.Dispose();
            selected.Clear();
            UpdateControls();
        }
        if (refreshMatches) await ScanResults(rematching: true);
        else RenderPage();
    }

    private void RemoveMissingResults() => ReplaceGroups(groups.Select(group => group
        .Where(item => stickers.ContainsKey(item.Path) && File.Exists(item.Path)).ToArray()).Where(group => group.Length > 1).ToArray());

    private static string DescribeError(Exception error) =>
        error.Message.Split(['\n'], StringSplitOptions.RemoveEmptyEntries).LastOrDefault()?.Trim() ?? error.Message;

    private void FocusIfActive(Control target)
    {
        if (Window.GetWindow(this)?.IsActive == true) target.Focus();
    }

    private void RenderPage(bool resetScroll = true, int? reveal = null)
    {
        CancelThumbnails();
        Results.Children.Clear();
        var pictures = new List<(Image Image, TextBlock Missing, string Path)>();
        var start = page * PageSize;
        var end = Math.Min(start + PageSize, entries.Length);
        WrapPanel? cards = null;
        int group = -1;
        Button? revealButton = null;
        for (int i = start; i < end; i++)
        {
            var entry = entries[i];
            if (entry.Group != group)
            {
                group = entry.Group;
                string type = entry.Image.Animated ? "动图" : "静态图";
                Results.Children.Add(new TextBlock
                {
                    Text = $"相似组 {group + 1} · {type} · {groups[group].Length} 张" + (entry.Index > 0 ? " · 续" : ""),
                    FontSize = 16, FontWeight = FontWeights.SemiBold, Margin = new Thickness(4, 0, 0, 5)
                });
                cards = new WrapPanel { Margin = new Thickness(0, 0, 0, 7) };
                Results.Children.Add(cards);
            }
            var (card, button) = MakeCard(i, entry.Image, pictures);
            cards!.Children.Add(card);
            if (i == reveal) revealButton = button;
        }
        if (pictures.Count == 0) Results.Children.Add(new TextBlock { Text = "暂无相似图片", Margin = new Thickness(4) });
        else
        {
            thumbnailCancellation = new CancellationTokenSource();
            _ = LoadThumbnails(pictures, thumbnailCancellation.Token);
        }
        if (resetScroll)
        {
            ResultScroll.CancelScrollMotion();
            ResultScroll.ScrollToTop();
        }
        if (revealButton is { } visible)
        {
            _ = Dispatcher.InvokeAsync(() =>
            {
                if (left || Reviewing) return;
                visible.BringIntoView();
                FocusIfActive(visible);
            }, DispatcherPriority.Loaded);
        }
        UpdateControls();
    }

    private (Border Card, Button Preview) MakeCard(int index, SimilarityImage item,
        List<(Image Image, TextBlock Missing, string Path)> pictures)
    {
        var card = new Border { Width = 180, Margin = new Thickness(0, 0, 7, 7), CornerRadius = new CornerRadius(8),
            BorderThickness = new Thickness(1), Padding = new Thickness(6), VerticalAlignment = VerticalAlignment.Top };
        card.SetResourceReference(Border.BackgroundProperty, "NoticeSurface");
        card.SetResourceReference(Border.BorderBrushProperty, "SettingCardEdge");
        var stack = new StackPanel();
        var image = new Image { Stretch = Stretch.Uniform };
        var missing = new TextBlock { Text = "无法预览", Visibility = Visibility.Collapsed,
            HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        var content = new Grid { Width = 164, Height = 132, Children = { image, missing } };
        var button = new Button { Content = content, Cursor = Cursors.Hand, ToolTip = Path.GetFileName(item.Path),
            Style = (Style)FindResource("FluentPushButton"), Padding = new Thickness(0), BorderThickness = new Thickness(0) };
        System.Windows.Automation.AutomationProperties.SetName(button, Path.GetFileName(item.Path));
        button.Click += (_, _) => OpenPicture(index);
        button.MouseEnter += (_, _) => PlayHover(image, item.Path);
        button.MouseLeave += (_, _) => { if (hovered == image) StopHover(); };
        stack.Children.Add(button);
        var text = new TextBlock { Text = Description(item), TextWrapping = TextWrapping.Wrap, FontSize = 11,
            Margin = new Thickness(1, 5, 1, 1) };
        text.SetResourceReference(TextBlock.ForegroundProperty, "Hint");
        stack.Children.Add(text);
        var mark = new CheckBox { Content = "选择删除", Style = (Style)FindResource("FluentCheckBox"), IsChecked = selected.Contains(item.Path) };
        mark.Checked += (_, _) => Select(item.Path, true);
        mark.Unchecked += (_, _) => Select(item.Path, false);
        stack.Children.Add(mark);
        card.Child = stack;
        pictures.Add((image, missing, item.Path));
        return (card, button);
    }

    private async Task LoadThumbnails(List<(Image Image, TextBlock Missing, string Path)> pictures, CancellationToken token)
    {
        try
        {
            foreach (var (picture, missing, path) in pictures)
            {
                var source = await Task.Run(() => MediaThumbnail.Load(path, 200), token);
                token.ThrowIfCancellationRequested();
                picture.Source = source;
                missing.Visibility = source is null ? Visibility.Visible : Visibility.Collapsed;
            }
        }
        catch (OperationCanceledException) { }
    }

    private string Description(SimilarityImage item)
    {
        var cats = stickers.GetValueOrDefault(item.Path)?.Categories ?? [];
        return $"{item.Width} × {item.Height}  ·  {item.Size / 1024.0:F1} KB" +
            (item.Animated ? $"  ·  {item.Duration / 1000.0:F2} s" : "") + "\n" + (cats.Length == 0 ? "无分类" : string.Join(", ", cats));
    }

    private void Select(string path, bool checkedForDelete)
    {
        if (checkedForDelete) selected.Add(path);
        else selected.Remove(path);
        UpdateControls();
    }

    private void SelectPicture(bool checkedForDelete)
    {
        if (Reviewing && !settingMark) Select(entries[pictureIndex].Image.Path, checkedForDelete);
    }

    private void OpenPicture(int index)
    {
        if (Busy || Confirming || index < 0 || index >= entries.Length) return;
        rematch.Stop();
        CancelThumbnails();
        Overview.Visibility = Visibility.Collapsed;
        Inspector.Visibility = Visibility.Visible;
        HeadingChanged?.Invoke("相似图片预览", "返回感知哈希去重");
        _ = DisplayPicture(index);
        FocusIfActive(MarkPicture);
    }

    private void MovePicture(int step)
    {
        if (!Reviewing) return;
        int next = RuntimePrimitives.Clamp(pictureIndex + step, 0, entries.Length - 1);
        if (next != pictureIndex) _ = DisplayPicture(next);
    }

    private async Task DisplayPicture(int index)
    {
        CancelPicture();
        pictureIndex = index;
        var entry = entries[index];
        var item = entry.Image;
        settingMark = true;
        MarkPicture.IsChecked = selected.Contains(item.Path);
        settingMark = false;
        PreviousPicture.IsEnabled = index > 0;
        NextPicture.IsEnabled = index + 1 < entries.Length;
        PictureDetails.Text = $"{index + 1} / {entries.Length}  ·  相似组 {entry.Group + 1}  ·  "
            + $"{entry.Index + 1} / {groups[entry.Group].Length}  ·  {Path.GetFileName(item.Path)}\n" + Description(item);
        MissingLarge.Visibility = Visibility.Collapsed;
        var operation = new CancellationTokenSource();
        pictureCancellation = operation;
        var token = operation.Token;
        int pixels = PreviewPixels(680);
        try
        {
            var source = await Task.Run(() => MediaThumbnail.Load(item.Path, pixels), token);
            token.ThrowIfCancellationRequested();
            LargePicture.Source = source;
            if (IsVisible) PlayLargePicture();
            MissingLarge.Visibility = LargePicture.Source is null ? Visibility.Visible : Visibility.Collapsed;
        }
        catch (OperationCanceledException) { }
        finally
        {
            if (ReferenceEquals(pictureCancellation, operation)) { pictureCancellation = null; operation.Dispose(); }
        }
    }

    private void InspectorKeys(object sender, KeyEventArgs e)
    {
        if (!Reviewing || Keyboard.Modifiers != ModifierKeys.None) return;
        switch (e.Key)
        {
            case Key.Left or Key.Up: MovePicture(-1); break;
            case Key.Right or Key.Down: MovePicture(1); break;
            case Key.Delete:
                if (!e.IsRepeat) MarkPicture.IsChecked = MarkPicture.IsChecked != true;
                break;
            default: return;
        }
        e.Handled = true;
    }

    private int PreviewPixels(int size) => (int)Math.Ceiling(size * VisualTreeHelper.GetDpi(this).PixelsPerDip);

    private void PlayLargePicture()
    {
        if (Reviewing)
            inspectorPlayback.Play(LargePicture, entries[pictureIndex].Image.Path, PreviewPixels(680),
                () => MissingLarge.Visibility = Visibility.Collapsed);
    }

    private void PlayHover(Image picture, string path)
    {
        StopHover();
        if (hoverPlayback.Play(picture, path, PreviewPixels(164))) hovered = picture;
    }

    private void StopHover() { hoverPlayback.Stop(); hovered = null; }

    private void CancelThumbnails()
    {
        StopHover();
        thumbnailCancellation?.Cancel();
        thumbnailCancellation?.Dispose();
        thumbnailCancellation = null;
    }

    private void CancelPicture()
    {
        pictureCancellation?.Cancel();
        pictureCancellation?.Dispose();
        pictureCancellation = null;
        inspectorPlayback.Stop();
        LargePicture.Source = null;
    }
}
