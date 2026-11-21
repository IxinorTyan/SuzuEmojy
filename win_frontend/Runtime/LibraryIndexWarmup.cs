using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading;

namespace SuzuEmojy.Windows;

// Backfill only v1 resource identities. No original or old hash column is modified.
internal static class LibraryIndexWarmup
{
    private const int ApngRuleVersion = 1;
    internal readonly record struct Outcome(int Count);
    private readonly record struct Receipt(long Size, long Modified);
    private readonly record struct Pending(FileInfo File, Receipt Receipt, string? Key);

    internal static Outcome Run(string data, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        var root = Path.GetFullPath(data);
        if (!Directory.Exists(root)) return default;
        root = RuntimePrimitives.ExistingPath(root);
        RuntimePrimitives.EnsureNotReparse(Path.Combine(root, "images"));
        var paths = LibrarySnapshotReader.ImageFiles(root, token)
            .Where(x => Path.GetExtension(x.Name).ToLowerInvariant() is ".png" or ".gif").Select(x => new FileInfo(x.Path)).ToArray();
        if (paths.Length == 0) return default;
        var dbPath = Path.Combine(root, "features.db");
        RuntimePrimitives.EnsureNotReparse(dbPath);
        if (!File.Exists(dbPath) && File.Exists(Path.Combine(root, "hashes.json"))) return default;

        using var db = WindowsSqlite.OpenForWrite(dbPath);
        db.Execute("BEGIN IMMEDIATE", CancellationToken.None);
        EnsureSchema(db, CancellationToken.None);
        db.Execute("COMMIT", CancellationToken.None);
        var keyed = ReadReceipts(db.Read("SELECT image_path, file_size, mtime_ns FROM image_features WHERE sync_key IS NOT NULL", 3, token));
        var excluded = ReadReceipts(db.Read("SELECT image_path, file_size, mtime_ns FROM sync_key_checks WHERE rule_version = ?", 3, token,
            ApngRuleVersion));
        var pending = new List<Pending>(30);
        int count = 0;

        foreach (var file in paths)
        {
            token.ThrowIfCancellationRequested();
            if (TryStat(file) is not { } before ||
                keyed.TryGetValue(file.Name, out var keyReceipt) && keyReceipt == before ||
                excluded.TryGetValue(file.Name, out var exclusionReceipt) && exclusionReceipt == before)
                continue;
            try
            {
                using var image = File.Open(file.FullName, FileMode.Open, FileAccess.Read, FileShare.Read);
                if (file.Extension.Equals(".png", StringComparison.OrdinalIgnoreCase) && ApngDecoder.HasAnimationControl(image))
                    pending.Add(new(file, before, null));
                else if (ResourceIdentityV1.TryRead(image, token) is { } key)
                    pending.Add(new(file, before, key));
                else
                    pending.Add(new(file, before, null));
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException)
            { continue; }
            if (pending.Count == 30) Flush();
        }
        Flush();
        return new(count);

        void Flush()
        {
            if (pending.Count == 0) return;
            db.Execute("BEGIN IMMEDIATE", token);
            foreach (var record in pending)
            {
                if (TryStat(record.File) != record.Receipt) continue;
                var name = record.File.Name;
                var (size, mtime) = record.Receipt;
                if (record.Key is { } key)
                {
                    db.Execute("INSERT OR IGNORE INTO image_features(image_path) VALUES (?)", token, name);
                    db.Execute("UPDATE image_features SET sync_key = ?, file_size = ?, mtime_ns = ? WHERE image_path = ?",
                        token, key, size, mtime, name);
                    db.Execute("DELETE FROM sync_key_checks WHERE image_path = ?", token, name);
                    count++;
                }
                else
                    db.Execute("INSERT OR REPLACE INTO sync_key_checks(image_path, rule_version, file_size, mtime_ns) VALUES (?, ?, ?, ?)",
                        token, name, ApngRuleVersion, size, mtime);
            }
            db.Execute("COMMIT", token);
            pending.Clear();
        }
    }

    internal static void EnsureSchema(WindowsSqlite db, CancellationToken token)
    {
        db.Execute("CREATE TABLE IF NOT EXISTS image_features (image_path TEXT PRIMARY KEY, md5 TEXT, dhash TEXT, phash TEXT, " +
            "quality_score REAL DEFAULT 0.0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)", token);
        var columns = new HashSet<string?>(db.Read("PRAGMA table_info(image_features)", 2, token).Select(row => row[1]), StringComparer.Ordinal);
        if (!columns.Contains("sync_key")) db.Execute("ALTER TABLE image_features ADD COLUMN sync_key TEXT", token);
        if (!columns.Contains("file_size")) db.Execute("ALTER TABLE image_features ADD COLUMN file_size INTEGER", token);
        if (!columns.Contains("mtime_ns")) db.Execute("ALTER TABLE image_features ADD COLUMN mtime_ns INTEGER", token);
        db.Execute("CREATE INDEX IF NOT EXISTS idx_features_sync_key ON image_features(sync_key)", token);
        db.Execute("CREATE TABLE IF NOT EXISTS sync_key_checks (image_path TEXT PRIMARY KEY, rule_version INTEGER NOT NULL, " +
            "file_size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL)", token);
    }

    private static Dictionary<string, Receipt> ReadReceipts(IEnumerable<string?[]> rows)
    {
        var result = new Dictionary<string, Receipt>(StringComparer.Ordinal);
        foreach (var row in rows)
            if (row[0] is { } name && long.TryParse(row[1], NumberStyles.Integer, CultureInfo.InvariantCulture, out long size) &&
                long.TryParse(row[2], NumberStyles.Integer, CultureInfo.InvariantCulture, out long time))
                result[name] = new(size, time);
        return result;
    }

    private static Receipt? TryStat(FileInfo file)
    {
        try
        {
            file.Refresh();
            if (!file.Exists || (file.Attributes & FileAttributes.ReparsePoint) != 0) return null;
            return new(file.Length, checked((file.LastWriteTimeUtc.Ticks - RuntimePrimitives.UnixEpoch.Ticks) * 100));
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or OverflowException) { return null; }
    }

}
