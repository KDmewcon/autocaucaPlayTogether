"""Dialog chọn 1 VÙNG (region) trên screenshot của window.

Khác RegionSelectorDialog (chỉ kéo crop 1 lần), dialog này cho:
- Kéo thả tạo vùng
- Di chuyển vùng (kéo giữa)
- Thay đổi kích thước qua 8 handle ở góc/cạnh (giống Macrorify)
- Trả về region theo % của window + màu trung bình hiện tại của vùng (BGR)

Region trả về dạng dict:
    {"unit": "percent", "x": .., "y": .., "w": .., "h": ..}
với x/y/w/h là phần trăm so với width/height window (0..100).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.window_manager import WindowManager
from ..utils.qt_utils import ndarray_bgr_to_qpixmap


# Handle index
_H_NONE = -1
_H_MOVE = 8
_HANDLE_SIZE = 9  # bán kính vùng bắt handle (px canvas)


class _RegionCanvas(QWidget):
    """Canvas vẽ pixmap + 1 rectangle có thể kéo/resize."""

    regionChanged = Signal(QRect)

    def __init__(self, pixmap):
        super().__init__()
        self._pixmap = pixmap
        self.setFixedSize(pixmap.size())
        self.setMouseTracking(True)
        self._rect = QRect()
        self._active_handle = _H_NONE
        self._drag_start = QPoint()
        self._rect_at_press = QRect()
        self._creating = False

    # ---- public
    def set_rect(self, r: QRect) -> None:
        self._rect = QRect(r).normalized()
        self.update()
        self.regionChanged.emit(self._rect)

    def rect_sel(self) -> QRect:
        return QRect(self._rect).normalized().intersected(self._pixmap.rect())

    # ---- handles geometry
    def _handle_points(self, r: QRect) -> list[QPoint]:
        # 0 TL, 1 TC, 2 TR, 3 RC, 4 BR, 5 BC, 6 BL, 7 LC
        cx = r.center().x()
        cy = r.center().y()
        return [
            QPoint(r.left(), r.top()),
            QPoint(cx, r.top()),
            QPoint(r.right(), r.top()),
            QPoint(r.right(), cy),
            QPoint(r.right(), r.bottom()),
            QPoint(cx, r.bottom()),
            QPoint(r.left(), r.bottom()),
            QPoint(r.left(), cy),
        ]

    def _hit_handle(self, pos: QPoint) -> int:
        if self._rect.isNull() or self._rect.width() == 0:
            return _H_NONE
        r = self._rect.normalized()
        for i, hp in enumerate(self._handle_points(r)):
            if (abs(pos.x() - hp.x()) <= _HANDLE_SIZE
                    and abs(pos.y() - hp.y()) <= _HANDLE_SIZE):
                return i
        if r.contains(pos):
            return _H_MOVE
        return _H_NONE

    def _cursor_for_handle(self, h: int):
        return {
            0: Qt.CursorShape.SizeFDiagCursor,
            1: Qt.CursorShape.SizeVerCursor,
            2: Qt.CursorShape.SizeBDiagCursor,
            3: Qt.CursorShape.SizeHorCursor,
            4: Qt.CursorShape.SizeFDiagCursor,
            5: Qt.CursorShape.SizeVerCursor,
            6: Qt.CursorShape.SizeBDiagCursor,
            7: Qt.CursorShape.SizeHorCursor,
            _H_MOVE: Qt.CursorShape.SizeAllCursor,
        }.get(h, Qt.CursorShape.CrossCursor)

    # ---- painting
    def paintEvent(self, e: QPaintEvent) -> None:
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pixmap)
        r = self._rect.normalized()
        if r.width() <= 0 or r.height() <= 0:
            return
        # Dim ngoài vùng
        overlay = QColor(0, 0, 0, 110)
        p.fillRect(QRect(0, 0, self.width(), r.top()), overlay)
        p.fillRect(
            QRect(0, r.bottom(), self.width(), self.height() - r.bottom()),
            overlay,
        )
        p.fillRect(QRect(0, r.top(), r.left(), r.height()), overlay)
        p.fillRect(
            QRect(r.right(), r.top(), self.width() - r.right(), r.height()),
            overlay,
        )
        # Viền vùng
        p.setPen(QPen(QColor(0, 220, 120), 2))
        p.drawRect(r)
        # Handles
        p.setPen(QPen(QColor(255, 255, 255), 1))
        for hp in self._handle_points(r):
            p.fillRect(
                QRect(hp.x() - 4, hp.y() - 4, 8, 8),
                QColor(0, 220, 120),
            )
            p.drawRect(QRect(hp.x() - 4, hp.y() - 4, 8, 8))

    # ---- mouse
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position().toPoint()
        h = self._hit_handle(pos)
        self._drag_start = pos
        self._rect_at_press = QRect(self._rect)
        if h == _H_NONE:
            # Tạo vùng mới
            self._creating = True
            self._active_handle = 4  # resize từ góc BR
            self._rect = QRect(pos, pos)
            self._rect_at_press = QRect(self._rect)
        else:
            self._creating = False
            self._active_handle = h
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        pos = e.position().toPoint()
        if self._active_handle == _H_NONE:
            # Chỉ cập nhật cursor
            self.setCursor(self._cursor_for_handle(self._hit_handle(pos)))
            return
        dx = pos.x() - self._drag_start.x()
        dy = pos.y() - self._drag_start.y()
        r = QRect(self._rect_at_press)
        h = self._active_handle
        if h == _H_MOVE:
            r.translate(dx, dy)
        else:
            left, top, right, bottom = r.left(), r.top(), r.right(), r.bottom()
            if h in (0, 6, 7):  # left edge
                left += dx
            if h in (2, 3, 4):  # right edge
                right += dx
            if h in (0, 1, 2):  # top edge
                top += dy
            if h in (4, 5, 6):  # bottom edge
                bottom += dy
            r = QRect(QPoint(left, top), QPoint(right, bottom))
        # Clamp vào pixmap
        r = r.intersected(self._pixmap.rect()) if h == _H_MOVE else r.normalized()
        r = r.intersected(self._pixmap.rect())
        self._rect = r
        self.update()
        self.regionChanged.emit(self.rect_sel())

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._active_handle = _H_NONE
            self._creating = False
            self._rect = self.rect_sel()
            self.update()
            self.regionChanged.emit(self._rect)


class RegionPickerDialog(QDialog):
    """Chọn vùng resizable trên window. Trả về region (% window) + màu BGR."""

    def __init__(
        self,
        window_id: int,
        initial_region: Optional[dict] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Chọn vùng theo dõi - kéo thả + chỉnh kích thước")
        self.resize(960, 760)
        self.region: Optional[dict] = None  # {"unit","x","y","w","h"}
        self.mean_bgr: Optional[tuple[float, float, float]] = None

        self._win = WindowManager.get_window(window_id)
        self._screenshot = WindowManager.capture_window(window_id)

        layout = QVBoxLayout(self)
        info = QLabel(
            "Kéo chuột để tạo vùng. Kéo <b>giữa</b> để di chuyển, kéo "
            "<b>8 chấm</b> ở viền để chỉnh kích thước. Vùng này sẽ được "
            "theo dõi màu (ví dụ chấm than ! khi câu cá)."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        if self._screenshot is None or self._win is None:
            layout.addWidget(QLabel("Không capture được window."))
            btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
            btns.rejected.connect(self.reject)
            layout.addWidget(btns)
            return

        pixmap = ndarray_bgr_to_qpixmap(self._screenshot)
        max_w, max_h = 1150, 760
        self._scale = 1.0
        if pixmap.width() > max_w or pixmap.height() > max_h:
            scaled = pixmap.scaled(
                max_w,
                max_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._scale = scaled.width() / pixmap.width()
            pixmap = scaled

        self._canvas = _RegionCanvas(pixmap)
        self._canvas.regionChanged.connect(self._on_region)

        scroll = QScrollArea()
        scroll.setWidget(self._canvas)
        scroll.setWidgetResizable(False)
        layout.addWidget(scroll, 1)

        # Restore initial region nếu có
        if initial_region:
            self._set_initial_region(initial_region)

        bottom = QHBoxLayout()
        self._info_lbl = QLabel("Chưa chọn vùng.")
        bottom.addWidget(self._info_lbl, 1)
        self._swatch = QLabel()
        self._swatch.setFixedSize(48, 24)
        self._swatch.setStyleSheet(
            "background:#000; border:1px solid #888;"
        )
        bottom.addWidget(QLabel("Màu vùng:"))
        bottom.addWidget(self._swatch)
        layout.addLayout(bottom)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _set_initial_region(self, region: dict) -> None:
        """Convert region (% window) -> canvas rect và set."""
        if self._screenshot is None:
            return
        sh_px, sw_px = self._screenshot.shape[:2]
        unit = region.get("unit", "percent")
        if unit == "percent":
            x = region.get("x", 0) / 100.0 * sw_px
            y = region.get("y", 0) / 100.0 * sh_px
            w = region.get("w", 0) / 100.0 * sw_px
            h = region.get("h", 0) / 100.0 * sh_px
        else:
            x, y, w, h = (
                region.get("x", 0), region.get("y", 0),
                region.get("w", 0), region.get("h", 0),
            )
        cr = QRect(
            int(x * self._scale), int(y * self._scale),
            int(w * self._scale), int(h * self._scale),
        )
        self._canvas.set_rect(cr)

    def _canvas_rect_to_img(self, r: QRect) -> tuple[int, int, int, int]:
        x = int(r.x() / self._scale)
        y = int(r.y() / self._scale)
        w = int(r.width() / self._scale)
        h = int(r.height() / self._scale)
        return x, y, w, h

    def _on_region(self, r: QRect) -> None:
        if r.width() <= 0 or r.height() <= 0 or self._screenshot is None:
            self._info_lbl.setText("Chưa chọn vùng.")
            return
        x, y, w, h = self._canvas_rect_to_img(r)
        sh_px, sw_px = self._screenshot.shape[:2]
        x = max(0, min(x, sw_px - 1))
        y = max(0, min(y, sh_px - 1))
        w = max(1, min(w, sw_px - x))
        h = max(1, min(h, sh_px - y))
        patch = self._screenshot[y : y + h, x : x + w]
        if patch.size:
            mean = patch.reshape(-1, patch.shape[-1]).mean(axis=0)
            b, g, rr = float(mean[0]), float(mean[1]), float(mean[2])
            self._swatch.setStyleSheet(
                f"background: rgb({int(rr)},{int(g)},{int(b)}); "
                "border:1px solid #888;"
            )
        # Region theo % window
        px = x / sw_px * 100.0
        py = y / sh_px * 100.0
        pw = w / sw_px * 100.0
        ph = h / sh_px * 100.0
        self._info_lbl.setText(
            f"Vùng: ({px:.1f}%, {py:.1f}%)  {pw:.1f}×{ph:.1f}%   "
            f"≈ {w}×{h}px"
        )

    def _accept(self) -> None:
        r = self._canvas.rect_sel()
        if r.width() < 3 or r.height() < 3:
            self._info_lbl.setText("Vùng quá nhỏ, chọn lớn hơn.")
            return
        x, y, w, h = self._canvas_rect_to_img(r)
        sh_px, sw_px = self._screenshot.shape[:2]
        x = max(0, min(x, sw_px - 1))
        y = max(0, min(y, sh_px - 1))
        w = max(1, min(w, sw_px - x))
        h = max(1, min(h, sh_px - y))
        patch = self._screenshot[y : y + h, x : x + w]
        mean = patch.reshape(-1, patch.shape[-1]).mean(axis=0)
        self.mean_bgr = (float(mean[0]), float(mean[1]), float(mean[2]))
        self.region = {
            "unit": "percent",
            "x": round(x / sw_px * 100.0, 3),
            "y": round(y / sh_px * 100.0, 3),
            "w": round(w / sw_px * 100.0, 3),
            "h": round(h / sh_px * 100.0, 3),
        }
        self.accept()
