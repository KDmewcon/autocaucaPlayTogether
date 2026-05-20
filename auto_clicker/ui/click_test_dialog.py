"""Dialog test các kiểu click khác nhau, không chạy scenario."""
from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.click_engine import ClickEngine, ClickMode, ClickType
from ..core.window_manager import WindowInfo, WindowManager


_MODE_LABELS = [
    (ClickMode.PID_POSTED,
     "PID Posted",
     "Gửi click vào PID app, KHÔNG chiếm chuột, KHÔNG kéo window lên trên. "
     "Yêu cầu: app target nhận event (nhiều game block). Tốt nhất khi work."),
    (ClickMode.HID_RESTORE,
     "HID Restore",
     "Move chuột tới vị trí, click, rồi đưa chuột về chỗ cũ. "
     "Có nháy chuột 1 cái nhưng làm việc với hầu hết app/game."),
    (ClickMode.HID_TAP,
     "HID Tap",
     "Move chuột tới vị trí và click, KHÔNG restore. "
     "Đơn giản nhất, làm việc với mọi app, nhưng chuột nằm ở vị trí target."),
]

_TYPE_LABELS = [
    (ClickType.LEFT, "Left click"),
    (ClickType.RIGHT, "Right click"),
    (ClickType.DOUBLE, "Double click (left)"),
    (ClickType.MIDDLE, "Middle click"),
]


