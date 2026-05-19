"""Main window cho Auto Clicker - scenario based."""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.click_engine import ClickEngine, ClickMode, ClickType
from ..core.image_matcher import ImageMatcher
from ..core.scenario import (
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
SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"
SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)


class _Bridge(QObject):
    log_signal = Signal(object)
    stats_signal = Signal(object)
    step_signal = Signal(int)
    finish_signal = Signal()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Auto Clicker - Scenario - macOS")
        self.resize(1480, 900)

        self._windows: list[WindowInfo] = []
        self._selected_window: Optional[WindowInfo] = None
        self._last_screenshot: Optional[np.ndarray] = None

        self._scenario = ScenarioConfig()
        self._scenario_path: Optional[str] = None

        self._manager = ScenarioManager()
        self._bridge = _Bridge()
        self._bridge.log_signal.connect(self._on_log)
        self._bridge.stats_signal.connect(self._on_stats)
        self._bridge.step_signal.connect(self._on_step_active)
        self._bridge.finish_signal.connect(self._on_finished)

        self._hotkeys = HotkeyManager()

        self._build_ui()
        self._build_menu()
        self._build_statusbar()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
        self._refresh_timer.timeout.connect(self._refresh_windows)
        self._refresh_timer.start()

        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(700)
        self._preview_timer.timeout.connect(self._update_preview)

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(500)
        self._stats_timer.timeout.connect(self._tick_runtime)
        self._stats_timer.start()

        QTimer.singleShot(100, self._initial_check)

    # ============================================================== UI
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_center_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([280, 540, 660])

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

        # Templates
        lay.addWidget(QLabel("<b>Templates</b>"))
        self.template_list = QListWidget()
        self.template_list.setIconSize(QSize(80, 60))
        self.template_list.itemSelectionChanged.connect(self._on_template_selected)
        lay.addWidget(self.template_list, 1)

        tpl_btns = QHBoxLayout()
        b_add = QPushButton("➕ Cắt từ window")
        b_add.clicked.connect(self._add_template_from_window)
        tpl_btns.addWidget(b_add)

        b_load = QPushButton("📂 Load file")
        b_load.clicked.connect(self._add_template_from_file)
        tpl_btns.addWidget(b_load)
        lay.addLayout(tpl_btns)

        tpl_btns2 = QHBoxLayout()
        b_ren = QPushButton("✏️ Rename")
        b_ren.clicked.connect(self._rename_template)
        tpl_btns2.addWidget(b_ren)

        b_del = QPushButton("🗑️ Xóa")
        b_del.clicked.connect(self._delete_template)
        tpl_btns2.addWidget(b_del)

        b_test = QPushButton("🔍 Test")
        b_test.clicked.connect(self._test_template)
        tpl_btns2.addWidget(b_test)
        lay.addLayout(tpl_btns2)

        return w

    def _build_center_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        lay.addWidget(QLabel("<b>Preview window</b>"))
        self.preview_lbl = QLabel("Chọn cửa sổ bên trái")
        self.preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_lbl.setMinimumSize(QSize(540, 360))
        self.preview_lbl.setFrameShape(QFrame.Shape.Box)
        self.preview_lbl.setStyleSheet(
            "QLabel { background:#1e1e1e; color:#aaa; }"
        )
        lay.addWidget(self.preview_lbl, 3)

        # Default settings
        cfg_box = QGroupBox("Cấu hình mặc định scenario")
        form = QFormLayout(cfg_box)

        self.scn_name_edit = QLineEdit(self._scenario.name)
        self.scn_name_edit.textChanged.connect(self._on_scn_changed)
        form.addRow("Tên scenario:", self.scn_name_edit)

        self.def_thr_spin = QDoubleSpinBox()
        self.def_thr_spin.setRange(0.5, 1.0)
        self.def_thr_spin.setDecimals(2)
        self.def_thr_spin.setSingleStep(0.01)
        self.def_thr_spin.setValue(self._scenario.default_threshold)
        self.def_thr_spin.valueChanged.connect(self._on_scn_changed)
        form.addRow("Default threshold:", self.def_thr_spin)

        self.def_click_combo = QComboBox()
        for ct in ClickType:
            self.def_click_combo.addItem(ct.value, ct)
        self.def_click_combo.currentIndexChanged.connect(self._on_scn_changed)
        form.addRow("Default click type:", self.def_click_combo)

        self.def_mode_combo = QComboBox()
        self.def_mode_combo.addItem(
            "HID + Restore cursor (khuyến nghị)", ClickMode.HID_RESTORE
        )
        self.def_mode_combo.addItem("Post tới PID", ClickMode.PID_POSTED)
        self.def_mode_combo.addItem("HID Tap", ClickMode.HID_TAP)
        self.def_mode_combo.currentIndexChanged.connect(self._on_scn_changed)
        form.addRow("Default click mode:", self.def_mode_combo)

        self.def_jitter_spin = QSpinBox()
        self.def_jitter_spin.setRange(0, 50)
        self.def_jitter_spin.setSuffix(" px")
        self.def_jitter_spin.setValue(self._scenario.default_click_jitter_px)
        self.def_jitter_spin.valueChanged.connect(self._on_scn_changed)
        form.addRow("Click jitter:", self.def_jitter_spin)

        self.def_poll_spin = QDoubleSpinBox()
        self.def_poll_spin.setRange(0.05, 10)
        self.def_poll_spin.setSingleStep(0.05)
        self.def_poll_spin.setSuffix(" s")
        self.def_poll_spin.setValue(self._scenario.default_poll_interval)
        self.def_poll_spin.valueChanged.connect(self._on_scn_changed)
        form.addRow("Default poll interval:", self.def_poll_spin)

        self.activate_chk = QCheckBox(
            "Activate window trước khi click (giúp app nhận event)"
        )
        self.activate_chk.setChecked(self._scenario.activate_before_click)
        self.activate_chk.toggled.connect(self._on_scn_changed)
        form.addRow("", self.activate_chk)

        self.multiscale_chk = QCheckBox("Multi-scale matching")
        self.multiscale_chk.setChecked(self._scenario.multi_scale)
        self.multiscale_chk.toggled.connect(self._on_scn_changed)
        form.addRow("", self.multiscale_chk)

        self.grayscale_chk = QCheckBox("Grayscale matching")
        self.grayscale_chk.setChecked(self._scenario.grayscale)
        self.grayscale_chk.toggled.connect(self._on_scn_changed)
        form.addRow("", self.grayscale_chk)

        lay.addWidget(cfg_box)
        return w

    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        # Scenario toolbar
        scn_bar = QHBoxLayout()
        scn_bar.addWidget(QLabel("<b>Scenario steps</b>"))
        scn_bar.addStretch(1)

        b_new = QToolButton()
        b_new.setText("📄 New")
        b_new.clicked.connect(self._new_scenario)
        scn_bar.addWidget(b_new)

        b_open = QToolButton()
        b_open.setText("📂 Open")
        b_open.clicked.connect(self._open_scenario)
        scn_bar.addWidget(b_open)

        b_save = QToolButton()
        b_save.setText("💾 Save")
        b_save.clicked.connect(self._save_scenario)
        scn_bar.addWidget(b_save)

        b_save_as = QToolButton()
        b_save_as.setText("💾 Save as")
        b_save_as.clicked.connect(self._save_scenario_as)
        scn_bar.addWidget(b_save_as)
        lay.addLayout(scn_bar)

        # Step list
        self.step_list = QListWidget()
        self.step_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.step_list.itemDoubleClicked.connect(lambda *_: self._edit_step())
        lay.addWidget(self.step_list, 1)

        # Step buttons
        step_btns = QHBoxLayout()
        b_add_step = QPushButton("➕ Add step")
        b_add_step.clicked.connect(self._add_step)
        step_btns.addWidget(b_add_step)

        b_edit = QPushButton("✏️ Edit")
        b_edit.clicked.connect(self._edit_step)
        step_btns.addWidget(b_edit)

        b_dup = QPushButton("📋 Duplicate")
        b_dup.clicked.connect(self._duplicate_step)
        step_btns.addWidget(b_dup)

        b_del_step = QPushButton("🗑️ Delete")
        b_del_step.clicked.connect(self._delete_step)
        step_btns.addWidget(b_del_step)
        lay.addLayout(step_btns)

        step_btns2 = QHBoxLayout()
        b_up = QPushButton("⬆ Up")
        b_up.clicked.connect(lambda: self._move_step(-1))
        step_btns2.addWidget(b_up)

        b_down = QPushButton("⬇ Down")
        b_down.clicked.connect(lambda: self._move_step(1))
        step_btns2.addWidget(b_down)

        b_toggle = QPushButton("👁 Toggle enable")
        b_toggle.clicked.connect(self._toggle_step_enabled)
        step_btns2.addWidget(b_toggle)

        step_btns2.addStretch(1)
        lay.addLayout(step_btns2)

        # Control + stats + log in tabs
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        # ----- Run tab
        run_w = QWidget()
        run_lay = QVBoxLayout(run_w)

        ctrl = QHBoxLayout()
        self.btn_start = QPushButton("▶  Start")
        self.btn_start.setStyleSheet(
            "QPushButton { background:#2e7d32; color:white; "
            "padding:8px 16px; font-weight:bold; }"
        )
        self.btn_start.clicked.connect(self._start)
        ctrl.addWidget(self.btn_start)

        self.btn_pause = QPushButton("⏸  Pause")
        self.btn_pause.clicked.connect(self._pause)
        self.btn_pause.setEnabled(False)
        ctrl.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("⏹  Stop")
        self.btn_stop.setStyleSheet(
            "QPushButton { background:#c62828; color:white; "
            "padding:8px 16px; font-weight:bold; }"
        )
        self.btn_stop.clicked.connect(self._stop)
        self.btn_stop.setEnabled(False)
        ctrl.addWidget(self.btn_stop)
        run_lay.addLayout(ctrl)

        # Stats
        st_box = QGroupBox("Thống kê")
        st_lay = QFormLayout(st_box)
        self.stat_status = QLabel("idle")
        self.stat_step = QLabel("-")
        self.stat_clicks = QLabel("0")
        self.stat_steps_exec = QLabel("0")
        self.stat_conf = QLabel("0.000")
        self.stat_runtime = QLabel("0s")
        st_lay.addRow("Trạng thái:", self.stat_status)
        st_lay.addRow("Step hiện tại:", self.stat_step)
        st_lay.addRow("Clicks:", self.stat_clicks)
        st_lay.addRow("Steps đã chạy:", self.stat_steps_exec)
        st_lay.addRow("Confidence cuối:", self.stat_conf)
        st_lay.addRow("Runtime:", self.stat_runtime)
        run_lay.addWidget(st_box)
        run_lay.addStretch(1)

        tabs.addTab(run_w, "Run")

        # ----- Log tab
        log_w = QWidget()
        log_lay = QVBoxLayout(log_w)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setStyleSheet(
            "QPlainTextEdit { font-family: Menlo, monospace; "
            "background:#0e0e0e; color:#ddd; }"
        )
        log_lay.addWidget(self.log_view)
        b_clear = QPushButton("Clear log")
        b_clear.clicked.connect(self.log_view.clear)
        log_lay.addWidget(b_clear)
        tabs.addTab(log_w, "Log")

        return w

    def _build_menu(self) -> None:
        bar = self.menuBar()
        m_file = bar.addMenu("File")
        a_new = QAction("New scenario", self)
        a_new.setShortcut("Cmd+N")
        a_new.triggered.connect(self._new_scenario)
        m_file.addAction(a_new)
        a_open = QAction("Open scenario...", self)
        a_open.setShortcut("Cmd+O")
        a_open.triggered.connect(self._open_scenario)
        m_file.addAction(a_open)
        a_save = QAction("Save", self)
        a_save.setShortcut("Cmd+S")
        a_save.triggered.connect(self._save_scenario)
        m_file.addAction(a_save)
        a_save_as = QAction("Save As...", self)
        a_save_as.setShortcut("Cmd+Shift+S")
        a_save_as.triggered.connect(self._save_scenario_as)
        m_file.addAction(a_save_as)
        m_file.addSeparator()
        a_quit = QAction("Quit", self)
        a_quit.setShortcut("Cmd+Q")
        a_quit.triggered.connect(self.close)
        m_file.addAction(a_quit)

        m_perm = bar.addMenu("Permissions")
        a_chk = QAction("Kiểm tra quyền", self)
        a_chk.triggered.connect(self._check_permissions_dialog)
        m_perm.addAction(a_chk)
        a_oa = QAction("Mở System Settings - Accessibility", self)
        a_oa.triggered.connect(lambda: open_system_settings("accessibility"))
        m_perm.addAction(a_oa)
        a_os = QAction("Mở System Settings - Screen Recording", self)
        a_os.triggered.connect(lambda: open_system_settings("screen"))
        m_perm.addAction(a_os)

        m_help = bar.addMenu("Help")
        a_about = QAction("About", self)
        a_about.triggered.connect(self._about)
        m_help.addAction(a_about)

    def _build_statusbar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.perm_lbl = QLabel()
        sb.addPermanentWidget(self.perm_lbl)
        sb.showMessage(
            "Hotkey: F8 = Start/Stop, F9 = Pause/Resume"
        )

    # ============================================================== Init
    def _initial_check(self) -> None:
        self._refresh_windows()
        self._check_permissions(silent=False)
        self._setup_hotkeys()
        self._render_steps()
        self._render_templates()

    def _setup_hotkeys(self) -> None:
        self._hotkeys.clear()
        # F8/F9 không cần modifier, dễ bấm
        self._hotkeys.set_binding(
            "f8", lambda: QTimer.singleShot(0, self._toggle_start_stop)
        )
        self._hotkeys.set_binding(
            "f9", lambda: QTimer.singleShot(0, self._toggle_pause)
        )
        self._hotkeys.start()

    # ============================================================== Permissions
    def _check_permissions(self, silent: bool = False) -> tuple[bool, bool]:
        sr = check_screen_recording()
        ax = check_accessibility()
        parts = [
            f"<span style='color:{'#4caf50' if sr else '#f44336'}'>●</span> "
            f"Screen Recording",
            f"<span style='color:{'#4caf50' if ax else '#f44336'}'>●</span> "
            f"Accessibility",
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
            f"Trạng thái quyền:\n"
            f"  • Screen Recording: {'OK' if sr else 'CHƯA CÓ'}\n"
            f"  • Accessibility:    {'OK' if ax else 'CHƯA CÓ'}\n\n"
        )
        if sr and ax:
            QMessageBox.information(self, "Permissions", msg + "Đủ quyền.")
            return
        msg += (
            "Cần cấp quyền cho Terminal/Python trong System Settings → "
            "Privacy & Security. Sau khi tick xong, **quit và mở lại** tool."
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Cần cấp quyền")
        box.setText(msg)
        b_a = box.addButton("Mở Accessibility", QMessageBox.ButtonRole.ActionRole)
        b_s = box.addButton(
            "Mở Screen Recording", QMessageBox.ButtonRole.ActionRole
        )
        b_p = box.addButton(
            "Yêu cầu Accessibility", QMessageBox.ButtonRole.ActionRole
        )
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is b_a:
            open_system_settings("accessibility")
        elif box.clickedButton() is b_s:
            open_system_settings("screen")
        elif box.clickedButton() is b_p:
            request_accessibility_prompt()

    # ============================================================== Window
    def _refresh_windows(self) -> None:
        try:
            self._windows = WindowManager.list_windows()
        except Exception as e:
            self._append_log("error", f"List windows lỗi: {e}")
            return
        self._render_window_list()

    def _render_window_list(self) -> None:
        keep_id = (
            self._selected_window.window_id if self._selected_window else None
        )
        filt = self.search_edit.text().strip().lower()
        self.window_list.blockSignals(True)
        self.window_list.clear()
        for w in self._windows:
            label = w.display_name
            if filt and filt not in label.lower():
                continue
            badge = "●" if w.on_screen else "○"
            item = QListWidgetItem(
                f"{badge}  {label}\n     {int(w.width)}×{int(w.height)} "
                f"@({int(w.x)},{int(w.y)}) pid={w.pid}"
            )
            item.setData(Qt.ItemDataRole.UserRole, w.window_id)
            self.window_list.addItem(item)
            if keep_id is not None and w.window_id == keep_id:
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
            self.preview_lbl.setText("Không capture được.")
            return
        self._last_screenshot = img
        pix = ndarray_bgr_to_qpixmap(img)
        scaled = pix.scaled(
            self.preview_lbl.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_lbl.setPixmap(scaled)

    # ============================================================== Templates
    def _render_templates(self) -> None:
        self.template_list.blockSignals(True)
        self.template_list.clear()
        for ref in self._scenario.templates:
            item = QListWidgetItem(ref.name)
            item.setData(Qt.ItemDataRole.UserRole, ref.template_id)
            item.setToolTip(ref.path)
            self.template_list.addItem(item)
        self.template_list.blockSignals(False)

    def _on_template_selected(self) -> None:
        # No-op for now, có thể preview sau
        pass

    def _selected_template(self) -> Optional[TemplateRef]:
        items = self.template_list.selectedItems()
        if not items:
            return None
        tid = items[0].data(Qt.ItemDataRole.UserRole)
        return self._scenario.get_template(tid)

    def _add_template_from_window(self) -> None:
        if not self._selected_window:
            QMessageBox.warning(self, "Chưa chọn", "Hãy chọn cửa sổ trước.")
            return
        img = WindowManager.capture_window(self._selected_window.window_id)
        if img is None:
            QMessageBox.warning(self, "Lỗi", "Không capture được.")
            return
        dlg = RegionSelectorDialog(img, self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        crop = dlg.cropped
        if crop is None or crop.size == 0:
            return
        name, ok = QInputDialog.getText(
            self, "Tên template", "Đặt tên:", text="template"
        )
        if not ok or not name.strip():
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = ASSETS_DIR / f"tpl_{ts}_{uuid.uuid4().hex[:6]}.png"
        cv2.imwrite(str(path), crop)
        ref = TemplateRef(
            template_id=uuid.uuid4().hex[:8], name=name.strip(), path=str(path)
        )
        self._scenario.templates.append(ref)
        self._render_templates()
        self._append_log("info", f"Thêm template '{ref.name}' tại {path}")

    def _add_template_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn ảnh template",
            str(ASSETS_DIR),
            "Images (*.png *.jpg *.jpeg *.bmp)",
        )
        if not path:
            return
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            QMessageBox.warning(self, "Lỗi", "Không đọc được ảnh.")
            return
        name, ok = QInputDialog.getText(
            self,
            "Tên template",
            "Đặt tên:",
            text=Path(path).stem,
        )
        if not ok or not name.strip():
            return
        ref = TemplateRef(
            template_id=uuid.uuid4().hex[:8], name=name.strip(), path=path
        )
        self._scenario.templates.append(ref)
        self._render_templates()

    def _rename_template(self) -> None:
        ref = self._selected_template()
        if not ref:
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename", "Tên mới:", text=ref.name
        )
        if not ok or not new_name.strip():
            return
        ref.name = new_name.strip()
        self._render_templates()

    def _delete_template(self) -> None:
        ref = self._selected_template()
        if not ref:
            return
        if QMessageBox.question(
            self, "Xác nhận", f"Xóa template '{ref.name}'?"
        ) != QMessageBox.StandardButton.Yes:
            return
        # Cảnh báo nếu có step đang dùng
        used = [
            i + 1
            for i, s in enumerate(self._scenario.steps)
            if s.params.get("template_id") == ref.template_id
        ]
        if used:
            if QMessageBox.warning(
                self,
                "Cảnh báo",
                f"Template này đang được dùng ở step {used}. "
                "Xóa vẫn tiếp tục?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
        self._scenario.templates = [
            t for t in self._scenario.templates if t.template_id != ref.template_id
        ]
        self._render_templates()
        self._render_steps()

    def _test_template(self) -> None:
        ref = self._selected_template()
        if not ref:
            QMessageBox.warning(self, "Chưa chọn", "Chọn template để test.")
            return
        if not self._selected_window:
            QMessageBox.warning(self, "Chưa chọn", "Chọn cửa sổ target.")
            return
        img = WindowManager.capture_window(self._selected_window.window_id)
        if img is None:
            QMessageBox.warning(self, "Lỗi", "Không capture được.")
            return
        tpl = cv2.imread(ref.path, cv2.IMREAD_COLOR)
        if tpl is None:
            QMessageBox.warning(self, "Lỗi", f"Không đọc được template: {ref.path}")
            return
        m = ImageMatcher(
            threshold=self._scenario.default_threshold,
            multi_scale=self._scenario.multi_scale,
            grayscale=self._scenario.grayscale,
        )
        res = m.find(img, tpl)
        msg = (
            f"Template: {ref.name}\n"
            f"Confidence: {res.confidence:.4f}\n"
            f"Threshold:  {self._scenario.default_threshold:.2f}\n"
            f"Found:      {res.found}\n"
        )
        if res.found:
            cx, cy = res.center
            msg += f"Center pixel: ({cx}, {cy})\n"
            vis = img.copy()
            cv2.rectangle(
                vis,
                (res.x, res.y),
                (res.x + res.width, res.y + res.height),
                (0, 255, 0),
                3,
            )
            cv2.drawMarker(
                vis, (cx, cy), (0, 0, 255), cv2.MARKER_CROSS, 30, 3
            )
            pix = ndarray_bgr_to_qpixmap(vis)
            scaled = pix.scaled(
                self.preview_lbl.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_lbl.setPixmap(scaled)
        QMessageBox.information(self, "Test match", msg)

    # ============================================================== Steps
    def _render_steps(self) -> None:
        names = self._scenario.template_name_map()
        active_idx = -1
        eng = self._manager.current()
        if eng and eng.is_alive():
            active_idx = eng.stats.last_step_idx

        cur_row = self.step_list.currentRow()
        self.step_list.blockSignals(True)
        self.step_list.clear()
        for i, step in enumerate(self._scenario.steps):
            prefix = f"#{i + 1:>3}  "
            label = step.label(names)
            if not step.enabled:
                label = "(disabled) " + label
            item = QListWidgetItem(prefix + label)
            if not step.enabled:
                item.setForeground(QBrush(QColor("#888")))
            if i == active_idx:
                item.setBackground(QBrush(QColor("#2a4a2a")))
            self.step_list.addItem(item)
        self.step_list.blockSignals(False)
        if 0 <= cur_row < self.step_list.count():
            self.step_list.setCurrentRow(cur_row)

    def _selected_step_idx(self) -> int:
        return self.step_list.currentRow()

    def _add_step(self) -> None:
        new_step = Step(type=StepType.SLEEP, params={"seconds": 1.0})
        dlg = StepEditorDialog(
            new_step,
            self._scenario,
            len(self._scenario.steps) + 1,
            self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        # Insert sau step đang chọn, hoặc cuối cùng
        idx = self._selected_step_idx()
        insert_at = idx + 1 if idx >= 0 else len(self._scenario.steps)
        self._scenario.steps.insert(insert_at, dlg.step)
        self._render_steps()
        self.step_list.setCurrentRow(insert_at)

    def _edit_step(self) -> None:
        idx = self._selected_step_idx()
        if idx < 0:
            return
        step = self._scenario.steps[idx]
        dlg = StepEditorDialog(
            step, self._scenario, len(self._scenario.steps), self
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        self._scenario.steps[idx] = dlg.step
        self._render_steps()
        self.step_list.setCurrentRow(idx)

    def _duplicate_step(self) -> None:
        idx = self._selected_step_idx()
        if idx < 0:
            return
        src = self._scenario.steps[idx]
        clone = Step(
            type=src.type,
            enabled=src.enabled,
            params=dict(src.params),
        )
        self._scenario.steps.insert(idx + 1, clone)
        self._render_steps()
        self.step_list.setCurrentRow(idx + 1)

    def _delete_step(self) -> None:
        idx = self._selected_step_idx()
        if idx < 0:
            return
        del self._scenario.steps[idx]
        self._render_steps()
        if self._scenario.steps:
            self.step_list.setCurrentRow(min(idx, len(self._scenario.steps) - 1))

    def _move_step(self, delta: int) -> None:
        idx = self._selected_step_idx()
        if idx < 0:
            return
        new_idx = idx + delta
        if not (0 <= new_idx < len(self._scenario.steps)):
            return
        self._scenario.steps[idx], self._scenario.steps[new_idx] = (
            self._scenario.steps[new_idx],
            self._scenario.steps[idx],
        )
        self._render_steps()
        self.step_list.setCurrentRow(new_idx)

    def _toggle_step_enabled(self) -> None:
        idx = self._selected_step_idx()
        if idx < 0:
            return
        self._scenario.steps[idx].enabled = not self._scenario.steps[idx].enabled
        self._render_steps()
        self.step_list.setCurrentRow(idx)

    # ============================================================== Scenario file
    def _on_scn_changed(self) -> None:
        self._scenario.name = self.scn_name_edit.text() or "Untitled"
        self._scenario.default_threshold = self.def_thr_spin.value()
        self._scenario.default_click_type = self.def_click_combo.currentData()
        self._scenario.default_click_mode = self.def_mode_combo.currentData()
        self._scenario.default_click_jitter_px = self.def_jitter_spin.value()
        self._scenario.default_poll_interval = self.def_poll_spin.value()
        self._scenario.activate_before_click = self.activate_chk.isChecked()
        self._scenario.multi_scale = self.multiscale_chk.isChecked()
        self._scenario.grayscale = self.grayscale_chk.isChecked()

    def _apply_scenario_to_ui(self) -> None:
        self.scn_name_edit.setText(self._scenario.name)
        self.def_thr_spin.setValue(self._scenario.default_threshold)
        idx = self.def_click_combo.findData(self._scenario.default_click_type)
        if idx >= 0:
            self.def_click_combo.setCurrentIndex(idx)
        idx = self.def_mode_combo.findData(self._scenario.default_click_mode)
        if idx >= 0:
            self.def_mode_combo.setCurrentIndex(idx)
        self.def_jitter_spin.setValue(self._scenario.default_click_jitter_px)
        self.def_poll_spin.setValue(self._scenario.default_poll_interval)
        self.activate_chk.setChecked(self._scenario.activate_before_click)
        self.multiscale_chk.setChecked(self._scenario.multi_scale)
        self.grayscale_chk.setChecked(self._scenario.grayscale)
        self._render_templates()
        self._render_steps()

    def _new_scenario(self) -> None:
        if self._manager.current() and self._manager.current().is_alive():
            QMessageBox.information(
                self, "Đang chạy", "Dừng scenario trước khi tạo mới."
            )
            return
        self._scenario = ScenarioConfig()
        if self._selected_window:
            self._scenario.window_id = self._selected_window.window_id
            self._scenario.pid = self._selected_window.pid
        self._scenario_path = None
        self._apply_scenario_to_ui()
        self._append_log("info", "New scenario.")

    def _open_scenario(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open scenario", str(SCENARIOS_DIR), "JSON (*.json)"
        )
        if not path:
            return
        try:
            self._scenario = ScenarioConfig.load(path)
        except Exception as e:
            QMessageBox.warning(self, "Lỗi", f"Không đọc được file: {e}")
            return
        self._scenario_path = path
        if self._selected_window:
            self._scenario.window_id = self._selected_window.window_id
            self._scenario.pid = self._selected_window.pid
        self._apply_scenario_to_ui()
        self._append_log("info", f"Đã load scenario: {path}")

    def _save_scenario(self) -> None:
        if not self._scenario_path:
            self._save_scenario_as()
            return
        try:
            self._scenario.save(self._scenario_path)
            self._append_log("info", f"Saved: {self._scenario_path}")
        except Exception as e:
            QMessageBox.warning(self, "Lỗi", f"Save failed: {e}")

    def _save_scenario_as(self) -> None:
        default = SCENARIOS_DIR / f"{self._scenario.name or 'scenario'}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save scenario as", str(default), "JSON (*.json)"
        )
        if not path:
            return
        if not path.endswith(".json"):
            path += ".json"
        try:
            self._scenario.save(path)
            self._scenario_path = path
            self._append_log("info", f"Saved as: {path}")
        except Exception as e:
            QMessageBox.warning(self, "Lỗi", f"Save failed: {e}")

    # ============================================================== Run
    def _start(self) -> None:
        if not self._selected_window:
            QMessageBox.warning(self, "Chưa chọn", "Chọn cửa sổ target trước.")
            return
        if not self._scenario.steps:
            QMessageBox.warning(
                self, "Empty scenario", "Hãy thêm ít nhất 1 step."
            )
            return
        sr = check_screen_recording()
        ax = check_accessibility()
        if not (sr and ax):
            self._check_permissions_dialog(sr, ax)
            return
        # Sync window/pid mới nhất
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
        self.btn_pause.setText("⏸  Pause")
        self.btn_stop.setEnabled(True)
        self.stat_status.setText("running")

    def _pause(self) -> None:
        eng = self._manager.current()
        if not eng:
            return
        eng.toggle_pause()
        if eng.is_paused():
            self.btn_pause.setText("▶  Resume")
            self.stat_status.setText("paused")
        else:
            self.btn_pause.setText("⏸  Pause")
            self.stat_status.setText("running")

    def _stop(self) -> None:
        self._manager.stop()

    def _toggle_start_stop(self) -> None:
        eng = self._manager.current()
        if eng and eng.is_alive():
            self._stop()
        else:
            self._start()

    def _toggle_pause(self) -> None:
        eng = self._manager.current()
        if eng and eng.is_alive():
            self._pause()

    def _on_finished(self) -> None:
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("⏸  Pause")
        self.btn_stop.setEnabled(False)
        self.stat_status.setText("stopped")
        self._render_steps()  # clear active highlight

    def _on_step_active(self, idx: int) -> None:
        self.stat_step.setText(f"#{idx + 1}")
        self._render_steps()

    # ============================================================== Log/stats
    def _on_log(self, ev: LogEvent) -> None:
        self._append_log(ev.level, ev.message, ts=ev.timestamp)

    def _append_log(self, level: str, msg: str, ts: float | None = None) -> None:
        when = datetime.fromtimestamp(ts or time.time()).strftime("%H:%M:%S")
        color = {
            "info": "#9ad",
            "warn": "#fa3",
            "error": "#f55",
            "click": "#5d5",
            "step": "#8cf",
            "miss": "#888",
        }.get(level, "#ddd")
        self.log_view.appendHtml(
            f"<span style='color:#666'>[{when}]</span> "
            f"<span style='color:{color}'>{level.upper():5s}</span> "
            f"<span style='color:#ddd'>{msg}</span>"
        )

    def _on_stats(self, stats: ScenarioStats) -> None:
        self.stat_clicks.setText(str(stats.clicks))
        self.stat_steps_exec.setText(str(stats.steps_executed))
        self.stat_conf.setText(f"{stats.last_confidence:.3f}")

    def _tick_runtime(self) -> None:
        eng = self._manager.current()
        if eng and eng.stats.started_at > 0:
            elapsed = time.time() - eng.stats.started_at
            self.stat_runtime.setText(self._fmt_duration(elapsed))

    @staticmethod
    def _fmt_duration(s: float) -> str:
        s = int(s)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        if m < 60:
            return f"{m}m {s}s"
        h, m = divmod(m, 60)
        return f"{h}h {m}m {s}s"

    # ============================================================== Misc
    def _about(self) -> None:
        QMessageBox.information(
            self,
            "About",
            "Auto Clicker - Scenario Engine\n"
            "macOS - PySide6 + OpenCV + Quartz\n\n"
            "Hotkeys:\n"
            "  F8  Start/Stop\n"
            "  F9  Pause/Resume\n",
        )

    def closeEvent(self, event) -> None:
        self._manager.stop()
        self._hotkeys.stop()
        super().closeEvent(event)
