from __future__ import annotations

import logging
import uuid
from collections import deque
from datetime import datetime
from typing import Any, Iterable

from aiohttp import web
import folder_paths
from comfy_execution.utils import get_executing_context
from server import PromptServer

from .storage import HistoryStorage


LOGGER = logging.getLogger(__name__)
EVENT_NAME = "generation_history.run_added"
STORAGE = HistoryStorage(
    folder_paths.get_output_directory(), folder_paths.get_temp_directory()
)

SEED_KEYS = ("seed", "noise_seed")
MODEL_KEYS = ("unet_name", "ckpt_name", "model_name", "checkpoint_name")


def _is_link(value: Any, prompt: dict[str, Any]) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and str(value[0]) in prompt
        and isinstance(value[1], int)
    )


def _links_in(value: Any, prompt: dict[str, Any]) -> Iterable[str]:
    if _is_link(value, prompt):
        yield str(value[0])
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _links_in(nested, prompt)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _links_in(nested, prompt)


def _upstream_nodes(
    prompt: dict[str, Any], unique_id: Any
) -> Iterable[dict[str, Any]]:
    current = prompt.get(str(unique_id), {})
    queue = deque(_links_in(current.get("inputs", {}), prompt))
    seen = {str(unique_id)}
    while queue:
        node_id = queue.popleft()
        if node_id in seen:
            continue
        seen.add(node_id)
        node = prompt.get(node_id)
        if not isinstance(node, dict):
            continue
        yield node
        queue.extend(_links_in(node.get("inputs", {}), prompt))


def _plain_value(value: Any, prompt: dict[str, Any]) -> Any | None:
    if _is_link(value, prompt) or isinstance(value, (dict, list, tuple)):
        return None
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return value
    return None


def extract_metadata(prompt: Any, unique_id: Any) -> dict[str, Any]:
    """Best-effort metadata lookup restricted to nodes upstream of this node."""
    if not isinstance(prompt, dict):
        return {"seed": "Unknown", "model": "Unknown", "loras": []}

    seed: Any = None
    model_candidates: list[tuple[int, Any]] = []
    loras: list[dict[str, Any]] = []
    seen_loras: set[tuple[str, Any]] = set()

    for node in _upstream_nodes(prompt, unique_id):
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue

        if seed is None:
            for key in SEED_KEYS:
                value = _plain_value(inputs.get(key), prompt)
                if value is not None:
                    seed = value
                    break

        class_type = str(node.get("class_type", "")).lower()
        for key in MODEL_KEYS:
            value = _plain_value(inputs.get(key), prompt)
            if value in (None, ""):
                continue
            priority = 2
            if (
                key == "unet_name"
                or "unetloader" in class_type
                or "diffusionmodelload" in class_type
            ):
                priority = 0
            elif key == "ckpt_name" or "checkpointloader" in class_type:
                priority = 1
            model_candidates.append((priority, value))

        lora_name = _plain_value(inputs.get("lora_name"), prompt)
        if lora_name not in (None, ""):
            strength = _plain_value(inputs.get("strength_model"), prompt)
            identity = (str(lora_name), strength)
            if identity not in seen_loras:
                seen_loras.add(identity)
                loras.append(
                    {"name": str(lora_name), "strength_model": strength}
                )

    return {
        "seed": seed if seed is not None else "Unknown",
        "model": (
            str(min(model_candidates, key=lambda item: item[0])[1])
            if model_candidates
            else "Unknown"
        ),
        "loras": loras,
    }


def _seed_override(value: str, automatic: Any) -> Any:
    value = str(value or "").strip()
    if not value:
        return automatic
    try:
        return int(value)
    except ValueError:
        return value


def current_execution_id() -> str:
    context = get_executing_context()
    prompt_id = getattr(context, "prompt_id", None)
    if prompt_id not in (None, ""):
        return str(prompt_id)
    LOGGER.warning(
        "[GenerationHistory] ComfyUI execution context unavailable; "
        "this invocation cannot be grouped"
    )
    return f"fallback:{uuid.uuid4()}"


routes = PromptServer.instance.routes


@routes.get("/generation-history/history")
async def get_generation_history(request: web.Request) -> web.Response:
    try:
        history_id = request.query.get("id", "")
        persist = request.query.get("persist", "0").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        return web.json_response(STORAGE.get_history(history_id, persist=persist))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except OSError as exc:
        LOGGER.exception("[GenerationHistory] Unable to read history")
        return web.json_response({"error": str(exc)}, status=500)


async def _request_json(request: web.Request) -> dict[str, Any]:
    data = await request.json()
    if not isinstance(data, dict):
        raise ValueError("request body must be a JSON object")
    return data


