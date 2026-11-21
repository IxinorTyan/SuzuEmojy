using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Media.Imaging;

namespace SuzuEmojy.Windows;

internal static class QqCacheScanner
{
    private static readonly ExternalOption[] Known =
    [
        new("personal_emoji", "个人表情"), new("emoji-recv", "接收到的表情"), new("marketface", "商店表情"),
        new("BaseEmojiSyastems", "系统表情"), new("emoji-related", "候选表情"), new("pic", "收藏的图片（含聊天图片）")
    ];
    private static readonly StringComparer FileNames = LibraryImagePaths.Comparer;
    private static readonly Regex Account = new(@"^\d+$", RegexOptions.CultureInvariant);
    private static readonly Regex Slice = new(@"_\d+$", RegexOptions.CultureInvariant);
    private static readonly Regex GenericDirectory = new(@"(?:[\\/]|^)(apng|png|ori|raw|thumb|preview)$",
        RegexOptions.CultureInvariant | RegexOptions.IgnoreCase);
    private static readonly HashSet<string> ExcludedMarketFace = new(FileNames)
        { ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".apng", ".json", ".ini" };
    private static readonly LibraryResult Empty = new(0, 0, 0, null, null, null, null);

    internal static LibraryResult? TryRun(LibraryCommand request, CancellationToken token, IProgress<LibraryProgress>? progress)
    {
        token.ThrowIfCancellationRequested();
        try
        {
            if (request.Action == "qq_discover")
            {
                var directory = string.IsNullOrEmpty(request.Value) ? QqCacheDiscovery.Find() : request.Value;
                if (directory is null || !Directory.Exists(directory)) return Empty with { Sources = [] };
                var root = RuntimePrimitives.ExistingPath(directory);
                var results = new List<ExternalAccount>();
                foreach (var folder in Directories(root).Where(folder => Account.IsMatch(folder.Name)).OrderBy(folder => folder.Name, StringComparer.Ordinal))
                {
                    token.ThrowIfCancellationRequested();
                    var found = Known.Where(option => Directory.Exists(CategoryPath(folder.FullName, option.Key))).ToList();
                    var emoji = CategoryPath(folder.FullName, "Emoji");
                    emoji = Path.GetDirectoryName(emoji)!;
                    if (Directory.Exists(emoji))
                        found.AddRange(Directories(emoji).Where(child => !Known.Any(option => FileNames.Equals(option.Key, child.Name)))
                            .OrderBy(child => child.Name, FileNames).Select(child => new ExternalOption(child.Name, child.Name)));
                    if (found.Count != 0) results.Add(new(folder.Name, found.ToArray()));
                }
                return Empty with { Destination = root, Sources = results.ToArray() };
            }
            return Scan(request, token, progress);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException or NotSupportedException or COMException)
        {
            return Empty with { Error = "无法读取 QQ 缓存，请检查目录和账号" };
        }
    }

