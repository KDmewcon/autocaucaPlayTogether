"""Main window cho Auto Clicker."""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal
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
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core.automation import (
    AutomationJob,
    AutomationManager,
    JobConfig,
    JobStats,
    JobStatus,
    LogEvent,
)
from ..core.click_engine import ClickType
from ..core.image_matcher import ImageMatcher
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

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)


class _Bridge(QObject):
    """Bridge để emit signal từ thread automation về Qt main thread."""

    log_signal = Signal(object)  # LogEvent
    stats_signal = Signal(object)  # JobStats
    finish_signal = Signal()


class MainWindow(QMainWindow):
    JOB_ID = "primary"

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Auto Clicker - Image Based - macOS")
        self.resize(1280, 820)

        self._windows: list[WindowInfo] = []
        self._selected_window: Optional[WindowInfo] = None
        self._template: Optional[np.ndarray] = None
        self._template_path: str = ""
        self._last_screenshot: Optional[np.ndarray] = None

        self._automation = AutomationManager()
        self._bridge = _Bridge()
        self._bridge.log_signal.connect(self._on_log)
        self._bridge.stats_signal.connect(self._on_stats)
        self._bridge.finish_signal.connect(self._on_job_finished)

        self._hotkeys = HotkeyManager()

        self._build_ui()
        self._build_menu()
        self._build_statusbar()

        # Auto refresh window list mỗi 5s
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
        self._refresh_timer.timeout.connect(self._refresh_windows)
        self._refresh_timer.start()

        # Live preview timer
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(500)
        self._preview_timer.timeout.connect(self._update_preview)

        # Stats refresh
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(500)
        self._stats_timer.timeout.connect(self._tick_stats)
        self._stats_timer.start()

        # Init
        QTimer.singleShot(100, self._initial_check)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # Left panel: window list
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(4, 4, 4, 4)
        left_lay.addWidget(QLabel("<b>Cửa sổ đang mở</b>"))

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Tìm theo tên app/title...")
        self.search_edit.textChanged.connect(self._filter_windows)
        left_lay.addWidget(self.search_edit)

        self.window_list = QListWidget()
        self.window_list.itemSelectionChanged.connect(self._on_window_selected)
        left_lay.addWidget(self.window_list, 1)

        btn_refresh = QPushButton("🔄  Refresh danh sách")
        btn_refresh.clicked.connect(self._refresh_windows)
        left_lay.addWidget(btn_refresh)

        splitter.addWidget(left)

        # Center: preview + template
        center = QWidget()
        center_lay = QVBoxLayout(center)
        center_lay.setContentsMargins(4, 4, 4, 4)

        self.preview_lbl = QLabel("Chọn cửa sổ ở bên trái để xem preview")
        self.preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_lbl.setMinimumSize(QSize(640, 360))
        self.preview_lbl.setFrameShape(QFrame.Shape.Box)
        self.preview_lbl.setStyleSheet(
            "QLabel { background:#1e1e1e; color:#aaa; }"
        )
        center_lay.addWidget(self.preview_lbl, 3)

        # Template row
        tmpl_box = QGroupBox("Template image")
        tmpl_lay = QHBoxLayout(tmpl_box)

        self.template_lbl = QLabel("Chưa có template")
        self.template_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.template_lbl.setMinimumSize(QSize(180, 120))
        self.template_lbl.setFrameShape(QFrame.Shape.Box)
        self.template_lbl.setStyleSheet(
            "QLabel { background:#222; color:#aaa; }"
        )
        tmpl_lay.addWidget(self.template_lbl, 1)

        tmpl_btns = QVBoxLayout()
        self.btn_capture_tmpl = QPushButton("📐  Cắt template từ window")
        self.btn_capture_tmpl.clicked.connect(self._capture_template_from_window)
        tmpl_btns.addWidget(self.btn_capture_tmpl)

        self.btn_load_tmpl = QPushButton("📂  Load file ảnh...")
        self.btn_load_tmpl.clicked.connect(self._load_template_file)
        tmpl_btns.addWidget(self.btn_load_tmpl)

        self.btn_test_match = QPushButton("🔍  Test match ngay")
        self.btn_test_match.clicked.connect(self._test_match)
        tmpl_btns.addWidget(self.btn_test_match)

        tmpl_btns.addStretch(1)
        tmpl_lay.addLayout(tmpl_btns)

        center_lay.addWidget(tmpl_box, 2)

        splitter.addWidget(center)

        # Right: job config + log
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(4, 4, 4, 4)

        cfg_box = QGroupBox("Cấu hình job")
        form = QFormLayout(cfg_box)

        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.5, 1.0)
        self.threshold_spin.setSingleStep(0.01)
        self.threshold_spin.setValue(0.85)
        self.threshold_spin.setDecimals(2)
        form.addRow("Ngưỡng match:", self.threshold_spin)

        self.click_combo = QComboBox()
        self.click_combo.addItems(["left", "right", "middle", "double"])
        form.addRow("Loại click:", self.click_combo)

        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.05, 600.0)
        self.interval_spin.setSingleStep(0.1)
        self.interval_spin.setValue(1.0)
        self.interval_spin.setSuffix(" s")
        form.addRow("Chu kỳ:", self.interval_spin)

        self.jitter_spin = QDoubleSpinBox()
        self.jitter_spin.setRange(0.0, 5.0)
        self.jitter_spin.setSingleStep(0.05)
        self.jitter_spin.setValue(0.15)
        self.jitter_spin.setSuffix(" s")
        form.addRow("Jitter chu kỳ:", self.jitter_spin)

        self.offset_x_spin = QSpinBox()
        self.offset_x_spin.setRange(-2000, 2000)
        self.offset_x_spin.setValue(0)
        form.addRow("Offset X click:", self.offset_x_spin)

        self.offset_y_spin = QSpinBox()
        self.offset_y_spin.setRange(-2000, 2000)
        self.offset_y_spin.setValue(0)
        form.addRow("Offset Y click:", self.offset_y_spin)

        self.click_jitter_spin = QSpinBox()
        self.click_jitter_spin.setRange(0, 50)
        self.click_jitter_spin.setValue(2)
        self.click_jitter_spin.setSuffix(" px")
        form.addRow("Jitter vị trí click:", self.click_jitter_spin)

        self.max_clicks_spin = QSpinBox()
        self.max_clicks_spin.setRange(0, 1_000_000)
        self.max_clicks_spin.setValue(0)
        self.max_clicks_spin.setSpecialValueText("Không giới hạn")
        form.addRow("Max clicks:", self.max_clicks_spin)

        self.stop_misses_spin = QSpinBox()
        self.stop_misses_spin.setRange(0, 10_000)
        self.stop_misses_spin.setValue(0)
        self.stop_misses_spin.setSpecialValueText("Không tự dừng")
        form.addRow("Dừng sau N miss liên tiếp:", self.stop_misses_spin)

        self.multiscale_chk = QCheckBox("Multi-scale matching")
        self.multiscale_chk.setChecked(True)
        form.addRow("", self.multiscale_chk)

        self.grayscale_chk = QCheckBox("Grayscale matching")
        self.grayscale_chk.setChecked(True)
        form.addRow("", self.grayscale_chk)

        right_lay.addWidget(cfg_box)

        # Control buttons
        ctrl_box = QGroupBox("Điều khiển")
        ctrl_lay = QHBoxLayout(ctrl_box)
        self.btn_start = QPushButton("▶  Start")
        self.btn_start.setStyleSheet(
            "QPushButton { background:#2e7d32; color:white; "
            "padding:8px 16px; font-weight:bold; }"
        )
        self.btn_start.clicked.connect(self._start_job)
        ctrl_lay.addWidget(self.btn_start)

        self.btn_pause = QPushButton("⏸  Pause")
        self.btn_pause.clicked.connect(self._pause_job)
        self.btn_pause.setEnabled(False)
        ctrl_lay.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("⏹  Stop")
        self.btn_stop.setStyleSheet(
            "QPushButton { background:#c62828; color:white; "
            "padding:8px 16px; font-weight:bold; }"
        )
        self.btn_stop.clicked.connect(self._stop_job)
        self.btn_stop.setEnabled(False)
        ctrl_lay.addWidget(self.btn_stop)

        right_lay.addWidget(ctrl_box)

        # Stats
        stats_box = QGroupBox("Thống kê")
        stats_lay = QFormLayout(stats_box)
        self.stat_status = QLabel("idle")
        self.stat_clicks = QLabel("0")
        self.stat_misses = QLabel("0")
        self.stat_conf = QLabel("0.000")
        self.stat_runtime = QLabel("0s")
        stats_lay.addRow("Trạng thái:", self.stat_status)
        stats_lay.addRow("Số lần click:", self.stat_clicks)
        stats_lay.addRow("Số lần miss:", self.stat_misses)
        stats_lay.addRow("Confidence cuối:", self.stat_conf)
        stats_lay.addRow("Runtime:", self.stat_runtime)
        right_lay.addWidget(stats_box)

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
        btn_clear_log = QPushButton("Clear log")
        btn_clear_log.clicked.connect(self.log_view.clear)
        log_lay.addWidget(btn_clear_log)
        right_lay.addWidget(log_box, 1)

        splitter.addWidget(right)
        splitter.setSizes([280, 620, 380])

    def _build_menu(self) -> None:
        bar = self.menuBar()
        m_file = bar.addMenu("File")
        act_quit = QAction("Quit", self)
        act_quit.setShortcut("Cmd+Q")
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_perm = bar.addMenu("Permissions")
        act_check = QAction("Kiểm tra quyền", self)
        act_check.triggered.connect(self._check_permissions_dialog)
        m_perm.addAction(act_check)
        act_open_acc = QAction("Mở System Settings - Accessibility", self)
        act_open_acc.triggered.connect(
            lambda: open_system_settings("accessibility")
        )
        m_perm.addAction(act_open_acc)
        act_open_sc = QAction("Mở System Settings - Screen Recording", self)
        act_open_sc.triggered.connect(lambda: open_system_settings("screen"))
        m_perm.addAction(act_open_sc)

        m_help = bar.addMenu("Help")
        act_about = QAction("About", self)
        act_about.triggered.connect(self._about)
        m_help.addAction(act_about)

    def _build_statusbar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.perm_lbl = QLabel()
        sb.addPermanentWidget(self.perm_lbl)
        sb.showMessage("Sẵn sàng. Hotkey: Cmd+Shift+S = Start/Stop, Cmd+Shift+P = Pause")

    # ------------------------------------------------------------------ Init
    def _initial_check(self) -> None:
        self._refresh_windows()
        self._check_permissions(silent=False)
        self._setup_hotkeys()

    def _setup_hotkeys(self) -> None:
        self._hotkeys.clear()
        self._hotkeys.set_binding(
            "<cmd>+<shift>+s", lambda: QTimer.singleShot(0, self._toggle_start_stop)
        )
        self._hotkeys.set_binding(
            "<cmd>+<shift>+p", lambda: QTimer.singleShot(0, self._toggle_pause)
        )
        self._hotkeys.start()

    # ------------------------------------------------------------------ Permissions
    def _check_permissions(self, silent: bool = False) -> tuple[bool, bool]:
        sr = check_screen_recording()
        ax = check_accessibility()
        parts = []
        parts.append(
            f"<span style='color:{'#4caf50' if sr else '#f44336'}'>"
            f"●</span> Screen Recording"
        )
        parts.append(
            f"<span style='color:{'#4caf50' if ax else '#f44336'}'>"
            f"●</span> Accessibility"
        )
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

        msg = "Trạng thái quyền:\n"
        msg += f"  • Screen Recording: {'OK' if sr else 'CHƯA CÓ'}\n"
        msg += f"  • Accessibility:    {'OK' if ax else 'CHƯA CÓ'}\n\n"
        if sr and ax:
            QMessageBox.information(self, "Permissions", msg + "Đủ quyền.")
            return
        msg += (
            "Cần cấp đủ 2 quyền trên cho Terminal/Python (hoặc app đang chạy "
            "tool này) trong System Settings → Privacy & Security.\n"
            "Sau khi cấp, **quit và mở lại tool** để chắc chắn quyền có hiệu lực."
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Cần cấp quyền")
        box.setText(msg)
        open_acc = box.addButton("Mở Accessibility", QMessageBox.ButtonRole.ActionRole)
        open_sc = box.addButton(
            "Mở Screen Recording", QMessageBox.ButtonRole.ActionRole
        )
        prompt_ax = box.addButton(
            "Yêu cầu Accessibility", QMessageBox.ButtonRole.ActionRole
        )
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_acc:
            open_system_settings("accessibility")
        elif box.clickedButton() is open_sc:
            open_system_settings("screen")
        elif box.clickedButton() is prompt_ax:
            request_accessibility_prompt()

    # ------------------------------------------------------------------ Window list
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
                f"{badge}  {label}\n     "
                f"{int(w.width)}×{int(w.height)} @({int(w.x)},{int(w.y)}) "
                f"pid={w.pid}"
            )
            item.setData(Qt.ItemDataRole.UserRole, w.window_id)
            self.window_list.addItem(item)
            if keep_id is not None and w.window_id == keep_id:
                item.setSelected(True)
                self.window_list.setCurrentItem(item)
        self.window_list.blockSignals(False)

    def _filter_windows(self) -> None:
        self._render_window_list()

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
        self._update_preview()
        if not self._preview_timer.isActive():
            self._preview_timer.start()

    # ------------------------------------------------------------------ Preview
    def _update_preview(self) -> None:
        if not self._selected_window:
            return
        # Đừng capture nếu cửa sổ vừa biến mất
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
        self._last_screenshot = img
        pix = ndarray_bgr_to_qpixmap(img)
        scaled = pix.scaled(
            self.preview_lbl.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_lbl.setPixmap(scaled)

    # ------------------------------------------------------------------ Template
    def _capture_template_from_window(self) -> None:
        if not self._selected_window:
            QMessageBox.warning(self, "Chưa chọn", "Hãy chọn 1 cửa sổ trước.")
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
        # Save vào assets
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = ASSETS_DIR / f"template_{ts}.png"
        cv2.imwrite(str(path), crop)
        self._set_template(crop, str(path))
        self._append_log("info", f"Đã lưu template: {path}")

    def _load_template_file(self) -> None:
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
        self._set_template(img, path)
        self._append_log("info", f"Đã load template: {path}")

    def _set_template(self, img: np.ndarray, path: str) -> None:
        self._template = img
        self._template_path = path
        pix = ndarray_bgr_to_qpixmap(img)
        scaled = pix.scaled(
            self.template_lbl.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.template_lbl.setPixmap(scaled)

    def _test_match(self) -> None:
        if self._template is None:
            QMessageBox.warning(self, "Thiếu template", "Hãy set template trước.")
            return
        if not self._selected_window:
            QMessageBox.warning(self, "Chưa chọn window", "Hãy chọn 1 cửa sổ.")
            return
        img = WindowManager.capture_window(self._selected_window.window_id)
        if img is None:
            QMessageBox.warning(self, "Lỗi", "Không capture được window.")
            return
        matcher = ImageMatcher(
            threshold=self.threshold_spin.value(),
            multi_scale=self.multiscale_chk.isChecked(),
            grayscale=self.grayscale_chk.isChecked(),
        )
        res = matcher.find(img, self._template)
        msg = (
            f"Confidence: {res.confidence:.4f}\n"
            f"Threshold:  {self.threshold_spin.value():.2f}\n"
            f"Found:      {res.found}\n"
        )
        if res.found:
            cx, cy = res.center
            msg += f"Match center (pixel): ({cx}, {cy})\n"
            # Vẽ overlay cho user thấy
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

    # ------------------------------------------------------------------ Job
    def _build_config(self) -> Optional[JobConfig]:
        if not self._selected_window:
            QMessageBox.warning(self, "Chưa chọn", "Hãy chọn 1 cửa sổ.")
            return None
        if not self._template_path or not os.path.exists(self._template_path):
            QMessageBox.warning(
                self, "Thiếu template", "Hãy chuẩn bị template trước."
            )
            return None
        click_type = ClickType(self.click_combo.currentText())
        return JobConfig(
            name=self._selected_window.display_name,
            window_id=self._selected_window.window_id,
            pid=self._selected_window.pid,
            template_path=self._template_path,
            threshold=self.threshold_spin.value(),
            click_type=click_type,
            interval_seconds=self.interval_spin.value(),
            interval_jitter=self.jitter_spin.value(),
            click_offset_x=self.offset_x_spin.value(),
            click_offset_y=self.offset_y_spin.value(),
            click_jitter_px=self.click_jitter_spin.value(),
            max_clicks=self.max_clicks_spin.value(),
            stop_after_misses=self.stop_misses_spin.value(),
            multi_scale=self.multiscale_chk.isChecked(),
            grayscale=self.grayscale_chk.isChecked(),
        )

    def _start_job(self) -> None:
        cfg = self._build_config()
        if cfg is None:
            return
        sr = check_screen_recording()
        ax = check_accessibility()
        if not (sr and ax):
            self._check_permissions_dialog(sr, ax)
            return

        self._automation.start_job(
            self.JOB_ID,
            cfg,
            on_log=lambda ev: self._bridge.log_signal.emit(ev),
            on_stats=lambda st: self._bridge.stats_signal.emit(st),
            on_finish=lambda: self._bridge.finish_signal.emit(),
        )
        self.btn_start.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self.stat_status.setText("running")
        self._append_log("info", f"Khởi động job: {cfg.name}")

    def _pause_job(self) -> None:
        job = self._automation.get_job(self.JOB_ID)
        if not job:
            return
        job.toggle_pause()
        if job.status == JobStatus.PAUSED:
            self.btn_pause.setText("▶  Resume")
            self.stat_status.setText("paused")
        else:
            self.btn_pause.setText("⏸  Pause")
            self.stat_status.setText("running")

    def _stop_job(self) -> None:
        self._automation.stop_job(self.JOB_ID)

    def _toggle_start_stop(self) -> None:
        job = self._automation.get_job(self.JOB_ID)
        if job and job.is_alive():
            self._stop_job()
        else:
            self._start_job()

    def _toggle_pause(self) -> None:
        job = self._automation.get_job(self.JOB_ID)
        if job and job.is_alive():
            self._pause_job()

    def _on_job_finished(self) -> None:
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("⏸  Pause")
        self.btn_stop.setEnabled(False)
        self.stat_status.setText("stopped")

    # ------------------------------------------------------------------ Log/stats
    def _on_log(self, ev: LogEvent) -> None:
        self._append_log(ev.level, ev.message, ts=ev.timestamp)

    def _append_log(self, level: str, msg: str, ts: float | None = None) -> None:
        when = datetime.fromtimestamp(ts or time.time()).strftime("%H:%M:%S")
        color = {
            "info": "#9ad",
            "warn": "#fa3",
            "error": "#f55",
            "click": "#5d5",
            "miss": "#888",
        }.get(level, "#ddd")
        self.log_view.appendHtml(
            f"<span style='color:#666'>[{when}]</span> "
            f"<span style='color:{color}'>{level.upper():5s}</span> "
            f"<span style='color:#ddd'>{msg}</span>"
        )

    def _on_stats(self, stats: JobStats) -> None:
        self.stat_clicks.setText(str(stats.clicks))
        self.stat_misses.setText(str(stats.misses))
        self.stat_conf.setText(f"{stats.last_confidence:.3f}")

    def _tick_stats(self) -> None:
        job = self._automation.get_job(self.JOB_ID)
        if job and job.stats.started_at > 0:
            elapsed = time.time() - job.stats.started_at
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

    # ------------------------------------------------------------------ Misc
    def _about(self) -> None:
        QMessageBox.information(
            self,
            "About",
            "Auto Clicker (Image-based, Non-intrusive)\n"
            "macOS - PySide6 + OpenCV + Quartz\n\n"
            "Hotkeys:\n"
            "  Cmd+Shift+S  Start/Stop\n"
            "  Cmd+Shift+P  Pause/Resume\n",
        )

    def closeEvent(self, event) -> None:
        self._automation.stop_all()
        self._hotkeys.stop()
        super().closeEvent(event)
