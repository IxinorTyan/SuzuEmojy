using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using SuzuEmojy.Windows.Controls;

namespace SuzuEmojy.Windows;

internal sealed record TelegramPackagePrompts(
    Func<string, string?> AskId, Func<string, bool> ConfirmOverwrite, Action<bool, string> ShowResult);

public partial class ExternalStickerPage : UserControl
{
    private readonly bool telegram;
    private readonly string prefix;
    private readonly FluentMenuPalette palette;
    private readonly Func<LibraryCommand, bool, CancellationToken, IProgress<LibraryProgress>?,
        Task<(LibraryResult Result, LibrarySnapshot? Snapshot)>> run;
    private readonly Action<LibrarySnapshot, string?> accept;
    private readonly Func<string, string?> chooseFolder;
    private readonly TelegramPackagePrompts? packagePrompts;
    private readonly HashSet<int> selected = [];
    private readonly List<Button> selectedActions = [];
    private readonly WrapPanel qqChoices = new();
    private readonly Button? scan;
    private readonly FluentChoice? format;
    private FluentChoice? account, kind, category;
    private readonly TextBox? sourceLink, apiProxy;
    private readonly PasswordBox? token;
    private readonly CheckBox? zip;
    private ExternalAccount[] sources = [];
    private ExternalSticker[] stickers = [];
    private string? directory, packName, packTitle, packUrl;
    private readonly OperationScope work = new();
    internal OperationScope PageWork => work;
    private bool left;
    private bool busy => work.IsBusy;
    internal bool IsWriting => work.BlocksLeave;

    internal ExternalStickerPage(bool telegram, FluentMenuPalette palette, LibraryCommands commands,
        Action<LibrarySnapshot, string?> accept, Func<string, string?> chooseFolder,
        IEnumerable<Category> categories, string? current, Func<string?>? askCategory = null,
        Action<LibraryCommand>? openBatch = null, TelegramPackagePrompts? packagePrompts = null)
        : this(telegram, palette, commands.Run, accept, chooseFolder, categories, current, askCategory, openBatch,
            packagePrompts) { }

    internal ExternalStickerPage(bool telegram, FluentMenuPalette palette,
        Func<LibraryCommand, bool, CancellationToken, IProgress<LibraryProgress>?,
            Task<(LibraryResult Result, LibrarySnapshot? Snapshot)>> run,
        Action<LibrarySnapshot, string?> accept, Func<string, string?> chooseFolder,
        IEnumerable<Category> categories, string? current, Func<string?>? askCategory = null,
        Action<LibraryCommand>? openBatch = null, TelegramPackagePrompts? packagePrompts = null)
    {
        this.telegram = telegram;
        prefix = telegram ? "tg" : "qq";
        this.palette = palette;
        this.run = run;
        this.accept = accept;
        this.chooseFolder = chooseFolder;
        this.packagePrompts = packagePrompts;
        InitializeComponent();
        masonry = new(Scroller, Tiles, item => item.Width > 0 && item.Height > 0 ? (double)item.Height / item.Width : 1, MakeTile);
        masonry.Attach();
        masonry.ViewportChanged += RequestVisible;
        PreviewTitle.Text = telegram ? "贴纸详细预览" : "表情详细预览";
        SizeChanged += (_, e) => UpdateInspectorLayout(e.NewSize.Width);

        if (telegram)
        {
            sourceLink = TextField("贴纸包链接或包名");
            sourceLink.MaxLength = 1024;
            token = new PasswordBox { Style = (Style)FindResource("FluentPasswordField"), Width = 172, MaxLength = 256 };
            Field(Options, "Bot Token", token);
            apiProxy = TextField("API 反代（可选，将接收 Bot Token）");
            apiProxy.MaxLength = 512;
            ButtonIn(Options, "解析贴纸包", () => _ = Parse(), primary: true);
            if (openBatch is not null)
                ButtonIn(Options, "批量打包", () =>
                {
                    if (!busy && !left) openBatch(Request("batch_package") with { Value = null });
                });
            sourceLink.TextChanged += (_, _) => ClearResults();
            apiProxy.TextChanged += (_, _) => ClearResults();
            token.PasswordChanged += (_, _) => ClearResults();
            format = Choice("auto", [("auto", "自动"), ("original", "原始格式"), ("png", "PNG"), ("gif", "GIF")]);
            Field(Options, "下载格式", format);
            zip = new CheckBox { Content = "打包为 ZIP 压缩文件", IsChecked = true, VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(0, 17, 8, 8), Style = (Style)FindResource("FluentCheckBox") };
            Options.Children.Add(zip);
            selectedActions.Add(ButtonIn(Actions, "下载所选", () => _ = Save(false, false), primary: true));
            ButtonIn(Actions, "下载全部", () => _ = Save(true, false));
            selectedActions.Add(ButtonIn(Actions, "入库所选", () => _ = Save(false, true)));
            ButtonIn(Actions, "入库全部", () => _ = Save(true, true));
            ButtonIn(Actions, "导出为资源包", () => _ = ExportTelegramPackage());
        }
        else
        {
            ButtonIn(Options, "选择 QQ 缓存目录…", () =>
            {
                if (chooseFolder("选择 QQ 缓存目录") is { } folder) _ = Discover(folder);
            });
            ButtonIn(Options, "自动检测", () => _ = Discover(null));
            Options.Children.Add(qqChoices);
            scan = ButtonIn(Options, "扫描", () => _ = Scan(), primary: true);
            scan.IsEnabled = false;
            category = new(palette, (Style)FindResource("FluentChoiceButton"), current ?? "", CategoryOptions(categories));
            Field(Actions, "导入到", category);
            if (askCategory is not null)
                ButtonIn(Actions, "新建分类", () =>
                {
                    if (!busy && !left && cleanup is null && askCategory() is { } name)
                        _ = CreateImportCategory(name);
                });
            selectedActions.Add(ButtonIn(Actions, "导入所选", () => _ = Save(false, true), primary: true));
            ButtonIn(Actions, "导入全部", () => _ = Save(true, true));
            selectedActions.Add(ButtonIn(Actions, "导出所选", () => _ = Save(false, false)));
            ButtonIn(Actions, "导出全部", () => _ = Save(true, false));
        }
        ButtonIn(Actions, "全选", () => Select(stickers.Select(item => item.Index)));
        ButtonIn(Actions, "清空选择", () => Select([]));
        if (!telegram)
        {
            cleanupButton = ButtonIn(Actions, "删除所选缓存…", BeginCleanup);
            selectedActions.Add(cleanupButton);
            CancelCleanup.Click += (_, _) => CloseCleanup();
            ConfirmCleanup.Click += async (_, _) => await RecycleConfirmed();
        }
        UpdateSelection();
    }

