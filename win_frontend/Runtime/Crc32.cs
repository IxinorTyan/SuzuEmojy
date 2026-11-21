using System;

namespace SuzuEmojy.Windows;

internal static class Crc32
{
    internal const uint Start = uint.MaxValue;
    private static readonly uint[] Table = MakeTable();

    internal static uint Update(uint crc, ReadOnlySpan<byte> bytes)
    {
        foreach (byte value in bytes) crc = Table[(crc ^ value) & 255] ^ (crc >> 8);
        return crc;
    }

    internal static uint Finish(uint crc) => ~crc;

    private static uint[] MakeTable()
    {
        var table = new uint[256];
        for (uint i = 0; i < table.Length; i++)
        {
            uint value = i;
            for (int j = 0; j < 8; j++) value = (value & 1) == 0 ? value >> 1 : (value >> 1) ^ 0xedb88320;
            table[i] = value;
        }
        return table;
    }
}
