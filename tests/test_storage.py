import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage import HistoryStorage  # noqa: E402


class HistoryStorageTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory()
        root = Path(self.sandbox.name)
        self.output = root / "output"
        self.temp = root / "temp"
        self.storage = HistoryStorage(self.output, self.temp)
        self.history_id = str(uuid.uuid4())
        self.image = object()
        self.storage._save_png = lambda _image, path: path.write_bytes(b"png")

    def tearDown(self):
        self.sandbox.cleanup()

    def add(self, count=1, persist=True):
        return self.storage.add_run(
            self.history_id,
            [self.image] * count,
            persist=persist,
            timestamp="2026-10-08T12:00:00+02:00",
            seed=123,
            model="model.safetensors",
            loras=[],
            label="",
        )

    def test_batch_is_one_run_and_newest_run_is_first(self):
        first = self.add(count=10)
        second = self.add(count=20)
        history = self.storage.get_history(self.history_id, persist=True)

        self.assertEqual(first["id"], 1)
        self.assertEqual(first["image_count"], 10)
        self.assertEqual(second["id"], 2)
        self.assertEqual([run["id"] for run in history["runs"]], [2, 1])
        self.assertEqual(history["runs"][0]["image_count"], 20)

    def test_delete_and_clear_never_reuse_run_numbers(self):
        self.add()
        self.add()
        self.add()
        self.assertTrue(self.storage.delete_run(self.history_id, 2))
        self.assertEqual(self.add()["id"], 4)

        self.storage.clear(self.history_id)
        history = self.storage.get_history(self.history_id, persist=True)
        self.assertEqual(history["runs"], [])
        self.assertEqual(self.add()["id"], 5)

    def test_persistent_counter_survives_new_storage_instance(self):
        self.add()
        self.add()
        restored = HistoryStorage(self.output, self.temp)
        restored._save_png = lambda _image, path: path.write_bytes(b"png")
        run = restored.add_run(
            self.history_id,
            [self.image],
            persist=True,
            timestamp="2026-10-08T12:01:00+02:00",
            seed=123,
            model="model.safetensors",
            loras=[],
            label="",
        )
        self.assertEqual(run["id"], 3)

    def test_concurrent_runs_get_distinct_numbers(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            runs = list(pool.map(lambda _index: self.add(), range(8)))
        self.assertEqual(sorted(run["id"] for run in runs), list(range(1, 9)))

    def test_rejects_untrusted_identifiers(self):
        with self.assertRaises(ValueError):
            self.storage.get_history("../../output", persist=True)
        with self.assertRaises(ValueError):
            self.storage.delete_run(self.history_id, "../1")

    def test_corrupt_manifest_is_preserved_and_does_not_reuse_disk_ids(self):
        history_dir = self.output / "generation_history" / self.history_id
        history_dir.mkdir(parents=True)
        (history_dir / "run_000007").mkdir()
        (history_dir / "manifest.json").write_text("{broken", encoding="utf-8")

        history = self.storage.get_history(self.history_id, persist=True)

        self.assertEqual(history["runs"], [])
        self.assertEqual(history["next_run_id"], 8)
        self.assertEqual(len(list(history_dir.glob("manifest.corrupt-*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
