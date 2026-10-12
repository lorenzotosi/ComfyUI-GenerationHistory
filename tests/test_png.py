import tempfile
import unittest
from pathlib import Path

from storage import HistoryStorage

try:
    import numpy as np
    from PIL import Image
except ImportError:
    np = Image = None


@unittest.skipIf(np is None, "NumPy and Pillow are required for actual PNG tests")
class PngTests(unittest.TestCase):
    def test_png_preserves_dimensions_channels_and_quantized_pixels(self):
        with tempfile.TemporaryDirectory() as root:
            for channels in (1, 3, 4):
                with self.subTest(channels=channels):
                    pixels = np.linspace(-0.1, 1.1, 7 * 11 * channels, dtype=np.float32).reshape(7, 11, channels)
                    path = Path(root) / f"image_{channels}.png"
                    HistoryStorage._save_png(pixels, path)
                    with Image.open(path) as image:
                        self.assertEqual(image.format, "PNG")
                        self.assertEqual(image.size, (11, 7))
                        expected = np.clip(pixels * 255, 0, 255).astype(np.uint8)
                        if channels == 1:
                            expected = expected[..., 0]
                        np.testing.assert_array_equal(np.asarray(image), expected)
                    with Image.open(path.with_suffix(".thumb.jpg")) as thumbnail:
                        self.assertEqual(thumbnail.format, "JPEG")
                        self.assertEqual(thumbnail.mode, "RGB")
                        self.assertEqual(thumbnail.size, (11, 7))

    def test_thumbnail_is_bounded_and_original_remains_full_resolution(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "image.png"
            HistoryStorage._save_png(np.zeros((1024, 2048, 3), dtype=np.float32), path)
            with Image.open(path) as original, Image.open(path.with_suffix(".thumb.jpg")) as thumbnail:
                self.assertEqual(original.size, (2048, 1024))
                self.assertEqual(thumbnail.size, (512, 256))


if __name__ == "__main__":
    unittest.main()
