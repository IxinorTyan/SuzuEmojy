using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Win32;

namespace SuzuEmojy.Windows;

internal static class QqCacheDiscovery
{
    private const string Public = @"C:\Users\Public\Documents";

    internal static string? Find()
    {
        var documents = Environment.GetFolderPath(Environment.SpecialFolder.CommonDocuments);
        string ini = Path.Combine(Public, "Tencent", "QQ", "UserDataInfo.ini");
        if (ConfiguredPath(ini) is { } configured) return configured;
        if (documents.Length > 0 && ConfiguredPath(Path.Combine(documents, "Tencent", "QQ", "UserDataInfo.ini")) is { } redirected)
            return redirected;
        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        string? shell = null;
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders");
            shell = key?.GetValue("Personal") as string;
        }
        catch (System.Security.SecurityException) { }
        return FindDocuments([shell, Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
            Path.Combine(home, "Documents"), home, documents, Public]);
    }

    internal static string? FindDocuments(IEnumerable<string?> roots)
    {
        foreach (var root in roots.Distinct(StringComparer.OrdinalIgnoreCase))
        {
            if (string.IsNullOrEmpty(root) || !RuntimePrimitives.IsAbsolutePath(root!)) continue;
            try
            {
                var target = Path.Combine(root!, "Tencent Files");
                if (Directory.Exists(target) && new DirectoryInfo(target).EnumerateDirectories().Any(child =>
                    child.Name.All(char.IsDigit) && Directory.Exists(Path.Combine(child.FullName, "nt_qq"))))
                    return target;
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException) { }
        }
        return null;
    }

    internal static string? ConfiguredPath(string ini)
    {
        try
        {
            var info = new FileInfo(ini);
            if (!info.Exists || info.Length > 1024 * 1024) return null;
            var bytes = File.ReadAllBytes(ini);
            foreach (var encoding in Encodings())
            {
                try
                {
                    string? path = Parse(encoding.GetString(bytes).TrimStart('\uFEFF'));
                    if (path is not null && RuntimePrimitives.IsAbsolutePath(path) && Directory.Exists(path)) return path;
                }
                catch (DecoderFallbackException) { }
            }
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException) { }
        return null;
    }

    private static IEnumerable<Encoding> Encodings()
    {
        yield return new UTF8Encoding(false, true);
        yield return new UnicodeEncoding(false, false, true);
        yield return new UnicodeEncoding(true, false, true);
#if !NETFRAMEWORK
        Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
#endif
        foreach (int code in new[] { 54936, 936, 950, 28591 })
            yield return Encoding.GetEncoding(code, EncoderFallback.ExceptionFallback, DecoderFallback.ExceptionFallback);
    }

    private static string? Parse(string ini)
    {
        bool section = false;
        foreach (string line in ini.Split('\n'))
        {
            var value = line.Trim();
            if (value.StartsWith("[", StringComparison.Ordinal))
                section = Regex.IsMatch(value, @"^\[UserDataSet\](?:\s*[#;].*)?$");
            else if (section && Regex.Match(value, @"^UserDataSavePath\s*[:=]\s*(.*)$", RegexOptions.IgnoreCase) is { Success: true } match)
                return match.Groups[1].Value.Trim();
        }
        return null;
    }
}
