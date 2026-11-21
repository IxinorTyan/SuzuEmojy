using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media.Imaging;
using Microsoft.Win32;
using SuzuEmojy.Windows.Controls;
using Forms = System.Windows.Forms;

namespace SuzuEmojy.Windows;

public partial class MainWindow
{
    private readonly OperationScope galleryWork = new();
    private readonly OperationScope inboxWork = new();
    private readonly ImportQueue imports;
    private bool busy => galleryWork.IsBusy;
    internal bool LibraryBusy => busy || savingSettings || Pages.HasBlockingOperation;
    internal bool HasPendingWrite => LibraryBusy || inboxWork.IsBusy || imports.IsRunning;
    private bool CanQueueImport => commands is not null && (imports.IsRunning || !LibraryBusy);
    internal event Action<LibrarySnapshot>? LibraryChanged;

    private void EnableLibraryActions()
    {
        SelectionActions.Visibility = Visibility.Visible;
        SelectionActions.Click += (_, _) =>
        {
            var menu = CardMenu(selected.ToArray());
            menu.PlacementTarget = SelectionActions;
            menu.IsOpen = true;
        };
        gallery.ContextMenuFor = item =>
        {
            var menu = CardMenu(selecting && selected.Count != 0 ? selected.ToArray() : [item]);
            menu.Placement = PlacementMode.MousePoint;
            return menu;
        };
        gallery.ReorderRequested += (source, target) =>
        {
            if (busy || selecting) return;
            var ordered = gallery.Items.ToList();
            var index = ordered.IndexOf(target);
            ordered.Remove(source);
            ordered.Insert(index, source);
            Run(new("order", ordered.Select(item => item.Path).ToArray()), "表情排序");
        };
        ConfigureDrop(GalleryPage, () => current?.Name);
        PreviewKeyDown += (_, e) =>
        {
            if (e.Handled || !OnGalleryPage || Search.IsKeyboardFocusWithin) return;
            if (e.Key == Key.V && Keyboard.Modifiers == ModifierKeys.Control && CanQueueImport)
            {
                e.Handled = true;
                ImportClipboard();
            }
            else if (e.Key == Key.Delete && !LibraryBusy && selected.Count > 0)
            {
                e.Handled = true;
                Delete(selected.ToArray());
            }
        };
    }

    private async void Run(LibraryCommand command, string title, bool reload = true, string? nextCategory = null,
        Action<LibraryResult>? completed = null, (string Title, string Detail)? activity = null)
    {
        if (await RunAction(command, title, reload, nextCategory, activity: activity) is not { } result) return;
        ReportActionWarnings(result);
        completed?.Invoke(result);
    }

    private async Task<LibraryResult?> RunAction(LibraryCommand command, string title, bool reload = true,
        string? nextCategory = null, bool inlineErrors = false,
        (string Title, string Detail)? activity = null, bool background = false)
    {
        if (LibraryBusy || commands is null || (background && inboxWork.IsBusy)) return null;
        using var operation = (background ? inboxWork : galleryWork).Begin(LeavePolicy.FinishBeforeLeaving);
        if (!background)
        {
            preview.Hide();
            Root.IsEnabled = false;
        }
        IProgress<LibraryProgress>? progress = null;
        if (activity is { } display)
        {
            ActivityProgress.Begin(display.Title, display.Detail);
            progress = operation.Progress(ActivityProgress.Report);
        }
        else if (!background) BusyProgress.Visibility = Visibility.Visible;
        try
        {
            var response = await commands.Run(command, reload, progress: progress);
            operation.StopReporting();
            if (response.Snapshot is { } snapshot)
                AcceptSnapshot(snapshot, nextCategory, command.Action is "settings" or "set_wallpaper" or "clear_wallpaper",
                    preserveSelection: background);
            return response.Result;
        }
        catch (Exception error)
        {
            operation.StopReporting();
            var message = error.Message.Split('\n', StringSplitOptions.RemoveEmptyEntries).LastOrDefault()?.Trim();
            if (background) Trace.TraceWarning("{0}: {1}", title, message ?? error.Message);
            else
            {
                ActivityProgress.Dismiss();
                ReportIssue(title, message ?? error.Message, MessageBoxImage.Error, inlineErrors);
            }
            return null;
        }
        finally
        {
            if (!background)
            {
                ActivityProgress.Dismiss();
                Root.IsEnabled = true;
                BusyProgress.Visibility = Visibility.Collapsed;
            }
        }
    }

