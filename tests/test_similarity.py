import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from services.similarity import (
    Feature, ScanCancelled, SimilarityIndex, animation_matches, distance,
    extract_feature, group_features, image_hash, luminance_hash, sample_indices, scan,
)


def feature(name, hashes):
    return Feature(name, 1, 1, 32, 32, tuple(hashes))


class SimilarityTests(unittest.TestCase):
    def test_flat_images_are_stable(self):
        for value in (0, 16, 128, 255):
            self.assertEqual(luminance_hash([value] * 1024), 0)

    def test_brightness_and_contrast(self):
        rng = random.Random(13)
        values = [rng.randrange(30, 150) for _ in range(1024)]
        self.assertEqual(luminance_hash(values), luminance_hash([v * 1.2 + 25 for v in values]))

    def test_transparency_and_structure(self):
        hidden = Image.new('RGBA', (32, 32), (255, 0, 0, 0))
        self.assertEqual(image_hash(hidden), image_hash(Image.new('RGB', (32, 32), 'white')))
        a = Image.new('RGB', (32, 32), 'white')
        b = a.copy()
        ImageDraw.Draw(a).rectangle((2, 2, 12, 26), fill='black')
        ImageDraw.Draw(b).ellipse((10, 12, 30, 29), fill='black')
        self.assertGreater(distance(image_hash(a), image_hash(b)), 16)

    def test_tree_matches_bruteforce(self):
        rng = random.Random(8)
        hashes = [rng.getrandbits(20) for _ in range(100)] + [0, 0]
        tree = SimilarityIndex()
        for i, h in enumerate(hashes):
            tree.add(h, i, lambda: False)
        for threshold in (0, 6, 16):
            for h in hashes[::9]:
                self.assertEqual(sorted(tree.find(h, threshold, lambda: False)),
                                 [i for i, other in enumerate(hashes) if distance(h, other) <= threshold])

    def test_groups_do_not_chain_or_repeat(self):
        # A~B and B~C, but A !~ C: C must not be chained into A's group.
        groups = group_features([feature('c', [3]), feature('b', [1]), feature('a', [0])], 1)
        self.assertEqual([[f.path for f in g] for g in groups], [['a', 'b']])

    def test_static_and_animated_separate(self):
        groups = group_features([feature('a', [0]), feature('b', [0]),
                                 feature('c', [0] * 8), feature('d', [0] * 8)])
        self.assertEqual([[f.path for f in g] for g in groups], [['a', 'b'], ['c', 'd']])

    def test_time_sampling_and_speed(self):
        self.assertEqual(sample_indices([100, 300]), [0, 0, 1, 1, 1, 1, 1, 1])
        self.assertEqual(sample_indices([100, 300]), sample_indices([200, 600]))
        self.assertEqual(sample_indices([20]), [0] * 8)

    def test_animation_boundary_and_same_first_frame(self):
        a = [0] * 8
        far = (1 << 63) - 1
        self.assertTrue(animation_matches(a, [0] * 7 + [far], 6))
        self.assertFalse(animation_matches(a, [0] * 6 + [far] * 2, 6))
        self.assertFalse(animation_matches(a, [0] + [far] * 7, 6))
        self.assertTrue(animation_matches(a, [63] * 8, 6))
        self.assertFalse(animation_matches(a, [127] * 8, 6))

    def test_cancellation(self):
        with self.assertRaises(ScanCancelled):
            group_features([feature('a', [0]), feature('b', [0])], cancel=lambda: True)
        with self.assertRaises(ScanCancelled):
            group_features([feature('a', [0] * 8)], cancel=lambda: True)

    def test_cache_reuse_invalidation_and_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp, 'a.png'))
            cache = str(Path(temp, 'cache.db'))
            Image.new('RGB', (32, 32), 'white').save(path)
            first, errors = scan([path], cache)
            self.assertFalse(errors)
            with patch('services.similarity.extract_feature', side_effect=AssertionError('cache miss')):
                second, errors = scan([path], cache)
            self.assertEqual(first, second)
            stat = os.stat(path)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000000))
            with patch('services.similarity.extract_feature', wraps=extract_feature) as decode:
                scan([path], cache)
                self.assertEqual(decode.call_count, 1)
            broken = Path(temp, 'broken.gif')
            broken.write_bytes(b'broken')
            features, errors = scan([path, str(broken), str(Path(temp, 'missing.png'))], cache)
            self.assertEqual(len(features), 1)
            self.assertEqual(len(errors), 2)

    def test_cancel_preserves_completed_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = [str(Path(temp, name)) for name in ('a.png', 'b.png')]
            for path in paths:
                Image.new('RGB', (32, 32), 'white').save(path)
            cache = str(Path(temp, 'cache.db'))
            completed = []
            with self.assertRaises(ScanCancelled):
                scan(paths, cache, cancel=lambda: bool(completed), progress=lambda *args: completed.append(args))
            with patch('services.similarity.extract_feature', wraps=extract_feature) as decode:
                result, _ = scan(paths, cache)
                self.assertEqual(decode.call_count, 1)
                self.assertEqual(len(result), 2)

    def test_gif_sampling_uses_composited_frames(self):
        with tempfile.TemporaryDirectory() as temp:
            a = Image.new('RGBA', (48, 48), 'white')
            b = a.copy()
            ImageDraw.Draw(a).rectangle((3, 3, 18, 35), fill='black')
            ImageDraw.Draw(b).ellipse((20, 12, 44, 40), fill='black')
            paths = [str(Path(temp, name)) for name in ('a.gif', 'b.gif')]
            for path, speed in zip(paths, (1, 2)):
                a.save(path, save_all=True, append_images=[b], duration=[100 * speed, 300 * speed],
                       loop=0, disposal=2, optimize=True)
            x, y = [extract_feature(path) for path in paths]
            self.assertTrue(x.animated)
            self.assertEqual(x.duration, 400)
            self.assertEqual(y.duration, 800)
            self.assertEqual(x.hashes, y.hashes)
            with Image.open(paths[0]) as img:
                first = image_hash(img)
                img.seek(1)
                second = image_hash(img)
            self.assertEqual(x.hashes, (first, first, second, second, second, second, second, second))
            items, _ = scan(paths, str(Path(temp, 'cache.db')), kind='static')
            self.assertEqual(items, [])


if __name__ == '__main__':
    unittest.main()
