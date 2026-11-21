using System;

namespace SuzuEmojy.Windows;

internal static class TelegramConnectionDefaults
{
    internal const string TokenVariable = "SUZU_TG_BOT_TOKEN";
    internal const string ProxyVariable = "SUZU_TG_API_PROXY";

    internal static (string Token, string Proxy) Resolve(LibraryCommand request, Func<string, string?> read)
    {
        var token = request.BotToken?.Trim() ?? "";
        var proxy = request.ApiProxy?.Trim() ?? "";
        // The configured defaults are a pair. Do not send a hidden token to
        // an override proxy, or send a newly entered token to the hidden proxy.
        if (token.Length != 0 || proxy.Length != 0) return (token, proxy);
        return (read(TokenVariable)?.Trim() ?? "", read(ProxyVariable)?.Trim() ?? "");
    }
}