    private void ReportActionWarnings(LibraryResult result)
    {
        if (result.Warnings is { Length: > 0 } || result.Failed > 0)
            ReportIssue("部分文件处理失败", string.Join(Environment.NewLine, result.Warnings is { Length: > 0 } warnings
                ? warnings : [$"失败 {result.Failed} 项"]), MessageBoxImage.Warning, inline: false);
    }

    private void ReportIssue(string title, string message, MessageBoxImage severity, bool inline)
    {
        if (inline) GalleryNotice.Show(title, message, warning: true);
        else if (!offscreen) FluentDialog.ShowMessage(this, library, title, message, severity);
        else Trace.TraceWarning($"{title}: {message}");
    }

    internal void AcceptSnapshot(LibrarySnapshot snapshot, string? nextCategory, bool updateSettings, bool preserveSelection = false)
    {
        preferences.Accept(snapshot);
        ApplyPreferences(nextCategory, updateSettings, preserveSelection);
    }

    private void ApplyPreferences(string? nextCategory, bool updateSettings, bool preserveSelection = false)
    {
        var name = nextCategory ?? current?.Name;
        var previous = library;
        library = preferences.Effective;
        current = library.Categories.FirstOrDefault(category => category.Name == name);
        bool rebuildSidebar = !previous.Categories.SequenceEqual(library.Categories);
        if (updateSettings)
        {
            RefreshAppearance();
            Topmost = library.Flag("always_on_top", true);
            multiSelectButton.Visibility = library.Flag("show_multi_select_button", true) ? Visibility.Visible : Visibility.Collapsed;
            settingsButton.Visibility = library.Flag("show_setting_button", true) ? Visibility.Visible : Visibility.Collapsed;
            bool previousMode = gridCategories;
            int previousWidth = PreferredSidebarWidth(previous);
            gridCategories = library.Flag("sidebar_is_grid_mode", false);
            if (previousMode != gridCategories || previousWidth != PreferredSidebarWidth(library))
            {
                SetSidebarWidth();
                rebuildSidebar = true;
            }
            rebuildSidebar |= gridCategories ? CategoryGridSize(previous) != CategoryGridSize(library)
                : previous.Number("sidebar_icon_size", 20, 12, 40) != library.Number("sidebar_icon_size", 20, 12, 40);
            gallery.SetSize(library.Number("thumbnail_size", 120, 40, 300));
        }
        if (!updateSettings && !preserveSelection) SetSelection(false);
        else if (preserveSelection && selected.Count > 0)
        {
            var paths = selected.Select(item => item.Path).ToHashSet(StringComparer.OrdinalIgnoreCase);
            selected.Clear();
            selected.UnionWith(library.Images.Where(item => paths.Contains(item.Path)));
        }
        if (rebuildSidebar) BuildCategories();
        else MarkCategory();
        if (!updateSettings) FilterGallery(categoryChange: false, preserveScroll: preserveSelection);
        if (Pages.Page is ExchangePage exchange) exchange.SetCanExportSelected(library.Categories.Length > 0);
        LibraryChanged?.Invoke(preferences.Saved);
    }

    internal void RefreshAppearance()
    {
        AppliedDark = library.Dark;
        FluentTheme.Apply(this, library);
        RefreshWallpaper();
        MenuPalette.Apply(library);
        preview.Refresh(library);
    }