    private TextBox TextField(string label)
    {
        var field = new TextBox { Style = (Style)FindResource("FluentField"), Width = 225 };
        Field(Options, label, field);
        return field;
    }

    private static void Field(Panel panel, string caption, Control input)
    {
        var block = new TextBlock { Text = caption, Margin = new Thickness(0, 0, 0, 4) };
        block.SetResourceReference(TextBlock.ForegroundProperty, "Ink");
        AutomationProperties.SetName(input, caption);
        var field = new StackPanel { Margin = new Thickness(0, 0, 8, 8) };
        field.Children.Add(block);
        field.Children.Add(input);
        panel.Children.Add(field);
    }

    private Button ButtonIn(Panel panel, string label, Action invoke, bool primary = false)
    {
        var button = new Button { Style = (Style)FindResource(primary ? "FluentPrimaryButton" : "FluentPushButton"),
            Content = label, Margin = new Thickness(0, 0, 8, 8), VerticalAlignment = VerticalAlignment.Bottom };
        button.Click += (_, _) => invoke();
        panel.Children.Add(button);
        return button;
    }

    private FluentChoice Choice(string key, IEnumerable<(string Key, string Label)> options) =>
        new(palette, (Style)FindResource("FluentChoiceButton"), key, options);

    private static IEnumerable<FluentChoiceOption> CategoryOptions(IEnumerable<Category> categories) =>
        new[] { new FluentChoiceOption("", CategoryIcon.Unclassified.Name, CategoryIcon.Unclassified) }
            .Concat(categories.Select(FluentChoiceOption.ForCategory));

    internal async Task CreateImportCategory(string name)
    {
        if (telegram || busy || left || cleanup is not null) return;
        name = name.Trim();
        await Execute(new("create_category", Value: name), "正在新建分类…", LeavePolicy.FinishBeforeLeaving,
            _ => Status.Text = $"已新建分类：{name}", reload: true, target: name);
    }

    internal async Task Discover(string? folder)
    {
        if (telegram || busy || left || cleanup is not null) return;
        ClearResults();
        directory = null;
        sources = [];
        SetQQOptions();
        await Execute(new("qq_discover", Value: folder), "正在查找 QQ 缓存…", LeavePolicy.CancelOnLeave, result =>
        {
            directory = result.Destination;
            sources = result.Sources ?? [];
            SetQQOptions();
            Status.Text = sources.Length > 0 ? directory : "未找到 QQ 缓存";
        });
    }

