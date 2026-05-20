"""Main window cho Auto Clicker - scenario based."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core.click_engine import ClickMode, ClickType
from ..core.image_matcher import ImageMatcher
from ..core.scenario import (
    AudioRef,
    LogEvent,
    ScenarioConfig,
    ScenarioEngine,
    ScenarioManager,
    ScenarioStats,
    Step,
    StepType,
    TemplateRef,
)
from ..core.window_manager import WindowInfo, WindowManager
from ..utils.hotkey import HotkeyManager
from ..utils.permissions import (
    check_accessibility,
    check_screen_recording,
    open_system_settings,
    request_accessibility_prompt,
)
from ..utils.qt_utils import ndarray_bgr_to_qpixmap
from .region_selector import RegionSelectorDialog
from .step_editor import StepEditorDialog

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "assets" / "scenarios"
SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)


class _Bridge(QObject):
    log_signal = Signal(object)
    stats_signal = Signal(object)
    step_signal = Signal(int)
    finish_signal = Signal()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Auto Clicker - Scenario Engine - macOS")
        self.resize(1400, 880)

        self._windows: list[WindowInfo] = []
        self._selected_window: Optional[WindowInfo] = None
        self._scenario = ScenarioConfig(name="New scenario")
        self._scenario_path: Optional[Path] = None

        self._manager = ScenarioManager()
        self._bridge = _Bridge()
        self._bridge.log_signal.connect(self._on_log)
        self._bridge.stats_signal.connect(self._on_stats)
        self._bridge.step_signal.connect(self._on_step)
        self._bridge.finish_signal.connect(self._on_finish)

        self._hotkeys = HotkeyManager()

        self._build_ui()
        self._build_menu()
        self._build_statusbar()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
        self._refresh_timer.timeout.connect(self._refresh_windows)
        self._refresh_timer.start()

        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(500)
        self._preview_timer.timeout.connect(self._update_preview)

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(500)
        self._stats_timer.timeout.connect(self._tick_stats)
        self._stats_timer.start()

        QTimer.singleShot(100, self._initial_check)

    # ============================================================ UI build
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # Left: window list
        left = self._build_left_panel()
        splitter.addWidget(left)

        # Center: preview + templates
        center = self._build_center_panel()
        splitter.addWidget(center)

        # Right: scenario steps + log
        right = self._build_right_panel()
        splitter.addWidget(right)

        splitter.setSizes([260, 600, 540])

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(QLabel("<b>Cửa sổ đang mở</b>"))

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Tìm theo tên app/title...")
        self.search_edit.textChanged.connect(self._render_window_list)
        lay.addWidget(self.search_edit)

        self.window_list = QListWidget()
        self.window_list.itemSelectionChanged.connect(self._on_window_selected)
        lay.addWidget(self.window_list, 1)

        btn = QPushButton("🔄  Refresh")
        btn.clicked.connect(self._refresh_windows)
        lay.addWidget(btn)

        # Scenario defaults
        defaults = QGroupBox("Mặc định scenario")
        f = QFormLayout(defaults)
        self.def_threshold = QDoubleSpinBox()
        self.def_threshold.setRange(0.5, 1.0)
        self.def_threshold.setSingleStep(0.01)
        self.def_threshold.setDecimals(2)
        self.def_threshold.setValue(0.85)
        self.def_threshold.valueChanged.connect(self._sync_defaults)
        f.addRow("Threshold:", self.def_threshold)

        self.def_click_type = QComboBox()
        self.def_click_type.addItems(["left", "right", "middle", "double"])
        self.def_click_type.currentTextChanged.connect(self._sync_defaults)
        f.addRow("Click type:", self.def_click_type)

        self.def_click_mode = QComboBox()
        self.def_click_mode.addItem("HID + Restore cursor", ClickMode.HID_RESTORE)
        self.def_click_mode.addItem("Post tới PID", ClickMode.PID_POSTED)
        self.def_click_mode.addItem("HID Tap", ClickMode.HID_TAP)
        self.def_click_mode.currentIndexChanged.connect(self._sync_defaults)
        f.addRow("Click mode:", self.def_click_mode)

        self.def_jitter = QSpinBox()
        self.def_jitter.setRange(0, 50)
        self.def_jitter.setValue(2)
        self.def_jitter.setSuffix(" px")
        self.def_jitter.valueChanged.connect(self._sync_defaults)
        f.addRow("Click jitter:", self.def_jitter)

        self.def_poll = QDoubleSpinBox()
        self.def_poll.setRange(0.05, 10)
        self.def_poll.setSingleStep(0.05)
        self.def_poll.setValue(0.5)
        self.def_poll.setSuffix(" s")
        self.def_poll.valueChanged.connect(self._sync_defaults)
        f.addRow("Poll interval:", self.def_poll)

        self.def_activate = QCheckBox("Activate window trước click")
        self.def_activate.setChecked(True)
        self.def_activate.toggled.connect(self._sync_defaults)
        f.addRow("", self.def_activate)

        self.def_multiscale = QCheckBox("Multi-scale")
        self.def_multiscale.setChecked(True)
        self.def_multiscale.toggled.connect(self._sync_defaults)
        f.addRow("", self.def_multiscale)

        self.def_grayscale = QCheckBox("Grayscale")
        self.def_grayscale.setChecked(True)
        self.def_grayscale.toggled.connect(self._sync_defaults)
        f.addRow("", self.def_grayscale)

        lay.addWidget(defaults)
        return w

    def _build_center_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        self.preview_lbl = QLabel("Chọn cửa sổ ở bên trái để preview")
        self.preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_lbl.setMinimumSize(QSize(640, 360))
        self.preview_lbl.setFrameShape(QFrame.Shape.Box)
        self.preview_lbl.setStyleSheet(
            "QLabel { background:#1e1e1e; color:#aaa; }"
        )
        lay.addWidget(self.preview_lbl, 3)

        # Templates section
        tmpl_box = QGroupBox("Thư viện template")
        tmpl_lay = QVBoxLayout(tmpl_box)

        row = QHBoxLayout()
        self.btn_add_template = QPushButton("📐  Cắt từ window...")
        self.btn_add_template.clicked.connect(self._add_template_from_window)
        row.addWidget(self.btn_add_template)

        self.btn_load_template = QPushButton("📂  Load file ảnh...")
        self.btn_load_template.clicked.connect(self._add_template_from_file)
        row.addWidget(self.btn_load_template)

        self.btn_test_template = QPushButton("🔍  Test match")
        self.btn_test_template.clicked.connect(self._test_template)
        row.addWidget(self.btn_test_template)

        self.btn_remove_template = QPushButton("🗑  Xóa")
        self.btn_remove_template.clicked.connect(self._remove_template)
        row.addWidget(self.btn_remove_template)
        tmpl_lay.addLayout(row)

        self.template_list = QListWidget()
        self.template_list.setMinimumHeight(150)
        self.template_list.itemSelectionChanged.connect(self._on_template_selected)
        self.template_list.itemDoubleClicked.connect(self._rename_template)
        tmpl_lay.addWidget(self.template_list, 1)

        self.template_preview = QLabel("Chọn template để xem preview")
        self.template_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.template_preview.setFixedHeight(120)
        self.template_preview.setFrameShape(QFrame.Shape.Box)
        self.template_preview.setStyleSheet(
            "QLabel { background:#222; color:#aaa; }"
        )
        tmpl_lay.addWidget(self.template_preview)

        lay.addWidget(tmpl_box, 2)

        # Audio library section
        audio_box = QGroupBox("Thư viện audio (mp3/wav reference)")
        audio_lay = QVBoxLayout(audio_box)
        audio_btn_row = QHBoxLayout()
        self.btn_add_audio = QPushButton("📂  Thêm file audio...")
        self.btn_add_audio.clicked.connect(self._add_audio_from_file)
        audio_btn_row.addWidget(self.btn_add_audio)
        self.btn_test_audio = QPushButton("🎧  Test match")
        self.btn_test_audio.clicked.connect(self._test_audio)
        audio_btn_row.addWidget(self.btn_test_audio)
        self.btn_remove_audio = QPushButton("🗑  Xóa")
        self.btn_remove_audio.clicked.connect(self._remove_audio)
        audio_btn_row.addWidget(self.btn_remove_audio)
        audio_lay.addLayout(audio_btn_row)

        self.audio_list = QListWidget()
        self.audio_list.setMinimumHeight(80)
        self.audio_list.itemDoubleClicked.connect(self._rename_audio)
        audio_lay.addWidget(self.audio_list, 1)

        lay.addWidget(audio_box, 1)
        return w

    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        # Scenario name + file ops
        head = QHBoxLayout()
        head.addWidget(QLabel("<b>Scenario:</b>"))
        self.scenario_name_edit = QLineEdit()
        self.scenario_name_edit.setText(self._scenario.name)
        self.scenario_name_edit.editingFinished.connect(self._on_name_changed)
        head.addWidget(self.scenario_name_edit, 1)

        self.btn_new = QPushButton("New")
        self.btn_new.clicked.connect(self._new_scenario)
        head.addWidget(self.btn_new)
        self.btn_open = QPushButton("Open...")
        self.btn_open.clicked.connect(self._open_scenario)
        head.addWidget(self.btn_open)
        self.btn_save = QPushButton("Save")
        self.btn_save.clicked.connect(self._save_scenario)
        head.addWidget(self.btn_save)
        lay.addLayout(head)

        # Steps
        steps_box = QGroupBox("Các bước (chạy tuần tự)")
        steps_lay = QVBoxLayout(steps_box)

        btn_row = QHBoxLayout()
        self.btn_add_step = QPushButton("➕  Thêm")
        self.btn_add_step.clicked.connect(self._add_step)
        btn_row.addWidget(self.btn_add_step)

        self.btn_edit_step = QPushButton("✏  Sửa")
        self.btn_edit_step.clicked.connect(self._edit_step)
        btn_row.addWidget(self.btn_edit_step)

        self.btn_dup_step = QPushButton("⎘  Nhân bản")
        self.btn_dup_step.clicked.connect(self._duplicate_step)
        btn_row.addWidget(self.btn_dup_step)

        self.btn_up_step = QPushButton("▲")
        self.btn_up_step.clicked.connect(lambda: self._move_step(-1))
        btn_row.addWidget(self.btn_up_step)

        self.btn_down_step = QPushButton("▼")
        self.btn_down_step.clicked.connect(lambda: self._move_step(+1))
        btn_row.addWidget(self.btn_down_step)

        self.btn_del_step = QPushButton("🗑")
        self.btn_del_step.clicked.connect(self._delete_step)
        btn_row.addWidget(self.btn_del_step)
        steps_lay.addLayout(btn_row)

        self.steps_list = QListWidget()
        self.steps_list.itemDoubleClicked.connect(self._edit_step)
        self.steps_list.itemChanged.connect(self._on_step_check_changed)
        steps_lay.addWidget(self.steps_list, 1)

        lay.addWidget(steps_box, 2)

        # Control
        ctrl_box = QGroupBox("Điều khiển")
        ctrl_lay = QHBoxLayout(ctrl_box)
        self.btn_start = QPushButton("▶  Start (Cmd+Shift+S)")
        self.btn_start.setStyleSheet(
            "QPushButton { background:#2e7d32; color:white; "
            "padding:8px 16px; font-weight:bold; }"
        )
        self.btn_start.clicked.connect(self._start)
        ctrl_lay.addWidget(self.btn_start)

        self.chk_loop_forever = QCheckBox("Loop ∞")
        self.chk_loop_forever.setToolTip("Lặp vô hạn: khi chạy hết step cuối sẽ quay lại step đầu")
        self.chk_loop_forever.setChecked(self._scenario.loop_forever)
        self.chk_loop_forever.toggled.connect(
            lambda v: setattr(self._scenario, "loop_forever", v)
        )
        ctrl_lay.addWidget(self.chk_loop_forever)

        self.btn_pause = QPushButton("⏸  Pause (Cmd+Shift+P)")
        self.btn_pause.clicked.connect(self._pause)
        self.btn_pause.setEnabled(False)
        ctrl_lay.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("⏹  Stop")
        self.btn_stop.setStyleSheet(
            "QPushButton { background:#c62828; color:white; "
            "padding:8px 16px; font-weight:bold; }"
        )
        self.btn_stop.clicked.connect(self._stop)
        self.btn_stop.setEnabled(False)
        ctrl_lay.addWidget(self.btn_stop)
        lay.addWidget(ctrl_box)

        # Stats
        stats_box = QGroupBox("Thống kê")
        sf = QFormLayout(stats_box)
        self.stat_status = QLabel("idle")
        self.stat_steps = QLabel("0")
        self.stat_clicks = QLabel("0")
        self.stat_conf = QLabel("0.000")
        self.stat_runtime = QLabel("0s")
        sf.addRow("Trạng thái:", self.stat_status)
        sf.addRow("Steps đã chạy:", self.stat_steps)
        sf.addRow("Clicks:", self.stat_clicks)
        sf.addRow("Confidence cuối:", self.stat_conf)
        sf.addRow("Runtime:", self.stat_runtime)
        lay.addWidget(stats_box)

        # Log
        log_box = QGroupBox("Log")
        log_lay = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setStyleSheet(
            "QPlainTextEdit { font-family: Menlo, monospace; "
            "background:#0e0e0e; color:#ddd; }"
        )
        log_lay.addWidget(self.log_view)
        btn_clear = QPushButton("Clear log")
        btn_clear.clicked.connect(self.log_view.clear)
        log_lay.addWidget(btn_clear)
        lay.addWidget(log_box, 1)

        return w

    def _build_menu(self) -> None:
        bar = self.menuBar()
        m_file = bar.addMenu("File")
        m_file.addAction(self._action("New scenario", self._new_scenario, "Cmd+N"))
        m_file.addAction(self._action("Open scenario...", self._open_scenario, "Cmd+O"))
        m_file.addAction(self._action("Save scenario", self._save_scenario, "Cmd+S"))
        m_file.addAction(
            self._action("Save scenario as...", self._save_scenario_as, "Cmd+Shift+S")
        )
        m_file.addSeparator()
        m_file.addAction(self._action("Quit", self.close, "Cmd+Q"))

        m_perm = bar.addMenu("Permissions")
        m_perm.addAction(self._action("Kiểm tra quyền", self._check_permissions_dialog))
        m_perm.addAction(
            self._action(
                "Mở Accessibility settings",
                lambda: open_system_settings("accessibility"),
            )
        )
        m_perm.addAction(
            self._action(
                "Mở Screen Recording settings",
                lambda: open_system_settings("screen"),
            )
        )

        m_help = bar.addMenu("Help")
        m_help.addAction(self._action("About", self._about))

        m_tools = bar.addMenu("Tools")
        m_tools.addAction(self._action("Setup Audio Capture...", self._open_audio_setup))
        m_tools.addAction(self._action("Audio Monitor...", self._open_audio_monitor))

    def _build_statusbar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.perm_lbl = QLabel()
        sb.addPermanentWidget(self.perm_lbl)
        sb.showMessage(
            "Hotkey: Cmd+Shift+R = Run/Stop scenario, Cmd+Shift+P = Pause/Resume"
        )

    def _action(self, text: str, slot, shortcut: str = "") -> QAction:
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(shortcut)
        a.triggered.connect(slot)
        return a

    # ============================================================ Init
    def _initial_check(self) -> None:
        self._refresh_windows()
        self._check_permissions(silent=True)
        self._setup_hotkeys()
        self._render_steps()
        self._render_template_list()
        self._render_audio_list()

    def _setup_hotkeys(self) -> None:
        self._hotkeys.clear()
        self._hotkeys.set_binding(
            "cmd+shift+r", lambda: QTimer.singleShot(0, self._toggle_run)
        )
        self._hotkeys.set_binding(
            "cmd+shift+p", lambda: QTimer.singleShot(0, self._toggle_pause)
        )
        self._hotkeys.start()

    # ============================================================ Permissions
    def _check_permissions(self, silent: bool = False) -> tuple[bool, bool]:
        sr = check_screen_recording()
        ax = check_accessibility()
        parts = [
            f"<span style='color:{'#4caf50' if sr else '#f44336'}'>●</span> ScreenRec",
            f"<span style='color:{'#4caf50' if ax else '#f44336'}'>●</span> Accessibility",
        ]
        self.perm_lbl.setText("  ".join(parts))
        if not silent and (not sr or not ax):
            self._check_permissions_dialog(sr, ax)
        return sr, ax

    def _check_permissions_dialog(
        self, sr: Optional[bool] = None, ax: Optional[bool] = None
    ) -> None:
        if sr is None:
            sr = check_screen_recording()
        if ax is None:
            ax = check_accessibility()
        msg = (
            f"Screen Recording: {'OK' if sr else 'CHƯA CÓ'}\n"
            f"Accessibility:    {'OK' if ax else 'CHƯA CÓ'}\n\n"
        )
        if sr and ax:
            QMessageBox.information(self, "Permissions", msg + "Đủ quyền.")
            return
        msg += (
            "Cần cấp đủ 2 quyền cho terminal/Python trong System Settings → "
            "Privacy & Security. Sau khi cấp, quit và mở lại tool."
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Cần cấp quyền")
        box.setText(msg)
        b1 = box.addButton("Mở Accessibility", QMessageBox.ButtonRole.ActionRole)
        b2 = box.addButton("Mở Screen Recording", QMessageBox.ButtonRole.ActionRole)
        b3 = box.addButton("Yêu cầu Accessibility", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is b1:
            open_system_settings("accessibility")
        elif box.clickedButton() is b2:
            open_system_settings("screen")
        elif box.clickedButton() is b3:
            request_accessibility_prompt()

    # ============================================================ Window list
    def _refresh_windows(self) -> None:
        try:
            self._windows = WindowManager.list_windows()
        except Exception as e:
            self._append_log("error", f"List windows lỗi: {e}")
            return
        self._render_window_list()

    def _render_window_list(self) -> None:
        keep = self._selected_window.window_id if self._selected_window else None
        filt = self.search_edit.text().strip().lower()
        self.window_list.blockSignals(True)
        self.window_list.clear()
        for w in self._windows:
            label = w.display_name
            if filt and filt not in label.lower():
                continue
            badge = "●" if w.on_screen else "○"
            item = QListWidgetItem(
                f"{badge}  {label}\n     "
                f"{int(w.width)}×{int(w.height)} pid={w.pid}"
            )
            item.setData(Qt.ItemDataRole.UserRole, w.window_id)
            self.window_list.addItem(item)
            if keep is not None and w.window_id == keep:
                item.setSelected(True)
                self.window_list.setCurrentItem(item)
        self.window_list.blockSignals(False)

    def _on_window_selected(self) -> None:
        items = self.window_list.selectedItems()
        if not items:
            self._selected_window = None
            self._preview_timer.stop()
            return
        wid = items[0].data(Qt.ItemDataRole.UserRole)
        win = next((w for w in self._windows if w.window_id == wid), None)
        if win is None:
            return
        self._selected_window = win
        self._scenario.window_id = win.window_id
        self._scenario.pid = win.pid
        self._update_preview()
        if not self._preview_timer.isActive():
            self._preview_timer.start()

    # ============================================================ Preview
    def _update_preview(self) -> None:
        if not self._selected_window:
            return
        win = WindowManager.get_window(self._selected_window.window_id)
        if win is None:
            self.preview_lbl.setText("Cửa sổ đã đóng.")
            self._selected_window = None
            self._preview_timer.stop()
            return
        self._selected_window = win
        img = WindowManager.capture_window(win.window_id)
        if img is None:
            self.preview_lbl.setText("Không capture được window.")
            return
        pix = ndarray_bgr_to_qpixmap(img)
        scaled = pix.scaled(
            self.preview_lbl.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_lbl.setPixmap(scaled)

    # ============================================================ Defaults sync
    def _sync_defaults(self) -> None:
        self._scenario.default_threshold = self.def_threshold.value()
        self._scenario.default_click_type = ClickType(
            self.def_click_type.currentText()
        )
        self._scenario.default_click_mode = self.def_click_mode.currentData()
        self._scenario.default_click_jitter_px = self.def_jitter.value()
        self._scenario.default_poll_interval = self.def_poll.value()
        self._scenario.activate_before_click = self.def_activate.isChecked()
        self._scenario.multi_scale = self.def_multiscale.isChecked()
        self._scenario.grayscale = self.def_grayscale.isChecked()

    def _load_defaults_to_ui(self) -> None:
        self.def_threshold.setValue(self._scenario.default_threshold)
        idx = self.def_click_type.findText(self._scenario.default_click_type.value)
        if idx >= 0:
            self.def_click_type.setCurrentIndex(idx)
        for i in range(self.def_click_mode.count()):
            if self.def_click_mode.itemData(i) == self._scenario.default_click_mode:
                self.def_click_mode.setCurrentIndex(i)
                break
        self.def_jitter.setValue(self._scenario.default_click_jitter_px)
        self.def_poll.setValue(self._scenario.default_poll_interval)
        self.def_activate.setChecked(self._scenario.activate_before_click)
        self.def_multiscale.setChecked(self._scenario.multi_scale)
        self.def_grayscale.setChecked(self._scenario.grayscale)

    # ============================================================ Templates
    def _render_template_list(self) -> None:
        self.template_list.clear()
        for ref in self._scenario.templates:
            item = QListWidgetItem(f"{ref.name}")
            item.setData(Qt.ItemDataRole.UserRole, ref.template_id)
            self.template_list.addItem(item)

    def _selected_template(self) -> Optional[TemplateRef]:
        items = self.template_list.selectedItems()
        if not items:
            return None
        tid = items[0].data(Qt.ItemDataRole.UserRole)
        return self._scenario.get_template(tid)

    def _on_template_selected(self) -> None:
        ref = self._selected_template()
        if ref is None or not ref.path:
            self.template_preview.setText("—")
            return
        img = cv2.imread(ref.path, cv2.IMREAD_COLOR)
        if img is None:
            self.template_preview.setText("(không đọc được)")
            return
        pix = ndarray_bgr_to_qpixmap(img)
        scaled = pix.scaled(
            self.template_preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.template_preview.setPixmap(scaled)

    def _add_template_from_window(self) -> None:
        if not self._selected_window:
            QMessageBox.warning(self, "Chưa chọn", "Hãy chọn 1 window trước.")
            return
        img = WindowManager.capture_window(self._selected_window.window_id)
        if img is None:
            QMessageBox.warning(self, "Lỗi", "Không capture được window.")
            return
        dlg = RegionSelectorDialog(img, self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        crop = dlg.cropped
        if crop is None or crop.size == 0:
            return
        name, ok = QInputDialog.getText(
            self, "Đặt tên template", "Tên:", text=f"tpl_{len(self._scenario.templates) + 1}"
        )
        if not ok or not name.strip():
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = ASSETS_DIR / f"tpl_{ts}_{uuid.uuid4().hex[:6]}.png"
        cv2.imwrite(str(path), crop)
        ref = TemplateRef(
            template_id=uuid.uuid4().hex[:10], name=name.strip(), path=str(path)
        )
        self._scenario.templates.append(ref)
        self._render_template_list()
        self._append_log("info", f"Thêm template '{name}': {path.name}")

    def _add_template_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn ảnh", str(ASSETS_DIR), "Images (*.png *.jpg *.jpeg *.bmp)"
        )
        if not path:
            return
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            QMessageBox.warning(self, "Lỗi", "Không đọc được ảnh.")
            return
        name, ok = QInputDialog.getText(
            self, "Đặt tên template", "Tên:", text=Path(path).stem
        )
        if not ok or not name.strip():
            return
        ref = TemplateRef(
            template_id=uuid.uuid4().hex[:10], name=name.strip(), path=path
        )
        self._scenario.templates.append(ref)
        self._render_template_list()

    def _remove_template(self) -> None:
        ref = self._selected_template()
        if ref is None:
            return
        if (
            QMessageBox.question(
                self,
                "Xóa template",
                f"Xóa '{ref.name}'? Các step đang dùng sẽ chỉ mất ref.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._scenario.templates = [
            t for t in self._scenario.templates if t.template_id != ref.template_id
        ]
        self._render_template_list()

    def _rename_template(self, item: QListWidgetItem) -> None:
        tid = item.data(Qt.ItemDataRole.UserRole)
        ref = self._scenario.get_template(tid)
        if ref is None:
            return
        name, ok = QInputDialog.getText(
            self, "Đổi tên", "Tên:", text=ref.name
        )
        if ok and name.strip():
            ref.name = name.strip()
            self._render_template_list()
            self._render_steps()

    def _test_template(self) -> None:
        ref = self._selected_template()
        if ref is None:
            QMessageBox.information(self, "Test match", "Hãy chọn template.")
            return
        if not self._selected_window:
            QMessageBox.information(self, "Test match", "Hãy chọn window.")
            return
        img = WindowManager.capture_window(self._selected_window.window_id)
        if img is None:
            QMessageBox.warning(self, "Lỗi", "Không capture được window.")
            return
        tmpl = cv2.imread(ref.path, cv2.IMREAD_COLOR)
        if tmpl is None:
            QMessageBox.warning(self, "Lỗi", "Không đọc được template.")
            return
        matcher = ImageMatcher(
            threshold=self._scenario.default_threshold,
            multi_scale=self._scenario.multi_scale,
            grayscale=self._scenario.grayscale,
        )
        res = matcher.find(img, tmpl)
        msg = (
            f"Template: {ref.name}\n"
            f"Confidence: {res.confidence:.4f}\n"
            f"Threshold:  {self._scenario.default_threshold:.2f}\n"
            f"Found:      {res.found}"
        )
        if res.found:
            cx, cy = res.center
            vis = img.copy()
            cv2.rectangle(
                vis,
                (res.x, res.y),
                (res.x + res.width, res.y + res.height),
                (0, 255, 0),
                3,
            )
            cv2.drawMarker(vis, (cx, cy), (0, 0, 255), cv2.MARKER_CROSS, 30, 3)
            pix = ndarray_bgr_to_qpixmap(vis)
            scaled = pix.scaled(
                self.preview_lbl.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_lbl.setPixmap(scaled)
        QMessageBox.information(self, "Test match", msg)

    # ============================================================ Audio library
    def _render_audio_list(self) -> None:
        self.audio_list.clear()
        for ref in self._scenario.audios:
            item = QListWidgetItem(f"🎵  {ref.name}    ({Path(ref.path).name})")
            item.setData(Qt.ItemDataRole.UserRole, ref.audio_id)
            self.audio_list.addItem(item)

    def _selected_audio(self) -> Optional[AudioRef]:
        items = self.audio_list.selectedItems()
        if not items:
            return None
        aid = items[0].data(Qt.ItemDataRole.UserRole)
        return self._scenario.get_audio(aid)

    def _add_audio_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn file audio reference",
            str(ASSETS_DIR),
            "Audio (*.mp3 *.wav *.m4a *.flac *.ogg *.aiff)",
        )
        if not path:
            return

        # Defer việc build pattern qua singleShot để cho NSOpenPanel hoàn tất
        # cleanup trên macOS (tránh segfault `getApplicationProperty` trên macOS 26).
        from PySide6.QtCore import QTimer

        QTimer.singleShot(150, lambda p=path: self._add_audio_from_file_finalize(p))

    def _add_audio_from_file_finalize(self, path: str) -> None:
        try:
            from ..core.audio_matcher import build_pattern

            pat = build_pattern("validate", "validate", path)
            dur = pat.duration_s
        except Exception as e:
            QMessageBox.warning(self, "Lỗi", f"Không load được audio:\n{e}")
            return
        name, ok = QInputDialog.getText(
            self,
            "Đặt tên audio",
            f"Tên (duration={dur:.2f}s):",
            text=Path(path).stem,
        )
        if not ok or not name.strip():
            return
        ref = AudioRef(
            audio_id=uuid.uuid4().hex[:10], name=name.strip(), path=path
        )
        self._scenario.audios.append(ref)
        self._render_audio_list()
        self._render_steps()  # cập nhật label nếu có step ref
        self._append_log(
            "info", f"Thêm audio '{name}' ({dur:.2f}s): {Path(path).name}"
        )

    def _remove_audio(self) -> None:
        ref = self._selected_audio()
        if ref is None:
            return
        if (
            QMessageBox.question(
                self,
                "Xóa audio",
                f"Xóa audio '{ref.name}'?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._scenario.audios = [
            a for a in self._scenario.audios if a.audio_id != ref.audio_id
        ]
        self._render_audio_list()
        self._render_steps()

    def _rename_audio(self, item: QListWidgetItem) -> None:
        aid = item.data(Qt.ItemDataRole.UserRole)
        ref = self._scenario.get_audio(aid)
        if ref is None:
            return
        name, ok = QInputDialog.getText(self, "Đổi tên", "Tên:", text=ref.name)
        if ok and name.strip():
            ref.name = name.strip()
            self._render_audio_list()
            self._render_steps()

    def _test_audio(self) -> None:
        """Mở dialog Audio Test Match, chạy match liên tục để user xem confidence."""
        ref = self._selected_audio()
        if ref is None:
            QMessageBox.information(self, "Test match", "Hãy chọn 1 audio.")
            return
        from .audio_test_dialog import AudioTestDialog

        dlg = AudioTestDialog(
            ref,
            device=self._scenario.audio_device,
            buffer_seconds=self._scenario.audio_buffer_seconds,
            initial_threshold=self._scenario.audio_match_threshold,
            parent=self,
        )
        dlg.exec()

    # ============================================================ Steps
    def _render_steps(self) -> None:
        self.steps_list.blockSignals(True)
        self.steps_list.clear()
        tmap = self._scenario.template_name_map()
        for i, step in enumerate(self._scenario.steps):
            label = f"{i + 1:>3}. {step.label(tmap)}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, step.step_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if step.enabled else Qt.CheckState.Unchecked
            )
            # Color hint cho 1 số loại
            color = {
                StepType.FIND_CLICK: QColor("#4caf50"),
                StepType.WAIT_FOR: QColor("#42a5f5"),
                StepType.WAIT_GONE: QColor("#42a5f5"),
                StepType.SLEEP: QColor("#888"),
                StepType.GOTO: QColor("#ffb74d"),
                StepType.IF_FOUND_GOTO: QColor("#ffb74d"),
                StepType.IF_NOT_FOUND_GOTO: QColor("#ffb74d"),
                StepType.LOOP_START: QColor("#ce93d8"),
                StepType.LOOP_END: QColor("#ce93d8"),
                StepType.STOP: QColor("#ef5350"),
            }.get(step.type)
            if color:
                item.setForeground(color)
            self.steps_list.addItem(item)
        self.steps_list.blockSignals(False)

    def _on_step_check_changed(self, item: QListWidgetItem) -> None:
        sid = item.data(Qt.ItemDataRole.UserRole)
        for s in self._scenario.steps:
            if s.step_id == sid:
                s.enabled = item.checkState() == Qt.CheckState.Checked
                break

    def _selected_step_index(self) -> int:
        rows = [i.row() for i in self.steps_list.selectedIndexes()]
        return rows[0] if rows else -1

    def _add_step(self) -> None:
        new_step = Step(type=StepType.FIND_CLICK, params={})
        dlg = StepEditorDialog(
            new_step, self._scenario, len(self._scenario.steps) + 1, self
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        # Insert sau step đang chọn (nếu có), không thì append cuối
        idx = self._selected_step_index()
        if idx < 0:
            self._scenario.steps.append(dlg.step)
        else:
            self._scenario.steps.insert(idx + 1, dlg.step)
        self._render_steps()

    def _edit_step(self) -> None:
        idx = self._selected_step_index()
        if idx < 0:
            return
        step = self._scenario.steps[idx]
        dlg = StepEditorDialog(step, self._scenario, len(self._scenario.steps), self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        self._scenario.steps[idx] = dlg.step
        self._render_steps()
        self.steps_list.setCurrentRow(idx)

    def _duplicate_step(self) -> None:
        idx = self._selected_step_index()
        if idx < 0:
            return
        s = self._scenario.steps[idx]
        new = Step(
            type=s.type, enabled=s.enabled, params=dict(s.params),
            step_id=uuid.uuid4().hex[:8],
        )
        self._scenario.steps.insert(idx + 1, new)
        self._render_steps()
        self.steps_list.setCurrentRow(idx + 1)

    def _move_step(self, direction: int) -> None:
        idx = self._selected_step_index()
        if idx < 0:
            return
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self._scenario.steps):
            return
        steps = self._scenario.steps
        steps[idx], steps[new_idx] = steps[new_idx], steps[idx]
        self._render_steps()
        self.steps_list.setCurrentRow(new_idx)

    def _delete_step(self) -> None:
        idx = self._selected_step_index()
        if idx < 0:
            return
        if (
            QMessageBox.question(self, "Xóa step", "Xóa step này?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        del self._scenario.steps[idx]
        self._render_steps()

    # ============================================================ Scenario file ops
    def _on_name_changed(self) -> None:
        self._scenario.name = self.scenario_name_edit.text().strip() or "Untitled"

    def _new_scenario(self) -> None:
        if (
            self._scenario.steps
            and QMessageBox.question(
                self,
                "New scenario",
                "Bỏ scenario hiện tại?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        wid = self._scenario.window_id
        pid = self._scenario.pid
        self._scenario = ScenarioConfig(name="New scenario")
        self._scenario.window_id = wid
        self._scenario.pid = pid
        self._scenario_path = None
        self.scenario_name_edit.setText(self._scenario.name)
        self._load_defaults_to_ui()
        self._render_template_list()
        self._render_audio_list()
        self._render_steps()

    def _open_scenario(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open scenario", str(SCENARIOS_DIR), "JSON (*.json)"
        )
        if not path:
            return
        try:
            sc = ScenarioConfig.load(path)
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không load được:\n{e}")
            return
        sc.window_id = self._scenario.window_id
        sc.pid = self._scenario.pid
        self._scenario = sc
        self._scenario_path = Path(path)
        self.scenario_name_edit.setText(sc.name)
        self._load_defaults_to_ui()
        self._render_template_list()
        self._render_audio_list()
        self._render_steps()
        self._append_log("info", f"Đã load: {path}")

    def _save_scenario(self) -> None:
        if not self._scenario_path:
            self._save_scenario_as()
            return
        try:
            self._scenario.save(str(self._scenario_path))
            self._append_log("info", f"Saved: {self._scenario_path}")
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Save lỗi:\n{e}")

    def _save_scenario_as(self) -> None:
        default = SCENARIOS_DIR / f"{self._scenario.name}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save scenario", str(default), "JSON (*.json)"
        )
        if not path:
            return
        try:
            self._scenario.save(path)
            self._scenario_path = Path(path)
            self._append_log("info", f"Saved: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Save lỗi:\n{e}")

    # ============================================================ Run control
    def _start(self) -> None:
        if not self._selected_window:
            QMessageBox.warning(self, "Chưa chọn", "Hãy chọn window target.")
            return
        if not self._scenario.steps:
            QMessageBox.warning(self, "Trống", "Scenario chưa có step nào.")
            return
        sr, ax = self._check_permissions(silent=True)
        if not (sr and ax):
            self._check_permissions_dialog(sr, ax)
            return

        self._scenario.window_id = self._selected_window.window_id
        self._scenario.pid = self._selected_window.pid

        self._manager.start(
            self._scenario,
            on_log=lambda ev: self._bridge.log_signal.emit(ev),
            on_stats=lambda st: self._bridge.stats_signal.emit(st),
            on_step=lambda i: self._bridge.step_signal.emit(i),
            on_finish=lambda: self._bridge.finish_signal.emit(),
        )
        self.btn_start.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self.stat_status.setText("running")
        self._append_log("info", f"Bắt đầu '{self._scenario.name}'")

    def _pause(self) -> None:
        eng = self._manager.current()
        if not eng or not eng.is_alive():
            return
        eng.toggle_pause()
        if eng.is_paused():
            self.btn_pause.setText("▶  Resume (Cmd+Shift+P)")
            self.stat_status.setText("paused")
        else:
            self.btn_pause.setText("⏸  Pause (Cmd+Shift+P)")
            self.stat_status.setText("running")

    def _stop(self) -> None:
        self._manager.stop()

    def _toggle_run(self) -> None:
        eng = self._manager.current()
        if eng and eng.is_alive():
            self._stop()
        else:
            self._start()

    def _toggle_pause(self) -> None:
        eng = self._manager.current()
        if eng and eng.is_alive():
            self._pause()

    def _on_finish(self) -> None:
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("⏸  Pause (Cmd+Shift+P)")
        self.btn_stop.setEnabled(False)
        self.stat_status.setText("stopped")
        # Bỏ highlight step
        for i in range(self.steps_list.count()):
            item = self.steps_list.item(i)
            font = item.font()
            font.setBold(False)
            item.setFont(font)

    # ============================================================ Bridge slots
    def _on_log(self, ev: LogEvent) -> None:
        self._append_log(ev.level, ev.message, ts=ev.timestamp)

    def _append_log(self, level: str, msg: str, ts: float | None = None) -> None:
        when = datetime.fromtimestamp(ts or time.time()).strftime("%H:%M:%S")
        color = {
            "info": "#9ad",
            "warn": "#fa3",
            "error": "#f55",
            "click": "#5d5",
            "step": "#bbb",
        }.get(level, "#ddd")
        # Escape minimal
        msg_esc = (
            msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        self.log_view.appendHtml(
            f"<span style='color:#666'>[{when}]</span> "
            f"<span style='color:{color}'>{level.upper():5s}</span> "
            f"<span style='color:#ddd'>{msg_esc}</span>"
        )

    def _on_stats(self, stats: ScenarioStats) -> None:
        self.stat_steps.setText(str(stats.steps_executed))
        self.stat_clicks.setText(str(stats.clicks))
        self.stat_conf.setText(f"{stats.last_confidence:.3f}")

    def _on_step(self, idx: int) -> None:
        # Bold step đang chạy
        for i in range(self.steps_list.count()):
            item = self.steps_list.item(i)
            font = item.font()
            font.setBold(i == idx)
            item.setFont(font)

    def _tick_stats(self) -> None:
        eng = self._manager.current()
        if eng and eng.stats.started_at > 0:
            elapsed = time.time() - eng.stats.started_at
            self.stat_runtime.setText(self._fmt_dur(elapsed))

    @staticmethod
    def _fmt_dur(s: float) -> str:
        s = int(s)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        if m < 60:
            return f"{m}m {s}s"
        h, m = divmod(m, 60)
        return f"{h}h {m}m {s}s"

    # ============================================================ Misc
    def _about(self) -> None:
        QMessageBox.information(
            self,
            "About",
            "Auto Clicker - Scenario Engine\n"
            "macOS · PySide6 · OpenCV · Quartz\n\n"
            "Hotkeys:\n"
            "  Cmd+Shift+R  Run/Stop scenario\n"
            "  Cmd+Shift+P  Pause/Resume\n",
        )

    def _open_audio_monitor(self) -> None:
        from .audio_monitor_dialog import AudioMonitorDialog

        dlg = AudioMonitorDialog(
            self,
            initial_device=self._scenario.audio_device,
            initial_threshold=self._scenario.audio_threshold,
        )
        if dlg.exec() == dlg.DialogCode.Accepted:
            self._scenario.audio_device = dlg.selected_device
            self._scenario.audio_threshold = dlg.selected_threshold
            self._append_log(
                "info",
                f"Audio settings: device={dlg.selected_device} "
                f"threshold={dlg.selected_threshold:.4f}",
            )

    def _open_audio_setup(self) -> None:
        from .audio_setup_dialog import AudioSetupDialog

        dlg = AudioSetupDialog(self)
        dlg.exec()

    def closeEvent(self, event) -> None:
        self._manager.stop()
        self._hotkeys.stop()
        super().closeEvent(event)
