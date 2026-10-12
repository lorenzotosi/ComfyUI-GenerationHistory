import importlib.util
import math
from pathlib import Path
import sys
import tempfile
import types
import unittest
import uuid
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "generation_history_test_package"


def save_png_stub(_image, path):
    path.write_bytes(b"png")
    path.with_suffix(".thumb.jpg").write_bytes(b"jpg")


class _Routes:
    def get(self, _path):
        return lambda function: function

    def post(self, _path):
        return lambda function: function


temp_root = tempfile.TemporaryDirectory()
sys.modules["aiohttp"] = types.SimpleNamespace(
    web=types.SimpleNamespace(
        Request=object,
        Response=object,
        HTTPBadRequest=ValueError,
        json_response=lambda data, status=200: (data, status),
    )
)
sys.modules["folder_paths"] = types.SimpleNamespace(
    get_temp_directory=lambda: str(Path(temp_root.name) / "temp"),
)
execution_utils = types.ModuleType("comfy_execution.utils")
execution_utils.get_executing_context = lambda: None
sys.modules["comfy_execution"] = types.ModuleType("comfy_execution")
sys.modules["comfy_execution.utils"] = execution_utils
sys.modules["server"] = types.SimpleNamespace(
    PromptServer=types.SimpleNamespace(
        instance=types.SimpleNamespace(routes=_Routes(), send_sync=lambda *_args: None)
    )
)

package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package

storage_spec = importlib.util.spec_from_file_location(f"{PACKAGE}.storage", ROOT / "storage.py")
storage_module = importlib.util.module_from_spec(storage_spec)
sys.modules[storage_spec.name] = storage_module
storage_spec.loader.exec_module(storage_module)

module_spec = importlib.util.spec_from_file_location(
    f"{PACKAGE}.generation_history", ROOT / "generation_history.py"
)
generation_history = importlib.util.module_from_spec(module_spec)
sys.modules[module_spec.name] = generation_history
module_spec.loader.exec_module(generation_history)


