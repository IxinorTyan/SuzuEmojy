using System;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using Microsoft.Win32;
using SuzuEmojy.Windows.Controls;
using SuzuEmojy.Windows.Desktop;

namespace SuzuEmojy.Windows;

public partial class MainWindow
{
    private void ShowExchange()
    {
        if (!OnGalleryPage || LibraryBusy || commands is null) return;
        var page = new ExchangePage(ImportPackage, () => ExportZip(null), ExportSelected, SplitPackage,
            OpenLanSync, PickImportFiles, PickImportFolder, () => OpenSource(false), () => OpenSource(true), library.Categories.Length > 0);
        OpenPage(new("导入导出", page, Resume: () => page.SetCanExportSelected(library.Categories.Length > 0),
            Work: galleryWork, Tool: new("前往资源站", "external-link",
                () => { if (!offscreen) DesktopLinks.Open(DesktopLinks.ResourceSite, error =>
                    ReportIssue("前往资源站", error, MessageBoxImage.Warning, inline: false)); })), exchangeButton);

        void OpenSource(bool telegram)
        {
            if (LibraryBusy || Pages.Page is not ExchangePage) return;
            string outputKey = telegram ? "tg_save_path" : "qq_save_path";
            var source = new ExternalStickerPage(telegram, MenuPalette, commands,
                (snapshot, category) => AcceptSnapshot(snapshot, category, updateSettings: false),
                ChooseFolder, library.Categories, current?.Name,
                askCategory: () => offscreen ? null : FluentDialog.AskText(this, library, "新建分类", "请输入分类名称："),
                openBatch: OpenBatch,
                packagePrompts: offscreen ? null : new(
                    title => FluentDialog.AskText(this, library, "导出为资源包",
                        "请输入资源包 ID（同时作为收藏夹名称和 ZIP 文件名）：", title),
                    path => FluentDialog.Confirm(this, library, "覆盖资源包", "文件已存在，是否覆盖？\n" + path),
                    (ok, detail) => FluentDialog.ShowMessage(this, library, ok ? "资源包导出完成" : "资源包导出失败",
                        detail, ok ? MessageBoxImage.None : MessageBoxImage.Error)),
                folders: new(library.String("qq_data_path", ""), library.String(outputKey, ""),
                    path => SaveFolder("qq_data_path", path), path => SaveFolder(outputKey, path)),
                details: offscreen ? null : new(
                    help => FluentDialog.ShowMessage(this, library, "使用帮助", help),
                    (log, clear) => FluentDialog.ShowDetails(this, library, "操作日志", log, clear)));
            var route = new PageRoute(telegram ? "下载 TG 贴纸" : "扫描 QQ 聊天表情", source,
                Suspend: () => source.SuspendMedia(true), Resume: () => source.SuspendMedia(false),
                Leave: source.Leave, HandleBack: source.TryGoBack, Work: source.PageWork);
            if (Pages.Push(route) && !telegram && !offscreen) _ = source.DiscoverInitial();
        }

        void OpenBatch(LibraryCommand request)
        {
            if (LibraryBusy || Pages.Page is not ExternalStickerPage) return;
            var batch = new TelegramBatchPage(request, commands.Run, ChooseFolder,
                snapshot => AcceptSnapshot(snapshot, null, updateSettings: false),
                detail => { if (!offscreen) FluentDialog.ShowDetails(this, library, "结果 / 详情", detail); },
                library.String("tg_batch_save_path", ""), path => SaveFolder("tg_batch_save_path", path));
            Pages.Push(new("批量打包 Telegram 贴纸", batch, Work: batch.PageWork));
        }

        void SaveFolder(string key, string path)
        {
            if (!string.Equals(library.String(key, ""), path, StringComparison.OrdinalIgnoreCase))
                SaveSettings(new() { [key] = path });
        }
    }

    private void PickImportFiles()
    {
        if (!CanQueueImport) return;
        var picker = new OpenFileDialog
        {
            Title = "导入图片", Multiselect = true,
            Filter = ImportFormats.PickerFilter,
        };
        if (picker.ShowDialog(this) == true) _ = ImportFiles(picker.FileNames, current?.Name);
    }

    private void PickImportFolder()
    {
        if (!CanQueueImport) return;
        if (ChooseFolder("导入文件夹") is { } folder) _ = ImportFiles([folder], current?.Name);
    }

    private void ImportPackage()
    {
        if (LibraryBusy) return;
        if (PickZip("导入资源包") is { } file)
            Run(new("import_zip", Value: file), "导入资源包", completed: ShowPackageImportResult,
                activity: ("导入资源包", "正在解压资源…"));
    }

    private void ExportSelected()
    {
        if (LibraryBusy) return;
        if (FluentDialog.ChooseCategories(this, library, "导出指定分类", library.Categories, multiple: true) is { } chosen)
            ExportZip(chosen);
    }

    private void SplitPackage()
    {
        if (LibraryBusy) return;
        if (PickZip("资源包切割分卷") is { } source && ChooseFolder("选择分卷保存文件夹") is { } folder)
            Run(new("split_zip", Value: source, Destination: folder), "资源包切割分卷", reload: false,
                completed: result => FluentDialog.ShowMessage(this, library, "分卷完成", result.Destination ?? folder),
                activity: ("正在切割资源包", "正在校验资源包"));
    }

    private string? PickZip(string title)
    {
        var picker = new OpenFileDialog { Title = title, Filter = "ZIP (*.zip)|*.zip", CheckFileExists = true };
        return picker.ShowDialog(this) == true ? picker.FileName : null;
    }

    private void ExportZip(string[]? categories)
    {
        if (LibraryBusy) return;
        var picker = new SaveFileDialog { Title = "导出资源包", Filter = "ZIP (*.zip)|*.zip", DefaultExt = ".zip",
            AddExtension = true, OverwritePrompt = true };
        if (picker.ShowDialog(this) == true)
            Run(new("export_zip", Value: picker.FileName, Categories: categories), "导出资源包", reload: false,
                completed: result => FluentDialog.ShowMessage(this, library, "导出完成", result.Destination ?? picker.FileName),
                activity: ("导出资源包", "正在打包表情…"));
    }

}
