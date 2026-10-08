import importlib.util
import math
from pathlib import Path
import sys
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "generation_history_test_package"


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
    get_output_directory=lambda: str(Path(temp_root.name) / "output"),
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


if __name__ == "__main__":
    unittest.main()
