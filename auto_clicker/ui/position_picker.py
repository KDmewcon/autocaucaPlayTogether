"""Dialog cho user click chọn 1 vị trí trên screenshot của window."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.window_manager import WindowManager
from ..utils.qt_utils import ndarray_bgr_to_qpixmap


class _PickCanvas(QWidget):
    posChanged = Signal(QPoint)

    def __init__(self, pixmap):
        super().__init__()
        self._pixmap = pixmap
        self.setFixedSize(pixmap.size())
        self._pos: Optional[QPoint] = None

    def picked(self) -> Optional[QPoint]:
        return self._pos

    def paintEvent(self, e: QPaintEvent) -> None:
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pixmap)
        if self._pos is not None:
            pen = QPen(QColor(0, 220, 255), 2)
            p.setPen(pen)
            r = 14
            p.drawLine(
                self._pos.x() - r, self._pos.y(),
                self._pos.x() + r, self._pos.y(),
            )
            p.drawLine(
                self._pos.x(), self._pos.y() - r,
                self._pos.x(), self._pos.y() + r,
            )
            p.drawEllipse(self._pos, 6, 6)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._pos = e.position().toPoint()
            self.update()
            self.posChanged.emit(self._pos)


class PositionPickerDialog(QDialog):
    """Dialog click 1 điểm trên screenshot. Trả về picked = (x_pt, y_pt, w, h)
    với x_pt/y_pt theo POINT (đơn vị window) và w/h là window size."""

    def __init__(self, window_id: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chọn vị trí - click vào điểm cần click")
        self.resize(900, 700)
        self.picked: Optional[tuple[float, float, float, float]] = None

        self._win = WindowManager.get_window(window_id)
        self._screenshot = WindowManager.capture_window(window_id)

        layout = QVBoxLayout(self)
        info = QLabel(
            "Click 1 điểm trên ảnh để chọn vị trí click. "
            "Nhấn OK để xác nhận."
        )
        layout.addWidget(info)

        if self._screenshot is None or self._win is None:
            layout.addWidget(QLabel("Không capture được window."))
            btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
            btns.rejected.connect(self.reject)
            layout.addWidget(btns)
            return

        pixmap = ndarray_bgr_to_qpixmap(self._screenshot)
        max_w, max_h = 1100, 700
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

        scroll = QScrollArea()
        scroll.setWidget(self._canvas)
        scroll.setWidgetResizable(False)
        layout.addWidget(scroll, 1)

        self._info_lbl = QLabel("Chưa chọn điểm.")
        layout.addWidget(self._info_lbl)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_pos(self, pt: QPoint) -> None:
        if self._screenshot is None or self._win is None:
            return
        # canvas pixel -> screenshot pixel
        sx_px = pt.x() / self._scale
        sy_px = pt.y() / self._scale
        sh_px, sw_px = self._screenshot.shape[:2]
        # screenshot pixel -> window point
        local_x_pt = sx_px / sw_px * self._win.width
        local_y_pt = sy_px / sh_px * self._win.height
        self._info_lbl.setText(
            f"Local: ({local_x_pt:.1f}, {local_y_pt:.1f}) point   "
            f"window {int(self._win.width)}×{int(self._win.height)}"
        )
        self.picked = (
            float(local_x_pt),
            float(local_y_pt),
            float(self._win.width),
            float(self._win.height),
        )

    def _accept(self) -> None:
        if self.picked is None:
            self._info_lbl.setText("Hãy click vào ảnh để chọn điểm.")
            return
        self.accept()
