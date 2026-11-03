"""Desktop folder scanning and bounded, ordered image preparation."""
import ctypes
import os
import re
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Semaphore

from PIL import Image
from PySide6.QtCore import QThread, Signal


RESERVED = {"全部表情", "未分类", "新建分类", "全部", "最近", "all"}


def category_name(name, fallback=None):
    name = name.strip()
    return name if name and name.casefold() not in RESERVED else fallback


def natural_key(name):
    return tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                 for part in re.split(r"(\d+)", name))


@dataclass
class FolderScan:
    items: list = field(default_factory=list)
    stats: dict = field(default_factory=lambda: dict(
        folder_name="", total_files=0, image_count=0, non_images=0,
        subdirs=0, issues=[]))


def scan_folders(folders, local_files, target, formats, cancelled=lambda: False,
                 progress=lambda directories, images: None):
    result = FolderScan()
    result.stats['folder_name'] = '、'.join(os.path.basename(os.path.normpath(p)) for p in folders)
    seen_items = set()

    def add(path, category):
        key = (os.path.normcase(os.path.abspath(path)), category)
        if key not in seen_items:
            seen_items.add(key)
            result.items.append((path, category))

    for path in local_files:
        add(path, target)
    directories = 0
    for root in folders:
        root_category = category_name(os.path.basename(os.path.normpath(root)), target)
        pending = [(root, root_category, True)]
        visited = set()
        while pending and not cancelled():
            path, category, is_root = pending.pop()
            # Do not follow junctions/symlinks: avoid cycles and escaping the chosen tree.
            real = os.path.normcase(os.path.realpath(path))
            if real in visited:
                continue
            visited.add(real)
            try:
                with os.scandir(path) as entries:
                    entries = sorted(entries, key=lambda e: (natural_key(e.name), e.name))
                children = []
                for entry in entries:
                    if cancelled():
                        break
                    try:
                        if entry.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(entry.path)):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            result.stats['subdirs'] += 1
                            children.append((entry.path, category_name(entry.name, category) if is_root else category, False))
                        elif entry.is_file(follow_symlinks=False):
                            result.stats['total_files'] += 1
                            if entry.name.lower().endswith(formats):
                                add(entry.path, category)
                            else:
                                result.stats['non_images'] += 1
                    except OSError as exc:
                        result.stats['issues'].append(f"{entry.path}: {exc}")
                pending.extend(reversed(children))
            except OSError as exc:
                result.stats['issues'].append(f"{path}: {exc}")
            directories += 1
            progress(directories, len(result.items))
    result.stats['total_files'] += len(local_files)
    result.stats['image_count'] = len(result.items)
    return result


