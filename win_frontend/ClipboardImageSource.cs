using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows;

namespace SuzuEmojy.Windows;

internal static class ClipboardImageSource
{
    private const string BrowserUrl = "UniformResourceLocatorW";
    private const string BrowserUrlAnsi = "UniformResourceLocator";
    private const string UriList = "text/uri-list";
    private const RegexOptions BaseHtmlOptions = RegexOptions.IgnoreCase | RegexOptions.CultureInvariant;
#if NETFRAMEWORK
    private const RegexOptions HtmlOptions = BaseHtmlOptions;
#else
    private const RegexOptions HtmlOptions = BaseHtmlOptions | RegexOptions.NonBacktracking;
#endif
    private static readonly TimeSpan HtmlTimeout = TimeSpan.FromMilliseconds(250);
    // CF_HTML can include a header; only an actual img element's src is a candidate.
    private static readonly Regex Tags = new(
        """<!--[\s\S]*?-->|<img\b((?:"[^"]*"|'[^']*'|[^'"<>])*)>""", HtmlOptions, HtmlTimeout);
    private static readonly Regex Attributes = new(
        """([^\s=/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?""", HtmlOptions, HtmlTimeout);

    internal static bool MayContainDropLink(IDataObject data) =>
        data.GetDataPresent(DataFormats.Html, false) || data.GetDataPresent(UriList, false) ||
        data.GetDataPresent(BrowserUrl, false) || data.GetDataPresent(BrowserUrlAnsi, false) || HttpLink(Text(data)) is not null;

    internal static Uri[] FindDropLinks(IDataObject data)
    {
        // The image source wins over the address of a page wrapping that image.
        if (FindLink(data.GetData(DataFormats.Html, false) as string, null) is { } image) return [image];
        if (ReadLinkData(data.GetData(UriList, false), Encoding.UTF8) is { } list)
        {
            var links = new HashSet<Uri>();
            var ordered = new List<Uri>();
            foreach (string line in list.Split('\n'))
                if (HttpLink(line) is { } link && links.Add(link)) ordered.Add(link);
            if (ordered.Count > 0) return ordered.ToArray();
        }
        return (HttpLink(ReadLinkData(data.GetData(BrowserUrl, false), Encoding.Unicode)) ??
            HttpLink(ReadLinkData(data.GetData(BrowserUrlAnsi, false), Encoding.GetEncoding(28591))) ?? HttpLink(Text(data))) is { } url
            ? [url] : [];
    }

    private static string? ReadLinkData(object? data, Encoding encoding) => data switch
    {
        string { Length: <= 65_536 } text => text.TrimEnd('\0'),
        MemoryStream { Length: <= 65_536 } stream => encoding.GetString(stream.ToArray()).TrimEnd('\0'),
        _ => null,
    };

    private static string? Text(IDataObject data) =>
        (data.GetData(DataFormats.UnicodeText) ?? data.GetData(DataFormats.Text)) as string;

    internal static Uri? FindLink(string? html, string? text)
    {
        try
        {
            if (html is null || html.Length is 0 or > 1_000_000) return HttpLink(text);
            foreach (Match tag in Tags.Matches(html))
            {
                if (!tag.Groups[1].Success) continue;
                foreach (Match attribute in Attributes.Matches(tag.Groups[1].Value))
                {
                    if (!attribute.Groups[1].Value.Equals("src", StringComparison.OrdinalIgnoreCase)) continue;
                    var source = attribute.Groups.Cast<Group>().Skip(2).FirstOrDefault(value => value.Success)?.Value;
                    if (HttpLink(WebUtility.HtmlDecode(source)) is { } link) return link;
                    break;
                }
            }
        }
        catch (RegexMatchTimeoutException) { /* Fall back to the plain-text URL. */ }
        return HttpLink(text);
    }

    private static Uri? HttpLink(string? value)
    {
        var trimmed = value?.Trim();
        if (trimmed is not { Length: > 0 and <= 8192 } || trimmed.Any(c => char.IsWhiteSpace(c) || char.IsControl(c)))
            return null;
        return Uri.TryCreate(trimmed, UriKind.Absolute, out var link) && link.Scheme is "http" or "https" &&
            link.Host.Length > 0 && link.UserInfo.Length == 0 ? link : null;
    }
}
