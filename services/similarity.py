"""Visual candidates only: deliberately independent of import hashes and sync keys.

Matches the Android similarity-v1 rules (63 AC bits, white background,
8 animation samples, disjoint anchor groups). No source files are changed.
"""
import json
import math
import os
import sqlite3
from dataclasses import asdict, dataclass
from contextlib import closing

from PIL import Image


class ScanCancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel():
        raise ScanCancelled()


_BASIS = tuple(tuple(math.cos((2 * x + 1) * u * math.pi / 64)
                     for x in range(32)) for u in range(8))


def luminance_hash(pixels):
    """Android's separable DCT, median of 63 AC terms, fixed zero high bit."""
    horizontal = [[sum(pixels[y * 32 + x] * _BASIS[u][x] for x in range(32))
                   for u in range(8)] for y in range(32)]
    coefficients = [sum(horizontal[y][u] * _BASIS[v][y] for y in range(32))
                    for v in range(8) for u in range(8) if u or v]
    median = sorted(coefficients)[31]
    return sum(1 << i for i, value in enumerate(coefficients) if value > median + 1e-7)


def image_hash(image):
    # Palette images must be converted before resizing (Pillow otherwise uses
    # nearest-neighbour for mode P). Composite numerically like Android.
    with image.convert('RGBA') as rgba:
        small = rgba.resize((32, 32), Image.Resampling.BILINEAR)
    data = small.get_flattened_data() if hasattr(small, 'get_flattened_data') else small.getdata()
    pixels = [(0.299 * r + 0.587 * g + 0.114 * b) * (a / 255.0)
              + 255.0 * (1 - a / 255.0) for r, g, b, a in data]
    small.close()
    return luminance_hash(pixels)


@dataclass(frozen=True)
class Feature:
    path: str
    size: int
    mtime_ns: int
    width: int
    height: int
    hashes: tuple
    duration: int = 0

    @property
    def animated(self):
        return len(self.hashes) == 8


def sample_indices(delays):
    duration = sum(delays)
    index, end = 0, delays[0]
    result = []
    for sample in range(8):
        time = duration * sample // 8
        while time >= end and index < len(delays) - 1:
            index += 1
            end += delays[index]
        result.append(index)
    return result


def extract_feature(path, cancel=lambda: False):
    before = os.stat(path)
    with Image.open(path) as img:
        width, height = img.size
        if getattr(img, 'n_frames', 1) > 1:
            delays = []
            # Sequential seek/load applies GIF disposal and checks every frame.
            for index in range(img.n_frames):
                check_cancel(cancel)
                img.seek(index)
                img.load()
                delay = int(img.info.get('duration', 100) or 100)
                delays.append(100 if delay < 20 else delay)
            targets = sample_indices(delays)
            sampled = {}
            img.seek(0)
            for index in range(targets[-1] + 1):
                check_cancel(cancel)
                img.seek(index)
                if index in targets:
                    sampled[index] = image_hash(img)
            hashes = tuple(sampled[i] for i in targets)
            duration = sum(delays)
        else:
            check_cancel(cancel)
            # JPEG decoder may downsample before full decompression.
            img.draft('RGB', (128, 128))
            hashes = (image_hash(img),)
            duration = 0
    after = os.stat(path)
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError('扫描期间文件发生变化')
    return Feature(path, after.st_size, after.st_mtime_ns, width, height, hashes, duration)


def scan(paths, cache_path, kind='all', cancel=lambda: False, progress=lambda *_: None):
    """Return complete features and per-file failures; committed cache survives cancel."""
    paths = sorted(set(os.path.abspath(p) for p in paths))
    os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
    features, failures = [], []
    with closing(sqlite3.connect(cache_path)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS similarity_v1 (path TEXT PRIMARY KEY, payload TEXT)')
        for i, path in enumerate(paths):
            check_cancel(cancel)
            try:
                stat = os.stat(path)
                row = db.execute('SELECT payload FROM similarity_v1 WHERE path=?', (path,)).fetchone()
                feature = None
                if row:
                    try:
                        data = json.loads(row[0])
                        data['hashes'] = tuple(data['hashes'])
                        cached = Feature(**data)
                        if (cached.size, cached.mtime_ns) == (stat.st_size, stat.st_mtime_ns):
                            feature = cached
                    except (ValueError, TypeError, KeyError):
                        pass
                if feature is None:
                    feature = extract_feature(path, cancel)
                    db.execute('INSERT OR REPLACE INTO similarity_v1 VALUES (?, ?)',
                               (path, json.dumps(asdict(feature))))
                    db.commit()
                if kind == 'all' or feature.animated == (kind == 'animated'):
                    features.append(feature)
            except (OSError, ValueError, EOFError, Image.DecompressionBombError) as error:
                failures.append((path, str(error)))
            progress(i + 1, len(paths))
    return features, failures


def distance(a, b):
    return (a ^ b).bit_count()


class SimilarityIndex:
    def __init__(self):
        self.root = None

    def add(self, value, index, cancel):
        if self.root is None:
            self.root = (value, [index], {})
            return
        node = self.root
        while True:
            check_cancel(cancel)
            d = distance(value, node[0])
            if not d:
                node[1].append(index)
                return
            if d not in node[2]:
                node[2][d] = (value, [index], {})
                return
            node = node[2][d]

    def find(self, value, threshold, cancel):
        pending = [self.root] if self.root else []
        result = []
        while pending:
            check_cancel(cancel)
            node = pending.pop()
            d = distance(value, node[0])
            if d <= threshold:
                result.extend(node[1])
            pending.extend(child for edge, child in node[2].items()
                           if d - threshold <= edge <= d + threshold)
        return result


def animation_matches(a, b, threshold):
    return len(a) == len(b) == 8 and sum(distance(x, y) <= threshold for x, y in zip(a, b)) >= 7


def group_features(features, threshold=6, cancel=lambda: False, progress=lambda *_: None):
    if not 0 <= threshold <= 16:
        raise ValueError('匹配阈值必须为 0–16')
    groups = []
    done = 0
    for animated in (False, True):
        items = sorted((f for f in features if f.animated == animated), key=lambda f: f.path)
        tree = SimilarityIndex()
        if not animated:
            for i, item in enumerate(items):
                check_cancel(cancel)
                tree.add(item.hashes[0], i, cancel)
        assigned = set()
        for anchor, item in enumerate(items):
            check_cancel(cancel)
            if anchor not in assigned:
                if animated:
                    members = [anchor]
                    for candidate in range(anchor + 1, len(items)):
                        check_cancel(cancel)
                        if candidate not in assigned and animation_matches(
                                item.hashes, items[candidate].hashes, threshold):
                            members.append(candidate)
                else:
                    members = sorted(i for i in tree.find(item.hashes[0], threshold, cancel)
                                     if i not in assigned)
                if len(members) > 1:
                    assigned.update(members)
                    groups.append([items[i] for i in members])
            done += 1
            progress(done, len(features))
    return groups
