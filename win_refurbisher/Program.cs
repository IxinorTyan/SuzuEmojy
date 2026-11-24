using System;
using System.IO;
using System.Threading;
using SuzuEmojy.Windows;

internal static class Program
{
    // No UI loop: WIC codecs and COM cleanup run in the multithreaded apartment.
    [MTAThread]
    private static int Main(string[] args)
    {
        Console.OutputEncoding = System.Text.Encoding.UTF8;
        if (args.Length != 2)
        {
            Console.Error.WriteLine("用法：SuzuEmojy.LibraryRefurbisher.exe <旧图库 data 目录> <尚不存在的新目录>");
            return 2;
        }
        var source = args[0];
        var destination = args[1];

        using var cancellation = new CancellationTokenSource();
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; cancellation.Cancel(); };
        try
        {
            var result = LibraryRefurbish.Run(source, destination, cancellation.Token,
                (count, total) => Console.WriteLine($"已处理 {count}/{total}"));
            Console.WriteLine($"图库副本：{Path.GetFullPath(destination)}");
            Console.WriteLine($"共 {result.Images} 张，清洗 {result.Recleaned} 张（缩小 {result.Resized} 张），" +
                $"原样保留动图 {result.PreservedAnimations} 张，未清洗 {result.UnchangedBecauseUnreadable.Length} 张。");
            Console.WriteLine($"精确重复 {result.ExactDuplicateGroups.Length} 组，未合并。详见 refurbish-report.json。");
            return 0;
        }
        catch (OperationCanceledException)
        {
            Console.Error.WriteLine("已取消。原图库未修改，未发布新图库。");
            return 130;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error);
            return 1;
        }
    }
}
