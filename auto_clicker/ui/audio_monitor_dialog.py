"""Dialog hiển thị level audio realtime + chọn input device.

Giúp user xác định threshold cho WAIT_FOR_SOUND step.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..core.audio_monitor import (
    AudioLevelMonitor,
    list_input_devices,
    rms_to_dbfs,
)


class AudioMonitorDialog(QDialog):
    """Dialog liên tục hiển thị RMS để user tinh chỉnh threshold."""

    def __init__(self, parent=None, initial_device: int = -1, initial_threshold: float = 0.05):
        super().__init__(parent)
        self.setWindowTitle("Audio Monitor")
        self.resize(560, 320)
        self._monitor: Optional[AudioLevelMonitor] = None

        layout = QVBoxLayout(self)

        head = QFormLayout()
        self.device_combo = QComboBox()
        self.device_combo.addItem("(Default input)", -1)
        self._devices = list_input_devices()
        for d in self._devices:
            self.device_combo.addItem(d.display, d.index)
        idx = self.device_combo.findData(initial_device)
        if idx >= 0:
            self.device_combo.setCurrentIndex(idx)
        self.device_combo.currentIndexChanged.connect(self._restart_monitor)
        head.addRow("Input device:", self.device_combo)

        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.001, 1.0)
        self.threshold_spin.setDecimals(4)
        self.threshold_spin.setSingleStep(0.005)
        self.threshold_spin.setValue(initial_threshold)
        head.addRow("Threshold (RMS):", self.threshold_spin)

        layout.addLayout(head)

        if not self._devices:
            warn = QLabel(
                "<span style='color:#fa3'>Không có input device. "
                "Hãy cắm mic, dùng AirPods, hoặc cài <b>BlackHole</b> "
                "(Tools → Setup Audio Capture) để bắt được âm thanh "
                "đang phát ra loa.</span>"
            )
            warn.setWordWrap(True)
            layout.addWidget(warn)
        else:
            tip = QLabel(
                "<i>Lưu ý: macOS chỉ cho capture <b>input device</b>. "
                "Để 'nghe' được audio đang phát ra loa, chọn loopback "
                "device như <b>BlackHole 2ch</b> ở trên (cần setup "
                "Multi-Output, xem Tools → Setup Audio Capture).</i>"
            )
            tip.setWordWrap(True)
            layout.addWidget(tip)

        # Level meters
        self.rms_lbl = QLabel("RMS: 0.0000  |  dBFS: -∞")
        self.rms_lbl.setStyleSheet("font-family: Menlo, monospace; font-size: 13px;")
        layout.addWidget(self.rms_lbl)

        self.rms_bar = QProgressBar()
        self.rms_bar.setRange(0, 1000)
        self.rms_bar.setFormat("RMS")
        layout.addWidget(self.rms_bar)

        self.peak_bar = QProgressBar()
        self.peak_bar.setRange(0, 1000)
        self.peak_bar.setFormat("Peak")
        layout.addWidget(self.peak_bar)

        self.threshold_bar_lbl = QLabel("Threshold:")
        layout.addWidget(self.threshold_bar_lbl)

        self.status_lbl = QLabel("...")
        layout.addWidget(self.status_lbl)

        # Bottom buttons
        bot = QHBoxLayout()
        bot.addStretch(1)
        close_btn = QPushButton("Đóng")
        close_btn.clicked.connect(self.accept)
        bot.addWidget(close_btn)
        layout.addLayout(bot)

        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._restart_monitor()

    def _restart_monitor(self) -> None:
        if self._monitor is not None:
            self._monitor.stop()
            self._monitor = None
        dev = self.device_combo.currentData()
        if dev == -1:
            dev = None
        self._monitor = AudioLevelMonitor(device=dev)
        if not self._monitor.start():
            self.status_lbl.setText(
                f"<span style='color:#f55'>Lỗi: {self._monitor.error}</span>"
            )
            self._monitor = None
        else:
            self.status_lbl.setText(
                "<span style='color:#5d5'>Đang nghe...</span>"
            )

    def _tick(self) -> None:
        if self._monitor is None:
            return
        rms = self._monitor.rms
        peak = self._monitor.peak
        thr = self.threshold_spin.value()
        self.rms_lbl.setText(
            f"RMS: {rms:.4f}  |  dBFS: {rms_to_dbfs(rms):.1f}  "
            f"|  Peak: {peak:.4f}  ({'TRIGGER' if rms >= thr else 'silent'})"
        )
        self.rms_bar.setValue(int(min(1.0, rms) * 1000))
        self.peak_bar.setValue(int(min(1.0, peak) * 1000))
        # Color code
        if rms >= thr:
            self.rms_bar.setStyleSheet(
                "QProgressBar::chunk { background:#5d5; }"
            )
        else:
            self.rms_bar.setStyleSheet(
                "QProgressBar::chunk { background:#888; }"
            )

    @property
    def selected_device(self) -> int:
        return self.device_combo.currentData()

    @property
    def selected_threshold(self) -> float:
        return self.threshold_spin.value()

    def closeEvent(self, event) -> None:
        if self._monitor is not None:
            self._monitor.stop()
            self._monitor = None
        super().closeEvent(event)