    private void SetQQOptions()
    {
        qqChoices.Children.Clear();
        account = kind = null;
        scan!.IsEnabled = sources.Length > 0;
        if (sources.Length == 0) return;
        account = Choice(sources[0].Key, sources.Select(item => (item.Key, item.Key)));
        Field(qqChoices, "QQ 账号", account);
        account.ValueChanged += SetQQKinds;
        SetQQKinds();
    }

    private void SetQQKinds()
    {
        if (qqChoices.Children.Count == 2) qqChoices.Children.RemoveAt(1);
        var choices = sources.First(source => source.Key == account!.Value).Options;
        kind = Choice(choices[0].Key, choices.Select(item => (item.Key, item.Label)));
        Field(qqChoices, "缓存分类", kind);
        kind.ValueChanged += ResetQQResults;
        ResetQQResults();
    }

    private void ResetQQResults()
    {
        ClearResults();
        Status.Text = directory;
    }

    private LibraryCommand Request(string action, ExternalSticker[]? items = null) => telegram
        ? new($"tg_{action}", Value: packName, Items: items, BotToken: token!.Password, ApiProxy: apiProxy!.Text)
        : new($"qq_{action}", Value: directory, Items: items, Account: account?.Value, Kind: kind?.Value ?? "");

    internal async Task Scan()
    {
        if (busy || left || telegram || kind is null || cleanup is not null) return;
        ClearResults();
        await Execute(Request("scan"), "正在扫描 QQ 缓存…", LeavePolicy.CancelOnLeave, SetResults);
    }

    internal async Task Parse()
    {
        if (busy || left || !telegram) return;
        ClearResults();
        await Execute(Request("parse") with { Value = sourceLink!.Text }, "正在解析贴纸包…", LeavePolicy.CancelOnLeave, SetResults);
    }

    private void ClearResults()
    {
        stickers = [];
        packName = packTitle = packUrl = null;
        selected.Clear();
        ResetMedia();
        masonry.SetItems(stickers);
        Results.Visibility = Visibility.Collapsed;
        Status.Text = "";
    }

    private void SetResults(LibraryResult result)
    {
        packName = result.SourceName;
        packTitle = result.SourceTitle;
        packUrl = result.SourceUrl;
        stickers = result.Items ?? [];
        if (stickers.Length == 0)
        {
            Status.Text = telegram ? "贴纸包没有贴纸" : "未找到表情";
            return;
        }
        Results.Visibility = Visibility.Visible;
        masonry.SetItems(stickers);
        UpdateSelection();
    }

    private void Select(IEnumerable<int> indices)
    {
        selected.Clear();
        selected.UnionWith(indices);
        UpdateSelection();
    }

    private void UpdateSelection()
    {
        Summary.Text = $"{(string.IsNullOrEmpty(packTitle) ? "" : packTitle + " · ")}共 {stickers.Length} 张，已选 {selected.Count} 张";
        foreach (var action in selectedActions) action.IsEnabled = selected.Count > 0;
        foreach (var tile in masonry.Realized.Values.OfType<StickerResultTile>())
            tile.SetSelected(selected.Contains(tile.Item.Index));
    }

    internal async Task ExportTelegramPackage()
    {
        if (!telegram || left || busy || stickers.Length == 0 || packName is null || packUrl is null) return;
        if (packagePrompts is null || packagePrompts.AskId(packTitle ?? packName) is not { } input) return;
        var id = input.Trim();
        if (id.Length is 0 or > 251 || id.EndsWith('.') || id.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 ||
            id.ToLowerInvariant() is "全部表情" or "未分类" or "新建分类" or "全部" or "最近" or "all" ||
            Regex.IsMatch(id.Split('.')[0].TrimEnd(), @"\A(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]|CONIN\$|CONOUT\$)\z",
                RegexOptions.IgnoreCase | RegexOptions.CultureInvariant))
        {
            Status.Text = "资源包 ID 不能作为收藏夹名称或 ZIP 文件名。";
            return;
        }
        if (chooseFolder("选择资源包保存目录") is not { } folder) return;
        var path = Path.Combine(folder, id + ".zip");
        bool overwrite = File.Exists(path);
        if (overwrite && !packagePrompts.ConfirmOverwrite(path)) return;
        bool exported = false;
        await Execute(Request("single_package", stickers) with
        {
            PackageId = id, Destination = folder, SourceUrl = packUrl, OverwritePackage = overwrite
        }, "正在导出资源包…", LeavePolicy.FinishBeforeLeaving, result =>
        {
            exported = true;
            var summary = $"处理成功 {result.Count + result.Skipped} 张，重复合并 {result.Skipped} 张，获取失败跳过 {result.Failed} 张。";
            Status.Text = string.Join("\n", new[] { $"ID: {id}", summary, result.Destination }
                .Concat(result.Warnings ?? []));
        }, reload: true, target: id);
        if (!left) packagePrompts.ShowResult(exported, Status.Text);
    }