    private void ConfigureDrop(UIElement target, Func<string?> category)
    {
        bool acceptsWebLinks = target == GalleryPage;
        target.AllowDrop = true;
        target.DragOver += (_, e) =>
        {
            bool internalDrag = e.Data.GetDataPresent(VirtualGallery.InternalDragFormat);
            if (internalDrag ? LibraryBusy : !CanQueueImport) return;
            if (!e.Data.GetDataPresent(DataFormats.FileDrop) &&
                (!acceptsWebLinks || !ClipboardImageSource.MayContainDropLink(e.Data))) return;
            e.Effects = DragDropEffects.Copy;
            e.Handled = true;
        };
        target.Drop += (_, e) =>
        {
            if (e.Data.GetData(VirtualGallery.InternalDragFormat) is string own)
            {
                e.Handled = true;
                if (!LibraryBusy && !acceptsWebLinks && category() is { } destination)
                    Run(new("add", [own], Value: destination), "添加到分类...");
                return;
            }
            if (!CanQueueImport) return;
            var paths = e.Data.GetData(DataFormats.FileDrop) as string[];
            var links = acceptsWebLinks ? ClipboardImageSource.FindDropLinks(e.Data) : [];
            if (paths is not { Length: > 0 } && links.Length == 0) return;
            e.Handled = true;
            _ = ImportDrop(paths ?? [], links, category());
        };
    }

    internal Task ImportDrop(string[] paths, Uri[] links, string? category)
    {
        var tasks = new List<Task>(links.Length + 1) { ImportFiles(paths, category) };
        tasks.AddRange(links.Select(link => ImportLink(link, category)));
        return Task.WhenAll(tasks);
    }

    private void CreateCategory()
    {
        if (FluentDialog.AskText(this, library, "新建分类", "请输入分类名称：") is { } name)
            Run(new("create_category", Value: name), "新建分类");
    }

    private void ConfigureCategoryButton(Button button, Category category)
    {
        var name = category.Name;
        var menu = new FluentMenu(MenuPalette) { Placement = PlacementMode.MousePoint };
        var heading = CategoryIcon.Label(category, menu, wrap: false);
        heading.Margin = new Thickness(18, 2, 16, 6);
        System.Windows.Documents.TextElement.SetFontWeight(heading, FontWeights.SemiBold);
        menu.Heading = heading;
        menu.AddItem("批量选择", () =>
        {
            if (LibraryBusy || !OnGalleryPage) return;
            if (library.Categories.FirstOrDefault(value => value.Name == name) is { } target)
                ShowCategory(target, batch: true);
        });
        menu.AddSeparator();
        menu.AddItem("重命名", () =>
        {
            if (FluentDialog.AskText(this, library, "重命名", "请输入新的分类名称：", name) is { Length: > 0 } replacement &&
                replacement != name)
                Run(new("rename_category", Category: name, Value: replacement), "重命名",
                    nextCategory: current?.Name == name ? replacement : null);
        });
        menu.AddItem("自定义 Emoji 图标", () =>
        {
            if (FluentDialog.AskText(this, library, "自定义图标", "请输入一个 Emoji 表情：", category.Icon ?? "") is { Length: > 0 } icon)
                Run(new("category_icon", Category: name, Value: icon), "自定义 Emoji 图标");
        });
        menu.AddItem("恢复默认图标", () => Run(new("category_icon", Category: name), "恢复默认图标"));
        menu.AddSeparator();
        menu.AddItem("导出此分类...", () => ExportFiles(library.Images.Where(item => item.Categories.Contains(name)).ToArray()));
        menu.AddSeparator();
        menu.AddItem("删除文件夹", () =>
        {
            var dialog = new FluentDialog(this, library, "删除确认");
            dialog.AddMessage($"即将删除分类：'{name}'\n此操作不可逆。");
            var images = dialog.AddCheckBox("是否同时彻底删除该文件夹内所含的表情？", new Thickness(0, 12, 0, 0));
            dialog.Button("确定", () => dialog.DialogResult = true);
            dialog.Button("取消", dialog.Close, primary: true);
            if (dialog.ShowModal() == true)
                Run(new("remove_category", Category: name, DeleteImages: images.IsChecked == true), "删除文件夹");
        });
        button.ContextMenu = menu;
        ConfigureDrop(button, () => name);
        ConfigureCategoryOrder(button, name);
    }

    private const string CategoryDrag = "SuzuEmojy.Windows.Category";

