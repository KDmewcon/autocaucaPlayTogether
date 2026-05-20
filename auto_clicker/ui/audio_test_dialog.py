"""Dialog test match 1 audio reference với input stream live."""
from __future__ import annotations

from typing import Optional

import numpy as np
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
    QVBoxLayout,
)

from ..core.audio_matcher import (
    AudioPattern,
    AudioStreamBuffer,
    SAMPLE_RATE,
    build_pattern,
    compute_log_mel,
    match_pattern,
)
from ..core.audio_monitor import list_input_devices
from ..core.scenario import AudioRef


class AudioTestDialog(QDialog):
    """Liên tục match audio reference với mic input và hiển thị confidence."""

    def __init__(
        self,
        audio_ref: AudioRef,
        device: int = -1,
        buffer_seconds: float = 5.0,
        initial_threshold: float = 0.7,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Test Audio Match - {audio_ref.name}")
        self.resize(620, 360)
        self._ref = audio_ref
        self._buffer_seconds = max(audio_ref_min_sec(audio_ref), buffer_seconds)
        self._stream = None
        self._buffer: Optional[AudioStreamBuffer] = None
        self._pattern: Optional[AudioPattern] = None
        self._best_conf = 0.0

        layout = QVBoxLayout(self)

        head = QFormLayout()
        self.device_combo = QComboBox()
        self.device_combo.addItem("(Default input)", -1)
        for d in list_input_devices():
            self.device_combo.addItem(d.display, d.index)
        idx = self.device_combo.findData(device)
        if idx >= 0:
            self.device_combo.setCurrentIndex(idx)
        self.device_combo.currentIndexChanged.connect(self._restart_stream)
        head.addRow("Input device:", self.device_combo)

        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.3, 1.0)
        self.threshold_spin.setSingleStep(0.01)
        self.threshold_spin.setDecimals(3)
        self.threshold_spin.setValue(initial_threshold)
        head.addRow("Threshold:", self.threshold_spin)

        info_lbl = QLabel(
            f"<b>Reference:</b> {audio_ref.name}<br>"
            f"<b>Path:</b> {audio_ref.path}"
        )
        info_lbl.setWordWrap(True)
        head.addRow("", info_lbl)
        layout.addLayout(head)

        self.conf_lbl = QLabel(
            "Đang load pattern..."
        )
        self.conf_lbl.setStyleSheet(
            "font-family: Menlo, monospace; font-size: 14px; padding: 8px;"
        )
        layout.addWidget(self.conf_lbl)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        layout.addWidget(self.bar)

        self.status_lbl = QLabel("")
        layout.addWidget(self.status_lbl)

        bot = QHBoxLayout()
        bot.addStretch(1)
        close_btn = QPushButton("Đóng")
        close_btn.clicked.connect(self.accept)
        bot.addWidget(close_btn)
        layout.addLayout(bot)

        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._tick)

        self._init_pattern_and_stream()

    def _init_pattern_and_stream(self) -> None:
        # Load pattern
        try:
            self._pattern = build_pattern(
                self._ref.audio_id, self._ref.name, self._ref.path
            )
        except Exception as e:
            self.status_lbl.setText(
                f"<span style='color:#f55'>Pattern load failed: {e}</span>"
            )
            return
        self._buffer_seconds = max(
            self._buffer_seconds, self._pattern.duration_s + 1.0
        )
        self._restart_stream()
        self._timer.start()

    def _restart_stream(self) -> None:
        self._stop_stream()
        try:
            import sounddevice as sd
        except Exception as e:
            self.status_lbl.setText(
                f"<span style='color:#f55'>sounddevice không khả dụng: {e}</span>"
            )
            return

        dev = self.device_combo.currentData()
        if dev == -1:
            dev = None

        self._buffer = AudioStreamBuffer(capacity_seconds=self._buffer_seconds)

        def cb(indata, frames, ti, status):
            try:
                self._buffer.append(indata.copy())
            except Exception:
                pass

        try:
            self._stream = sd.InputStream(
                device=dev,
                channels=1,
                samplerate=SAMPLE_RATE,
                dtype="float32",
                blocksize=max(256, SAMPLE_RATE // 20),
                callback=cb,
            )
            self._stream.start()
            self.status_lbl.setText(
                "<span style='color:#5d5'>Đang nghe...</span>"
            )
            self._best_conf = 0.0
        except Exception as e:
            self.status_lbl.setText(
                f"<span style='color:#f55'>Stream error: {e}</span>"
            )
            self._stream = None
            self._buffer = None

    def _stop_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        self._stream = None
        self._buffer = None

    def _tick(self) -> None:
        if self._pattern is None or self._buffer is None:
            return
        snap = self._buffer.snapshot()
        thr = self.threshold_spin.value()
        if snap.size < SAMPLE_RATE * (self._pattern.duration_s + 0.05):
            self.conf_lbl.setText(
                f"Đang fill buffer... "
                f"({snap.size / SAMPLE_RATE:.1f}s / "
                f"{self._pattern.duration_s + 0.05:.1f}s cần)"
            )
            return
        buf_lm = compute_log_mel(snap)
        res = match_pattern(self._pattern, buf_lm, threshold=thr)
        if res.confidence > self._best_conf:
            self._best_conf = res.confidence
        self.conf_lbl.setText(
            f"Conf: {res.confidence:.4f}  |  Threshold: {thr:.3f}  |  "
            f"Best: {self._best_conf:.4f}  |  "
            f"<b style='color:{'#5d5' if res.matched else '#888'}'>"
            f"{'MATCHED' if res.matched else 'no match'}</b>"
        )
        self.bar.setValue(int(res.confidence * 1000))
        self.bar.setStyleSheet(
            "QProgressBar::chunk { background:%s; }"
            % ("#5d5" if res.matched else "#888")
        )

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self._stop_stream()
        super().closeEvent(event)


def audio_ref_min_sec(_ref: AudioRef) -> float:
    return 5.0
