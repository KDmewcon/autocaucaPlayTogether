"""Audio monitor - đo RMS từ input device.

Dùng cho step WAIT_FOR_SOUND. User chọn 1 input device (mic, BlackHole loopback...)
và threshold. Engine sẽ chờ đến khi RMS vượt threshold để trigger.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

try:
    import sounddevice as sd

    _HAS_SD = True
except Exception:  # pragma: no cover
    _HAS_SD = False


@dataclass
class AudioDeviceInfo:
    index: int
    name: str
    max_input_channels: int
    default_samplerate: float

    @property
    def display(self) -> str:
        return f"#{self.index}  {self.name}  ({self.max_input_channels}ch)"


def list_input_devices() -> list[AudioDeviceInfo]:
    """Liệt kê input devices."""
    if not _HAS_SD:
        return []
    try:
        devs = sd.query_devices()
    except Exception:
        return []
    out: list[AudioDeviceInfo] = []
    for i, d in enumerate(devs):
        try:
            ch = int(d.get("max_input_channels", 0) or 0)
            if ch <= 0:
                continue
            out.append(
                AudioDeviceInfo(
                    index=i,
                    name=str(d.get("name", f"device {i}")),
                    max_input_channels=ch,
                    default_samplerate=float(d.get("default_samplerate", 44100)),
                )
            )
        except Exception:
            continue
    return out


def get_default_input_index() -> Optional[int]:
    if not _HAS_SD:
        return None
    try:
        idx = sd.default.device[0]
        if isinstance(idx, int) and idx >= 0:
            return idx
    except Exception:
        pass
    devs = list_input_devices()
    return devs[0].index if devs else None


class AudioLevelMonitor:
    """Mở 1 stream và liên tục cập nhật RMS hiện tại.

    Dùng RMS chuẩn hoá [0..1]. dBFS có thể dùng convert qua 20*log10(rms).
    Stream chạy ở callback thread của portaudio, lock chỉ khi update _rms.
    """

    def __init__(
        self,
        device: Optional[int] = None,
        samplerate: int = 44100,
        block_ms: int = 50,
        smoothing: float = 0.4,
    ):
        self._device = device
        self._samplerate = samplerate
        self._block_ms = block_ms
        self._smoothing = smoothing
        self._stream: Optional["sd.InputStream"] = None
        self._lock = threading.Lock()
        self._rms = 0.0
        self._peak = 0.0
        self._error: Optional[str] = None
        self._running = False

    @property
    def is_available(self) -> bool:
        return _HAS_SD

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def device(self) -> Optional[int]:
        return self._device

    @property
    def error(self) -> Optional[str]:
        return self._error

    @property
    def rms(self) -> float:
        return self._rms

    @property
    def peak(self) -> float:
        return self._peak

    def _callback(self, indata, frames, time_info, status):  # noqa
        try:
            data = indata if indata.ndim == 1 else indata[:, 0]
            if data.size == 0:
                return
            r = float(np.sqrt(np.mean(np.square(data, dtype=np.float64))))
            p = float(np.max(np.abs(data)))
            with self._lock:
                # EMA smoothing
                if self._smoothing > 0:
                    self._rms = (
                        self._smoothing * self._rms
                        + (1 - self._smoothing) * r
                    )
                else:
                    self._rms = r
                # Peak với decay
                self._peak = max(self._peak * 0.85, p)
        except Exception as e:
            self._error = str(e)

    def start(self) -> bool:
        if not _HAS_SD:
            self._error = "sounddevice module không khả dụng"
            return False
        if self._running:
            return True
        try:
            blocksize = max(1, int(self._samplerate * self._block_ms / 1000))
            self._stream = sd.InputStream(
                device=self._device,
                channels=1,
                samplerate=self._samplerate,
                blocksize=blocksize,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
            self._running = True
            self._error = None
            return True
        except Exception as e:
            self._error = f"Không mở được audio stream: {e}"
            self._running = False
            return False

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        self._stream = None
        self._running = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


def rms_to_dbfs(rms: float) -> float:
    if rms <= 1e-9:
        return -120.0
    return 20.0 * math.log10(min(1.0, max(0.0, rms)))
