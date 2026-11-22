using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Threading;
using Microsoft.Win32;
using SuzuEmojy.Windows.Controls;
using SuzuEmojy.Windows.Desktop;

namespace SuzuEmojy.Windows;

public partial class MainWindow
{
    private readonly DispatcherTimer layoutSave = new() { Interval = TimeSpan.FromMilliseconds(250) };
    private bool savingSettings;
    private Task settingsSave = Task.CompletedTask;
    private string? mainHotkeyError, quickHotkeyError;
    private SettingsPage? settingsPage;
    internal LoginStartup? StartupRegistration { get; set; }
    internal UpdateInstallation? Updates { get; set; }
    internal Func<StagedUpdate, Task>? InstallUpdate { get; set; }
    internal bool OnSettingsPage => Pages.Page is SettingsPage;
    internal bool RecordingHotkey => OnSettingsPage && settingsPage?.IsRecordingHotkey == true;
    internal event Action<bool>? HotkeyRecordingChanged;
    internal int QuickThumbnailSize => QuickPanel.ReadThumbnailSize(library);
    internal void SetQuickThumbnailSize(int size) => SetLayoutSetting(QuickPanel.ThumbnailSizeKey, size);

    internal void ShowSettings()
    {
        if (LibraryBusy || commands is null || !OnGalleryPage) return;
        settingsPage ??= CreateSettingsPage();
        OpenPage(SettingsRoute(settingsPage), settingsButton);
    }

    private PageRoute SettingsRoute(SettingsPage page) =>
        new("设置", page, Leave: () =>
        {
            FlushSettings();
            HotkeyRecordingChanged?.Invoke(false);
        }, Work: galleryWork);

    internal void FlushSettings()
    {
        if (layoutSave.IsEnabled) FlushLayoutSettings();
        settingsPage?.CommitAndFlush();
    }

    private void SetLayoutSetting(string key, object value)
    {
        preferences.StageLayout(key, value);
        library = preferences.Effective;
        if (key == "thumbnail_size" && !OnSettingsPage) settingsPage = null;
        if (commands is null) return;
        layoutSave.Stop();
        layoutSave.Start();
    }

    private void FlushLayoutSettings()
    {
        layoutSave.Stop();
        preferences.FlushLayout();
        StartSettingsSave();
    }

    internal void ShowHotkeyStatus(string? main, string? quick, bool announce)
    {
        mainHotkeyError = main;
        quickHotkeyError = quick;
        settingsPage?.SetHotkeyStatus(main, quick);
        if (announce && OnGalleryPage && (main ?? quick) is { } message)
            GalleryNotice.Show("快捷键不可用", message, warning: true);
    }

    private SettingsPage CreateSettingsPage()
    {
        var page = new SettingsPage(library, MenuPalette, StartupRegistration);
        page.SetWallpaperState(library.Wallpaper is not null, Wallpaper.HasImage);
        page.SetHotkeyStatus(mainHotkeyError, quickHotkeyError);
        page.HotkeyRecordingChanged += recording => HotkeyRecordingChanged?.Invoke(recording);
        page.ChangesRequested += SaveSettings;
        page.WallpaperRequested += selected =>
        {
            if (!selected) { _ = ChangeWallpaper(null); return; }
            if (busy || offscreen) return;
            var picker = new OpenFileDialog
            {
                Title = "选择背景图片",
                Filter = "图片|*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.tif;*.tiff",
                CheckFileExists = true
            };
            if (picker.ShowDialog(this) == true) _ = ChangeWallpaper(picker.FileName);
        };
        page.WallpaperTonePreview += (key, level) =>
        {
            preferences.Preview(key, level);
            ApplyWallpaperTone();
        };
        page.ShortcutConflict += () => { if (!offscreen) FluentDialog.ShowMessage(this, library, "快捷键冲突", "两个快捷键不能相同"); };
        page.StartupError += message => ReportIssue("开机自启动", message, MessageBoxImage.Error, inline: false);
        page.MaintenanceRequested += progress => { if (!offscreen) _ = MaintainLibrary(progress); };
        page.AboutRequested += () =>
        {
            if (commands is null) return;
            var about = new AboutPage(commands.Updates, Updates, InstallUpdate);
            OpenPage(new("关于软件", about, Leave: about.Cancel, Work: about.PageWork));
        };
        return page;
    }