    private void ConfigureCategoryOrder(Button button, string name)
    {
        Point? origin = null;
        button.PreviewMouseLeftButtonDown += (_, e) => origin = e.GetPosition(button);
        button.PreviewMouseLeftButtonUp += (_, _) => origin = null;
        button.PreviewMouseMove += (_, e) =>
        {
            if (busy || e.LeftButton != MouseButtonState.Pressed || origin is not { } start) return;
            var delta = e.GetPosition(button) - start;
            if (Math.Abs(delta.X) < SystemParameters.MinimumHorizontalDragDistance &&
                Math.Abs(delta.Y) < SystemParameters.MinimumVerticalDragDistance) return;
            origin = null;
            DragDrop.DoDragDrop(button, new DataObject(CategoryDrag, name), DragDropEffects.Move);
        };
        button.DragOver += (_, e) =>
        {
            if (busy || !e.Data.GetDataPresent(CategoryDrag)) return;
            e.Effects = DragDropEffects.Move;
            e.Handled = true;
        };
        button.Drop += (_, e) =>
        {
            if (busy || e.Data.GetData(CategoryDrag) is not string source || source == name) return;
            var order = library.Categories.Select(item => item.Name).ToList();
            var index = order.IndexOf(name);
            if (index < 0 || !order.Remove(source)) return;
            e.Handled = true;
            order.Insert(index, source);
            Run(new("category_order", Categories: order.ToArray()), "分类排序");
        };
    }

    private FluentMenu CardMenu(Sticker[] items)
    {
        preview.Hide();
        var menu = new FluentMenu(MenuPalette);
        if (items.Length == 0) return menu;
        var paths = items.Select(item => item.Path).ToArray();
        var source = current?.Name;
        var destinations = library.Categories.Where(item => item.Name != source).ToArray();
        void AddDestinations(string action, string title)
        {
            menu.AddSubmenu(title, submenu =>
            {
                foreach (var destination in destinations)
                    submenu.AddItem(destination.Name, () =>
                    {
                        if (OnGalleryPage && current?.Name == source)
                            Run(new(action, paths, source, destination.Name), title);
                    }, category: destination);
            });
        }
        AddDestinations("add", "添加到分类");
        if (source is { } category)
        {
            AddDestinations("move", "移动到分类");
            if (items.Length == 1)
                menu.AddItem($"设为 '{category}' 的分类图标",
                    () => Run(new("category_icon", paths, category), "设置分类图标"), category: current);
            menu.AddItem($"从分类 '{category}' 移出", () => Run(new("remove", paths, category), "从分类移出"), category: current);
        }
        menu.AddSeparator();
        void Tags(string action, string title, string label, string previous = "")
        {
            if (FluentDialog.AskText(this, library, title, label, previous, allowEmpty: true) is { } tags)
                Run(new(action, paths, Value: tags), title);
        }
        if (items.Length == 1 && !selecting)
        {
            menu.AddItem("设置/修改关键词...", () => Tags("tags", "设置关键词", "输入关键词 (多个词用空格隔开):", items[0].Keywords));
            menu.AddItem("批量选择...", () => { SetSelection(true); selected.Add(items[0]); RefreshSelection(); });
        }
        else
        {
            menu.AddItem("批量添加关键词...", () => Tags("add_tags", "批量添加关键词", "请输入关键词："));
            menu.AddItem("批量移除关键词...", () => Tags("remove_tags", "批量移除关键词", "请输入关键词："));
        }
        menu.AddSeparator();
        menu.AddItem(items.Length == 1 ? "彻底删除此表情" : "批量删除", () => Delete(items));
        menu.AddItem("导出...", () => ExportFiles(items));
        if (items.Length == 1)
        {
            menu.AddSeparator();
            menu.AddItem("在资源管理器中显示", () => Reveal(items[0].Path));
            menu.AddItem("复制文件路径", () =>
            {
                try { Clipboard.SetText(items[0].Path); }
                catch (System.Runtime.InteropServices.ExternalException) { FluentDialog.ShowMessage(this, library, "复制失败", "无法复制文件路径"); }
            });
        }
        return menu;
    }

    private void Reveal(string path)
    {
        try { Process.Start("explorer.exe", $"/select,\"{path}\""); }
        catch (System.ComponentModel.Win32Exception error) { FluentDialog.ShowMessage(this, library, "在资源管理器中显示", error.Message); }
    }

    private void Delete(Sticker[] items)
    {
        if (items.Length == 0) return;
        if (!FluentDialog.Confirm(this, library, "确认删除", $"确定要彻底删除这 {items.Length} 张表情吗？")) return;
        Run(new("delete", items.Select(item => item.Path).ToArray()), "确认删除");
    }

