"""Workspace panel: list các scenario, click để switch, start/stop riêng."""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from ..core.scenario import ScenarioManager, ScenarioConfig


WORKSPACE_DIR = Path.home() / ".autoclicker" / "workspace"


class ScenarioWorkspacePanel(QWidget):
    """Sidebar list các scenario JSON trong workspace folder.

    Signals:
        scenario_selected(path:str) - user click chọn 1 scenario, main load nó
        request_save_current()      - main lưu scenario hiện tại (trước switch)
        request_start(path:str)     - start scenario theo path (cho phép song song)
        request_stop(key:str)       - stop instance theo key
    """

    scenario_selected = Signal(str)
    request_save_current = Signal()
    request_start = Signal(str)  # path
    request_stop = Signal(str)  # key

    def __init__(self, manager: "ScenarioManager", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._manager = manager
        self._current_path: Optional[Path] = None
        self._build_ui()
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self.refresh()

    # ---------- UI ----------
    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)

        head = QLabel("<b>Workspace scenarios</b>")
        lay.addWidget(head)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_double_click)
        self.list_widget.itemActivated.connect(self._on_double_click)
        self.list_widget.itemSelectionChanged.connect(self._on_select)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_ctx_menu)
        font = QFont()
        font.setPointSize(11)
        self.list_widget.setFont(font)
        lay.addWidget(self.list_widget, 1)

        # Action row
        actions = QHBoxLayout()
        self.btn_new = QPushButton("➕ New")
        self.btn_new.clicked.connect(self._on_new)
        actions.addWidget(self.btn_new)

        self.btn_dup = QPushButton("⎘ Dup")
        self.btn_dup.clicked.connect(self._on_dup)
        actions.addWidget(self.btn_dup)

        self.btn_del = QPushButton("✕ Del")
        self.btn_del.setStyleSheet("QPushButton { color: #c62828; }")
        self.btn_del.clicked.connect(self._on_del)
        actions.addWidget(self.btn_del)

        lay.addLayout(actions)

        run_actions = QHBoxLayout()
        self.btn_start_one = QPushButton("▶ Start")
        self.btn_start_one.setStyleSheet(
            "QPushButton { background:#2e7d32; color:white; padding:6px; "
            "font-weight:bold; }"
        )
        self.btn_start_one.clicked.connect(self._on_start_one)
        run_actions.addWidget(self.btn_start_one)

        self.btn_stop_one = QPushButton("⏹ Stop")
        self.btn_stop_one.setStyleSheet(
            "QPushButton { background:#c62828; color:white; padding:6px; "
            "font-weight:bold; }"
        )
        self.btn_stop_one.clicked.connect(self._on_stop_one)
        run_actions.addWidget(self.btn_stop_one)

        lay.addLayout(run_actions)

        # Bulk actions: Start All / Stop All
        bulk_actions = QHBoxLayout()
        self.btn_start_all = QPushButton("▶▶ Start ALL")
        self.btn_start_all.setStyleSheet(
            "QPushButton { background:#1b5e20; color:white; padding:6px; "
            "font-weight:bold; }"
        )
        self.btn_start_all.setToolTip(
            "Start tất cả scenario trong workspace song song"
        )
        self.btn_start_all.clicked.connect(self._on_start_all)
        bulk_actions.addWidget(self.btn_start_all)

        self.btn_stop_all = QPushButton("⏹⏹ Stop ALL")
        self.btn_stop_all.setStyleSheet(
            "QPushButton { background:#8b0000; color:white; padding:6px; "
            "font-weight:bold; }"
        )
        self.btn_stop_all.setToolTip("Stop tất cả scenario đang chạy")
        self.btn_stop_all.clicked.connect(self._on_stop_all)
        bulk_actions.addWidget(self.btn_stop_all)

        lay.addLayout(bulk_actions)

        # Mini hint
        hint = QLabel(
            "<i>Double-click = switch sang scenario. Right-click = đổi tên / xóa. "
            "Start nhiều scenario song song = OK.</i>"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")
        lay.addWidget(hint)

    # ---------- public ----------
    def set_current_path(self, path: Optional[str]) -> None:
        """Đánh dấu scenario nào đang được edit ở main."""
        self._current_path = Path(path) if path else None
        self._highlight_current()

    def refresh(self) -> None:
        """Re-scan folder workspace + sync running state."""
        sel = self._selected_path()
        self.list_widget.blockSignals(True)
        self.list_widget.clear()

        files = sorted(WORKSPACE_DIR.glob("*.json"))
        running_keys = {k for k, _ in self._manager.list_running()}

        for f in files:
            name = f.stem
            n_running = sum(1 for k in running_keys if k == name or k.startswith(name + " #"))
            status_icon = self._status_icon(name, n_running)
            display = f"{status_icon}  {name}"
            if n_running > 1:
                display += f"  × {n_running}"
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, str(f))
            item.setToolTip(str(f))
            self.list_widget.addItem(item)

        self.list_widget.blockSignals(False)

        # Restore selection
        if sel:
            for i in range(self.list_widget.count()):
                if self.list_widget.item(i).data(Qt.ItemDataRole.UserRole) == sel:
                    self.list_widget.setCurrentRow(i)
                    break
        self._highlight_current()

    def _status_icon(self, name: str, n_running: int) -> str:
        if n_running > 0:
            # Check paused
            for k, e in self._manager.list_running():
                if k == name or k.startswith(name + " #"):
                    if e.is_paused():
                        return "⏸"
            return "▶"
        return "·"

    def _highlight_current(self) -> None:
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            path = Path(item.data(Qt.ItemDataRole.UserRole))
            if self._current_path and path == self._current_path:
                f = item.font()
                f.setBold(True)
                item.setFont(f)
                item.setForeground(Qt.GlobalColor.darkCyan)
            else:
                f = item.font()
                f.setBold(False)
                item.setFont(f)
                item.setForeground(Qt.GlobalColor.black)

    # ---------- internals ----------
    def _tick(self) -> None:
        # Chỉ update icon, không re-create items
        running_keys = {k for k, _ in self._manager.list_running()}
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            path = Path(item.data(Qt.ItemDataRole.UserRole))
            name = path.stem
            n_running = sum(1 for k in running_keys if k == name or k.startswith(name + " #"))
            status_icon = self._status_icon(name, n_running)
            display = f"{status_icon}  {name}"
            if n_running > 1:
                display += f"  × {n_running}"
            if item.text() != display:
                item.setText(display)

    def _selected_path(self) -> Optional[str]:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _on_select(self) -> None:
        # Single-click = chỉ highlight, không switch (để tránh accidental save/load)
        pass

    def _on_double_click(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        # Yêu cầu main lưu scenario hiện tại trước khi switch
        self.request_save_current.emit()
        self.scenario_selected.emit(path)

    def _show_ctx_menu(self, pos) -> None:
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        a_open = QAction("Mở (switch)", self)
        a_open.triggered.connect(lambda: self._on_double_click(item))
        menu.addAction(a_open)

        a_rename = QAction("Đổi tên...", self)
        a_rename.triggered.connect(lambda: self._on_rename(item))
        menu.addAction(a_rename)

        a_dup = QAction("Nhân bản", self)
        a_dup.triggered.connect(self._on_dup)
        menu.addAction(a_dup)

        menu.addSeparator()
        a_start = QAction("▶ Start", self)
        a_start.triggered.connect(self._on_start_one)
        menu.addAction(a_start)

        a_stop = QAction("⏹ Stop tất cả instance", self)
        a_stop.triggered.connect(self._on_stop_one)
        menu.addAction(a_stop)

        menu.addSeparator()
        a_del = QAction("Xóa file", self)
        a_del.triggered.connect(self._on_del)
        menu.addAction(a_del)

        menu.exec(self.list_widget.mapToGlobal(pos))

    def _on_new(self) -> None:
        name, ok = QInputDialog.getText(self, "Scenario mới", "Tên:", text="Untitled")
        if not ok or not name.strip():
            return
        name = name.strip()
        path = WORKSPACE_DIR / f"{name}.json"
        if path.exists():
            QMessageBox.warning(self, "Đã tồn tại", f"'{name}' đã có rồi.")
            return
        # Tạo scenario rỗng
        from ..core.scenario import ScenarioConfig
        sc = ScenarioConfig(name=name)
        sc.save(str(path))
        self.refresh()
        # Auto-switch sang scenario mới
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == str(path):
                self.list_widget.setCurrentRow(i)
                self._on_double_click(it)
                break

    def _on_dup(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        src = Path(item.data(Qt.ItemDataRole.UserRole))
        new_name, ok = QInputDialog.getText(
            self, "Nhân bản", "Tên mới:", text=f"{src.stem} copy"
        )
        if not ok or not new_name.strip():
            return
        new_path = WORKSPACE_DIR / f"{new_name.strip()}.json"
        if new_path.exists():
            QMessageBox.warning(self, "Đã tồn tại", f"'{new_name}' đã có rồi.")
            return
        try:
            from ..core.scenario import ScenarioConfig
            sc = ScenarioConfig.load(str(src))
            sc.name = new_name.strip()
            sc.save(str(new_path))
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không nhân bản được:\n{e}")
            return
        self.refresh()

    def _on_del(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        if QMessageBox.question(
            self, "Xóa scenario",
            f"Xóa file '{path.name}'? Không thể undo."
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink()
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không xóa được:\n{e}")
            return
        self.refresh()

    def _on_rename(self, item: QListWidgetItem) -> None:
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        new_name, ok = QInputDialog.getText(
            self, "Đổi tên", "Tên mới:", text=path.stem
        )
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        new_path = path.with_name(f"{new_name}.json")
        if new_path == path:
            return
        if new_path.exists():
            QMessageBox.warning(self, "Đã tồn tại", f"'{new_name}' đã có rồi.")
            return
        try:
            # Update internal name
            from ..core.scenario import ScenarioConfig
            sc = ScenarioConfig.load(str(path))
            sc.name = new_name
            sc.save(str(new_path))
            path.unlink()
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không đổi tên được:\n{e}")
            return
        # Nếu đang edit chính file đó, cập nhật current path
        if self._current_path and self._current_path == path:
            self._current_path = new_path
            # Báo main biết để cập nhật scenario_path + reload
            self.scenario_selected.emit(str(new_path))
        self.refresh()

    def _on_start_one(self) -> None:
        path = self._selected_path()
        if not path:
            return
        self.request_start.emit(path)

    def _on_start_all(self) -> None:
        """Start tất cả scenario trong workspace, mỗi cái 1 instance."""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            path = item.data(Qt.ItemDataRole.UserRole)
            if path:
                self.request_start.emit(path)

    def _on_stop_one(self) -> None:
        path = self._selected_path()
        if not path:
            return
        # Stop all instance khớp tên scenario
        name = Path(path).stem
        keys_to_stop = [
            k for k, _ in self._manager.list_running()
            if k == name or k.startswith(name + " #")
        ]
        for k in keys_to_stop:
            self.request_stop.emit(k)

    def _on_stop_all(self) -> None:
        """Stop tất cả scenario đang chạy."""
        for k, _ in list(self._manager.list_running()):
            self.request_stop.emit(k)