    internal async Task Save(bool all, bool import)
    {
        if (left || busy || cleanup is not null) return;
        var items = all ? stickers : stickers.Where(item => selected.Contains(item.Index)).ToArray();
        if (items.Length == 0) return;
        string? destination = null;
        if (!import && (destination = chooseFolder(telegram ? "选择下载文件夹" : "选择导出文件夹")) is null) return;
        string? target = !import ? null : telegram ? $"TG_{packName}" : category?.Value;
        var command = telegram
            ? Request("download", items) with { Destination = destination, Kind = import ? "library" : "files",
                OutputFormat = import ? "auto" : format!.Value, ZipOutput = !import && zip!.IsChecked == true }
            : import
                ? Request("import") with { Category = target, Paths = items.Select(item => item.Id).ToArray() }
                : Request("export", items) with { Destination = destination };
        bool exporting = !import && !telegram;
        var activity = import ? "正在导入…" : exporting ? "正在导出…" : "正在下载…";
        var policy = exporting ? LeavePolicy.CancelAndWait : LeavePolicy.FinishBeforeLeaving;
        await Execute(command, activity, policy, result =>
        {
            string summary = import
                ? $"导入 {result.Count} 张，重复 {result.Skipped} 张，失败 {result.Failed} 张"
                : $"{(exporting ? "导出" : "下载")} {result.Count} 张，失败 {result.Failed} 张";
            if (result.Cancelled > 0) summary = "导出已取消，" + summary;
            Status.Text = string.Join("\n", new[] { summary, result.Destination }.Concat(result.Warnings ?? [])
                .Where(text => !string.IsNullOrEmpty(text)));
        }, reload: import, target: target);
    }

    private async Task Execute(LibraryCommand command, string activity, LeavePolicy policy, Action<LibraryResult> complete,
        bool reload = false, string? target = null)
    {
        bool canCancel = policy == LeavePolicy.CancelAndWait;
        using var operation = work.Begin(policy, canCancel ? Work.ShowCancelling : null);
        Options.IsEnabled = Results.IsEnabled = false;
        Work.Begin(activity, activity, canCancel ? () => work.RequestCancel() : null);
        try
        {
            var progress = operation.Progress(update =>
            {
                if (operation.Token.IsCancellationRequested) return;
                Work.Report(update.Phase switch
                {
                    "download" => update with { Detail = $"正在下载：{update.Current} / {update.Total}" },
                    "export" => update with { Detail = update.Total > 0
                        ? $"正在打包：{update.Current} / {update.Total}" : "正在打包…" },
                    "qq_export" => update with { Detail = $"正在导出：{update.Current} / {update.Total}" },
                    "qq_recycle" => update with { Detail = $"正在清理：{update.Current} / {update.Total}" },
                    _ => update
                });
            });
            var (result, snapshot) = await run(command, reload, operation.Token, progress);
            if (left || !operation.AcceptsResult) return;
            if (snapshot is not null)
            {
                if (category is not null)
                    category.SetOptions(command.Action == "create_category" ? target! : category.Value,
                        CategoryOptions(snapshot.Categories));
                accept(snapshot, string.IsNullOrEmpty(target) ? null : target);
            }
            if (result.Error is { } error)
                Status.Text = string.Join("\n", new[] { error }.Concat(result.Warnings ?? []));
            else complete(result);
        }
        catch (OperationCanceledException) when (operation.Token.IsCancellationRequested)
        {
            // A cancellable write can be cancelled before it acquires the command gate.
            if (canCancel) complete(new(0, 0, 0, null, null, null, null, Cancelled: 1));
        }
        catch (Exception)
        {
            if (!left) Status.Text = command.Action == "create_category"
                ? "新建分类失败，请检查名称是否已存在或不合法。" : "操作失败，请重试";
        }
        finally
        {
            operation.Dispose();
            Options.IsEnabled = Results.IsEnabled = true;
            Work.Dismiss();
            if (!left) _ = LoadVisible();
        }
    }

    internal void Leave()
    {
        left = true;
        CloseCleanup(restoreFocus: false);
        ResetMedia();
        masonry.Dispose();
        token?.Clear();
    }
}
