namespace SuzuEmojy.Windows;

internal static class AboutPageContent
{
    internal sealed record Feature(string Title, string Detail);
    internal sealed record Credit(string Name, string Author, string Detail)
    {
        internal string Uri => $"https://github.com/{Author}/{Name}";
    }

    internal static readonly Feature[] Features =
    [
        new("全局瞬时呼出", "用全局快捷键唤出表情面板，选中表情后返回聊天窗口。"),
        new("关键词检索", "按关键词查找本地图库里的表情。"),
        new("智能归类整理", "批量导入、整理收藏夹、拖拽排序及去重。"),
        new("QQ 聊天表情提取", "从本机 QQNT 缓存中扫描并导入表情。"),
        new("TG 贴纸快速解析", "支持解析和导入 Telegram 贴纸包与表情包。"),
        new("高清悬停与防文件化", "悬停预览表情，复制发送静态图时可重编码以减少被当作文件发送。")
    ];

    internal static readonly Credit[] Credits =
    [
        new("PyQt-Fluent-Widgets", "zhiyiYo", "为早期界面提供了 Fluent Design 风格组件与设计参考。"),
        new("QQFavoriteExtract", "VanillaNahida", "为 QQNT 本地表情扫描、数据解析和提取导出提供参考。"),
        new("tg_sticker_downloader", "Kiowx", "为 Telegram 贴纸包解析、下载和格式转换提供参考。")
    ];
}
