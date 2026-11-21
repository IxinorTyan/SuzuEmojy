using System.Linq;

namespace SuzuEmojy.Windows;

internal static class ImportFormats
{
    internal static readonly string[] Suffixes = [".png", ".apng", ".jpg", ".jpeg", ".gif", ".webp", ".webm", ".tgs"];
    internal static string PickerFilter => $"图片|{string.Join(";", Suffixes.Select(suffix => "*" + suffix))}|所有文件|*.*";
}
