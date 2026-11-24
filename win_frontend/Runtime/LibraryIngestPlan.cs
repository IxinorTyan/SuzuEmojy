using System;
using System.IO;

namespace SuzuEmojy.Windows;

// A verified payload and its identity are independent of how the bytes arrived
// (exchange ZIP, local cleaning, or a future native network importer).
internal sealed record IngestAsset(long Length, uint Crc, Func<Stream> Open);

internal sealed record IngestResource(IngestAsset Asset, string Name, string SyncKey, string Md5,
    double Quality, string[] Keywords, int[] Categories, (string Path, string Revision)? UnscaledSource = null);
internal sealed record IngestIcon(string? Text = null, string? AttachmentName = null, IngestAsset? Attachment = null);
internal sealed record IngestCategory(int Ref, string Name, IngestIcon? Icon = null);
internal sealed record LibraryIngestPlan(IngestResource[] Resources, IngestCategory[] Categories, bool Promote = false);
