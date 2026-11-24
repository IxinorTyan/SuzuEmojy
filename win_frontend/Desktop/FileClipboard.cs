using System;
using System.Buffers.Binary;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace SuzuEmojy.Windows.Desktop;

internal static class FileClipboard
{
    private const uint FileDropFormat = 15;
    private const uint DibV5Format = 17;
    private const uint MovableMemory = 0x0002;

    internal static uint Sequence => GetClipboardSequenceNumber();

    internal enum CopyResult { Copied, Superseded, Busy, Failed }
    internal delegate CopyResult Writer(nint owner, ClipboardMedia media, uint expectedSequence, out uint sequence);

    internal static CopyResult TryCopy(nint owner, ClipboardMedia media, uint expectedSequence, out uint sequence)
    {
        sequence = 0;
        if (!File.Exists(media.Path)) return CopyResult.Failed;
        var files = Allocate(DropFilesPayload(media.Path));
        if (files == 0) return CopyResult.Failed;
        nint bitmap = 0;
        try
        {
            if (media.DibV5 is not null) bitmap = Allocate(media.DibV5);
            if (!OpenClipboard(owner)) return CopyResult.Busy;
            try
            {
                if (expectedSequence != 0 && Sequence != expectedSequence) return CopyResult.Superseded;
                if (!EmptyClipboard() || SetClipboardData(FileDropFormat, files) == 0) return CopyResult.Failed;
                files = 0; // Windows now owns the HGLOBAL.
                if (bitmap != 0 && SetClipboardData(DibV5Format, bitmap) != 0) bitmap = 0;
            }
            finally { CloseClipboard(); }
            // Read the published generation only after CloseClipboard. If another
            // writer took ownership, its generation cannot authorize our paste.
            if (GetClipboardOwner() != owner) return CopyResult.Superseded;
            sequence = Sequence;
            return GetClipboardOwner() == owner && Sequence == sequence ? CopyResult.Copied : CopyResult.Superseded;
        }
        finally
        {
            if (files != 0) GlobalFree(files);
            if (bitmap != 0) GlobalFree(bitmap);
        }
    }

    private static nint Allocate(byte[] payload)
    {
        var memory = GlobalAlloc(MovableMemory, (nuint)payload.Length);
        if (memory == 0) return 0;
        var buffer = GlobalLock(memory);
        if (buffer == 0) { GlobalFree(memory); return 0; }
        try { Marshal.Copy(payload, 0, buffer, payload.Length); }
        finally { GlobalUnlock(memory); }
        return memory;
    }

    internal static byte[] DropFilesPayload(string path)
    {
        const int headerSize = 20; // DROPFILES uses only 32-bit fields.
        byte[] files = Encoding.Unicode.GetBytes(path + "\0\0");
        var payload = new byte[headerSize + files.Length];
        BinaryPrimitives.WriteUInt32LittleEndian(payload, headerSize);
        BinaryPrimitives.WriteUInt32LittleEndian(payload.AsSpan(16), 1); // fWide
        files.CopyTo(payload, headerSize);
        return payload;
    }

    [DllImport("kernel32.dll")] private static extern nint GlobalAlloc(uint flags, nuint bytes);
    [DllImport("kernel32.dll")] private static extern nint GlobalLock(nint memory);
    [DllImport("kernel32.dll")] private static extern bool GlobalUnlock(nint memory);
    [DllImport("kernel32.dll")] private static extern nint GlobalFree(nint memory);
    [DllImport("user32.dll")] private static extern bool OpenClipboard(nint owner);
    [DllImport("user32.dll")] private static extern bool EmptyClipboard();
    [DllImport("user32.dll")] private static extern nint SetClipboardData(uint format, nint data);
    [DllImport("user32.dll")] private static extern bool CloseClipboard();
    [DllImport("user32.dll")] private static extern nint GetClipboardOwner();
    [DllImport("user32.dll")] private static extern uint GetClipboardSequenceNumber();
}