    private async Task MaintainLibrary(OperationProgress progress)
    {
        const string title = "旧图库维护";
        if (LibraryBusy || commands is null || !FluentDialog.Confirm(this, library, title,
                "备份索引和原始数据记录后，迁移旧数据、清理失效记录，并重建索引。不会删除图片。继续吗？")) return;
        FlushSettings();
        while (savingSettings) await settingsSave;
        if (!OnSettingsPage || LibraryBusy) return;
        using var operation = galleryWork.Begin(LeavePolicy.FinishBeforeLeaving);
        progress.Begin(title, "正在备份…");
        try
        {
            var response = await commands.Run(new("maintain_library"), reload: true, progress: operation.Progress(update =>
                progress.Report(update with { Detail = update.Detail ?? (update.Phase switch
                {
                    "migration" => "正在迁移旧数据…", "cleanup" => "正在清理失效记录…",
                    "index" or "scan" => $"正在补全索引：{update.Current} / {update.Total}", _ => null,
                }) })));
            operation.StopReporting();
            if (response.Snapshot is { } snapshot) AcceptSnapshot(snapshot, null, updateSettings: false);
            var result = response.Result;
            var message = result.Error ?? (result.Failed == 0 ? "维护完成。" : $"维护已完成，有 {result.Failed} 张图片未能编入索引。");
            if (result.Destination is { } backup) message += "\n备份位置：" + backup;
            FluentDialog.ShowMessage(this, library, title, message,
                result.Error is null ? MessageBoxImage.Information : MessageBoxImage.Warning);
        }
        catch (Exception error) { ReportIssue(title, error.Message.Trim(), MessageBoxImage.Error, inline: false); }
        finally { progress.Dismiss(); }
    }

    internal async Task<bool> ChangeWallpaper(string? path)
    {
        // A slider may have just committed its latest value as the picker opened.
        while (savingSettings) await settingsSave;
        var action = path is null ? "clear_wallpaper" : "set_wallpaper";
        var result = await RunAction(new(action, Value: path), "背景图片", inlineErrors: true);
        if (result?.Error is { } error) ReportIssue("背景图片", error, MessageBoxImage.Warning, inline: true);
        return result is { Error: null };
    }

    private void RefreshWallpaper()
    {
        ApplyWallpaperTone();
        Wallpaper.ShowImage(library.Wallpaper);
        UpdateWallpaperSettings(Wallpaper.HasImage);
    }

    private void ApplyWallpaperTone() => Wallpaper.ApplyTone(preferences.Effective);

    private void UpdateWallpaperSettings(bool visible) =>
        settingsPage?.SetWallpaperState(library.Wallpaper is not null, visible);

    private void SaveSettings(Dictionary<string, object> changes)
    {
        preferences.Queue(changes);
        StartSettingsSave();
    }

    private void StartSettingsSave()
    {
        if (savingSettings || commands is null) return;
        savingSettings = true;
        settingsSave = SaveQueuedSettings(commands);
    }

    private async Task SaveQueuedSettings(LibraryCommands writer)
    {
        try
        {
            while (preferences.TakeWrite() is { } values)
            {
                var persisted = await writer.SaveSettings(values);
                // A reply to an earlier wheel step must not undo a newer on-screen size.
                preferences.Acknowledge(values);
                AcceptSnapshot(preferences.Saved with { Settings = persisted }, null, updateSettings: true);
            }
        }
        catch (Exception error)
        {
            preferences.Revert();
            ApplyPreferences(null, updateSettings: true);
            layoutSave.Stop();
            var previousPage = settingsPage;
            previousPage?.DiscardEdits();
            settingsPage = CreateSettingsPage();
            if (previousPage is not null) Pages.ReplaceCurrent(previousPage, SettingsRoute(settingsPage));
            var message = error.Message.Trim();
            if (!offscreen)
                FluentDialog.ShowMessage(this, library, "设置", message, MessageBoxImage.Error);
            else System.Diagnostics.Trace.TraceError(message);
        }
        finally { savingSettings = false; }
    }
}
