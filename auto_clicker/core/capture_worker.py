"""Single capture worker thread.

Quartz CGWindowListCreateImage là API hệ thống bị Cocoa serialize. Khi N
scenario thread cùng gọi → kẹt nhau.

Worker này gom yêu cầu capture vào 1 thread duy nhất, mỗi window chỉ capture
1 lần / TTL. Các scenario thread chỉ "request" và đọc kết quả từ shared dict.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import numpy as np


class CaptureWorker:
    """Singleton worker. Capture window theo lịch tự định, scenario chỉ đọc cache.

    Cách dùng:
        worker = CaptureWorker.instance()
        worker.subscribe(window_id)   # đăng ký window cần capture
        img = worker.get(window_id)   # đọc frame mới nhất (None nếu chưa có)
        worker.unsubscribe(window_id) # khi không cần
    """

    _instance: Optional["CaptureWorker"] = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls) -> "CaptureWorker":
        with cls._lock:
            if cls._instance is None:
                cls._instance = CaptureWorker()
                cls._instance.start()
        return cls._instance

    def __init__(self, interval: float = 0.04):
        self._interval = interval  # 40ms ~ 25fps
        self._subscribers: dict[int, int] = {}  # window_id -> ref count
        self._frames: dict[int, tuple[float, np.ndarray]] = {}
        self._sub_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="CaptureWorker"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def subscribe(self, window_id: int) -> None:
        if window_id <= 0:
            return
        with self._sub_lock:
            self._subscribers[window_id] = self._subscribers.get(window_id, 0) + 1

    def unsubscribe(self, window_id: int) -> None:
        with self._sub_lock:
            cur = self._subscribers.get(window_id, 0)
            if cur <= 1:
                self._subscribers.pop(window_id, None)
                self._frames.pop(window_id, None)
            else:
                self._subscribers[window_id] = cur - 1

    def get(self, window_id: int, max_age: float = 0.5) -> Optional[np.ndarray]:
        """Trả frame gần nhất nếu còn tươi (max_age giây). None nếu hết hạn / chưa có."""
        rec = self._frames.get(window_id)
        if rec is None:
            return None
        ts, img = rec
        if time.time() - ts > max_age:
            return None
        return img

    def get_or_capture(self, window_id: int, max_age: float = 0.2) -> Optional[np.ndarray]:
        """Tiện: nếu cache hết hạn, fallback capture trực tiếp 1 lần."""
        img = self.get(window_id, max_age)
        if img is not None:
            return img
        # Capture trực tiếp 1 lần để khởi đầu
        from .window_manager import WindowManager
        return WindowManager.capture_window(window_id)

    def _loop(self) -> None:
        from .window_manager import WindowManager
        while not self._stop_event.is_set():
            with self._sub_lock:
                wids = list(self._subscribers.keys())
            if not wids:
                # Idle - sleep dài hơn để tiết kiệm CPU
                if self._stop_event.wait(0.2):
                    return
                continue
            t0 = time.time()
            for wid in wids:
                if self._stop_event.is_set():
                    return
                try:
                    img = WindowManager.capture_window(wid)
                except Exception:
                    img = None
                if img is not None:
                    self._frames[wid] = (time.time(), img)
            elapsed = time.time() - t0
            sleep_for = max(0.0, self._interval - elapsed)
            if self._stop_event.wait(sleep_for):
                return
