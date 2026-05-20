"""Window manager - liệt kê và capture window trên macOS qua Quartz."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import Quartz
from Quartz import (
    CGWindowListCopyWindowInfo,
    CGWindowListCreateImage,
    kCGNullWindowID,
    kCGWindowImageBoundsIgnoreFraming,
    kCGWindowImageNominalResolution,
    kCGWindowListExcludeDesktopElements,
    kCGWindowListOptionIncludingWindow,
    kCGWindowListOptionOnScreenOnly,
)


@dataclass
class WindowInfo:
    window_id: int
    pid: int
    owner: str
    title: str
    x: float
    y: float
    width: float
    height: float
    layer: int
    on_screen: bool

    @property
    def display_name(self) -> str:
        title = self.title or "(no title)"
        return f"{self.owner} - {title}"

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.width, self.height)


class WindowManager:
    """Quản lý liệt kê + capture window."""

    # Cache capture với TTL ngắn để khi nhiều scenario song song cùng window
    # chỉ thực sự capture 1 lần (capture là I/O đắt nhất).
    _capture_cache: dict[int, tuple[float, np.ndarray]] = {}
    _capture_lock = threading.Lock()
    _capture_ttl_seconds = 0.06  # ~60ms

    @staticmethod
    def list_windows(include_offscreen: bool = True) -> list[WindowInfo]:
        """Liệt kê tất cả window có owner + title hợp lệ."""
        options = kCGWindowListExcludeDesktopElements
        if not include_offscreen:
            options |= kCGWindowListOptionOnScreenOnly

        infos = CGWindowListCopyWindowInfo(options, kCGNullWindowID) or []
        results: list[WindowInfo] = []

        for info in infos:
            owner = info.get("kCGWindowOwnerName", "") or ""
            title = info.get("kCGWindowName", "") or ""
            layer = int(info.get("kCGWindowLayer", 0) or 0)

            # Bỏ qua window hệ thống / không có owner
            if not owner:
                continue
            # Layer 0 thường là user-level window. Cho phép cả layer khác nhưng skip menu bar bg
            if layer < 0:
                continue
            # Một số window không có title nhưng vẫn click được - giữ lại nếu owner OK
            bounds = info.get("kCGWindowBounds", {}) or {}
            w = float(bounds.get("Width", 0) or 0)
            h = float(bounds.get("Height", 0) or 0)
            if w < 50 or h < 50:
                continue

            results.append(
                WindowInfo(
                    window_id=int(info.get("kCGWindowNumber", 0)),
                    pid=int(info.get("kCGWindowOwnerPID", 0)),
                    owner=owner,
                    title=title,
                    x=float(bounds.get("X", 0) or 0),
                    y=float(bounds.get("Y", 0) or 0),
                    width=w,
                    height=h,
                    layer=layer,
                    on_screen=bool(info.get("kCGWindowIsOnscreen", False)),
                )
            )

        # Sort: on_screen trước, layer 0 trước, theo owner
        results.sort(key=lambda w: (not w.on_screen, w.layer, w.owner.lower()))
        return results

    @staticmethod
    def get_window(window_id: int) -> Optional[WindowInfo]:
        """Lấy thông tin một window theo ID, refresh từ hệ thống."""
        infos = (
            CGWindowListCopyWindowInfo(
                kCGWindowListOptionIncludingWindow, window_id
            )
            or []
        )
        for info in infos:
            if int(info.get("kCGWindowNumber", 0)) != window_id:
                continue
            bounds = info.get("kCGWindowBounds", {}) or {}
            return WindowInfo(
                window_id=window_id,
                pid=int(info.get("kCGWindowOwnerPID", 0)),
                owner=info.get("kCGWindowOwnerName", "") or "",
                title=info.get("kCGWindowName", "") or "",
                x=float(bounds.get("X", 0) or 0),
                y=float(bounds.get("Y", 0) or 0),
                width=float(bounds.get("Width", 0) or 0),
                height=float(bounds.get("Height", 0) or 0),
                layer=int(info.get("kCGWindowLayer", 0) or 0),
                on_screen=bool(info.get("kCGWindowIsOnscreen", False)),
            )
        return None

    @classmethod
    def capture_window(cls, window_id: int) -> Optional[np.ndarray]:
        """Capture 1 window theo windowID. Trả về ndarray BGR (OpenCV format).

        Có cache TTL ngắn để nhiều scenario song song cùng window không phải
        capture lại nhiều lần.
        """
        now = time.time()
        with cls._capture_lock:
            cached = cls._capture_cache.get(window_id)
            if cached is not None:
                ts, img = cached
                if now - ts < cls._capture_ttl_seconds:
                    return img

        image_ref = CGWindowListCreateImage(
            Quartz.CGRectNull,
            kCGWindowListOptionIncludingWindow,
            window_id,
            kCGWindowImageBoundsIgnoreFraming | kCGWindowImageNominalResolution,
        )
        if image_ref is None:
            return None

        width = Quartz.CGImageGetWidth(image_ref)
        height = Quartz.CGImageGetHeight(image_ref)
        if width == 0 or height == 0:
            return None

        bytes_per_row = Quartz.CGImageGetBytesPerRow(image_ref)
        data_provider = Quartz.CGImageGetDataProvider(image_ref)
        data = Quartz.CGDataProviderCopyData(data_provider)

        buf = np.frombuffer(data, dtype=np.uint8)
        buf = buf.reshape((height, bytes_per_row // 4, 4))
        buf = buf[:, :width, :]
        bgr = buf[:, :, :3].copy()

        with cls._capture_lock:
            cls._capture_cache[window_id] = (now, bgr)
            # Cleanup old entries (>1s)
            if len(cls._capture_cache) > 8:
                cls._capture_cache = {
                    wid: (t, im) for wid, (t, im) in cls._capture_cache.items()
                    if now - t < 1.0
                }
        return bgr

    @classmethod
    def capture_window_uncached(cls, window_id: int) -> Optional[np.ndarray]:
        """Bypass cache - dùng khi cần frame fresh tuyệt đối."""
        with cls._capture_lock:
            cls._capture_cache.pop(window_id, None)
        return cls.capture_window(window_id)

    @staticmethod
    def get_screen_scale() -> float:
        """Lấy scale factor (Retina = 2.0). Dùng để map pixel ↔ point."""
        try:
            main_display = Quartz.CGMainDisplayID()
            mode = Quartz.CGDisplayCopyDisplayMode(main_display)
            pixel_w = Quartz.CGDisplayModeGetPixelWidth(mode)
            point_w = Quartz.CGDisplayPixelsWide(main_display)
            if point_w > 0:
                return pixel_w / point_w
        except Exception:
            pass
        return 1.0