@routes.post("/generation-history/reset-volatile")
async def reset_volatile_generation_history(request: web.Request) -> web.Response:
    try:
        data = await _request_json(request)
        removed = STORAGE.reset_volatile(data.get("history_id", ""))
        LOGGER.info(
            "[GenerationHistory] Reset volatile history (%s runs removed)",
            removed,
        )
        return web.json_response({"ok": True, "removed": removed})
    except (ValueError, web.HTTPBadRequest) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except OSError as exc:
        LOGGER.exception("[GenerationHistory] Unable to reset volatile history")
        return web.json_response({"error": str(exc)}, status=500)


@routes.post("/generation-history/delete-run")
async def delete_generation_history_run(request: web.Request) -> web.Response:
    try:
        data = await _request_json(request)
        persistent = data.get("persistent")
        if persistent is not None and not isinstance(persistent, bool):
            raise ValueError("persistent must be a boolean")
        deleted = STORAGE.delete_run(
            data.get("history_id", ""),
            data.get("run_id"),
            persistent=persistent,
        )
        if not deleted:
            return web.json_response({"error": "run not found"}, status=404)
        LOGGER.info("[GenerationHistory] Deleted run #%s", data.get("run_id"))
        return web.json_response({"ok": True})
    except (ValueError, web.HTTPBadRequest) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except OSError as exc:
        LOGGER.exception("[GenerationHistory] Unable to delete run")
        return web.json_response({"error": str(exc)}, status=500)


@routes.post("/generation-history/clear")
async def clear_generation_history(request: web.Request) -> web.Response:
    try:
        data = await _request_json(request)
        removed = STORAGE.clear(data.get("history_id", ""))
        LOGGER.info("[GenerationHistory] Cleared %s runs", removed)
        return web.json_response({"ok": True, "removed": removed})
    except (ValueError, web.HTTPBadRequest) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except OSError as exc:
        LOGGER.exception("[GenerationHistory] Unable to clear history")
        return web.json_response({"error": str(exc)}, status=500)


class GenerationHistory:
    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "images": ("IMAGE",),
                "persist_history": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "label_on": "persist on",
                        "label_off": "persist off",
                    },
                ),
                "history_id": (
                    "STRING",
                    {
                        "default": "",
                        "advanced": True,
                        "tooltip": "Managed automatically by Generation History.",
                    },
                ),
                "seed_override": (
                    "STRING",
                    {"default": "", "advanced": True},
                ),
                "model_override": (
                    "STRING",
                    {"default": "", "advanced": True},
                ),
                "run_label": (
                    "STRING",
                    {"default": "", "advanced": True},
                ),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
                "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "record"
    CATEGORY = "image/history"
    OUTPUT_NODE = True
    DESCRIPTION = "Keeps each queued execution as one comparable history row."

    @classmethod
    def IS_CHANGED(cls, **_kwargs: Any) -> float:
        # ComfyUI documents NaN as the supported V1 always-changed fingerprint.
        return float("NaN")

    def record(
        self,
        images: Any,
        persist_history: bool,
        history_id: str,
        seed_override: str = "",
        model_override: str = "",
        run_label: str = "",
        prompt: Any = None,
        extra_pnginfo: Any = None,
        unique_id: Any = None,
    ) -> dict[str, Any] | tuple[Any]:
        del extra_pnginfo
        try:
            try:
                history_id = HistoryStorage.validate_history_id(history_id)
            except ValueError:
                history_id = str(uuid.uuid4())

            metadata = extract_metadata(prompt, unique_id)
            seed = _seed_override(seed_override, metadata["seed"])
            model = str(model_override or "").strip() or metadata["model"]
            execution_id = current_execution_id()
            run, new_images, created = STORAGE.append_images(
                history_id,
                execution_id,
                images,
                persist=bool(persist_history),
                timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
                seed=seed,
                model=model,
                loras=metadata["loras"],
                label=str(run_label or "").strip(),
            )
            payload = {
                "history_id": history_id,
                "node_id": str(unique_id),
                "persist": bool(persist_history),
                "execution_id": execution_id,
                "run_id": run["id"],
                "is_new": created,
                "new_images": new_images,
                "image_count": run["image_count"],
                "metadata": {
                    "model": run["model"],
                    "seeds": run["seeds"],
                    "loras": run["loras"],
                },
                "run": {**run, "images": new_images},
            }
            PromptServer.instance.send_sync(EVENT_NAME, payload)
            LOGGER.info(
                "[GenerationHistory] %s run #%s: +%s images (%s total)",
                "Created" if created else "Updated",
                run["id"],
                len(new_images),
                run["image_count"],
            )
        except Exception as exc:
            LOGGER.exception("[GenerationHistory] Unable to record generation")
            if persist_history:
                raise RuntimeError(
                    f"Generation History could not persist this run: {exc}"
                ) from exc
        return {"result": (images,)}
