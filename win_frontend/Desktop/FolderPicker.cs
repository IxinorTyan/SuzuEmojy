using System.Windows;
#if NETFRAMEWORK
using System;
using System.Runtime.InteropServices;
using System.Windows.Interop;
#else
using Microsoft.Win32;
#endif

namespace SuzuEmojy.Windows.Desktop;

internal static class FolderPicker
{
    internal static string? Choose(Window owner, string title)
    {
#if NETFRAMEWORK
        // WPF added OpenFolderDialog in .NET 8. Use the same Windows Shell picker on .NET Framework.
        var dialog = (IFileDialog)new FileOpenDialog();
        try
        {
            dialog.GetOptions(out uint options);
            dialog.SetOptions(options | 0x20 | 0x40); // FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM
            dialog.SetTitle(title);
            int result = dialog.Show(new WindowInteropHelper(owner).Handle);
            if (result == unchecked((int)0x800704C7)) return null; // ERROR_CANCELLED
            Marshal.ThrowExceptionForHR(result);
            dialog.GetResult(out var item);
            try
            {
                item.GetDisplayName(0x80058000, out var path); // SIGDN_FILESYSPATH
                try { return Marshal.PtrToStringUni(path); }
                finally { Marshal.FreeCoTaskMem(path); }
            }
            finally { Marshal.ReleaseComObject(item); }
        }
        finally { Marshal.ReleaseComObject(dialog); }
#else
        var dialog = new OpenFolderDialog { Title = title };
        return dialog.ShowDialog(owner) == true ? dialog.FolderName : null;
#endif
    }

#if NETFRAMEWORK
    [ComImport, Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")]
    private class FileOpenDialog { }

    // The unused methods retain their vtable slots; only the declared signatures below are called.
    [ComImport, Guid("42F85136-DB7E-439C-85F1-E4075D135FC8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IFileDialog
    {
        [PreserveSig] int Show(nint owner);
        void SetFileTypes(); void SetFileTypeIndex(); void GetFileTypeIndex(); void Advise(); void Unadvise();
        void SetOptions(uint options);
        void GetOptions(out uint options);
        void SetDefaultFolder(); void SetFolder(); void GetFolder(); void GetCurrentSelection(); void SetFileName(); void GetFileName();
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
        void SetOkButtonLabel(); void SetFileNameLabel();
        void GetResult(out IShellItem item);
    }

    [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IShellItem
    {
        void BindToHandler(); void GetParent();
        void GetDisplayName(uint form, out nint path);
    }
#endif
}