class MetadataTests(unittest.TestCase):
    def test_extracts_nearest_upstream_seed_model_and_lora(self):
        prompt = {
            "1": {"class_type": "GenerationHistory", "inputs": {"images": ["2", 0]}},
            "2": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["3", 0], "vae": ["5", 2]},
            },
            "3": {
                "class_type": "KSampler",
                "inputs": {"seed": 123, "model": ["4", 0]},
            },
            "4": {
                "class_type": "LoraLoader",
                "inputs": {
                    "model": ["5", 0],
                    "lora_name": "detail.safetensors",
                    "strength_model": 0.8,
                },
            },
            "5": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "base.safetensors"},
            },
        }

        metadata = generation_history.extract_metadata(prompt, "1")

        self.assertEqual(metadata["seed"], 123)
        self.assertEqual(metadata["model"], "base.safetensors")
        self.assertEqual(
            metadata["loras"],
            [{"name": "detail.safetensors", "strength_model": 0.8}],
        )

    def test_missing_metadata_is_non_fatal(self):
        metadata = generation_history.extract_metadata({}, "1")
        self.assertEqual(metadata, {"seed": "Unknown", "model": "Unknown", "loras": []})

    def test_krea_style_graph_prefers_unet_and_ignores_clip_loader(self):
        prompt = {
            "history": {
                "class_type": "GenerationHistory",
                "inputs": {"images": ["decode", 0]},
            },
            "decode": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["sampler", 0]},
            },
            "sampler": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 456,
                    "model": ["unet", 0],
                    "positive": ["sequence", 0],
                },
            },
            "sequence": {
                "class_type": "ComfyUIPromptSequenceText",
                "inputs": {"clip": ["clip", 0]},
            },
            "clip": {
                "class_type": "CLIPLoader",
                "inputs": {"clip_name": "clip.safetensors"},
            },
            "unet": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": "flux-unet.safetensors"},
            },
        }

        metadata = generation_history.extract_metadata(prompt, "history")

        self.assertEqual(metadata["seed"], 456)
        self.assertEqual(metadata["model"], "flux-unet.safetensors")

    def test_execution_id_comes_from_current_comfy_context(self):
        original = generation_history.get_executing_context
        generation_history.get_executing_context = lambda: types.SimpleNamespace(
            prompt_id="prompt-123"
        )
        try:
            self.assertEqual(generation_history.current_execution_id(), "prompt-123")
        finally:
            generation_history.get_executing_context = original

    def test_always_changed_fingerprint_is_nan(self):
        fingerprint = generation_history.GenerationHistory.IS_CHANGED()
        self.assertTrue(math.isnan(fingerprint))
        self.assertNotEqual(fingerprint, fingerprint)

    def test_record_keeps_the_image_pass_through_and_session_history(self):
        original_storage = generation_history.STORAGE
        storage = storage_module.HistoryStorage(Path(temp_root.name) / "record-temp")
        storage._save_png = save_png_stub
        generation_history.STORAGE = storage
        images = [object()]
        history_id = str(uuid.uuid4())
        try:
            result = generation_history.GenerationHistory().record(
                images,
                False,
                history_id,
                prompt={},
                unique_id="1",
            )
        finally:
            generation_history.STORAGE = original_storage

        self.assertIs(result["result"][0], images)
        run = storage.get_history(history_id)["runs"][0]
        self.assertEqual(run["image_count"], 1)
        self.assertEqual(run["images"][0]["type"], "temp")

    def test_routes_delegate_storage_operations_to_thread_executor(self):
        storage = storage_module.HistoryStorage(Path(temp_root.name) / "route-temp")
        storage._save_png = save_png_stub
        history_id = str(uuid.uuid4())
        calls = []
        async def executor(function, *args):
            calls.append(function.__name__)
            return function(*args)
        async def request_json():
            return {
                "history_id": history_id, "run_id": 1,
                "generation": storage.get_history(history_id)["generation"],
            }
        request = types.SimpleNamespace(query={"id": history_id}, json=request_json)
        routes = [
            generation_history.get_generation_history,
            generation_history.reset_generation_history,
            generation_history.delete_generation_history_run,
            generation_history.clear_generation_history,
            generation_history.release_generation_history,
        ]
        with patch.object(generation_history, "STORAGE", storage), patch.object(generation_history.asyncio, "to_thread", executor):
            for route in routes:
                storage.reset(history_id)
                storage.append_images(
                    history_id, "prompt", [object()], timestamp="now",
                    seed=1, model="model", loras=[], label="",
                )
                with self.assertRaises(StopIteration) as completed:
                    route(request).send(None)
                self.assertEqual(completed.exception.value[1], 200)
        self.assertEqual(calls, ["get_history", "reset", "delete_run", "clear", "release"])

    def test_delete_route_reports_disk_error_and_preserves_the_run(self):
        storage = storage_module.HistoryStorage(Path(temp_root.name) / "route-error")
        storage._save_png = save_png_stub
        history_id = str(uuid.uuid4())
        storage.append_images(
            history_id, "prompt", [object()], timestamp="now",
            seed=1, model="model", loras=[], label="",
        )
        async def executor(function, *args):
            return function(*args)
        async def request_json():
            return {"history_id": history_id, "run_id": 1}
        with patch.object(generation_history, "STORAGE", storage), patch.object(generation_history.asyncio, "to_thread", executor), patch.object(storage_module.shutil, "rmtree", side_effect=PermissionError("locked")):
            with self.assertLogs(generation_history.LOGGER, level="ERROR"):
                with self.assertRaises(StopIteration) as completed:
                    generation_history.delete_generation_history_run(types.SimpleNamespace(json=request_json)).send(None)
        self.assertEqual(completed.exception.value, ({"error": "locked"}, 500))
        self.assertEqual(len(storage.get_history(history_id)["runs"]), 1)

    def test_record_does_not_publish_images_after_gallery_release(self):
        storage = storage_module.HistoryStorage(Path(temp_root.name) / "released-temp")
        history_id = str(uuid.uuid4())
        storage.reset(history_id)
        storage.release(history_id, storage.get_history(history_id)["generation"])
        images = [object()]
        with patch.object(generation_history, "STORAGE", storage), patch.object(generation_history.PromptServer.instance, "send_sync") as publish:
            result = generation_history.GenerationHistory().record(images, False, history_id)
        self.assertIs(result["result"][0], images)
        publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
