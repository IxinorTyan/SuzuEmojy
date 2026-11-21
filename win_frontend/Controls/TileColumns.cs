using System;

namespace SuzuEmojy.Windows.Controls;

// Fit whole tiles first, then optionally distribute unused space between columns.
internal readonly record struct TileColumns(int Count, double Stride)
{
    internal static TileColumns Fit(double available, double tile, double gap, bool distribute)
    {
        int count = Math.Max(1, (int)Math.Floor((available + gap) / (tile + gap)));
        double stride = distribute && count > 1 ? (available - tile) / (count - 1) : tile + gap;
        return new(count, stride);
    }
}
