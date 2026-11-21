using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace SuzuEmojy.Windows;

internal static class LibrarySimilarity
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    private static readonly IComparer<string> Scalar = Comparer<string>.Create(LibrarySnapshotReader.CompareNames);
    internal sealed record Feature(string Path, long Size, long MtimeNs, int Width, int Height, long[] Hashes, int Duration)
    {
        [JsonIgnore] internal bool Animated => Hashes.Length == 8;
        internal SimilarityImage Image() => new(Path, Width, Height, Size, Duration, Animated);
    }

    internal static LibraryResult? TryRun(string data, LibraryCommand request, CancellationToken token,
        IProgress<LibraryProgress>? progress, bool indexOnly = false)
    {
        if (request.Threshold is < 0 or > 16) throw new ArgumentException("匹配阈值必须为 0–16");
        var snapshot = LibrarySnapshotReader.Read(data, token);
        var paths = snapshot.Images.Where(item => string.IsNullOrEmpty(request.Category) || item.Categories.Contains(request.Category!))
            .Select(item => item.Path).Distinct(StringComparer.Ordinal).OrderBy(path => path, Scalar).ToArray();
        if (paths.Length == 0) return new(0, 0, 0, null, [], [], null);
        var cache = Path.Combine(Path.GetFullPath(data), "cache");
        RuntimePrimitives.EnsureNotReparse(cache);
        Directory.CreateDirectory(cache);
        var dbPath = Path.Combine(cache, "similarity-v1.db");
        RuntimePrimitives.EnsureNotReparse(dbPath);
        using var db = WindowsSqlite.OpenForWrite(dbPath);
        db.Execute("CREATE TABLE IF NOT EXISTS similarity_v1 (path TEXT PRIMARY KEY, payload TEXT)", token);
        var saved = db.Read("SELECT path, payload FROM similarity_v1", 2, token).Where(row => row[0] is not null)
            .ToDictionary(row => row[0]!, row => row[1], StringComparer.Ordinal);
        var features = new List<Feature>(paths.Length);
        var pending = new List<Feature>(32);
        var warnings = new List<string>();
        int failed = 0;
        for (int i = 0; i < paths.Length; i++)
        {
            token.ThrowIfCancellationRequested();
            string path = paths[i];
            try
            {
                using var input = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
                long length = input.Length;
                long modified = checked((RuntimePrimitives.LastWriteTimeUtc(input.SafeFileHandle).Ticks - RuntimePrimitives.UnixEpoch.Ticks) * 100);
                Feature? feature = ReadCache(saved.GetValueOrDefault(path), path, length, modified);
                if (feature is null)
                {
                    feature = Extract(path, input, length, modified, token);
                    pending.Add(feature);
                }
                if (feature is not null && (request.Kind == "all" || feature.Animated == (request.Kind == "animated")))
                    features.Add(feature);
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or FormatException or
                NotSupportedException or COMException or OverflowException or ArgumentException or InvalidOperationException)
            {
                failed++;
                if (warnings.Count < 5) warnings.Add($"{Path.GetFileName(path)}：无法计算图片特征");
            }
            if (pending.Count == 32) Flush();
            progress?.Report(new("scan", i + 1, paths.Length, null));
        }
        Flush();
        return new(0, 0, failed, null, warnings.ToArray(), indexOnly ? [] : Group(features, request.Threshold, token, progress), null);

        void Flush()
        {
            if (pending.Count == 0) return;
            db.Execute("BEGIN IMMEDIATE", token);
            foreach (var item in pending)
                db.Execute("INSERT OR REPLACE INTO similarity_v1 VALUES (?, ?)", token, item.Path, JsonSerializer.Serialize(item, Json));
            db.Execute("COMMIT", token);
            pending.Clear();
        }
    }

    private static Feature Extract(string path, FileStream input, long size, long modified, CancellationToken token)
    {
        int width = 0, height = 0;
        var frames = new List<(long Hash, int Delay)>();
        string? kind = QqCacheScanner.SignatureAtStart(input);
        if (kind == "png" && ApngDecoder.HasAnimationControl(input))
            ApngDecoder.ReadFullFrames(input, token, _ => { }, (pixels, w, h, _, delay) =>
                Add(BitmapSource.Create(w, h, 96, 96, PixelFormats.Bgra32, null, pixels, w * 4), (int)delay));
        else if (kind == "webp" && NativeWebp.Available)
            NativeWebp.ReadFullFrames(NativeWebp.Read(input, token), token, Add);
        else if (kind == "gif")
        {
            input.Position = 6;
            int w = input.ReadByte() | input.ReadByte() << 8, h = input.ReadByte() | input.ReadByte() << 8;
            Guard(w, h);
            input.Position = 0;
            using var gif = GifPreview.Open(File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read), Math.Max(w, h));
            if (gif is null)
            {
                var decoder = new GifBitmapDecoder(input, BitmapCreateOptions.PreservePixelFormat, BitmapCacheOption.OnDemand);
                Add(SingleFrameGif.Decode(input, decoder.Frames[0], token) ?? decoder.Frames[0], 0);
            }
            else
            {
                if (gif.FrameCount > 4096) throw new FormatException();
                width = w;
                height = h;
                var delays = Enumerable.Range(0, gif.FrameCount).Select(gif.SimilarityDelay).ToArray();
                var hashes = SampleIndices(delays).Select(index => (Index: index, Hash: 0L)).ToArray();
                int frame = 0;
                for (int sample = 0; sample < hashes.Length; sample++)
                {
                    token.ThrowIfCancellationRequested();
                    var index = hashes[sample].Index;
                    if (sample > 0 && index == hashes[sample - 1].Index)
                    { hashes[sample].Hash = hashes[sample - 1].Hash; continue; }
                    while (frame < index)
                    {
                        token.ThrowIfCancellationRequested();
                        if (!gif.Advance()) throw new FormatException();
                        frame++;
                    }
                    hashes[sample].Hash = Hash(gif.Bitmap);
                }
                return new(path, size, modified, w, h, hashes.Select(item => item.Hash).ToArray(), delays.Sum());
            }
        }
        else
        {
            var bitmap = ResourceIdentityV1.DecodePng(input, token) ?? ResourceIdentityV1.DecodeOtherStill(input, token);
            if (bitmap is not null) Add(bitmap, 0);
        }
        if (frames.Count == 0) throw new FormatException("无法计算图片特征");
        if (frames.Count == 1) return new(path, size, modified, width, height, [frames[0].Hash], 0);
        var timeline = frames.Select(record => record.Delay).ToArray();
        return new(path, size, modified, width, height, SampleIndices(timeline).Select(i => frames[i].Hash).ToArray(), timeline.Sum());

        long Hash(BitmapSource image) => SimilarityV1Hash.Compute(image.Format == PixelFormats.Bgra32 ? image :
            new FormatConvertedBitmap(image, PixelFormats.Bgra32, null, 0), token);
        void Add(BitmapSource image, int delay)
        {
            Guard(image.PixelWidth, image.PixelHeight);
            width = image.PixelWidth;
            height = image.PixelHeight;
            frames.Add((Hash(image), delay < 20 ? 100 : delay));
        }
        static void Guard(int w, int h)
        {
            if (w is < 1 or > 8192 || h is < 1 or > 8192 || (long)w * h > 16_000_000) throw new FormatException();
        }
    }

    private static int[] SampleIndices(int[] delays)
    {
        long duration = delays.Sum(), end = delays[0];
        var result = new int[8];
        int index = 0;
        for (int sample = 0; sample < result.Length; sample++)
        {
            long time = duration * sample / result.Length;
            while (time >= end && index < delays.Length - 1) end += delays[++index];
            result[sample] = index;
        }
        return result;
    }

    private static Feature? ReadCache(string? payload, string path, long length, long modified)
    {
        if (string.IsNullOrEmpty(payload)) return null;
        try
        {
            var feature = JsonSerializer.Deserialize<Feature>(payload!, Json);
            return feature is { Hashes.Length: 1 or 8, Width: > 0, Height: > 0 } && feature.Size == length &&
                feature.MtimeNs == modified && feature.Path == path && feature.Hashes.All(hash => hash >= 0) ? feature : null;
        }
        catch (JsonException) { return null; }
    }

    internal static SimilarityImage[][] Group(IReadOnlyList<Feature> features, int threshold, CancellationToken token,
        IProgress<LibraryProgress>? progress = null)
    {
        var result = new List<SimilarityImage[]>();
        int done = 0;
        foreach (bool animated in new[] { false, true })
        {
            var items = features.Where(item => item.Animated == animated).OrderBy(item => item.Path, Scalar).ToArray();
            var assigned = new bool[items.Length];
            Node? tree = null;
            if (!animated)
            {
                for (int i = 0; i < items.Length; i++)
                {
                    token.ThrowIfCancellationRequested();
                    if (tree is null) tree = new(items[i].Hashes[0], i);
                    else tree.Add(items[i].Hashes[0], i, token);
                }
            }
            for (int anchor = 0; anchor < items.Length; anchor++)
            {
                token.ThrowIfCancellationRequested();
                if (!assigned[anchor])
                {
                    var members = new List<int>();
                    if (animated)
                    {
                        members.Add(anchor);
                        for (int candidate = anchor + 1; candidate < items.Length; candidate++)
                        {
                            token.ThrowIfCancellationRequested();
                            if (!assigned[candidate] && Matches(items[anchor].Hashes, items[candidate].Hashes, threshold)) members.Add(candidate);
                        }
                    }
                    else
                    {
                        tree!.Find(items[anchor].Hashes[0], threshold, assigned, members, token);
                        members.Sort();
                    }
                    if (members.Count > 1)
                    {
                        foreach (int index in members) assigned[index] = true;
                        result.Add(members.Select(index => items[index].Image()).ToArray());
                    }
                }
                done++;
                progress?.Report(new("match", done, features.Count, null));
            }
        }
        return result.ToArray();
    }

    private static bool Matches(long[] a, long[] b, int threshold)
    {
        int misses = 0;
        for (int i = 0; i < 8; i++)
            if (Distance(a[i], b[i]) > threshold && ++misses > 1) return false;
        return true;
    }

    private static int Distance(long a, long b)
    {
        ulong remaining = (ulong)(a ^ b);
        int count = 0;
        while (remaining != 0) { remaining &= remaining - 1; count++; }
        return count;
    }

    private sealed class Node(long value, int index)
    {
        private readonly long value = value;
        private readonly List<int> indices = [index];
        private readonly Dictionary<int, Node> edges = new();

        internal void Add(long hash, int index, CancellationToken token)
        {
            var node = this;
            while (true)
            {
                token.ThrowIfCancellationRequested();
                int edge = Distance(node.value, hash);
                if (edge == 0) { node.indices.Add(index); break; }
                if (node.edges.TryGetValue(edge, out var child)) node = child;
                else { node.edges.Add(edge, new(hash, index)); break; }
            }
        }

        internal void Find(long hash, int threshold, bool[] assigned, List<int> output, CancellationToken token)
        {
            var pending = new Stack<Node>();
            pending.Push(this);
            while (pending.Count > 0)
            {
                token.ThrowIfCancellationRequested();
                var node = pending.Pop();
                int distance = Distance(node.value, hash);
                if (distance <= threshold)
                    foreach (int i in node.indices)
                        if (!assigned[i]) output.Add(i);
                foreach (var edge in node.edges)
                    if (Math.Abs(edge.Key - distance) <= threshold) pending.Push(edge.Value);
            }
        }
    }
}
