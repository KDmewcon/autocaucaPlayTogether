"""Dialog cho phép user crop vùng template từ screenshot của window."""
from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..utils.qt_utils import ndarray_bgr_to_qpixmap


class _CropCanvas(QWidget):
    """Hiển thị pixmap + cho user kéo chọn rectangle."""

    selectionChanged = Signal(QRect)

    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self._pixmap = pixmap
        self.setFixedSize(pixmap.size())
        self._dragging = False
        self._start: QPoint = QPoint()
        self._end: QPoint = QPoint()

    def selection_rect(self) -> QRect:
        r = QRect(self._start, self._end).normalized()
        # Clamp vào pixmap bounds
        r = r.intersected(self._pixmap.rect())
        return r

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pixmap)
        if self._dragging or not self._start.isNull():
            r = self.selection_rect()
            if r.width() > 0 and r.height() > 0:
                # Dim bên ngoài selection
                overlay = QColor(0, 0, 0, 100)
                p.fillRect(QRect(0, 0, self.width(), r.top()), overlay)
                p.fillRect(
                    QRect(0, r.bottom(), self.width(), self.height()),
                    overlay,
                )
                p.fillRect(QRect(0, r.top(), r.left(), r.height()), overlay)
                p.fillRect(
                    QRect(
                        r.right(), r.top(), self.width() - r.right(),
                        r.height(),
                    ),
                    overlay,
                )
                pen = QPen(QColor(0, 200, 255), 2)
                p.setPen(pen)
                p.drawRect(r)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._start = e.position().toPoint()
            self._end = self._start
            self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._dragging:
            self._end = e.position().toPoint()
            self.update()
            self.selectionChanged.emit(self.selection_rect())

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._end = e.position().toPoint()
            self.update()
            self.selectionChanged.emit(self.selection_rect())


class RegionSelectorDialog(QDialog):
    """Dialog chọn vùng template từ ảnh BGR."""

    def __init__(self, screenshot: np.ndarray, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Chọn vùng làm template - kéo chuột để chọn")
        self.resize(900, 700)
        self._screenshot = screenshot
        self._cropped: Optional[np.ndarray] = None
        self._scale = 1.0

        layout = QVBoxLayout(self)

        info = QLabel(
            "Kéo chuột trái để chọn vùng. Vùng được chọn sẽ là template "
            "image dùng để tìm kiếm."
        )
        layout.addWidget(info)

        # Scale ảnh để fit vào màn hình
        pixmap = ndarray_bgr_to_qpixmap(screenshot)
        max_w, max_h = 1100, 750
        if pixmap.width() > max_w or pixmap.height() > max_h:
            scaled = pixmap.scaled(
                max_w,
                max_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._scale = scaled.width() / pixmap.width()
            pixmap = scaled

        self._canvas = _CropCanvas(pixmap)
        self._canvas.selectionChanged.connect(self._on_sel)

        scroll = QScrollArea()
        scroll.setWidget(self._canvas)
        scroll.setWidgetResizable(False)
        layout.addWidget(scroll, 1)

        self._info_lbl = QLabel("Chưa chọn vùng nào.")
        layout.addWidget(self._info_lbl)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_sel(self, rect: QRect) -> None:
        if rect.width() <= 0 or rect.height() <= 0:
            self._info_lbl.setText("Chưa chọn vùng nào.")
            return
        # Convert về tọa độ ảnh gốc
        x = int(rect.x() / self._scale)
        y = int(rect.y() / self._scale)
        w = int(rect.width() / self._scale)
        h = int(rect.height() / self._scale)
        self._info_lbl.setText(
            f"Vùng chọn: ({x}, {y})  size {w}x{h}"
        )

    def _accept(self) -> None:
        rect = self._canvas.selection_rect()
        if rect.width() < 5 or rect.height() < 5:
            self._info_lbl.setText(
                "Vùng quá nhỏ, vui lòng chọn vùng lớn hơn."
            )
            return
        x = int(rect.x() / self._scale)
        y = int(rect.y() / self._scale)
        w = int(rect.width() / self._scale)
        h = int(rect.height() / self._scale)
        h_max, w_max = self._screenshot.shape[:2]
        x = max(0, min(x, w_max - 1))
        y = max(0, min(y, h_max - 1))
        w = max(1, min(w, w_max - x))
        h = max(1, min(h, h_max - y))
        self._cropped = self._screenshot[y : y + h, x : x + w].copy()
        self.accept()

    @property
    def cropped(self) -> Optional[np.ndarray]:
        return self._cropped
