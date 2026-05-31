"""Dialog: import 1 ảnh từ máy, click để chấm (eyedropper) lấy màu.

Trả về:
- picked_bgr: (b, g, r) màu tại điểm/vùng đã chấm
Dùng để thêm màu mục tiêu cho step WATCH_COLOR mà không cần capture window.
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..utils.qt_utils import ndarray_bgr_to_qpixmap


class _PickCanvas(QWidget):
    posChanged = Signal(QPoint)

    def __init__(self, pixmap):
        super().__init__()
        self._pixmap = pixmap
        self.setFixedSize(pixmap.size())
        self.setMouseTracking(True)
        self._pos: Optional[QPoint] = None
        self._hover: Optional[QPoint] = None

    def picked(self) -> Optional[QPoint]:
        return self._pos

    def paintEvent(self, e: QPaintEvent) -> None:
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pixmap)
        pt = self._pos
        if pt is not None:
            p.setPen(QPen(QColor(255, 0, 0), 2))
            r = 12
            p.drawLine(pt.x() - r, pt.y(), pt.x() + r, pt.y())
            p.drawLine(pt.x(), pt.y() - r, pt.x(), pt.y() + r)
            p.drawEllipse(pt, 5, 5)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._pos = e.position().toPoint()
            self.update()
            self.posChanged.emit(self._pos)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        self._hover = e.position().toPoint()
        self.posChanged.emit(self._hover)


class ImageColorPickerDialog(QDialog):
    """Import ảnh + chấm màu. picked_bgr = (b,g,r) hoặc None nếu cancel."""

    def __init__(self, image_path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chấm màu từ ảnh - click vào điểm cần lấy màu")
        self.resize(900, 720)
        self.picked_bgr: Optional[tuple[float, float, float]] = None

        self._img: Optional[np.ndarray] = None  # BGR
        self._scale = 1.0
        self._last_canvas_pt: Optional[QPoint] = None

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self._open_btn = QPushButton("📂  Chọn ảnh từ máy...")
        self._open_btn.clicked.connect(self._open_image)
        top.addWidget(self._open_btn)
        top.addWidget(QLabel("Bán kính lấy mẫu:"))
        self._radius = QSpinBox()
        self._radius.setRange(0, 30)
        self._radius.setValue(2)
        self._radius.setSuffix(" px")
        self._radius.setToolTip(
            "Lấy màu trung bình của ô vuông quanh điểm click (chống nhiễu)."
        )
        self._radius.valueChanged.connect(lambda _: self._resample())
        top.addWidget(self._radius)
        top.addStretch(1)
        layout.addLayout(top)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        layout.addWidget(self._scroll, 1)
        self._canvas: Optional[_PickCanvas] = None

        bottom = QHBoxLayout()
        self._info_lbl = QLabel("Chưa chọn ảnh.")
        bottom.addWidget(self._info_lbl, 1)
        bottom.addWidget(QLabel("Màu:"))
        self._swatch = QLabel()
        self._swatch.setFixedSize(48, 24)
        self._swatch.setStyleSheet("background:#000; border:1px solid #888;")
        bottom.addWidget(self._swatch)
        layout.addLayout(bottom)

        self._btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._btns.accepted.connect(self._accept)
        self._btns.rejected.connect(self.reject)
        self._btns.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        layout.addWidget(self._btns)

        if image_path:
            self._load(image_path)

    def _open_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn ảnh",
            "",
            "Ảnh (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;Tất cả (*)",
        )
        if path:
            self._load(path)

    def _load(self, path: str) -> None:
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            self._info_lbl.setText(f"Không đọc được ảnh: {path}")
            return
        self._img = img
        pixmap = ndarray_bgr_to_qpixmap(img)
        max_w, max_h = 1080, 660
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
        self._canvas = _PickCanvas(pixmap)
        self._canvas.posChanged.connect(self._on_pos)
        self._scroll.setWidget(self._canvas)
        self._info_lbl.setText(
            f"Ảnh {self._img.shape[1]}×{self._img.shape[0]}px. "
            "Click vào điểm cần lấy màu."
        )

    def _sample_at(self, canvas_pt: QPoint) -> Optional[tuple[float, float, float]]:
        if self._img is None:
            return None
        x = int(canvas_pt.x() / self._scale)
        y = int(canvas_pt.y() / self._scale)
        h, w = self._img.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            return None
        rad = self._radius.value()
        x0 = max(0, x - rad)
        y0 = max(0, y - rad)
        x1 = min(w, x + rad + 1)
        y1 = min(h, y + rad + 1)
        patch = self._img[y0:y1, x0:x1]
        if patch.size == 0:
            return None
        mean = patch.reshape(-1, patch.shape[-1]).mean(axis=0)
        return float(mean[0]), float(mean[1]), float(mean[2])

    def _on_pos(self, pt: QPoint) -> None:
        # Chỉ cập nhật preview khi đã có điểm click cố định
        clicked = self._canvas.picked() if self._canvas else None
        if clicked is None:
            return
        self._last_canvas_pt = clicked
        self._resample()

    def _resample(self) -> None:
        if self._last_canvas_pt is None:
            return
        bgr = self._sample_at(self._last_canvas_pt)
        if bgr is None:
            return
        self.picked_bgr = bgr
        b, g, r = int(bgr[0]), int(bgr[1]), int(bgr[2])
        self._swatch.setStyleSheet(
            f"background: rgb({r},{g},{b}); border:1px solid #888;"
        )
        self._info_lbl.setText(
            f"Màu đã chấm: BGR({b},{g},{r})  /  RGB({r},{g},{b})  "
            f"#{r:02x}{g:02x}{b:02x}"
        )
        self._btns.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)

    def _accept(self) -> None:
        if self.picked_bgr is None:
            self._info_lbl.setText("Hãy click vào ảnh để chấm màu.")
            return
        self.accept()
