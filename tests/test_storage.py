import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage import HistoryStorage  # noqa: E402


class HistoryStorageTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory()
        self.temp = Path(self.sandbox.name) / "temp"
        self.storage = HistoryStorage(self.temp)
        self.history_id = str(uuid.uuid4())
        self.image = object()
        self.storage._save_png = lambda _image, path: path.write_bytes(b"png")

    def tearDown(self):
        self.sandbox.cleanup()

    def add(self, count=1, execution_id=None, seed=123, model="model.safetensors"):
        return self.storage.append_images(
            self.history_id,
            execution_id or str(uuid.uuid4()),
            [self.image] * count,
            timestamp="2026-10-08T12:00:00+02:00",
            seed=seed,
            model=model,
            loras=[],
            label="",
        )

    def test_one_execution_is_one_run_for_batch_and_list_shapes(self):
        cases = ([1], [6], [1] * 6, [2, 2, 2], [4] * 6, [8])
        for batches in cases:
            with self.subTest(batches=batches):
                history_id = str(uuid.uuid4())
                execution_id = str(uuid.uuid4())
                for count in batches:
                    self.storage.append_images(
                        history_id,
                        execution_id,
                        [self.image] * count,
                        timestamp="2026-10-08T12:00:00+02:00",
                        seed=123,
                        model="model.safetensors",
                        loras=[],
                        label="",
                    )
                history = self.storage.get_history(history_id)
                self.assertEqual(len(history["runs"]), 1)
                run = history["runs"][0]
                self.assertEqual(run["image_count"], sum(batches))
                self.assertEqual(
                    [image["filename"] for image in run["images"]],
                    [f"image_{index:03d}.png" for index in range(sum(batches))],
                )
                self.assertTrue(all(image["type"] == "temp" for image in run["images"]))

    def test_different_executions_create_different_runs(self):
        first, _, first_created = self.add(count=6, execution_id="prompt-a")
        second, _, second_created = self.add(count=6, execution_id="prompt-b")
        history = self.storage.get_history(self.history_id)

        self.assertTrue(first_created)
        self.assertTrue(second_created)
        self.assertEqual((first["id"], second["id"]), (1, 2))
        self.assertEqual(
            [run["execution_id"] for run in history["runs"]],
            ["prompt-b", "prompt-a"],
        )

    def test_histories_and_models_remain_independent(self):
        other_history_id = str(uuid.uuid4())
        self.add(count=6, execution_id="same-prompt", model="unet-a.safetensors")
        self.storage.append_images(
            other_history_id,
            "same-prompt",
            [self.image] * 6,
            timestamp="2026-10-08T12:00:00+02:00",
            seed=123,
            model="unet-b.safetensors",
            loras=[],
            label="",
        )

        first = self.storage.get_history(self.history_id)["runs"]
        second = self.storage.get_history(other_history_id)["runs"]
        self.assertEqual(
            (first[0]["model"], second[0]["model"]),
            ("unet-a.safetensors", "unet-b.safetensors"),
        )

    def test_append_event_reports_only_new_images_and_merges_seeds(self):
        _run, new_images, created = self.add(2, execution_id="prompt-a", seed=10)
        self.assertTrue(created)
        self.assertEqual(len(new_images), 2)

        run, new_images, created = self.add(3, execution_id="prompt-a", seed=11)
        self.assertFalse(created)
        self.assertEqual(run["image_count"], 5)
        self.assertEqual(run["seeds"], [10, 11])
        self.assertEqual([image["index"] for image in new_images], [2, 3, 4])

    def test_delete_and_clear_never_reuse_run_numbers(self):
        self.add()
        self.add()
        self.add()
        self.assertTrue(self.storage.delete_run(self.history_id, 2))
        self.assertEqual(self.add()[0]["id"], 4)

        self.storage.clear(self.history_id)
        self.assertEqual(self.storage.get_history(self.history_id)["runs"], [])
        self.assertEqual(self.add()[0]["id"], 5)

    def test_concurrent_appends_to_one_execution_are_serialised(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _index: self.add(execution_id="prompt-a"), range(8)))
        history = self.storage.get_history(self.history_id)
        self.assertEqual(len(history["runs"]), 1)
        run = history["runs"][0]
        self.assertEqual(run["image_count"], 8)
        self.assertEqual(len({image["filename"] for image in run["images"]}), 8)

    def test_reset_removes_session_images_and_starts_from_run_one(self):
        self.add(count=2, execution_id="prompt-a")
        history_dir = self.storage._history_dir(self.history_id)
        self.assertTrue((history_dir / "run_000001" / "image_000.png").is_file())

        self.assertEqual(self.storage.reset(self.history_id), 1)
        self.assertFalse(history_dir.exists())
        self.assertEqual(self.storage.get_history(self.history_id)["runs"], [])
        self.assertEqual(self.add(execution_id="prompt-b")[0]["id"], 1)

    def test_rejects_untrusted_identifiers(self):
        with self.assertRaises(ValueError):
            self.storage.get_history("../../output")
        with self.assertRaises(ValueError):
            self.storage.delete_run(self.history_id, "../1")
        with self.assertRaises(ValueError):
            self.storage.append_images(
                self.history_id,
                "",
                [self.image],
                timestamp="now",
                seed=1,
                model="model",
                loras=[],
                label="",
            )


if __name__ == "__main__":
    unittest.main()
