using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace SuzuEmojy.Windows;

internal sealed class LibrarySqliteException(string message) : IOException(message);

// Use the Windows inbox SQLite only. Snapshots always open a read-only connection.
internal sealed class WindowsSqlite : IDisposable
{
    private const string Dll = "winsqlite3.dll";
    private const int Ok = 0, Row = 100, Done = 101;
    private readonly Connection database;
    private readonly string name;

    private WindowsSqlite(Connection database, string path)
    {
        this.database = database;
        name = Path.GetFileName(path);
    }

    internal static WindowsSqlite? OpenIfPresent(string path)
    {
        if (!File.Exists(path)) return null;
        return Open(path, 1); // SQLITE_OPEN_READONLY
    }

    internal static WindowsSqlite OpenForWrite(string path) => Open(path, 2 | 4); // READWRITE | CREATE

    private static WindowsSqlite Open(string path, int flags)
    {
        int result = sqlite3_open_v2(Utf8(path), out var db, flags, 0);
        var connection = new WindowsSqlite(db, path);
        try
        {
            connection.Verify(result);
            connection.Verify(sqlite3_busy_timeout(db, flags == 1 ? 1000 : 5000));
            return connection;
        }
        catch { connection.Dispose(); throw; }
    }

    internal List<string?[]> Read(string sql, int columns, CancellationToken token, params object?[] parameters)
    {
        token.ThrowIfCancellationRequested();
        int prepared = sqlite3_prepare_v2(database, Utf8(sql), -1, out var statement, 0);
        using (statement)
        {
            Verify(prepared);
            if (statement.IsInvalid || sqlite3_bind_parameter_count(statement) != parameters.Length)
                throw new ArgumentException("Invalid SQLite statement or parameter count.");
            for (int i = 0; i < parameters.Length; i++)
                Verify(parameters[i] switch
                {
                    string value => BindText(statement, i + 1, value),
                    int value => sqlite3_bind_int(statement, i + 1, value),
                    long value => sqlite3_bind_int64(statement, i + 1, value),
                    double value => sqlite3_bind_double(statement, i + 1, value),
                    null => sqlite3_bind_null(statement, i + 1),
                    _ => throw new ArgumentException("Unsupported SQLite parameter type.")
                });
            var values = new List<string?[]>();
            while (true)
            {
                token.ThrowIfCancellationRequested();
                int result = sqlite3_step(statement);
                if (result == Done) return values;
                if (result != Row) Verify(result);
                var row = new string?[columns];
                for (int i = 0; i < columns; i++)
                {
                    var text = sqlite3_column_text(statement, i);
                    if (text != 0) row[i] = ReadUtf8(text, sqlite3_column_bytes(statement, i));
                }
                values.Add(row);
            }
        }
    }

    internal void Execute(string sql, CancellationToken token, params object?[] parameters) =>
        Read(sql, 0, token, parameters);

    internal void BackupTo(string path, CancellationToken token)
    {
        using var target = OpenForWrite(path);
        var backup = sqlite3_backup_init(target.database, Utf8("main"), database, Utf8("main"));
        if (backup == 0)
        {
            target.Verify(sqlite3_errcode(target.database));
            throw new LibrarySqliteException("无法备份图库索引");
        }
        int status = Ok;
        try
        {
            int retries = 0;
            while (true)
            {
                token.ThrowIfCancellationRequested();
                status = sqlite3_backup_step(backup, 128);
                if (status == Done) break;
                if (status == Ok) { retries = 0; continue; }
                if (status is not (5 or 6) || ++retries > 20) break; // SQLITE_BUSY/LOCKED
                token.WaitHandle.WaitOne(100);
            }
        }
        finally
        {
            int finished = sqlite3_backup_finish(backup);
            if (status == Done) target.Verify(finished);
        }
        if (status != Done) target.Verify(status);
    }

    private static int BindText(Statement statement, int index, string value)
    {
        var utf8 = Utf8(value);
        return sqlite3_bind_text(statement, index, utf8, utf8.Length - 1, (nint)(-1)); // SQLITE_TRANSIENT
    }

    private void Verify(int code)
    {
        if (code == Ok) return;
        var text = database.IsInvalid ? 0 : sqlite3_errmsg(database);
        int length = 0;
        while (text != 0 && Marshal.ReadByte(text, length) != 0) length++;
        throw new LibrarySqliteException($"无法访问图库索引 {name} ({code})：{ReadUtf8(text, length)}");
    }

    private static byte[] Utf8(string value) => Encoding.UTF8.GetBytes(value + '\0');

    private static string ReadUtf8(nint pointer, int size)
    {
        if (size == 0) return "";
        var bytes = new byte[size];
        Marshal.Copy(pointer, bytes, 0, size);
        return Encoding.UTF8.GetString(bytes);
    }

    public void Dispose() => database.Dispose();

    private sealed class Connection : SafeHandleZeroOrMinusOneIsInvalid
    {
        public Connection() : base(true) { }
        protected override bool ReleaseHandle() => sqlite3_close(handle) == Ok;
    }

    private sealed class Statement : SafeHandleZeroOrMinusOneIsInvalid
    {
        public Statement() : base(true) { }
        protected override bool ReleaseHandle()
        {
            sqlite3_finalize(handle); // Finalize always releases the statement, even after a failed step.
            return true;
        }
    }

    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_open_v2(byte[] name, out Connection connection, int flags, nint vfs);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_close(nint connection);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_busy_timeout(Connection connection, int milliseconds);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern nint sqlite3_errmsg(Connection connection);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_errcode(Connection connection);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern nint sqlite3_backup_init(Connection destination, byte[] destinationName, Connection source, byte[] sourceName);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_backup_step(nint backup, int pages);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_backup_finish(nint backup);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_prepare_v2(Connection connection, byte[] sql, int length, out Statement statement, nint tail);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_step(Statement statement);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_finalize(nint statement);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern nint sqlite3_column_text(Statement statement, int column);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_column_bytes(Statement statement, int column);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_bind_text(Statement statement, int index, byte[] value, int length, nint destructor);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_bind_int(Statement statement, int index, int value);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_bind_int64(Statement statement, int index, long value);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_bind_double(Statement statement, int index, double value);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_bind_null(Statement statement, int index);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport(Dll, CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_bind_parameter_count(Statement statement);
}
