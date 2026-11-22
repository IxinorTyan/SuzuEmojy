using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

namespace SuzuEmojy.Windows;

internal sealed class UpdateTransaction(string root)
{
    internal const string PlanFile = "plan";
    private string Work => Path.Combine(root, PortableLayout.Journal);
    private string Plan => Path.Combine(Work, PlanFile);
    private string Stage => Path.Combine(Work, "new");
    private string Backup => Path.Combine(Work, "old");
    private string Committed => Path.Combine(Work, "committed");

    internal void Apply(string zipPath, string digest, string version, Action probe)
    {
        AssertAncestors(root);
        AssertPlain(Work);
        if (Directory.Exists(Work)) Recover();
        Directory.CreateDirectory(Work);
        try
        {
            using (var source = File.Open(zipPath, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                using var sha = SHA256.Create();
                var hash = BitConverter.ToString(sha.ComputeHash(source)).Replace("-", "");
                if (!hash.Equals(digest, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("更新包校验失败。");
                source.Position = 0;
                using var zip = new ZipArchive(source, ZipArchiveMode.Read, leaveOpen: true);
                var files = PackageLayout.Validate(zip, version);
                var plan = new List<string>(files.Length);
                var buffer = new byte[65536];
                foreach (var entry in files)
                {
                    var name = entry.FullName;
                    // The outer launcher runs recovery itself, so it cannot
                    // be part of the replacement transaction.
                    if (name.Equals(PortableLayout.Launcher, StringComparison.OrdinalIgnoreCase)) continue;
                    var target = Child(root, name);
                    if (Directory.Exists(target)) throw new IOException($"文件名被目录占用：{name}");
                    plan.Add((File.Exists(target) ? "+" : "-") + name);
                    var staged = Child(Stage, name);
                    Directory.CreateDirectory(Path.GetDirectoryName(staged)!);
                    using var input = entry.Open();
                    using var output = new FileStream(staged, FileMode.CreateNew, FileAccess.Write, FileShare.None);
                    long remaining = entry.Length;
                    while (remaining != 0)
                    {
                        int count = input.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
                        if (count == 0) throw new InvalidDataException("ZIP 文件不完整。");
                        output.Write(buffer, 0, count);
                        remaining -= count;
                    }
                    if (input.ReadByte() != -1) throw new InvalidDataException("ZIP 文件超出声明大小。");
                    output.Flush(flushToDisk: true);
                }
                WriteReceipt(Plan, plan);
                foreach (var line in plan)
                {
                    var name = line.Substring(1);
                    var target = Child(root, name);
                    var staged = Child(Stage, name);
                    Directory.CreateDirectory(Path.GetDirectoryName(target)!);
                    if (line[0] == '+')
                    {
                        var original = Child(Backup, name);
                        Directory.CreateDirectory(Path.GetDirectoryName(original)!);
                        File.Replace(staged, target, original, ignoreMetadataErrors: false);
                    }
                    else File.Move(staged, target);
                }
            }
            probe();
            WriteReceipt(Committed, ["ok"]);
        }
        catch
        {
            Recover();
            throw;
        }
        Recover();
    }

    internal void Recover()
    {
        AssertAncestors(root);
        AssertPlain(Work);
        if (File.Exists(Work)) throw new IOException("更新日志路径被文件占用。");
        if (!Directory.Exists(Work)) return;
        AssertPlain(Plan);
        AssertPlain(Committed);
        AssertPlain(Plan + ".tmp");
        AssertPlain(Committed + ".tmp");
        if (Directory.EnumerateFileSystemEntries(Work).Any(path => Path.GetFileName(path) is not
                (PlanFile or "plan.tmp" or "committed" or "committed.tmp" or "new" or "old")))
            throw new IOException("更新目录包含未知文件，未修改程序文件。");
        var stagedFiles = CheckTree(Stage);
        var backups = CheckTree(Backup);
        if (File.Exists(Plan) && !File.Exists(Committed))
        {
            if (new FileInfo(Plan).Length > 65536) throw new IOException("更新日志过大。");
            var rows = File.ReadAllLines(Plan);
            if (rows.Length > 300 || rows.Any(line => line.Length < 2 || line[0] is not ('+' or '-') ||
                    !line.Substring(1).StartsWith(PortableLayout.ProgramDirectory + "/", StringComparison.OrdinalIgnoreCase) ||
                    !PackageLayout.Allowed(line.Substring(1))) ||
                rows.Select(line => line.Substring(1)).Distinct(StringComparer.OrdinalIgnoreCase).Count() != rows.Length)
                throw new IOException("更新日志无效，未修改程序文件。");
            foreach (var line in rows.Reverse())
            {
                var name = line.Substring(1);
                var target = Child(root, name);
                if (line[0] == '+')
                {
                    var previous = Child(Backup, name);
                    if (!File.Exists(previous)) continue;
                    if (File.Exists(target)) File.Replace(previous, target, null);
                    else File.Move(previous, target);
                }
                else if (!File.Exists(Child(Stage, name))) File.Delete(target);
            }
            File.Delete(Committed + ".tmp");
            WriteReceipt(Committed, ["rolled back"]);
        }
        // Keep the receipt until backups have been removed. An interrupted
        // committed cleanup must never be taken for an interrupted install.
        Purge(Backup, backups);
        Purge(Stage, stagedFiles);
        File.Delete(Plan);
        File.Delete(Plan + ".tmp");
        File.Delete(Committed);
        File.Delete(Committed + ".tmp");
        Directory.Delete(Work, recursive: false);
    }

    private static void WriteReceipt(string path, IEnumerable<string> lines)
    {
        var temporary = path + ".tmp";
        using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
        {
            var bytes = Encoding.UTF8.GetBytes(string.Join("\n", lines) + "\n");
            file.Write(bytes, 0, bytes.Length);
            file.Flush(flushToDisk: true);
        }
        File.Move(temporary, path);
    }

    private static string Child(string directory, string name)
    {
        AssertPlain(directory);
        foreach (var part in name.Split('/'))
        {
            directory = Path.Combine(directory, part);
            AssertPlain(directory);
        }
        return directory;
    }

    private static string[] CheckTree(string directory)
    {
        AssertPlain(directory);
        if (!Directory.Exists(directory)) return [];
        var files = new List<string>();
        void Visit(string path, string prefix, int depth)
        {
            if (depth > 3) throw new IOException("更新日志目录结构无效。");
            foreach (var item in Directory.EnumerateFileSystemEntries(path))
            {
                AssertPlain(item);
                var name = prefix + Path.GetFileName(item);
                if (Directory.Exists(item))
                {
                    if (!PackageLayout.AllowedDirectory(name)) throw new IOException("更新日志包含未知目录。");
                    Visit(item, name + "/", depth + 1);
                }
                else
                {
                    if (files.Count >= 300 || !PackageLayout.Allowed(name)) throw new IOException("更新日志包含未知文件。");
                    files.Add(item);
                }
            }
        }
        Visit(directory, "", 0);
        return files.ToArray();
    }

    private static void Purge(string directory, string[] files)
    {
        AssertPlain(directory);
        foreach (var item in files)
        {
            AssertPlain(item);
            File.Delete(item);
        }
        if (!Directory.Exists(directory)) return;
        foreach (var sub in Directory.GetDirectories(directory)) Purge(sub, []);
        Directory.Delete(directory, recursive: false);
    }

    internal static void AssertAncestors(string path)
    {
        for (DirectoryInfo? directory = new(path); directory is not null; directory = directory.Parent)
            AssertPlain(directory.FullName);
    }

    internal static void AssertPlain(string path)
    {
        try
        {
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) throw new IOException("更新路径不能包含链接。");
        }
        catch (FileNotFoundException) { }
        catch (DirectoryNotFoundException) { }
    }
}