def memory_budget():
    available = 2 * 1024**3
    if os.name == 'nt':
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in
                ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            available = status.available
    return max(32 * 1024**2, min(512 * 1024**2, available // 8))


def estimate_memory(path, budget):
    try:
        size = os.path.getsize(path)
        with Image.open(path) as image:
            pixels = image.width * image.height
            estimate = size * 3 + pixels * 24
            if getattr(image, 'is_animated', False):
                estimate += pixels * 8 * min(getattr(image, 'n_frames', 1), 32)
        return min(budget, max(1024**2, estimate))
    except Exception:
        return budget  # Unknown formats/converters run alone.


class ImportThread(QThread):
    progress = Signal(int, int)
    status = Signal(str)
    completed = Signal(int, int, int)

    def __init__(self, filepaths, storage, target_category, delete_after=False,
                 parent=None, file_categories=None, folders=None):
        super().__init__(parent)
        self.filepaths = list(filepaths)
        self.storage = storage
        self.target_category = target_category
        self.delete_after = delete_after
        self.file_categories = file_categories or {}
        self.folders = list(folders or [])
        self.folder_stats = None
        self.issues = []
        self.parallelism = 1
        self.was_cancelled = False
        self._conversion_slots = Semaphore(2)
        self._last_update = 0

    def report(self, message):
        now = time.monotonic()
        if now - self._last_update >= .1:
            self.status.emit(message)
            self._last_update = now

    def prepare(self, path):
        # The converters may themselves use multiple CPU threads.
        heavy = path.lower().endswith(('.webm', '.tgs'))
        if not heavy:
            try:
                with Image.open(path) as image:
                    heavy = getattr(image, 'is_animated', False)
            except OSError:
                pass
        if heavy:
            with self._conversion_slots:
                return self.storage.prepare_file(path)
        return self.storage.prepare_file(path)

    def cleanup_source(self, path, failed=False):
        if not self.delete_after or not os.path.isfile(path):
            return
        try:
            if failed:
                import shutil
                import uuid
                inbox = os.path.normcase(os.path.abspath(self.storage.inbox_dir))
                source = os.path.normcase(os.path.abspath(path))
                if os.path.commonpath([source, inbox]) == inbox:
                    destination = os.path.join(self.storage.inbox_failed_dir, uuid.uuid4().hex + '_' + os.path.basename(path))
                    shutil.move(path, destination)
                # Failed sources outside the inbox must remain available for retry.
            else:
                os.remove(path)
        except OSError as exc:
            self.issues.append(f"清理 {path}: {exc}")

    def run(self):
        saved = skipped = failed = 0
        committed = []
        try:
            if self.folders:
                self.status.emit('正在扫描文件夹…')
                scan = scan_folders(self.folders, self.filepaths, self.target_category,
                                    self.storage.SUPPORTED_FORMATS, self.isInterruptionRequested,
                                    lambda d, n: self.report(f'已扫描 {d} 个目录，发现 {n} 张图片'))
                self.folder_stats = scan.stats
                items = scan.items
            else:
                items = [(p, self.file_categories.get(p, self.target_category)) for p in self.filepaths]
            total = len(items)
            budget = memory_budget()
            limit = min(8, max(1, (os.cpu_count() or 2) - 1), max(1, budget // (32 * 1024**2)))
            width = 1
            pending = deque()
            next_item = processed = reserved = 0
            first_images = {}
            stage_started = time.monotonic()
            stage_count = 0
            previous_rate = None
            tuning = total >= 32
            with ThreadPoolExecutor(max_workers=limit, thread_name_prefix='image-import') as pool:
                while (next_item < total or pending) and not self.isInterruptionRequested():
                    while next_item < total and len(pending) < width:
                        path, category = items[next_item]
                        estimate = estimate_memory(path, budget)
                        if pending and reserved + estimate > budget:
                            break
                        pending.append((pool.submit(self.prepare, path), path, category, estimate))
                        reserved += estimate
                        next_item += 1
                    future, path, category, estimate = pending.popleft()
                    try:
                        prepared = future.result()
                        if self.isInterruptionRequested():
                            break
                        destination, duplicate = self.storage.commit_prepared(prepared, strict=True, promote_duplicate=False)
                        del prepared
                        if category not in RESERVED and category:
                            self.storage.add_image_to_category(destination, category, strict=True)
                            if category not in first_images:
                                first_images[category] = destination
                                self.storage.assign_missing_category_icon(category, destination)
                        saved += not duplicate
                        skipped += duplicate
                        committed.append(destination)
                        self.cleanup_source(path)
                    except Exception as exc:
                        failed += 1
                        self.issues.append(f'{path}: {exc}')
                        self.cleanup_source(path, failed=True)
                    reserved -= estimate
                    # A Future retains its encoded result until all references are released.
                    del future
                    processed += 1
                    stage_count += 1
                    self.parallelism = width
                    self.report(f'正在导入 {processed} / {total}（{width} 路并行）')
                    self.progress.emit(processed, total)
                    if tuning and stage_count >= max(8, width * 4):
                        rate = stage_count / max(.001, time.monotonic() - stage_started)
                        if previous_rate is not None and rate < previous_rate * 1.15:
                            width = max(1, width // 2)
                            tuning = False
                        elif width < limit:
                            previous_rate = rate
                            width = min(limit, width * 2)
                        else:
                            tuning = False
                        stage_count = 0
                        stage_started = time.monotonic()
                        budget = min(budget, memory_budget())
                for future, *_ in pending:
                    future.cancel()
            self.was_cancelled = self.isInterruptionRequested()
        except Exception as exc:
            self.issues.append(str(exc))
            failed += 1
        finally:
            try:
                self.storage.order_imported_images(committed)
            except Exception as exc:
                self.issues.append(f'保存导入顺序失败: {exc}')
            self.completed.emit(saved, skipped, failed)
