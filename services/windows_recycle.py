"""Send one file to the Windows Recycle Bin; never fall back to unlink."""
import ctypes
from ctypes import wintypes
import os
from uuid import UUID


def recycle_file(path):
    if os.name != "nt":
        raise OSError("回收站仅支持 Windows")

    ole = ctypes.OleDLL("ole32")
    shell = ctypes.OleDLL("shell32")
    kernel = ctypes.WinDLL("kernel32")
    pointer = ctypes.c_void_p
    path = os.fspath(path)

    # Do not ask the shell to operate on network or removable volumes.
    volume = ctypes.create_unicode_buffer(32768)
    if (not kernel.GetVolumePathNameW(ctypes.c_wchar_p(path), volume, len(volume))
            or kernel.GetDriveTypeW(volume) != 3):  # DRIVE_FIXED
        raise OSError("此磁盘不支持回收站")

    def guid(value):
        return (ctypes.c_ubyte * 16).from_buffer_copy(UUID(value).bytes_le)

    def method(interface, slot, returns, *arguments):
        vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(pointer))).contents
        return ctypes.WINFUNCTYPE(returns, pointer, *arguments)(vtable[slot])

    def checked(code):
        if code < 0:
            raise OSError(f"回收站操作失败（0x{code & 0xffffffff:08X}）")

    ole.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    operation, item = pointer(), pointer()
    try:
        file_operation = guid("3ad05575-8857-4850-9277-11b85bdb8e09")
        i_file_operation = guid("947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8")
        i_shell_item = guid("43826d1e-e718-42ee-bc55-a1e261c37bfe")
        ole.CoCreateInstance(ctypes.byref(file_operation), None, 1, ctypes.byref(i_file_operation),
                             ctypes.byref(operation))  # CLSCTX_INPROC_SERVER
        shell.SHCreateItemFromParsingName(ctypes.c_wchar_p(path), None, ctypes.byref(i_shell_item),
                                          ctypes.byref(item))

        # IFileOperation::SetOperationFlags. FOFX_RECYCLEONDELETE requests
        # recycling explicitly, rather than merely preserving undo if possible.
        flags = (
            0x00080000  # FOFX_RECYCLEONDELETE
            | 0x00002000  # FOF_NO_CONNECTED_ELEMENTS (only the named file)
            | 0x00000400  # FOF_NOERRORUI
            | 0x00000010  # FOF_NOCONFIRMATION (confirmation shown in the app)
            | 0x00000004  # FOF_SILENT
        )
        checked(method(operation, 5, ctypes.c_long, wintypes.DWORD)(operation, flags))
        checked(method(operation, 18, ctypes.c_long, pointer, pointer)(operation, item, None))  # DeleteItem
        checked(method(operation, 21, ctypes.c_long)(operation))  # PerformOperations
        aborted = wintypes.BOOL()
        checked(method(operation, 22, ctypes.c_long, ctypes.POINTER(wintypes.BOOL))(operation, ctypes.byref(aborted)))
        if aborted.value or os.path.lexists(path):
            raise OSError("未能移到回收站")
    finally:
        for interface in (item, operation):
            if interface:
                method(interface, 2, wintypes.ULONG)(interface)  # IUnknown::Release
        ole.CoUninitialize()
