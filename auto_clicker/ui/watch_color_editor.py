"""Widget phụ cho step WATCH_COLOR: danh sách nhiều màu mục tiêu (mode match).

Mỗi dòng = 1 màu BGR + tolerance riêng. Cho phép thêm màu bằng cách lấy mẫu
từ vùng đang chọn trên window (eyedropper), hoặc nhập tay.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class ColorListWidget(QWidget):
    """Bảng nhiều màu mục tiêu cho mode 'match'.

    Cột: [swatch màu] [B] [G] [R] [Tolerance].
    collect() -> list[{"color":[b,g,r], "tolerance":N}]
    """

    def __init__(
        self,
        get_region_color=None,
        initial: Optional[list[dict]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        # callback trả về (b,g,r) màu trung bình vùng hiện tại, hoặc None
        self._get_region_color = get_region_color

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        head = QHBoxLayout()
        head.addWidget(QLabel("<b>Màu mục tiêu (khớp bất kỳ màu nào)</b>"))
        head.addStretch(1)
        btn_sample = QPushButton("➕ Lấy màu vùng")
        btn_sample.setToolTip(
            "Thêm 1 dòng = màu trung bình của vùng đang theo dõi hiện tại."
        )
        btn_sample.clicked.connect(self._add_from_region)
        head.addWidget(btn_sample)
        btn_image = QPushButton("🖼 Chấm màu từ ảnh")
        btn_image.setToolTip(
            "Import 1 ảnh từ máy rồi click để lấy màu, thêm vào danh sách."
        )
        btn_image.clicked.connect(self._add_from_image)
        head.addWidget(btn_image)
        btn_blank = QPushButton("➕ Dòng trống")
        btn_blank.clicked.connect(lambda: self._add_row())
        head.addWidget(btn_blank)
        btn_del = QPushButton("➖ Xóa")
        btn_del.clicked.connect(self._del_row)
        head.addWidget(btn_del)
        layout.addLayout(head)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Màu", "B", "G", "R", "Tolerance"]
        )
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 64)
        for c in (1, 2, 3, 4):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(190)
        self.table.verticalHeader().setDefaultSectionSize(30)
        layout.addWidget(self.table, 1)

        hint = QLabel(
            "<i>Tolerance = độ rộng chấp nhận quanh màu. Tolerance <b>nhỏ</b> "
            "= bắt <b>chuẩn/khắt khe</b> (chỉ đúng màu đó); tolerance <b>lớn</b> "
            "= bắt <b>dễ/rộng</b> hơn (nhiều sắc thái) nhưng dễ báo nhầm. "
            "Thêm nhiều màu để phủ các sắc thái của dấu !.</i>"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        layout.addWidget(hint)

        for c in initial or []:
            self._add_row(c)

    def _add_from_region(self) -> None:
        col = None
        if self._get_region_color is not None:
            try:
                col = self._get_region_color()
            except Exception:
                col = None
        if not col:
            # vẫn thêm dòng trống để user nhập tay
            self._add_row()
            return
        self._add_row({"color": [col[0], col[1], col[2]], "tolerance": 25})

    def _add_from_image(self) -> None:
        from .image_color_picker import ImageColorPickerDialog

        dlg = ImageColorPickerDialog(parent=self)
        if dlg.exec() != dlg.DialogCode.Accepted or dlg.picked_bgr is None:
            return
        b, g, r = dlg.picked_bgr
        self._add_row({"color": [b, g, r], "tolerance": 25})

    def _add_row(self, data: Optional[dict] = None) -> None:
        data = data or {}
        color = data.get("color") or [0, 0, 0]
        tol = int(data.get("tolerance", 25))
        row = self.table.rowCount()
        self.table.insertRow(row)

        swatch = QTableWidgetItem("")
        b, g, r = int(color[0]), int(color[1]), int(color[2])
        from PySide6.QtGui import QColor

        swatch.setBackground(QColor(r, g, b))
        swatch.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.table.setItem(row, 0, swatch)

        def _make_spin(val, mx=255):
            s = QSpinBox()
            s.setRange(0, mx)
            s.setValue(int(val))
            return s

        b_spin = _make_spin(b)
        g_spin = _make_spin(g)
        r_spin = _make_spin(r)
        tol_spin = _make_spin(tol, mx=441)

        def _refresh_swatch():
            item = self.table.item(row, 0)
            if item:
                item.setBackground(
                    QColor(r_spin.value(), g_spin.value(), b_spin.value())
                )

        for s in (b_spin, g_spin, r_spin):
            s.valueChanged.connect(lambda _=0: _refresh_swatch())

        self.table.setCellWidget(row, 1, b_spin)
        self.table.setCellWidget(row, 2, g_spin)
        self.table.setCellWidget(row, 3, r_spin)
        self.table.setCellWidget(row, 4, tol_spin)

    def _del_row(self) -> None:
        rows = sorted(
            {i.row() for i in self.table.selectedIndexes()}, reverse=True
        )
        if not rows and self.table.rowCount() > 0:
            rows = [self.table.rowCount() - 1]
        for r in rows:
            self.table.removeRow(r)

    def collect(self) -> list[dict]:
        out: list[dict] = []
        for row in range(self.table.rowCount()):
            b = self.table.cellWidget(row, 1).value()
            g = self.table.cellWidget(row, 2).value()
            r = self.table.cellWidget(row, 3).value()
            tol = self.table.cellWidget(row, 4).value()
            out.append(
                {"color": [float(b), float(g), float(r)], "tolerance": int(tol)}
            )
        return out
