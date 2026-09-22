"""Ordered, bounded Telegram package pipeline: parse -> download -> process."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue, Empty, Full
from threading import Event
import tempfile
import shutil
import re


def package_filename_stem(title):
    """Make a ZIP basename safe on Windows without changing package metadata."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).rstrip(' .')
    if not name:
        name = 'package'
    # Device names remain reserved even with an extension, such as CON.zip.
    if re.fullmatch(r'CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]',
                    name.split('.')[0].rstrip(' '), re.IGNORECASE):
        name = '_' + name
    return name


@dataclass
class PreparedPack:
    index: int
    link: str
    pack: object = None
    downloader: object = None
    sources: dict = field(default_factory=dict)
    error: Exception = None
    directory: Path = None


class TGPackageBatchRunner:
    def __init__(self, links, downloader_factory, process_pack, progress,
                 cancel_event=None, download_workers=4):
        self.links = links
        self.downloader_factory = downloader_factory
        self.process_pack = process_pack
        self.progress = progress
        self.cancel_event = cancel_event if cancel_event is not None else Event()
        self.download_workers = download_workers
        self._downloaders = []

    @contextmanager
    def _sessions(self):
        try:
            yield
        finally:
            for downloader in self._downloaders:
                downloader.session.close()
            self._downloaders.clear()

    def _put(self, queue, value):
        while not self.cancel_event.is_set():
            try:
                queue.put(value, timeout=0.1)
                return
            except Full:
                pass

    def _take(self, queue):
        while not self.cancel_event.is_set():
            try:
                return queue.get(timeout=0.1)
            except Empty:
                pass
        return None

    def _parse(self, queue):
        for index, link in enumerate(self.links):
            if self.cancel_event.is_set():
                break
            item = PreparedPack(index, link)
            self.progress(index, "parse", 0, 0, "")
            try:
                item.downloader = self.downloader_factory()
                self._downloaders.append(item.downloader)
                item.pack = item.downloader.get_sticker_set(link)
                self.progress(index, "parsed", 0, item.pack.total_count, item.pack.title)
            except Exception as exc:
                item.error = exc
            self._put(queue, item)
        self._put(queue, None)

    def _download(self, parsed, downloaded, root):
        while not self.cancel_event.is_set():
            item = self._take(parsed)
            if item is None:
                break
            if item.error is None:
                try:
                    item.directory = root / str(item.index)
                    item.directory.mkdir()
                    stickers = item.pack.stickers
                    self.progress(item.index, "download", 0, len(stickers), "")

                    def download(sticker):
                        if self.cancel_event.is_set():
                            raise InterruptedError()
                        path = item.downloader.get_file_path(sticker.file_id)
                        data = item.downloader.download_file_bytes(path)
                        output = item.directory / f"{sticker.index}.download"
                        output.write_bytes(data)
                        return str(output)

                    with ThreadPoolExecutor(max_workers=self.download_workers) as pool:
                        futures = {pool.submit(download, s): s for s in stickers}
                        for done, future in enumerate(as_completed(futures), 1):
                            if self.cancel_event.is_set():
                                for pending in futures:
                                    pending.cancel()
                                break
                            sticker = futures[future]
                            try:
                                item.sources[sticker.index] = future.result()
                            except Exception as exc:
                                # Preserve the typed missing-source error for the existing importer.
                                item.sources[sticker.index] = exc
                            self.progress(item.index, "download", done, len(stickers), "")
                    self.progress(item.index, "ready", len(stickers), len(stickers), "")
                except Exception as exc:
                    item.error = exc
            self._put(downloaded, item)
        self._put(downloaded, None)

    def _guard(self, operation, *args):
        try:
            operation(*args)
        except BaseException:
            self.cancel_event.set()
            raise

    def run(self):
        # Each boundary holds one pack: downloads cannot accumulate the whole batch on disk.
        parsed, downloaded = Queue(maxsize=1), Queue(maxsize=1)
        results = []
        with self._sessions(), tempfile.TemporaryDirectory(prefix="tg_batch_") as directory:
            with ThreadPoolExecutor(max_workers=2) as stages:
                parsing = stages.submit(self._guard, self._parse, parsed)
                downloading = stages.submit(self._guard, self._download, parsed, downloaded, Path(directory))
                try:
                    while not self.cancel_event.is_set():
                        item = self._take(downloaded)
                        if item is None:
                            break
                        try:
                            if item.error is not None:
                                raise item.error
                            outcome = self.process_pack(item, self.cancel_event)
                            # A completed atomic export remains successful even if cancel was clicked just after it.
                            results.append(dict(index=item.index, status="done", **outcome))
                            self.progress(item.index, "done", 1, 1, outcome["message"])
                        except InterruptedError:
                            self.cancel_event.set()
                        except Exception as exc:
                            if not self.cancel_event.is_set():
                                message = str(exc)
                                results.append(dict(index=item.index, status="failed", message=message))
                                self.progress(item.index, "failed", 0, 0, message)
                        finally:
                            if item.directory is not None:
                                shutil.rmtree(item.directory)
                finally:
                    # Also releases producers blocked by a full queue if the consumer fails.
                    self.cancel_event.set()
                parsing.result()
                downloading.result()
        completed = {result["index"] for result in results}
        for index in range(len(self.links)):
            if index not in completed:
                results.append(dict(index=index, status="cancelled", message=""))
                self.progress(index, "cancelled", 0, 0, "")
        return sorted(results, key=lambda result: result["index"])