class ClickTestDialog(QDialog):
    """Test các kiểu click khác nhau để xem cái nào work với window đang chọn."""

    def __init__(
        self,
        window: Optional[WindowInfo] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Click Tester")
        self.resize(640, 580)
        self._window = window
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Window info
        info_box = QGroupBox("Target")
        info_lay = QFormLayout(info_box)
        if self._window:
            info_lay.addRow(
                QLabel(
                    f"<b>{self._window.title or self._window.app_name}</b><br>"
                    f"PID: {self._window.pid}  ·  "
                    f"Origin: ({self._window.x:.0f}, {self._window.y:.0f})  ·  "
                    f"Size: {self._window.width:.0f}×{self._window.height:.0f}"
                )
            )
        else:
            info_lay.addRow(QLabel(
                "<i>Không có window được chọn ở main. Click sẽ dùng HID mode "
                "(không có PID để post).</i>"
            ))
        layout.addWidget(info_box)

        # Coords
        coord_box = QGroupBox("Toạ độ")
        coord_lay = QFormLayout(coord_box)
        unit_row = QHBoxLayout()
        self.unit_combo = QComboBox()
        self.unit_combo.addItem("Local (trong window)", "local")
        self.unit_combo.addItem("Global (toàn screen)", "global")
        self.unit_combo.setCurrentIndex(0 if self._window else 1)
        if not self._window:
            self.unit_combo.setEnabled(False)
        unit_row.addWidget(self.unit_combo)
        unit_row.addStretch(1)
        coord_lay.addRow("Hệ toạ độ:", unit_row)

        self.x_spin = QDoubleSpinBox()
        self.x_spin.setRange(0, 10000)
        self.x_spin.setDecimals(1)
        self.x_spin.setValue(
            self._window.width / 2 if self._window else 200
        )
        self.y_spin = QDoubleSpinBox()
        self.y_spin.setRange(0, 10000)
        self.y_spin.setDecimals(1)
        self.y_spin.setValue(
            self._window.height / 2 if self._window else 200
        )
        coord_lay.addRow("X:", self.x_spin)
        coord_lay.addRow("Y:", self.y_spin)
        layout.addWidget(coord_box)

        # Click options
        opt_box = QGroupBox("Tuỳ chọn click")
        opt_lay = QFormLayout(opt_box)
        self.type_combo = QComboBox()
        for t, lbl in _TYPE_LABELS:
            self.type_combo.addItem(lbl, t)
        opt_lay.addRow("Loại click:", self.type_combo)

        self.activate_chk = QCheckBox("Activate window trước click (HID modes)")
        self.activate_chk.setChecked(False)
        self.activate_chk.setToolTip(
            "Bring window lên trước khi click. PID_POSTED không cần."
        )
        opt_lay.addRow("", self.activate_chk)

        self.delay_spin = QDoubleSpinBox()
        self.delay_spin.setRange(0.0, 5.0)
        self.delay_spin.setDecimals(2)
        self.delay_spin.setSingleStep(0.5)
        self.delay_spin.setSuffix(" s")
        self.delay_spin.setValue(2.0)
        self.delay_spin.setToolTip(
            "Đếm ngược trước khi click. Cho bạn kịp đưa chuột tránh chỗ click."
        )
        opt_lay.addRow("Delay:", self.delay_spin)

        self.repeat_spin = QSpinBox()
        self.repeat_spin.setRange(1, 100)
        self.repeat_spin.setValue(1)
        self.repeat_spin.setSuffix(" lần")
        opt_lay.addRow("Số lần:", self.repeat_spin)

        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.05, 10)
        self.interval_spin.setSingleStep(0.1)
        self.interval_spin.setSuffix(" s")
        self.interval_spin.setValue(0.5)
        opt_lay.addRow("Interval lặp:", self.interval_spin)

        layout.addWidget(opt_box)

        # 3 mode buttons
        modes_box = QGroupBox("Test các mode click (mỗi mode 1 nút)")
        modes_lay = QVBoxLayout(modes_box)
        self._mode_buttons = []
        for mode, name, desc in _MODE_LABELS:
            row = QHBoxLayout()
            btn = QPushButton(f"▶ Test  {name}")
            btn.setMinimumWidth(180)
            f = btn.font()
            f.setBold(True)
            btn.setFont(f)
            btn.clicked.connect(lambda _=False, m=mode, n=name: self._do_test(m, n))
            row.addWidget(btn)
            label = QLabel(f"<i>{desc}</i>")
            label.setWordWrap(True)
            row.addWidget(label, 1)
            modes_lay.addLayout(row)
            self._mode_buttons.append(btn)
        layout.addWidget(modes_box)

        # Log
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
        f = QFont("Menlo")
        f.setPointSize(10)
        self.log.setFont(f)
        layout.addWidget(self.log)

        # Close
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        btn_close = QPushButton("Đóng")
        btn_close.clicked.connect(self.accept)
        close_row.addWidget(btn_close)
        layout.addLayout(close_row)

    def _resolve_global(self) -> tuple[float, float]:
        """Convert input coord (theo unit chọn) sang global screen point."""
        x = self.x_spin.value()
        y = self.y_spin.value()
        unit = self.unit_combo.currentData()
        if unit == "local" and self._window:
            # Refresh window info để lấy origin mới (window có thể đã di chuyển)
            try:
                w = WindowManager.get_window(self._window.window_id)
                if w:
                    self._window = w
            except Exception:
                pass
            return (self._window.x + x, self._window.y + y)
        return (x, y)

    def _append_log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {msg}")

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for b in self._mode_buttons:
            b.setEnabled(enabled)

    def _do_test(self, mode: ClickMode, mode_name: str) -> None:
        gx, gy = self._resolve_global()
        # currentData() có thể trả str (sau Qt serialize), cast về enum
        ct_data = self.type_combo.currentData()
        if isinstance(ct_data, str):
            try:
                click_type = ClickType(ct_data)
            except ValueError:
                click_type = ClickType.LEFT
        else:
            click_type = ct_data or ClickType.LEFT
        if isinstance(mode, str):
            try:
                mode = ClickMode(mode)
            except ValueError:
                mode = ClickMode.HID_RESTORE
        repeat = self.repeat_spin.value()
        interval = self.interval_spin.value()
        delay = self.delay_spin.value()
        pid = self._window.pid if self._window else 0

        self._append_log(
            f"▶ {mode_name} {click_type.value} @({gx:.0f},{gy:.0f}) "
            f"× {repeat}, delay={delay}s"
        )

        # Activate nếu cần
        if (
            self.activate_chk.isChecked()
            and mode != ClickMode.PID_POSTED
            and pid > 0
        ):
            ok = ClickEngine.activate_app(pid)
            self._append_log(f"  Activate pid={pid} → {ok}")

        # Disable buttons trong khi đang chạy + countdown
        self._set_buttons_enabled(False)

        if delay > 0:
            self._countdown_then_run(delay, mode, click_type, gx, gy, pid, repeat, interval)
        else:
            self._do_run(mode, click_type, gx, gy, pid, repeat, interval)

    def _countdown_then_run(
        self, delay: float, mode, click_type, gx, gy, pid, repeat, interval
    ) -> None:
        remaining = [delay]

        def tick():
            if remaining[0] <= 0.05:
                self._do_run(mode, click_type, gx, gy, pid, repeat, interval)
                return
            self._append_log(f"  ⏱ {remaining[0]:.1f}s...")
            remaining[0] -= 1.0
            QTimer.singleShot(1000, tick)

        tick()

    def _do_run(self, mode, click_type, gx, gy, pid, repeat, interval) -> None:
        try:
            for i in range(repeat):
                ClickEngine.click(
                    gx, gy, pid=pid if pid > 0 else None,
                    click_type=click_type, mode=mode,
                )
                self._append_log(f"  ✓ click {i + 1}/{repeat}")
                if i < repeat - 1:
                    time.sleep(interval)
            self._append_log("  Done.")
        except Exception as e:
            self._append_log(f"  ✗ Error: {e}")
        finally:
            self._set_buttons_enabled(True)
