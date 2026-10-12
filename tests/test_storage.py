import os
import sys
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage import HistoryStorage  # noqa: E402


class HistoryStorageTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory()
        self.temp = Path(self.sandbox.name) / "temp"
        self.storage = HistoryStorage(self.temp)
        self.history_id = str(uuid.uuid4())
        self.image = object()
        def save_png(_image, path):
            path.write_bytes(b"png")
            path.with_suffix(".thumb.jpg").write_bytes(b"jpg")
        self.storage._save_png = save_png

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
        generation = self.storage.get_history(self.history_id)["generation"]
        self.assertTrue((history_dir / generation / "run_000001" / "image_000.png").is_file())

        self.assertEqual(self.storage.reset(self.history_id), 1)
        self.assertFalse(history_dir.exists())
        self.assertEqual(self.storage.get_history(self.history_id)["runs"], [])
        self.assertEqual(self.add(execution_id="prompt-b")[0]["id"], 1)

    def test_failed_deletions_keep_metadata_for_retry(self):
        for operation in ("delete_run", "clear", "reset", "release"):
            with self.subTest(operation=operation):
                self.storage.reset(self.history_id)
                run, _, _ = self.add()
                before = self.storage.get_history(self.history_id)
                args = [self.history_id]
                if operation == "delete_run":
                    args.append(run["id"])
                elif operation == "release":
                    args.append(before["generation"])
                with patch("storage.shutil.rmtree", side_effect=PermissionError("locked")):
                    with self.assertRaises(PermissionError):
                        getattr(self.storage, operation)(*args)
                self.assertEqual(self.storage.get_history(self.history_id), before)
                self.assertTrue(self.storage._history_dir(self.history_id).exists())
                self.assertEqual(getattr(self.storage, operation)(*args), 1)
                self.assertEqual(self.storage.get_history(self.history_id)["runs"], [])

    def test_encoding_does_not_block_read_clear_or_release(self):
        for operation in ("clear", "reset", "release"):
            with self.subTest(operation=operation):
                self.storage.reset(self.history_id)
                started = threading.Event()
                finish = threading.Event()
                original_save = self.storage._save_png
                def slow_save(image, path):
                    started.set()
                    if not finish.wait(5):
                        raise RuntimeError("writer was not released")
                    original_save(image, path)
                self.storage._save_png = slow_save
                try:
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        writer = pool.submit(self.add)
                        self.assertTrue(started.wait(2))
                        reader = pool.submit(self.storage.get_history, self.history_id)
                        history = reader.result(timeout=2)
                        args = [self.history_id]
                        if operation == "release":
                            args.append(history["generation"])
                        pool.submit(getattr(self.storage, operation), *args).result(timeout=2)
                        finish.set()
                        self.assertEqual(writer.result(timeout=2), (None, [], False))
                finally:
                    finish.set()
                    self.storage._save_png = original_save
                self.assertEqual(self.storage.get_history(self.history_id)["runs"], [])
                self.assertEqual(list(self.storage.temp_root.glob("batch_*")), [])

    def test_release_removes_history_and_rejects_later_appends(self):
        self.add()
        generation = self.storage.get_history(self.history_id)["generation"]
        self.assertEqual(self.storage.release(self.history_id, generation), 1)
        self.assertNotIn(self.history_id, self.storage._histories)
        self.assertFalse(self.storage._history_dir(self.history_id).exists())
        self.assertEqual(self.add(), (None, [], False))
        self.storage.reset(self.history_id)
        self.assertIsNotNone(self.add()[0])

    def test_old_release_cannot_remove_a_reopened_gallery(self):
        self.add()
        old_generation = self.storage.get_history(self.history_id)["generation"]
        self.storage.reset(self.history_id)
        self.add()
        self.assertEqual(self.storage.release(self.history_id, old_generation), 0)
        self.assertEqual(len(self.storage.get_history(self.history_id)["runs"]), 1)

    def test_reset_and_clear_change_image_urls(self):
        previous_image = self.add()[1][0]
        self.storage.reset(self.history_id)
        next_image = self.add()[1][0]
        self.assertNotEqual(previous_image["subfolder"], next_image["subfolder"])
        self.storage.clear(self.history_id)
        self.assertNotEqual(next_image["subfolder"], self.add()[1][0]["subfolder"])

    def test_append_returns_only_new_images_without_copying_the_full_run(self):
        self.add(count=20, execution_id="prompt-a")
        run, images, _ = self.add(count=1, execution_id="prompt-a")
        self.assertEqual(run["image_count"], 21)
        self.assertEqual(len(run["images"]), 1)
        self.assertEqual(run["images"], images)
        self.assertEqual(len(self.storage.get_history(self.history_id)["runs"][0]["images"]), 21)

    def test_failed_batch_encoding_cleans_staging_files(self):
        original_save = self.storage._save_png
        calls = 0
        def fail_second_image(image, path):
            nonlocal calls
            calls += 1
            original_save(image, path)
            if calls == 2:
                raise OSError("disk full")
        self.storage._save_png = fail_second_image
        with self.assertRaises(OSError):
            self.add(count=2)
        self.assertEqual(self.storage.get_history(self.history_id)["runs"], [])
        self.assertEqual(list(self.storage.temp_root.rglob("*.png")), [])
        self.assertEqual(list(self.storage.temp_root.rglob("*.jpg")), [])

    def test_invalid_generation_cannot_erase_history(self):
        self.add()
        before = self.storage.get_history(self.history_id)
        for operation in (self.storage.reset, self.storage.clear):
            with self.assertRaises(ValueError):
                operation(self.history_id, "invalid")
            self.assertEqual(self.storage.get_history(self.history_id), before)

    def test_failed_commit_rolls_back_original_and_thumbnail_together(self):
        self.add(execution_id="prompt-a")
        before = self.storage.get_history(self.history_id)
        original_replace = os.replace
        calls = 0
        def fail_thumbnail(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PermissionError("locked")
            original_replace(source, target)
        with patch("storage.os.replace", side_effect=fail_thumbnail):
            with self.assertRaises(PermissionError):
                self.add(execution_id="prompt-a")
        self.assertEqual(self.storage.get_history(self.history_id), before)
        self.assertEqual(len(list(self.storage.temp_root.rglob("*.png"))), 1)
        self.assertEqual(len(list(self.storage.temp_root.rglob("*.jpg"))), 1)

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
