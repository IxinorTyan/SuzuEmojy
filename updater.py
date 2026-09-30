"""Standalone transactional installer. Built as a small PyInstaller executable.

Never imports the application or Qt. A complete backup precedes every mutation;
the durable journal is also the launchers' recovery gate after interruption.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from services.update_protocol import MANIFEST, atomic_json, child, read_manifest, version_key


def replace_file(source, target, work):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = work / "replacement.tmp"
    shutil.copy2(source, temporary)
    # Antivirus/file handles can linger briefly after the process exits.
    for attempt in range(20):
        try:
            os.replace(temporary, target)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.25)


def recover(root):
    root = Path(root).resolve()
    work = root / ".update"
    journal = work / "journal.json"
    if not journal.exists():
        return
    state = json.loads(journal.read_text(encoding="utf-8"))
    if state["phase"] == "replacing":
        for name, existed in state["entries"].items():
            target = child(root, name)
            if existed:
                replace_file(child(work / "backup", name), target, work)
            else:
                target.unlink(missing_ok=True)
    elif state["phase"] not in ("backing_up", "complete"):
        raise ValueError("Unknown update journal state; refusing to start")
    journal.unlink()


def install(root, stage):
    root, stage = Path(root).resolve(), Path(stage).resolve()
    work = root / ".update"
    if (work / "journal.json").exists():
        raise RuntimeError("Previous update must be recovered first")
    old, new = read_manifest(root), read_manifest(stage, verify=True)
    if old["flavor"] != new["flavor"] or version_key(new["version"]) <= version_key(old["version"]):
        raise ValueError("Package type/version mismatch")
    old_names = {n.casefold(): n for n in old["files"]}
    for name in new["files"]:
        if name.casefold() in old_names and old_names[name.casefold()] != name:
            raise ValueError("Case-only renames are unsupported: " + name)
        if name not in old["files"] and child(root, name).exists():
            raise ValueError("Update would overwrite an unowned file: " + name)
    names = sorted(set(old["files"]) | set(new["files"])) + [MANIFEST]
    entries = {}
    for name in names:
        target = child(root, name)
        if target.exists() and not target.is_file():
            raise ValueError("Update destination is not a file: " + name)
        entries[name] = target.exists()
    needed = sum(child(root, n).stat().st_size for n, exists in entries.items() if exists)
    if shutil.disk_usage(work).free < needed + max((child(stage, n).stat().st_size for n in new["files"]), default=0) + 16 * 1024 ** 2:
        raise OSError("Insufficient space for update backup")
    backup = work / "backup"
    # Only our fixed backup directory is retired, never user data.
    if backup.is_symlink() or (hasattr(backup, "is_junction") and backup.is_junction()):
        raise ValueError("Backup directory cannot be a link")
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir()
    journal = work / "journal.json"
    state = {"phase": "backing_up", "entries": entries, "from": old["version"], "to": new["version"]}
    atomic_json(journal, state)
    try:
        for name, existed in entries.items():
            if existed:
                destination = child(backup, name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(child(root, name), destination)
        state["phase"] = "replacing"
        atomic_json(journal, state)
        for name in names:
            target = child(root, name)
            if name in new["files"] or name == MANIFEST:
                replace_file(child(stage, name), target, work)
            else:
                target.unlink(missing_ok=True)
        read_manifest(root, verify=True)
        state["phase"] = "complete"
        atomic_json(journal, state)
        journal.unlink()
    except Exception:
        recover(root)
        raise


def wait_for_parent(pid, handshake):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        raise OSError("Cannot wait for the application process")
    try:
        handshake.write_text("ready", encoding="ascii")
        if kernel.WaitForSingleObject(handle, 120000) != 0:
            raise TimeoutError("Application did not exit; no files were changed")
    finally:
        kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    work = root / ".update"
    if not work.is_dir() or work.is_symlink() or (hasattr(work, "is_junction") and work.is_junction()):
        raise ValueError("Invalid update workspace")
    import msvcrt
    lock = (work / "installer.lock").open("a+b")
    lock.seek(0)
    # A second launch must not start another installer or the partially updated app.
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return
    restart = False
    error = None
    try:
        if args.recover:
            recover(root)
            restart = True
        else:
            if not args.pid:
                raise ValueError("Missing application PID")
            wait_for_parent(args.pid, work / "installer-ready")
            item = json.loads((work / "ready.json").read_text(encoding="utf-8"))
            if "/" in item["directory"] or not item["directory"].startswith("stage-"):
                raise ValueError("Invalid stage path")
            stage = child(work, item["directory"])
            install(root, stage)
            (work / "ready.json").unlink(missing_ok=True)
            shutil.rmtree(stage, ignore_errors=True)
            restart = True
    except Exception as exc:
        error = str(exc)
        # If rollback failed, keep the journal and refuse to launch mixed files.
        (work / "last-error.txt").write_text(error, encoding="utf-8")
    finally:
        (work / "installer-ready").unlink(missing_ok=True)
        lock.close()
    if error:
        ctypes.windll.user32.MessageBoxW(None, "更新未完成 / Update failed:\n\n" + error + "\n\n请重新启动软件以恢复，或查看 .update/last-error.txt。", "SuzuEmojy", 0x10)
    if restart:
        subprocess.Popen([str(root / "SuzuEmojy.exe")], cwd=str(root))


if __name__ == "__main__":
    main()