    private static LibraryResult? Scan(LibraryCommand request, CancellationToken token, IProgress<LibraryProgress>? progress)
    {
        var (folder, kind) = ResolveFolder(request, direct: false);
        bool market = FileNames.Equals(kind, "marketface"), pictures = FileNames.Equals(kind, "pic");
        if (FileNames.Equals(kind, "personal_emoji")) folder = Path.Combine(folder, "Ori");
        if (!Directory.Exists(folder)) return Empty with { Items = [] };
        if ((File.GetAttributes(folder) & FileAttributes.ReparsePoint) != 0) throw new ArgumentException();
        folder = RuntimePrimitives.ExistingPath(folder);
        var candidates = Walk(folder, market, pictures, token, progress);
        var best = new Dictionary<string, (FileInfo File, double Score)>(StringComparer.Ordinal);
        var selected = new List<FileInfo>();
        var recovered = new Dictionary<string, (int Width, int Height)>(FileNames);
        ReportCheck(0);
        for (int i = 0; i < candidates.Count; i++)
        {
            token.ThrowIfCancellationRequested();
            var file = candidates[i];
            try
            {
                using var input = Open(file.FullName);
                if (market)
                {
                    if (Recover(input, token) is { } record)
                    {
                        recovered[file.FullName] = (record.Width, record.Height);
                        selected.Add(file);
                    }
                }
                else if (Signature(input) is { } type)
                {
                    if (pictures) selected.Add(file);
                    else
                    {
                        input.Position = 0;
                        double score = Score(file, type, input);
                        string key = Group(file, folder);
                        if (!best.TryGetValue(key, out var previous) || score > previous.Score) best[key] = (file, score);
                    }
                }
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException or NotSupportedException or COMException) { }
            ReportCheck(i + 1);
        }
        if (!market && !pictures) selected.AddRange(best.Values.Select(value => value.File));
        selected.Sort((a, b) => market || pictures ? FileNames.Compare(a.FullName, b.FullName) :
            LibrarySnapshotReader.CompareNames(a.FullName, b.FullName));
        var results = new List<ExternalSticker>(selected.Count);
        ReportMetadata(0);
        for (int i = 0; i < selected.Count; i++)
        {
            token.ThrowIfCancellationRequested();
            var file = selected[i];
            try
            {
                var path = RuntimePrimitives.ExistingPath(file.FullName);
                if (!RuntimePrimitives.IsWithin(folder, path) ||
                    (File.GetAttributes(file.FullName) & FileAttributes.ReparsePoint) != 0) continue;
                using var input = Open(path);
                var id = RuntimePrimitives.QqFileIdentity(input);
                (int Width, int Height, bool Animated)? dimensions = recovered.TryGetValue(file.FullName, out var original)
                    ? (original.Width, original.Height, true) : Dimensions(input, token);
                var (width, height, animated) = dimensions ?? (0, 0, false);
                results.Add(new(path, results.Count, file.Name, Animated: animated, Width: width, Height: height, FileIdentity: id));
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
            finally { ReportMetadata(i + 1); }
        }
        return Empty with { Items = results.ToArray() };

        void ReportCheck(int current) => progress?.Report(new("qq_check", current, candidates.Count, $"正在检查文件：{current} / {candidates.Count}"));
        void ReportMetadata(int current) => progress?.Report(new("qq_metadata", current, selected.Count, $"正在准备图片：{current} / {selected.Count}"));
    }

    private static List<FileInfo> Walk(string folder, bool market, bool pictures, CancellationToken token, IProgress<LibraryProgress>? progress)
    {
        var pending = new Stack<string>();
        pending.Push(folder);
        var result = new List<FileInfo>();
        int directories = 0;
        Report();
        while (pending.Count > 0)
        {
            token.ThrowIfCancellationRequested();
            var path = pending.Pop();
            directories++;
            var children = new List<string>();
            bool accept = !pictures || FileNames.Equals(Path.GetFileName(path), "Ori");
            try
            {
                foreach (var entry in new DirectoryInfo(path).EnumerateFileSystemInfos())
                {
                    token.ThrowIfCancellationRequested();
                    try
                    {
                        if ((entry.Attributes & FileAttributes.ReparsePoint) != 0) continue;
                        if (entry is DirectoryInfo) children.Add(entry.FullName);
                        else if (entry is FileInfo file && accept && (!market || !ExcludedMarketFace.Contains(file.Extension)))
                        {
                            result.Add(file);
                            if (result.Count % 64 == 0) Report();
                        }
                    }
                    catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
                }
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
            for (int i = children.Count - 1; i >= 0; i--) pending.Push(children[i]);
            Report();
        }
        return result;
        void Report() => progress?.Report(new("qq_walk", directories, 0, $"已遍历 {directories} 个文件夹，发现 {result.Count} 个候选文件"));
    }

    private static string CategoryPath(string account, string kind)
    {
        var root = Path.Combine(account, "nt_qq", "nt_data");
        return FileNames.Equals(kind, "pic") ? Path.Combine(root, "Pic") : Path.Combine(root, "Emoji", kind);
    }

    internal static (string Folder, string Kind) ResolveFolder(LibraryCommand request, bool direct)
    {
        string basePath = RuntimePrimitives.ExistingPath(request.Value ?? "");
        string kind = request.Kind;
        if (request.Account is not { Length: > 0 } account || !Account.IsMatch(account) ||
            kind is "" or "." or ".." || kind.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0)
            throw new ArgumentException("请选择 QQ 账号和缓存分类");
        var accountPath = Path.Combine(basePath, account);
        var expected = CategoryPath(accountPath, kind);
        var folder = RuntimePrimitives.ExistingPath(expected);
        if (!Directory.Exists(folder) || !RuntimePrimitives.IsWithin(accountPath, folder) ||
            direct && !FileNames.Equals(expected, folder)) throw new IOException("QQ 缓存分类不存在");
        return (folder, kind);
    }

    internal static string ResolveFile(string folder, string kind, string path, bool direct = false)
    {
        var boundary = FileNames.Equals(kind, "personal_emoji") ? Path.Combine(folder, "Ori") : folder;
        var actual = RuntimePrimitives.ExistingPath(path);
        if (!File.Exists(path) || !RuntimePrimitives.IsWithin(boundary, actual) ||
            (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0 ||
            direct && !FileNames.Equals(actual, path) ||
            FileNames.Equals(kind, "pic") && !FileNames.Equals(Path.GetFileName(Path.GetDirectoryName(path)), "Ori"))
            throw new ArgumentException("QQ 缓存文件不存在或已移动");
        return actual;
    }

    private static IEnumerable<DirectoryInfo> Directories(string path) => new DirectoryInfo(path).EnumerateDirectories()
        .Where(folder => (folder.Attributes & FileAttributes.ReparsePoint) == 0);
    internal static FileStream Open(string path) => File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);

    private static string Group(FileInfo file, string folder)
    {
        var relative = file.DirectoryName!.Substring(folder.TrimEnd('\\').Length).TrimStart('\\');
        var subtree = GenericDirectory.Replace(relative, "").Replace('\\', '/').ToLowerInvariant().Trim('/');
        var name = Slice.Replace(Path.GetFileNameWithoutExtension(file.Name).ToLowerInvariant(), "");
        return subtree.Length > 0 ? $"{subtree}/{name}" : name;
    }

    private static double Score(FileInfo file, string type, Stream input)
    {
        var path = file.FullName.Replace('\\', '/').ToLowerInvariant();
        double score = type == "gif" || type == "png" && ApngDecoder.HasAnimationControl(input) ? 1000 : 0;
        if (!Slice.IsMatch(Path.GetFileNameWithoutExtension(file.Name))) score += 200;
        if (path.Contains("/apng/")) score += 100;
        else if (path.Contains("/ori/") || path.Contains("/raw/")) score += 80;
        else if (path.Contains("/png/")) score += 40;
        else if (path.Contains("/thumb/") || path.Contains("/preview/")) score -= 100;
        return score + Math.Min(file.Length / 1024d, 50);
    }

    internal static string? SignatureAtStart(Stream input)
    {
        input.Position = 0;
        try { return Signature(input); }
        finally { input.Position = 0; }
    }

    private static string? Signature(Stream input)
    {
        var head = new byte[16];
        int length = input.Read(head, 0, head.Length);
        var bytes = head.AsSpan(0, length);
        if (bytes.StartsWith(new byte[] { 0xff, 0xd8, 0xff })) return "jpg";
        if (bytes.StartsWith(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 })) return "png";
        if (bytes.StartsWith("GIF87a"u8) || bytes.StartsWith("GIF89a"u8)) return "gif";
        if (bytes.StartsWith("BM"u8)) return "bmp";
        if (bytes.StartsWith("II*\0"u8) || bytes.StartsWith("MM\0*"u8)) return "tiff";
        if (bytes.StartsWith("RIFF"u8) || bytes.StartsWith("WEBP"u8)) return "webp";
        if (bytes.StartsWith("\0\0\u0001\0"u8) || bytes.StartsWith("\0\0\u0002\0"u8)) return "ico";
        if (bytes.StartsWith("8BPS"u8)) return "psd";
        if (bytes.StartsWith("<?xml"u8) || bytes.StartsWith("<svg"u8)) return "svg";
        if (bytes.StartsWith("ftypheic"u8) || bytes.StartsWith("ftypheix"u8) ||
            bytes.StartsWith("ftyphevc"u8) || bytes.StartsWith("ftyphevx"u8)) return "heic";
        return bytes.StartsWith("ftypavif"u8) || bytes.StartsWith("ftypavis"u8) ? "avif" : null;
    }

    private static (int Width, int Height, bool Animated)? Dimensions(Stream source, CancellationToken token)
    {
        var header = new byte[30];
        int count = source.Read(header, 0, header.Length);
        source.Position = 0;
        if (NativeWebp.Available && NativeWebp.IsWebp(source))
        {
            try { return NativeWebp.Dimensions(source, token); }
            catch (FormatException) { return (0, 0, false); }
        }
        if (count >= 30 && header.AsSpan(0, 4).SequenceEqual("RIFF"u8) && header.AsSpan(8, 4).SequenceEqual("WEBP"u8) &&
            header.AsSpan(12, 4).SequenceEqual("VP8X"u8))
            return (1 + (header[24] | header[25] << 8 | header[26] << 16),
                1 + (header[27] | header[28] << 8 | header[29] << 16), (header[20] & 2) != 0);
        try
        {
            var decoder = BitmapDecoder.Create(source, BitmapCreateOptions.DelayCreation, BitmapCacheOption.None);
            var frame = decoder.Frames[0];
            int width = frame.PixelWidth, height = frame.PixelHeight;
            bool animated = decoder.Frames.Count > 1;
            if (decoder is PngBitmapDecoder)
            {
                source.Position = 0;
                animated = ApngDecoder.HasAnimationControl(source);
            }
            else if (decoder is GifBitmapDecoder && count >= 10)
            {
                width = BinaryPrimitives.ReadUInt16LittleEndian(header.AsSpan(6));
                height = BinaryPrimitives.ReadUInt16LittleEndian(header.AsSpan(8));
            }
            return (width, height, animated);
        }
        catch (Exception e) when (e is IOException or ArgumentException or FormatException or NotSupportedException or COMException)
        { return null; }
    }

    internal sealed record RecoveredGif(byte[] Bytes, int Width, int Height, int Frames);

    internal static RecoveredGif? Recover(Stream source, CancellationToken token)
    {
        if (source.Length > 48 * 1024 * 1024) return null;
        var bytes = new byte[checked((int)source.Length)];
        int total = 0;
        while (total < bytes.Length)
        {
            token.ThrowIfCancellationRequested();
            int count = source.Read(bytes, total, Math.Min(65536, bytes.Length - total));
            if (count == 0) return null;
            total += count;
        }
        bool Gif() => bytes.AsSpan().StartsWith("GIF87a"u8) || bytes.AsSpan().StartsWith("GIF89a"u8);
        if (!Gif())
        {
            for (int start = 0; start < bytes.Length; start += 50)
                for (int i = start; i < Math.Min(start + 20, bytes.Length); i++) bytes[i] ^= 255;
            if (!Gif()) return null;
        }
        try
        {
            using var data = new MemoryStream(bytes, writable: false);
            var decoder = new GifBitmapDecoder(data, BitmapCreateOptions.PreservePixelFormat, BitmapCacheOption.None);
            if (decoder.Frames.Count == 0) return null;
            foreach (var frame in decoder.Frames)
            {
                token.ThrowIfCancellationRequested();
                int stride = (frame.PixelWidth * frame.Format.BitsPerPixel + 7) / 8;
                if ((long)stride * frame.PixelHeight > 64 * 1024 * 1024) return null;
                frame.CopyPixels(new byte[stride * frame.PixelHeight], stride, 0);
            }
            return new(bytes, BinaryPrimitives.ReadUInt16LittleEndian(bytes.AsSpan(6)),
                BinaryPrimitives.ReadUInt16LittleEndian(bytes.AsSpan(8)), decoder.Frames.Count);
        }
        catch (Exception e) when (e is IOException or FormatException or NotSupportedException or COMException or ArgumentException)
        { return null; }
    }
}
