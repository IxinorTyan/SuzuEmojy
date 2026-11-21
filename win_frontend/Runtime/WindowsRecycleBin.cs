using System;
using System.IO;
using System.Runtime.ExceptionServices;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace SuzuEmojy.Windows;

internal static class WindowsRecycleBin
{
    internal static void Recycle(string path)
    {
        var volume = new StringBuilder(32768);
        if (!GetVolumePathNameW(path, volume, (uint)volume.Capacity) || GetDriveTypeW(volume.ToString()) != 3)
            throw new IOException("此磁盘不支持回收站");

        // Library workers run on the pool. The shell operation has its own STA
        // and never presents a system confirmation window or error dialog.
        if (Thread.CurrentThread.GetApartmentState() != ApartmentState.STA)
        {
            ExceptionDispatchInfo? failure = null;
            var thread = new Thread(() =>
            {
                try { Perform(path); }
                catch (Exception error) { failure = ExceptionDispatchInfo.Capture(error); }
            }) { IsBackground = true };
            thread.SetApartmentState(ApartmentState.STA);
            thread.Start();
            thread.Join();
            failure?.Throw();
        }
        else Perform(path);
    }

    private static void Perform(string path)
    {
        var clsid = new Guid("3ad05575-8857-4850-9277-11b85bdb8e09");
        var iid = new Guid("947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8");
        var itemId = new Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe");
        nint operation = 0, item = 0;
        try
        {
            Marshal.ThrowExceptionForHR(CoCreateInstance(ref clsid, 0, 1, ref iid, out operation));
            Marshal.ThrowExceptionForHR(SHCreateItemFromParsingName(path, 0, ref itemId, out item));
            const uint RecycleOnDelete = 0x00080000, NoConnectedElements = 0x2000, NoErrorUi = 0x0400,
                NoConfirmation = 0x0010, Silent = 0x0004;
            Marshal.ThrowExceptionForHR(Method<SetFlags>(operation, 5)(operation,
                RecycleOnDelete | NoConnectedElements | NoErrorUi | NoConfirmation | Silent));
            Marshal.ThrowExceptionForHR(Method<DeleteItem>(operation, 18)(operation, item, 0));
            Marshal.ThrowExceptionForHR(Method<PerformOperations>(operation, 21)(operation));
            Marshal.ThrowExceptionForHR(Method<OperationsAborted>(operation, 22)(operation, out var aborted));
            if (aborted || File.Exists(path)) throw new IOException("未能移到回收站");
        }
        finally
        {
            if (item != 0) Marshal.Release(item);
            if (operation != 0) Marshal.Release(operation);
        }
    }

    private static T Method<T>(nint instance, int slot) where T : Delegate =>
        Marshal.GetDelegateForFunctionPointer<T>(Marshal.ReadIntPtr(Marshal.ReadIntPtr(instance), slot * IntPtr.Size));

    [UnmanagedFunctionPointer(CallingConvention.StdCall)] private delegate int SetFlags(nint self, uint flags);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] private delegate int DeleteItem(nint self, nint item, nint sink);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] private delegate int PerformOperations(nint self);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] private delegate int OperationsAborted(nint self, [MarshalAs(UnmanagedType.Bool)] out bool aborted);
    [DllImport("ole32.dll")] private static extern int CoCreateInstance(ref Guid clsid, nint outer, uint context, ref Guid iid, out nint result);
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern int SHCreateItemFromParsingName(string path, nint context, ref Guid iid, out nint result);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern bool GetVolumePathNameW(string path, StringBuilder volume, uint length);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern uint GetDriveTypeW(string volume);
}
