"""Shared audio capture bus.

Chỉ mở 1 sounddevice.InputStream per device, broadcast samples cho mọi
subscriber. Tránh xung đột "device busy" khi N scenario cùng cần audio.

Usage:
    bus = AudioBus.instance()
    bus.subscribe(device=2, ref_buffer)  # ref_buffer: AudioStreamBuffer hoặc level monitor
    ...
    bus.unsubscribe(device=2, ref_buffer)
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import numpy as np


# Sample rate phải khớp với audio_matcher.SAMPLE_RATE
DEFAULT_SR = 16000


class AudioBus:
    """Singleton audio bus. Mỗi device chỉ 1 stream."""

    _instance: Optional["AudioBus"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "AudioBus":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = AudioBus()
        return cls._instance

    def __init__(self):
        self._streams: dict[Optional[int], dict] = {}
        # Mỗi entry:
        #   {"stream": sd.InputStream, "subs": list[Callable], "ref_count": int,
        #    "lock": threading.Lock(), "last_rms": float}
        self._lock = threading.Lock()

    def subscribe(
        self,
        device: Optional[int],
        callback: Callable[[np.ndarray], None],
        samplerate: int = DEFAULT_SR,
    ) -> bool:
        """Đăng ký callback nhận samples mono float32. Return True nếu OK."""
        with self._lock:
            entry = self._streams.get(device)
            if entry is not None:
                entry["subs"].append(callback)
                entry["ref_count"] += 1
                return True

            # Tạo stream mới
            try:
                import sounddevice as sd
            except Exception:
                return False
            entry = {
                "stream": None,
                "subs": [callback],
                "ref_count": 1,
                "lock": threading.Lock(),
                "samplerate": samplerate,
            }

            def _cb(indata, frames, time_info, status):
                # indata shape (frames, channels)
                try:
                    if indata.ndim > 1:
                        mono = indata.mean(axis=1).astype(np.float32, copy=False)
                    else:
                        mono = indata.astype(np.float32, copy=False)
                    # Snapshot subs để tránh giữ lock khi callback chạy
                    with entry["lock"]:
                        subs = list(entry["subs"])
                    for cb in subs:
                        try:
                            cb(mono)
                        except Exception:
                            pass
                except Exception:
                    pass

            try:
                blocksize = max(256, samplerate // 20)
                stream = sd.InputStream(
                    device=device if device is not None and device >= 0 else None,
                    channels=1,
                    samplerate=samplerate,
                    dtype="float32",
                    blocksize=blocksize,
                    callback=_cb,
                )
                stream.start()
                entry["stream"] = stream
            except Exception:
                return False

            self._streams[device] = entry
            return True

    def unsubscribe(
        self, device: Optional[int], callback: Callable[[np.ndarray], None]
    ) -> None:
        with self._lock:
            entry = self._streams.get(device)
            if entry is None:
                return
            with entry["lock"]:
                try:
                    entry["subs"].remove(callback)
                except ValueError:
                    pass
            entry["ref_count"] -= 1
            if entry["ref_count"] <= 0:
                stream = entry.get("stream")
                if stream is not None:
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
                self._streams.pop(device, None)

    def stop_all(self) -> None:
        with self._lock:
            for dev, entry in list(self._streams.items()):
                stream = entry.get("stream")
                if stream is not None:
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
            self._streams.clear()
