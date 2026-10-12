from __future__ import annotations

import os
import shutil
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any


class HistoryStorage:
    """Thread-safe storage for the current ComfyUI session."""

    def __init__(self, temp_dir: str | Path) -> None:
        self.session_name = f"session_{uuid.uuid4().hex}"
        self.temp_root = Path(temp_dir) / "generation_history" / self.session_name
        self._histories: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._released: set[str] = set()

    @staticmethod
    def validate_history_id(value: str) -> str:
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("history_id must be a valid UUID") from exc

    @staticmethod
    def validate_run_id(value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("run_id must be a positive integer")
        try:
            run_id = int(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("run_id must be a positive integer") from exc
        if run_id < 1 or str(value).strip() != str(run_id):
            raise ValueError("run_id must be a positive integer")
        return run_id

    def _lock_for(self, history_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(history_id, threading.RLock())

    @staticmethod
    def _empty_history(history_id: str, next_run_id: int = 1) -> dict[str, Any]:
        return {
            "history_id": history_id,
            "generation": uuid.uuid4().hex,
            "next_run_id": max(1, int(next_run_id)),
            "runs": [],
        }

    def _history_dir(self, history_id: str) -> Path:
        return self.temp_root / history_id

    @staticmethod
    def _save_png(image: Any, path: Path) -> None:
        import numpy as np
        from PIL import Image

        array = image.cpu().numpy() if hasattr(image, "cpu") else np.asarray(image)
        array = np.clip(array * 255.0, 0, 255).astype(np.uint8)
        if array.ndim == 3 and array.shape[-1] == 1:
            array = array[..., 0]
        if array.ndim != 2 and (array.ndim != 3 or array.shape[-1] not in (3, 4)):
            raise ValueError(f"unsupported image shape: {array.shape}")
        image = Image.fromarray(array)
        image.save(path, format="PNG", compress_level=1)
        image.thumbnail((512, 512))
        if image.mode == "RGBA":
            thumbnail = Image.new("RGB", image.size, (0, 0, 0))
            thumbnail.paste(image, mask=image.getchannel("A"))
        else:
            thumbnail = image.convert("RGB")
        thumbnail.save(path.with_suffix(".thumb.jpg"), format="JPEG", quality=85)

    def append_images(
        self,
        history_id: str,
        execution_id: str,
        images: Any,
        *,
        timestamp: str,
        seed: Any,
        model: str,
        loras: list[dict[str, Any]],
        label: str,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], bool]:
        history_id = self.validate_history_id(history_id)
        execution_id = str(execution_id or "").strip()
        if not execution_id or len(execution_id) > 256:
            raise ValueError("execution_id must be a non-empty string")
        if len(images) < 1:
            raise ValueError("images batch is empty")

        with self._lock_for(history_id):
            if history_id in self._released:
                return None, [], False
            history = self._histories.setdefault(
                history_id, self._empty_history(history_id)
            )

        batch_dir = self.temp_root / f"batch_{uuid.uuid4().hex}"
        batch_dir.mkdir(parents=True)
        try:
            for index, image in enumerate(images):
                self._save_png(image, batch_dir / f"image_{index:03d}.png")
            with self._lock_for(history_id):
                if self._histories.get(history_id) is not history:
                    return None, [], False
                return self._commit_images(
                    history_id, history, execution_id, images, batch_dir,
                    timestamp=timestamp, seed=seed, model=model, loras=loras,
                    label=label,
                )
        finally:
            shutil.rmtree(batch_dir)

    def _commit_images(
        self, history_id: str, history: dict[str, Any], execution_id: str,
        images: Any, batch_dir: Path, *, timestamp: str, seed: Any, model: str,
        loras: list[dict[str, Any]], label: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
        run = next(
            (
                item
                for item in history["runs"]
                if item["execution_id"] == execution_id
            ),
            None,
        )

        created = run is None
        if created:
            run = {
                "id": history["next_run_id"],
                "execution_id": execution_id,
                "generation": history["generation"],
                "timestamp": timestamp,
                "seeds": [],
                "model": model or "Unknown",
                "label": label,
                "loras": [],
                "image_count": 0,
                "images": [],
            }

        run_name = f"run_{run['id']:06d}"
        final_dir = self._history_dir(history_id) / history["generation"] / run_name
        final_dir.mkdir(parents=True, exist_ok=True)
        start_index = max(
            (image.get("index", -1) for image in run["images"]), default=-1
        ) + 1
        committed: list[Path] = []
        try:
            for offset in range(len(images)):
                index = start_index + offset
                for suffix in (".png", ".thumb.jpg"):
                    final_path = final_dir / f"image_{index:03d}{suffix}"
                    os.replace(batch_dir / f"image_{offset:03d}{suffix}", final_path)
                    committed.append(final_path)
        except Exception:
            for final_path in committed:
                final_path.unlink(missing_ok=True)
            if created:
                shutil.rmtree(final_dir, ignore_errors=True)
            raise

        prefix = (
            f"generation_history/{self.session_name}/{history_id}/"
            f"{history['generation']}/{run_name}"
        )
        image_entries = [
            {
                "filename": f"image_{index:03d}.png",
                "thumbnail": f"image_{index:03d}.thumb.jpg",
                "subfolder": prefix,
                "type": "temp",
                "index": index,
                "seed": seed,
            }
            for index in range(start_index, start_index + len(images))
        ]
        run["images"].extend(image_entries)
        run["image_count"] = len(run["images"])
        if seed != "Unknown" and seed not in run["seeds"]:
            run["seeds"].append(seed)
        if run["model"] == "Unknown" and model:
            run["model"] = model
        if not run["label"] and label:
            run["label"] = label
        known_loras = {
            (item["name"], item.get("strength_model")) for item in run["loras"]
        }
        run["loras"].extend(
            item
            for item in loras
            if (item["name"], item.get("strength_model")) not in known_loras
        )

        if created:
            history["runs"].insert(0, run)
            history["next_run_id"] = run["id"] + 1
        return deepcopy({**run, "images": image_entries}), deepcopy(image_entries), created

    def get_history(self, history_id: str) -> dict[str, Any]:
        history_id = self.validate_history_id(history_id)
        with self._lock_for(history_id):
            return deepcopy(
                self._histories.get(history_id, self._empty_history(history_id))
            )

    def reset(self, history_id: str, generation: str | None = None) -> int:
        history_id = self.validate_history_id(history_id)
        generation = uuid.UUID(str(generation)).hex if generation is not None else uuid.uuid4().hex
        with self._lock_for(history_id):
            current = self._histories.get(history_id)
            removed = len(current["runs"]) if current is not None else 0
            self._remove_directory(self._history_dir(history_id))
            self._histories[history_id] = self._empty_history(history_id)
            self._histories[history_id]["generation"] = generation
            self._released.discard(history_id)
            return removed

    def delete_run(self, history_id: str, run_id: Any) -> bool:
        history_id = self.validate_history_id(history_id)
        run_id = self.validate_run_id(run_id)
        with self._lock_for(history_id):
            history = self._histories.get(history_id)
            if history is None:
                return False
            kept = [run for run in history["runs"] if run["id"] != run_id]
            if len(kept) == len(history["runs"]):
                return False
            self._remove_directory(
                self._history_dir(history_id) / history["generation"] / f"run_{run_id:06d}",
            )
            history["runs"] = kept
            return True

    def clear(self, history_id: str, generation: str | None = None) -> int:
        history_id = self.validate_history_id(history_id)
        generation = uuid.UUID(str(generation)).hex if generation is not None else uuid.uuid4().hex
        with self._lock_for(history_id):
            history = self._histories.setdefault(
                history_id, self._empty_history(history_id)
            )
            removed = len(history["runs"])
            next_run_id = history["next_run_id"]
            self._remove_directory(self._history_dir(history_id))
            self._histories[history_id] = self._empty_history(
                history_id, next_run_id
            )
            self._histories[history_id]["generation"] = generation
            return removed

    @staticmethod
    def _remove_directory(path: Path) -> None:
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            if path.exists():
                raise

    def release(self, history_id: str, generation: str) -> int:
        history_id = self.validate_history_id(history_id)
        with self._lock_for(history_id):
            history = self._histories.get(history_id)
            if history is None or history["generation"] != generation:
                return 0
            self._remove_directory(self._history_dir(history_id))
            removed = len(history["runs"])
            del self._histories[history_id]
            self._released.add(history_id)
            return removed
