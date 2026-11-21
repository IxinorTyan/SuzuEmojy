using System.Text;

namespace SuzuEmojy.Windows;

internal static class WindowsCommandLine
{
    // The Win32 argv parser doubles backslashes preceding a quote and our closing quote.
    internal static string QuoteArgument(string argument)
    {
        var result = new StringBuilder().Append('"');
        int backslashes = 0;
        foreach (char c in argument)
        {
            if (c == '\\') { backslashes++; continue; }
            result.Append('\\', c == '"' ? 2 * backslashes + 1 : backslashes).Append(c);
            backslashes = 0;
        }
        return result.Append('\\', 2 * backslashes).Append('"').ToString();
    }
}
