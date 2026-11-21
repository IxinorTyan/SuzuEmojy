using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading;

namespace SuzuEmojy.Windows;

// Classic stored ZIP has an exactly known byte cost. On .NET Framework,
// ZipArchive's NoCompression can still wrap bytes in deflate blocks.
internal sealed class StoredZipVolume(Stream stream)
{
    internal const int MaxEntries = 65534;
    internal const int Footer = 22;
    private const ushort Utf8 = 1 << 11, DosDate = (1 << 5) | 1; // 1980-01-01
    private readonly BinaryWriter writer = new(stream, Encoding.UTF8, leaveOpen: true);
    private readonly List<(byte[] Name, uint Length, uint Crc, uint Offset)> entries = [];

    internal static long EntrySize(string path, long length) => 76 + 2L * Encoding.UTF8.GetByteCount(path) + length;

    internal void Add(string path, byte[] value, byte[] buffer, CancellationToken token)
    {
        using var contents = new MemoryStream(value, writable: false);
        Write(path, contents, value.Length, Crc32.Finish(Crc32.Update(Crc32.Start, value)), buffer, token);
    }

    internal void Write(string path, Stream contents, long length, uint crc, byte[] buffer, CancellationToken token)
    {
        if (entries.Count >= MaxEntries) throw new InvalidDataException("分卷文件过多。");
        var name = Encoding.UTF8.GetBytes(path);
        uint size = checked((uint)length);
        uint offset = checked((uint)stream.Position);
        writer.Write(0x04034b50u);
        EntryHeader(size, crc, name);
        writer.Write(name);
        long written = 0;
        uint computed = Crc32.Start;
        int count;
        while ((count = contents.Read(buffer, 0, buffer.Length)) != 0)
        {
            token.ThrowIfCancellationRequested();
            written += count;
            if (written > size) throw new InvalidDataException("分卷资源大小已变化。");
            computed = Crc32.Update(computed, buffer.AsSpan(0, count));
            writer.Write(buffer, 0, count);
        }
        if (written != size || Crc32.Finish(computed) != crc) throw new InvalidDataException("分卷资源校验失败。");
        entries.Add((name, size, crc, offset));
    }

    internal void Finish()
    {
        uint start = checked((uint)stream.Position);
        foreach (var entry in entries)
        {
            writer.Write(0x02014b50u);
            writer.Write((ushort)20); // Version made by, MS-DOS.
            EntryHeader(entry.Length, entry.Crc, entry.Name);
            writer.Write((ushort)0); // Entry comment length.
            writer.Write((ushort)0); // Starting disk.
            writer.Write((ushort)0); // Internal attributes.
            writer.Write(0u); // External attributes.
            writer.Write(entry.Offset);
            writer.Write(entry.Name);
        }
        uint centralSize = checked((uint)stream.Position - start);
        writer.Write(0x06054b50u);
        writer.Write(0u); // This disk and the central directory disk.
        writer.Write((ushort)entries.Count);
        writer.Write((ushort)entries.Count);
        writer.Write(centralSize);
        writer.Write(start);
        writer.Write((ushort)0); // Archive comment.
        writer.Flush();
    }

    private void EntryHeader(uint size, uint crc, byte[] name)
    {
        writer.Write((ushort)20);
        writer.Write(Utf8);
        writer.Write((ushort)0); // Stored.
        writer.Write((ushort)0);
        writer.Write(DosDate);
        writer.Write(crc);
        writer.Write(size);
        writer.Write(size);
        writer.Write(checked((ushort)name.Length));
        writer.Write((ushort)0); // No ZIP64 or other extras.
    }
}
