using System.Collections.Generic;
using System.IO;
using System.Threading;

namespace SuzuEmojy.Windows;

// Multi-index edits use one rollback-journal transaction across attached,
// already-initialized SQLite files. Avoid partial cross-DB commits in WAL mode.
internal static class LibraryDatabases
{
    internal static bool CanAttach(string root, bool recent, CancellationToken token)
    {
        var databases = new List<string> { "features.db", "metadata.db", "categories.db", "order.db" };
        if (recent) databases.Add("recent.db");
        foreach (var name in databases)
        {
            string path = Path.Combine(root, name);
            RuntimePrimitives.EnsureNotReparse(path);
            using var reader = WindowsSqlite.OpenIfPresent(path);
            if (reader is not null && !RollbackJournal(reader, "main", token)) return false;
        }
        return true;
    }

    internal static WindowsSqlite? TryOpen(string root, bool recent, CancellationToken token, out bool hasRecent)
    {
        var features = Path.Combine(root, "features.db");
        var attachments = new List<(string File, string Alias)>
        {
            ("metadata.db", "meta"), ("categories.db", "category"), ("order.db", "gallery_order")
        };
        var history = Path.Combine(root, "recent.db");
        if (recent) RuntimePrimitives.EnsureNotReparse(history);
        hasRecent = recent && File.Exists(history);
        if (hasRecent) attachments.Add(("recent.db", "history"));

        if (!Compatible(features)) return null;
        foreach (var item in attachments)
            if (!Compatible(Path.Combine(root, item.File))) return null;
        Init("metadata.db", LibraryEdits.CreateMetadata);
        Init("categories.db", LibraryEdits.CreateCategories, LibraryEdits.CreateMembership);
        Init("order.db", LibraryEdits.CreateOrder);
        var db = WindowsSqlite.OpenForWrite(features);
        bool ready = false;
        try
        {
            db.Execute("BEGIN IMMEDIATE", CancellationToken.None);
            LibraryIndexWarmup.EnsureSchema(db, CancellationToken.None);
            db.Execute("COMMIT", CancellationToken.None);
            foreach (var item in attachments)
                db.Execute($"ATTACH DATABASE ? AS {item.Alias}", token, Path.Combine(root, item.File));
            if (!RollbackJournal(db, "main", token)) return null;
            foreach (var item in attachments)
                if (!RollbackJournal(db, item.Alias, token)) return null;
            db.Execute("BEGIN IMMEDIATE", token);
            ready = true;
            return db;
        }
        finally { if (!ready) db.Dispose(); }

        bool Compatible(string path)
        {
            RuntimePrimitives.EnsureNotReparse(path);
            using var reader = WindowsSqlite.OpenIfPresent(path);
            return reader is null || RollbackJournal(reader, "main", token);
        }

        void Init(string name, params string[] statements)
        {
            using var initializer = LibraryEdits.Open(root, name, token, statements);
            initializer.Execute("COMMIT", CancellationToken.None);
        }
    }

    private static bool RollbackJournal(WindowsSqlite db, string schema, CancellationToken token)
    {
        var rows = db.Read($"PRAGMA {schema}.journal_mode", 1, token);
        return rows.Count == 1 && rows[0][0] is "delete" or "truncate" or "persist";
    }
}