    private string? ChooseFolder(string title)
    {
        var picker = new OpenFolderDialog { Title = title };
        return picker.ShowDialog(this) == true ? picker.FolderName : null;
    }

    private void ExportFiles(Sticker[] items)
    {
        if (items.Length > 0 && ChooseFolder("选择导出文件夹") is { } folder)
            Run(new("export_files", items.Select(item => item.Path).ToArray(), Value: folder), "导出...", reload: false,
                completed: result => FluentDialog.ShowMessage(this, library, "导出完成", $"成功导出 {result.Count} 张表情"));
    }

    internal Task ImportFiles(string[] paths, string? category)
    {
        if (paths.Length == 0 || !CanQueueImport) return Task.CompletedTask;
        return imports.Enqueue(new(new("import", paths.ToArray(), category), paths.Any(Directory.Exists)));
    }

    internal Task ImportLink(Uri link, string? category) => CanQueueImport
        ? imports.Enqueue(new(new("import_link", Category: category, Value: link.AbsoluteUri))) : Task.CompletedTask;

    private void UpdateImportQueueCount(int waiting)
    {
        ActivityProgress.SetWaiting(waiting);
        if (waiting > 0 && ActivityProgress.Visibility != Visibility.Visible)
            GalleryNotice.Show("已加入导入队列", $"等待中的任务：{waiting}");
    }

    private async Task RunImport(ImportRequest request)
    {
        var writer = commands!;
        bool showProgress = request.IsBatch || request.IsLink;
        var title = request.IsLink ? "下载失败" : "导入失败";
        using var operation = galleryWork.Begin(LeavePolicy.CancelAndWait,
            showProgress ? ActivityProgress.ShowCancelling : null);
        if (request.IsLink)
            ActivityProgress.Begin("正在下载", "正在从网络获取图片，请稍候…", Cancel, imports.WaitingCount);
        else if (request.IsBatch)
            ActivityProgress.Begin("正在导入", request.ContainsFolder ? "正在扫描文件夹…" :
                $"准备导入 {request.FileCount} 个文件…", Cancel, imports.WaitingCount);
        LibraryResult? result = null;
        string? error = null;
        try
        {
            var response = await writer.Run(request.Command, reload: true, cancellation: operation.Token,
                progress: showProgress ? operation.Progress(update =>
                {
                    if (!operation.Token.IsCancellationRequested) ActivityProgress.Report(update);
                }) : null);
            operation.StopReporting();
            if (response.Snapshot is { } snapshot)
                AcceptSnapshot(snapshot, null, updateSettings: false, preserveSelection: true);
            result = response.Result;
            error = result.Error;
        }
        catch (OperationCanceledException) when (operation.Token.IsCancellationRequested)
        {
            // The write was cancelled before acquiring the library command gate.
            result = new(0, 0, 0, null, null, null, null, Cancelled: 1);
        }
        catch (Exception failure)
        {
            error = failure.Message.Split('\n', StringSplitOptions.RemoveEmptyEntries).LastOrDefault()?.Trim() ?? failure.Message;
        }
        finally { ActivityProgress.Dismiss(); }
        if (error is not null) ReportIssue(title, error, MessageBoxImage.Error, inline: !request.ContainsFolder);
        else if (result is not null) ShowImportResult(result, request.IsBatch);

        void Cancel() => galleryWork.RequestCancel();
    }

    internal async Task<bool> ImportInbox(InboxFile[] ready)
    {
        if (LibraryBusy || inboxWork.IsBusy || commands is null || middleScroll.Active || ready.Length == 0 ||
            (IsVisible && !OnGalleryPage))
            return false;
        var result = await RunAction(new("import_inbox", InboxFiles: ready), "收件箱自动导入", background: true);
        if (result is { Count: > 0 } && OnGalleryPage)
            GalleryNotice.Show("收件箱自动导入完成", $"成功添加 {result.Count} 个表情包");
        if (result?.Warnings is { Length: > 0 } warnings) Trace.TraceWarning("Inbox: {0}", string.Join(Environment.NewLine, warnings));
        return true;
    }

