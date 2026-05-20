"""Dialog quản lý các scenario instance đang chạy song song."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from ..core.scenario import ScenarioManager


def _fmt_runtime(secs: float) -> str:
    if secs < 60:
        return f"{secs:.1f}s"
    m, s = divmod(int(secs), 60)
    if m < 60:
        return f"{m:02d}:{s:02d}"
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class ScenarioManagerDialog(QDialog):
    """Bảng các instance scenario đang chạy. Tự refresh."""

    COLUMNS = ["Tên (key)", "Status", "Step", "Steps run", "Clicks", "Runtime", ""]

    def __init__(self, manager: "ScenarioManager", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Quản lý scenarios chạy song song")
        self.resize(820, 480)
        self._manager = manager
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        header_row = QHBoxLayout()
        self.header_lbl = QLabel("<b>Đang chạy: 0 scenario</b>")
        header_row.addWidget(self.header_lbl)
        header_row.addStretch(1)

        btn_refresh = QPushButton("↻ Refresh")
        btn_refresh.clicked.connect(self._refresh)
        header_row.addWidget(btn_refresh)

        btn_stop_all = QPushButton("⏹ Stop ALL")
        btn_stop_all.setStyleSheet(
            "QPushButton { background:#c62828; color:white; "
            "padding:6px 12px; font-weight:bold; }"
        )
        btn_stop_all.clicked.connect(self._stop_all)
        header_row.addWidget(btn_stop_all)
        layout.addLayout(header_row)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in (1, 2, 3, 4, 5):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        # Footer hint
        hint = QLabel(
            "<i>Mỗi scenario chạy ở thread riêng. Pause/Stop áp dụng theo "
            "từng instance. Đóng dialog không stop scenario.</i>"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")
        layout.addWidget(hint)

    def _refresh(self) -> None:
        running = self._manager.list_running()
        self.header_lbl.setText(f"<b>Đang chạy: {len(running)} scenario</b>")

        # Lưu position hiện tại
        sel_row = self.table.currentRow()

        self.table.setRowCount(len(running))
        for row, (key, eng) in enumerate(running):
            cfg_name = eng.config.name or "(unnamed)"
            display = f"{cfg_name}  ·  [{key}]" if key != cfg_name else cfg_name

            self.table.setItem(row, 0, QTableWidgetItem(display))

            status = "paused" if eng.is_paused() else "running"
            status_item = QTableWidgetItem(status)
            if status == "paused":
                status_item.setForeground(Qt.GlobalColor.darkYellow)
            else:
                status_item.setForeground(Qt.GlobalColor.darkGreen)
            self.table.setItem(row, 1, status_item)

            stats = eng.stats
            step_idx = stats.last_step_idx
            step_str = f"#{step_idx + 1}" if step_idx >= 0 else "—"
            self.table.setItem(row, 2, QTableWidgetItem(step_str))
            self.table.setItem(row, 3, QTableWidgetItem(str(stats.steps_executed)))
            self.table.setItem(row, 4, QTableWidgetItem(str(stats.clicks)))

            rt = (time.time() - stats.started_at) if stats.started_at > 0 else 0.0
            self.table.setItem(row, 5, QTableWidgetItem(_fmt_runtime(rt)))

            # Action buttons (pause + stop)
            actions = QWidget()
            ah = QHBoxLayout(actions)
            ah.setContentsMargins(2, 2, 2, 2)
            ah.setSpacing(4)
            btn_pause = QPushButton("▶" if eng.is_paused() else "⏸")
            btn_pause.setFixedWidth(34)
            btn_pause.setToolTip("Pause / Resume")
            btn_pause.clicked.connect(lambda _=False, e=eng: self._toggle_pause(e))
            ah.addWidget(btn_pause)
            btn_stop = QPushButton("⏹")
            btn_stop.setFixedWidth(34)
            btn_stop.setStyleSheet("QPushButton { color: #c62828; font-weight: bold; }")
            btn_stop.setToolTip("Stop instance này")
            btn_stop.clicked.connect(lambda _=False, k=key: self._stop_one(k))
            ah.addWidget(btn_stop)
            self.table.setCellWidget(row, 6, actions)

        if 0 <= sel_row < self.table.rowCount():
            self.table.setCurrentCell(sel_row, 0)

    def _toggle_pause(self, engine) -> None:
        try:
            engine.toggle_pause()
        except Exception:
            pass
        self._refresh()

    def _stop_one(self, key: str) -> None:
        self._manager.stop(key=key)
        self._refresh()

    def _stop_all(self) -> None:
        self._manager.stop_all()
        self._refresh()

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)
