"""Editor dạng table cho step WAIT_ANY (multi branch race)."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
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

from ..core.scenario import ScenarioConfig


_BRANCH_TYPES = [
    ("image", "Image found"),
    ("image_gone", "Image gone"),
    ("audio", "Audio pattern"),
    ("sound", "Sound RMS > thr"),
]


class WaitAnyBranchesWidget(QWidget):
    """Widget table cho list branches của WAIT_ANY.

    Có nút Thêm / Xóa, mỗi row có:
    - Type combo
    - Resource (template hoặc audio combo, hoặc trống cho sound)
    - Threshold
    - Goto step
    """

    def __init__(
        self,
        scenario: ScenarioConfig,
        total_steps: int,
        initial_branches: Optional[list[dict]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._scenario = scenario
        self._total_steps = total_steps

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        head = QHBoxLayout()
        head.addWidget(QLabel("<b>Branches (cái nào trigger trước → goto)</b>"))
        head.addStretch(1)
        btn_add = QPushButton("➕ Thêm branch")
        btn_add.clicked.connect(lambda: self._add_row())
        head.addWidget(btn_add)
        btn_del = QPushButton("➖ Xóa")
        btn_del.clicked.connect(self._del_row)
        head.addWidget(btn_del)
        layout.addLayout(head)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Type", "Resource", "Threshold", "Goto step"]
        )
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        for b in initial_branches or []:
            self._add_row(b)

    def _add_row(self, data: Optional[dict] = None) -> None:
        data = data or {}
        row = self.table.rowCount()
        self.table.insertRow(row)

        # Type combo
        type_combo = QComboBox()
        for k, lbl in _BRANCH_TYPES:
            type_combo.addItem(lbl, k)
        idx = type_combo.findData(data.get("type", "image"))
        if idx >= 0:
            type_combo.setCurrentIndex(idx)
        self.table.setCellWidget(row, 0, type_combo)

        # Resource combo (init dựa vào type hiện tại)
        resource = QComboBox()
        self._populate_resource(resource, type_combo.currentData(), data)
        self.table.setCellWidget(row, 1, resource)

        # Threshold spin
        thr = QDoubleSpinBox()
        thr.setRange(0.0, 1.0)
        thr.setDecimals(3)
        thr.setSingleStep(0.01)
        thr.setSpecialValueText("(default)")
        thr.setValue(float(data.get("threshold") or 0.0))
        self.table.setCellWidget(row, 2, thr)

        # Goto step (combo theo step_id để stable khi thêm/xóa)
        goto = QComboBox()
        for i, s in enumerate(self._scenario.steps):
            type_short = s.type.value.replace("_", " ").title()
            label = f"#{i + 1} · {type_short}"
            goto.addItem(label, s.step_id)
        # Match step_id ưu tiên, fallback index
        target_id = data.get("goto_id", "")
        sel = -1
        if target_id:
            for i in range(goto.count()):
                if goto.itemData(i) == target_id:
                    sel = i
                    break
        if sel < 0:
            sel = max(0, min(goto.count() - 1, int(data.get("goto", 0))))
        if goto.count() > 0:
            goto.setCurrentIndex(sel)
        self.table.setCellWidget(row, 3, goto)

        type_combo.currentIndexChanged.connect(
            lambda _, r=row: self._on_type_changed(r)
        )

    def _on_type_changed(self, row: int) -> None:
        type_combo = self.table.cellWidget(row, 0)
        resource = self.table.cellWidget(row, 1)
        if type_combo and resource:
            self._populate_resource(resource, type_combo.currentData(), {})

    def _populate_resource(
        self, combo: QComboBox, type_key: str, data: dict
    ) -> None:
        combo.blockSignals(True)
        combo.clear()
        if type_key in ("image", "image_gone"):
            combo.addItem("(chưa chọn)", "")
            for ref in self._scenario.templates:
                combo.addItem(ref.name, ref.template_id)
            cur = data.get("template_id", "")
            idx = combo.findData(cur or "")
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.setEnabled(True)
        elif type_key == "audio":
            combo.addItem("(chưa chọn)", "")
            for ref in self._scenario.audios:
                combo.addItem(ref.name, ref.audio_id)
            cur = data.get("audio_id", "")
            idx = combo.findData(cur or "")
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.setEnabled(True)
        else:  # sound
            combo.addItem("— (đo RMS) —", "")
            combo.setEnabled(False)
        combo.blockSignals(False)

    def _del_row(self) -> None:
        rows = sorted(
            {i.row() for i in self.table.selectedIndexes()}, reverse=True
        )
        for r in rows:
            self.table.removeRow(r)

    def collect(self) -> list[dict]:
        out: list[dict] = []
        for row in range(self.table.rowCount()):
            type_combo: QComboBox = self.table.cellWidget(row, 0)
            resource: QComboBox = self.table.cellWidget(row, 1)
            thr: QDoubleSpinBox = self.table.cellWidget(row, 2)
            goto: QComboBox = self.table.cellWidget(row, 3)
            type_key = type_combo.currentData()
            goto_id = goto.currentData() if goto.count() > 0 else ""
            entry: dict = {
                "type": type_key,
                "goto_id": goto_id,
                "goto": goto.currentIndex() if goto.count() > 0 else 0,  # legacy
            }
            if type_key in ("image", "image_gone"):
                rid = resource.currentData()
                if not rid:
                    continue
                entry["template_id"] = rid
            elif type_key == "audio":
                rid = resource.currentData()
                if not rid:
                    continue
                entry["audio_id"] = rid
            # threshold sentinel
            if thr.value() > 0.001:
                entry["threshold"] = float(thr.value())
            out.append(entry)
        return out
