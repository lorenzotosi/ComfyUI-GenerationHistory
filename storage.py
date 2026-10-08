from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)
RUN_DIR_RE = re.compile(r"^run_(\d{6,})$")
IMAGE_FILE_RE = re.compile(r"^image_(\d{3,})\.png$")


class HistoryStorage:
    """Thread-safe storage for persistent and current-process histories."""

    def __init__(self, output_dir: str | Path, temp_dir: str | Path) -> None:
        self.output_root = Path(output_dir) / "generation_history"
        self.session_name = f"session_{uuid.uuid4().hex}"
        self.temp_root = Path(temp_dir) / "generation_history" / self.session_name
        self._volatile: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

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
    def _empty_manifest(history_id: str, next_run_id: int = 1) -> dict[str, Any]:
        return {
            "version": 1,
            "history_id": history_id,
            "next_run_id": max(1, int(next_run_id)),
            "runs": [],
        }

    def _persistent_dir(self, history_id: str) -> Path:
        return self.output_root / history_id

    def _volatile_dir(self, history_id: str) -> Path:
        return self.temp_root / history_id

    @staticmethod
    def _scan_next_run_id(history_dir: Path) -> int:
        highest = 0
        if history_dir.is_dir():
            for child in history_dir.iterdir():
                match = RUN_DIR_RE.fullmatch(child.name)
                if child.is_dir() and match:
                    highest = max(highest, int(match.group(1)))
        return highest + 1

    def _normalise_run(self, history_id: str, run: Any) -> dict[str, Any] | None:
        if not isinstance(run, dict):
            return None
        try:
            run_id = self.validate_run_id(run.get("id"))
        except ValueError:
            return None

        expected_subfolder = f"generation_history/{history_id}/run_{run_id:06d}"
        images = []
        for image in run.get("images", []):
            if not isinstance(image, dict):
                continue
            filename = str(image.get("filename", ""))
            if IMAGE_FILE_RE.fullmatch(filename):
                images.append(
                    {
                        "filename": filename,
                        "subfolder": expected_subfolder,
                        "type": "output",
                    }
                )

        loras = []
        for lora in run.get("loras", []):
            if isinstance(lora, dict) and lora.get("name"):
                loras.append(
                    {
                        "name": str(lora["name"]),
                        "strength_model": lora.get("strength_model"),
                    }
                )

        return {
            "id": run_id,
            "timestamp": str(run.get("timestamp", "")),
            "seed": run.get("seed", "Unknown"),
            "model": str(run.get("model") or "Unknown"),
            "label": str(run.get("label") or ""),
            "loras": loras,
            "image_count": len(images),
            "images": images,
        }

    def _load_persistent_unlocked(self, history_id: str) -> dict[str, Any]:
        history_dir = self._persistent_dir(history_id)
        manifest_path = history_dir / "manifest.json"
        if not manifest_path.exists():
            return self._empty_manifest(history_id, self._scan_next_run_id(history_dir))

        try:
            with manifest_path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, dict) or raw.get("history_id") != history_id:
                raise ValueError("manifest identity does not match its directory")
            runs = [
                normalised
                for item in raw.get("runs", [])
                if (normalised := self._normalise_run(history_id, item)) is not None
            ]
            runs.sort(key=lambda item: item["id"], reverse=True)
            next_run_id = max(
                int(raw.get("next_run_id", 1)),
                max((item["id"] for item in runs), default=0) + 1,
                self._scan_next_run_id(history_dir),
            )
            return {
                "version": 1,
                "history_id": history_id,
                "next_run_id": next_run_id,
                "runs": runs,
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            backup = manifest_path.with_name(
                f"manifest.corrupt-{uuid.uuid4().hex[:8]}.json"
            )
            try:
                os.replace(manifest_path, backup)
                LOGGER.error(
                    "[GenerationHistory] Corrupt manifest preserved as %s: %s",
                    backup,
                    exc,
                )
            except OSError:
                LOGGER.exception("[GenerationHistory] Unable to preserve corrupt manifest")
            return self._empty_manifest(history_id, self._scan_next_run_id(history_dir))

    @staticmethod
    def _atomic_write(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

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
        Image.fromarray(array).save(path, format="PNG", compress_level=4)

    def add_run(
        self,
        history_id: str,
        images: Any,
        *,
        persist: bool,
        timestamp: str,
        seed: Any,
        model: str,
        loras: list[dict[str, Any]],
        label: str,
    ) -> dict[str, Any]:
        history_id = self.validate_history_id(history_id)
        if len(images) < 1:
            raise ValueError("images batch is empty")

        with self._lock_for(history_id):
            persistent = self._load_persistent_unlocked(history_id)
            volatile = self._volatile.setdefault(
                history_id, self._empty_manifest(history_id)
            )
            run_id = max(persistent["next_run_id"], volatile["next_run_id"])
            base_dir = (
                self._persistent_dir(history_id)
                if persist
                else self._volatile_dir(history_id)
            )
            while (base_dir / f"run_{run_id:06d}").exists():
                run_id += 1

            run_name = f"run_{run_id:06d}"
            final_dir = base_dir / run_name
            temporary_dir = base_dir / f".{run_name}.{uuid.uuid4().hex}.tmp"
            temporary_dir.mkdir(parents=True, exist_ok=False)
            try:
                for index, image in enumerate(images):
                    self._save_png(image, temporary_dir / f"image_{index:03d}.png")
                os.replace(temporary_dir, final_dir)
            except Exception:
                shutil.rmtree(temporary_dir, ignore_errors=True)
                raise

            prefix = (
                f"generation_history/{history_id}/{run_name}"
                if persist
                else f"generation_history/{self.session_name}/{history_id}/{run_name}"
            )
            image_entries = [
                {
                    "filename": f"image_{index:03d}.png",
                    "subfolder": prefix,
                    "type": "output" if persist else "temp",
                }
                for index in range(len(images))
            ]
            run = {
                "id": run_id,
                "timestamp": timestamp,
                "seed": seed,
                "model": model or "Unknown",
                "label": label,
                "loras": loras,
                "image_count": len(image_entries),
                "images": image_entries,
            }

            next_run_id = run_id + 1
            volatile["next_run_id"] = next_run_id
            if persist:
                persistent["next_run_id"] = next_run_id
                persistent["runs"].insert(0, run)
                self._atomic_write(
                    self._persistent_dir(history_id) / "manifest.json", persistent
                )
            else:
                volatile["runs"].insert(0, run)
            return run

    def get_history(self, history_id: str, *, persist: bool) -> dict[str, Any]:
        history_id = self.validate_history_id(history_id)
        with self._lock_for(history_id):
            if persist:
                return self._load_persistent_unlocked(history_id)
            return self._empty_manifest(history_id)

    def delete_run(self, history_id: str, run_id: Any) -> bool:
        history_id = self.validate_history_id(history_id)
        run_id = self.validate_run_id(run_id)
        found = False
        with self._lock_for(history_id):
            persistent = self._load_persistent_unlocked(history_id)
            kept = [run for run in persistent["runs"] if run["id"] != run_id]
            if len(kept) != len(persistent["runs"]):
                persistent["runs"] = kept
                self._atomic_write(
                    self._persistent_dir(history_id) / "manifest.json", persistent
                )
                shutil.rmtree(
                    self._persistent_dir(history_id) / f"run_{run_id:06d}",
                    ignore_errors=True,
                )
                found = True

            volatile = self._volatile.get(history_id)
            if volatile is not None:
                kept = [run for run in volatile["runs"] if run["id"] != run_id]
                if len(kept) != len(volatile["runs"]):
                    volatile["runs"] = kept
                    shutil.rmtree(
                        self._volatile_dir(history_id) / f"run_{run_id:06d}",
                        ignore_errors=True,
                    )
                    found = True
        return found

    def clear(self, history_id: str) -> int:
        history_id = self.validate_history_id(history_id)
        with self._lock_for(history_id):
            persistent_dir = self._persistent_dir(history_id)
            persistent = self._load_persistent_unlocked(history_id)
            volatile = self._volatile.setdefault(
                history_id, self._empty_manifest(history_id)
            )
            next_run_id = max(
                persistent["next_run_id"], volatile["next_run_id"]
            )

            removed = len(persistent["runs"]) + len(volatile["runs"])
            for base_dir in (persistent_dir, self._volatile_dir(history_id)):
                if base_dir.is_dir():
                    for child in base_dir.iterdir():
                        if child.is_dir() and RUN_DIR_RE.fullmatch(child.name):
                            shutil.rmtree(child, ignore_errors=True)

            if persistent_dir.exists():
                self._atomic_write(
                    persistent_dir / "manifest.json",
                    self._empty_manifest(history_id, next_run_id),
                )
            self._volatile[history_id] = self._empty_manifest(
                history_id, next_run_id
            )
            return removed
