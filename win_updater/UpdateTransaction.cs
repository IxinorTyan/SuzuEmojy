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
    private string Work => Path.Combine(root, PackageLayout.JournalDirectory);
    private string Plan => Path.Combine(Work, PlanFile);
    private string Stage => Path.Combine(Work, "new");
    private string Backup => Path.Combine(Work, "old");
    private string Committed => Path.Combine(Work, "committed");

    internal void Apply(string zipPath, string digest, Action probe)
    {
        AssertPlain(root);
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
                var files = PackageLayout.Validate(zip);
                var plan = new List<string>(files.Length);
                foreach (var entry in files)
                {
                    var name = entry.FullName;
                    var target = Child(root, name);
                    if (Directory.Exists(target)) throw new IOException($"文件名被目录占用：{name}");
                    plan.Add((File.Exists(target) ? "+" : "-") + name);
                    var staged = Child(Stage, name);
                    Directory.CreateDirectory(Path.GetDirectoryName(staged)!);
                    using var input = entry.Open();
                    using var output = new FileStream(staged, FileMode.CreateNew, FileAccess.Write, FileShare.None);
                    input.CopyTo(output);
                    if (output.Length != entry.Length) throw new InvalidDataException("ZIP 文件不完整。");
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
        AssertPlain(root);
        AssertPlain(Work);
        if (!Directory.Exists(Work)) return;
        AssertPlain(Plan);
        AssertPlain(Committed);
        if (File.Exists(Plan) && !File.Exists(Committed))
        {
            var rows = File.ReadAllLines(Plan);
            if (rows.Length > 300 || rows.Any(line => line.Length < 2 || line[0] is not ('+' or '-') ||
                    !PackageLayout.Allowed(line.Substring(1))))
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
        }
        // Keep the receipt until backups have been removed. An interrupted
        // committed cleanup must never be taken for an interrupted install.
        Purge(Backup);
        Purge(Stage);
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

    private static void Purge(string directory)
    {
        AssertPlain(directory);
        if (!Directory.Exists(directory)) return;
        foreach (var item in Directory.EnumerateFileSystemEntries(directory))
        {
            AssertPlain(item);
            if (Directory.Exists(item)) Purge(item);
            else File.Delete(item);
        }
        Directory.Delete(directory, recursive: false);
    }

    private static void AssertPlain(string path)
    {
        try
        {
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) throw new IOException("更新路径不能包含链接。");
        }
        catch (FileNotFoundException) { }
        catch (DirectoryNotFoundException) { }
    }
}
