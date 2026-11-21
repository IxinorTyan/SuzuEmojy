using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using SuzuEmojy.Windows.Controls;

namespace SuzuEmojy.Windows;

public partial class ExchangePage : UserControl
{
    private readonly List<SettingCard> cards = new();
    private readonly Button exportSelectedButton;
    private const string Folder = "M1,5 V3 H6 L8,5 H15 V13 H1 Z";
    private const string Import = "M1,8 V15 H15 V8 M8,1 V11 M5,8 L8,11 11,8";
    private const string Export = "M1,8 V15 H15 V8 M8,11 V1 M5,4 L8,1 11,4";
    private const string Split = "M2,2 H7 V14 H2 Z M9,2 H14 V14 H9 Z";
    private const string Picture = "M1,2 H15 V14 H1 Z M1,12 L5,8 8,11 11,6 15,11";

    internal ExchangePage(Action package, Action exportAll, Action exportSelected, Action split,
        Action pictures, Action folder, Action qq, Action telegram, bool hasCategories)
    {
        InitializeComponent();
        Add(Packages, "导入资源包", "导入外部表情包文件（.zip），自动识别表情分类并去重", Import, "导入...", package);
        Add(Packages, "导出全部资源包", "将本地表情库的所有分类及表情完整导出为一个资源包", Export, "导出全部", exportAll);
        exportSelectedButton = Add(Packages, "导出指定分类", "自由勾选需要导出的表情分类，单独打包生成资源包", Export, "挑选导出...",
            exportSelected, hasCategories);
        Add(Packages, "资源包切割分卷", "将已导出的资源包按完整图片分卷，每卷不超过 100 MB；逐卷导入后自动合并分类",
            Split, "选择资源包...", split);
        Add(LocalFiles, "导入图片", "", Picture, "导入...", pictures);
        Add(LocalFiles, "导入文件夹", "", Folder, "导入...", folder);
        Add(ExternalSources, "扫描 QQ 聊天表情", "扫描本地 QQ 缓存目录，提取接收到的表情与个人表情", Picture, "扫描...", qq);
        Add(ExternalSources, "下载 TG 贴纸", "从 Telegram 贴纸包链接下载贴纸", Import, "下载...", telegram);
        SizeChanged += (_, e) =>
        {
            foreach (var card in cards) card.SetCompact(e.NewSize.Width < 480);
        };
    }

    internal void SetCanExportSelected(bool enabled) => exportSelectedButton.IsEnabled = enabled;

    private Button Add(StackPanel group, string title, string detail, string glyph, string label, Action action, bool enabled = true)
    {
        var button = new Button { Style = (Style)FindResource("FluentPushButton"), Content = label,
            MinWidth = 110, IsEnabled = enabled };
        button.Click += (_, _) => action();
        var card = new SettingCard(title, detail, glyph, button);
        card.Cursor = enabled ? Cursors.Hand : Cursors.Arrow;
        button.IsEnabledChanged += (_, _) => card.Cursor = button.IsEnabled ? Cursors.Hand : Cursors.Arrow;
        card.MouseLeftButtonUp += (_, e) => { if (!e.Handled && button.IsEnabled) action(); };
        cards.Add(card);
        group.Children.Add(card);
        return button;
    }
}
