"""Shared library cho templates + audios - dùng chung cho mọi scenario.

Lưu ở ~/.autoclicker/library.json. Mỗi scenario chỉ tham chiếu template_id /
audio_id. Engine khi resolve sẽ ưu tiên scenario.templates (legacy/local),
fallback library.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from .scenario import AudioRef, TemplateRef


LIBRARY_DIR = Path.home() / ".autoclicker"
LIBRARY_PATH = LIBRARY_DIR / "library.json"
LIBRARY_ASSETS_DIR = LIBRARY_DIR / "assets"


class MediaLibrary:
    """Singleton library load/save shared templates + audios."""

    _instance: Optional["MediaLibrary"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "MediaLibrary":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = MediaLibrary()
                cls._instance.load()
        return cls._instance

    def __init__(self):
        self.templates: list[TemplateRef] = []
        self.audios: list[AudioRef] = []
        self._lock = threading.Lock()
        LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        LIBRARY_ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    # ---------- IO ----------
    def load(self) -> None:
        if not LIBRARY_PATH.exists():
            return
        try:
            d = json.loads(LIBRARY_PATH.read_text())
        except Exception:
            return
        with self._lock:
            self.templates = [
                TemplateRef.from_dict(t) for t in d.get("templates", [])
            ]
            self.audios = [
                AudioRef.from_dict(a) for a in d.get("audios", [])
            ]

    def save(self) -> None:
        with self._lock:
            d = {
                "templates": [t.to_dict() for t in self.templates],
                "audios": [a.to_dict() for a in self.audios],
            }
        try:
            LIBRARY_PATH.write_text(json.dumps(d, indent=2))
        except Exception:
            pass

    # ---------- Templates ----------
    def add_template(self, ref: TemplateRef) -> None:
        with self._lock:
            # Replace nếu trùng id
            self.templates = [
                t for t in self.templates if t.template_id != ref.template_id
            ]
            self.templates.append(ref)
        self.save()

    def remove_template(self, template_id: str) -> bool:
        with self._lock:
            n = len(self.templates)
            self.templates = [
                t for t in self.templates if t.template_id != template_id
            ]
            removed = len(self.templates) != n
        if removed:
            self.save()
        return removed

    def get_template(self, template_id: str) -> Optional[TemplateRef]:
        with self._lock:
            for t in self.templates:
                if t.template_id == template_id:
                    return t
        return None

    def list_templates(self) -> list[TemplateRef]:
        with self._lock:
            return list(self.templates)

    # ---------- Audios ----------
    def add_audio(self, ref: AudioRef) -> None:
        with self._lock:
            self.audios = [a for a in self.audios if a.audio_id != ref.audio_id]
            self.audios.append(ref)
        self.save()

    def remove_audio(self, audio_id: str) -> bool:
        with self._lock:
            n = len(self.audios)
            self.audios = [a for a in self.audios if a.audio_id != audio_id]
            removed = len(self.audios) != n
        if removed:
            self.save()
        return removed

    def get_audio(self, audio_id: str) -> Optional[AudioRef]:
        with self._lock:
            for a in self.audios:
                if a.audio_id == audio_id:
                    return a
        return None

    def list_audios(self) -> list[AudioRef]:
        with self._lock:
            return list(self.audios)