    private void ShowImportResult(LibraryResult result, bool batch)
    {
        if (result.Cancelled > 0)
        {
            GalleryNotice.Show("导入已取消", "已完成的图片已保留\n" + FormatImportResult(result));
            return;
        }
        if (result.FolderStats is { } stats)
        {
            ShowFolderImportResult(result, stats);
        }
        else if (result.Failed > 0 || result.Warnings is { Length: > 0 })
        {
            // A mixed batch still reports the images that were actually added.
            var summary = FormatImportResult(result);
            if (result.Warnings is { Length: > 0 } warnings)
                summary += Environment.NewLine + string.Join(Environment.NewLine, warnings);
            GalleryNotice.Show("部分文件处理失败", summary, warning: true);
        }
        else if (batch) GalleryNotice.Show("导入完成", FormatImportResult(result));
        else if (result.Count > 0) GalleryNotice.Show("导入完成", $"成功添加了 {result.Count} 个表情包");
        else if (result.Skipped > 0) GalleryNotice.Show("导入完成", "该图片已存在，已跳过保存");
    }

    private static string FormatImportResult(LibraryResult result) =>
        $"成功导入 {result.Count} 张，跳过 {result.Skipped} 张，失败 {result.Failed} 张。";

    internal static string FormatFolderImportResult(LibraryResult result, FolderImportStats stats)
    {
        var details = new List<string>
        {
            $"文件夹：{stats.FolderName}",
            $"扫描文件总数：{stats.TotalFiles}",
            $"其中图片文件：{stats.ImageCount}",
            $"成功导入：{result.Count}",
            $"重复跳过：{result.Skipped}",
            $"解析失败：{result.Failed}",
            $"忽略非图片文件：{stats.NonImages}",
            $"扫描子文件夹：{stats.Subdirs}",
        };
        if (stats.Issues is { Length: > 0 } issues)
        {
            details.Add("");
            details.AddRange(issues);
            if (stats.IssueCount > issues.Length) details.Add($"另有 {stats.IssueCount - issues.Length} 条错误");
        }
        return string.Join(Environment.NewLine, details);
    }

    private void ShowFolderImportResult(LibraryResult result, FolderImportStats stats)
    {
        if (offscreen) return;
        var dialog = new FluentDialog(this, library, "文件夹导入完成", 480);
        dialog.AddMessage(FormatFolderImportResult(result, stats));
        dialog.Button("确定", dialog.Close, primary: true);
        dialog.ShowModal();
    }

    private void ShowPackageImportResult(LibraryResult result)
    {
        if (!offscreen) FluentDialog.ShowMessage(this, library, "导入完成", FormatImportResult(result));
    }

    private async void ImportClipboard()
    {
        try { await ImportClipboardData(Clipboard.GetDataObject(), Clipboard.GetImage); }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or System.Runtime.InteropServices.ExternalException)
        { ReportIssue("导入失败", error.Message, MessageBoxImage.Error, inline: true); }
    }

    internal async Task ImportClipboardData(IDataObject? data, Func<BitmapSource?> getBitmap)
    {
        if (!CanQueueImport || !OnGalleryPage) return;
        var category = current?.Name;
        if (data?.GetData(DataFormats.FileDrop) is string[] { Length: > 0 } paths)
        {
            await ImportFiles(paths, category);
            return;
        }
        if (ClipboardImageSource.FindLink(data?.GetData(DataFormats.Html) as string,
            (data?.GetData(DataFormats.UnicodeText) ?? data?.GetData(DataFormats.Text)) as string) is { } link)
        {
            await ImportLink(link, category);
            return;
        }
        if (getBitmap() is not { } bitmap) return;

        string? temp = null;
        try
        {
            var staged = Path.Combine(Path.GetTempPath(), $"suzu-{Guid.NewGuid():N}.png");
            using (var stream = File.Open(staged, FileMode.CreateNew))
            {
                temp = staged; // Only clean up a file this import actually created.
                var encoder = new PngBitmapEncoder();
                encoder.Frames.Add(BitmapFrame.Create(bitmap));
                encoder.Save(stream);
            }
            await ImportFiles([temp], category);
        }
        finally
        {
            if (temp is not null)
            {
                try { File.Delete(temp); }
                catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
            }
        }
    }
}
