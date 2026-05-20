"""Dialog hướng dẫn setup audio capture (BlackHole)."""
from __future__ import annotations

import shutil
import subprocess

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
)

from ..utils.audio_setup import (
    detect_loopback_devices,
    install_instructions_html,
    is_blackhole_installed_via_brew,
)


class AudioSetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Setup Audio Capture")
        self.resize(720, 640)

        layout = QVBoxLayout(self)

        # Status row
        status_box = QFrame()
        status_box.setFrameShape(QFrame.Shape.StyledPanel)
        status_lay = QVBoxLayout(status_box)

        loopbacks = detect_loopback_devices()
        if loopbacks:
            html = "<h3 style='color:#5d5'>✓ Phát hiện loopback driver:</h3><ul>"
            for lb in loopbacks:
                html += f"<li><b>{lb.name}</b> (vendor: {lb.vendor})</li>"
            html += "</ul>"
            html += (
                "<p>Mày đã có driver. Chỉ cần tạo Multi-Output Device "
                "và set làm system output (xem hướng dẫn dưới).</p>"
            )
        else:
            brew_v = is_blackhole_installed_via_brew()
            if brew_v:
                html = (
                    "<h3 style='color:#fa3'>⚠ BlackHole đã cài qua brew "
                    f"({brew_v}) nhưng tool chưa thấy device.</h3>"
                    "<p>Khởi động lại tool sau khi cài, hoặc reboot Mac.</p>"
                )
            else:
                html = (
                    "<h3 style='color:#fa3'>✗ Chưa có loopback driver.</h3>"
                    "<p>Tool chỉ thấy mic vật lý hoặc không có input gì cả. "
                    "Để bắt được âm thanh đang phát ra loa, cần cài "
                    "<b>BlackHole</b> hoặc tương đương.</p>"
                )

        lbl = QLabel(html)
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        status_lay.addWidget(lbl)

        # Quick action buttons
        btn_row = QHBoxLayout()
        if shutil.which("brew") is not None and not loopbacks:
            btn_install = QPushButton("📦 Cài BlackHole qua brew")
            btn_install.clicked.connect(self._install_blackhole)
            btn_row.addWidget(btn_install)

        btn_open_audio_midi = QPushButton("🎚 Mở Audio MIDI Setup")
        btn_open_audio_midi.clicked.connect(self._open_audio_midi)
        btn_row.addWidget(btn_open_audio_midi)

        btn_open_sound = QPushButton("🔊 Mở Sound Settings")
        btn_open_sound.clicked.connect(self._open_sound_settings)
        btn_row.addWidget(btn_open_sound)

        btn_download = QPushButton("🌐 Tải BlackHole")
        btn_download.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://existential.audio/blackhole/")
            )
        )
        btn_row.addWidget(btn_download)
        btn_row.addStretch(1)
        status_lay.addLayout(btn_row)

        layout.addWidget(status_box)

        # Instructions
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(install_instructions_html())
        layout.addWidget(browser, 1)

        bot = QHBoxLayout()
        bot.addStretch(1)
        close = QPushButton("Đóng")
        close.clicked.connect(self.accept)
        bot.addWidget(close)
        layout.addLayout(bot)

    def _install_blackhole(self) -> None:
        if shutil.which("brew") is None:
            QMessageBox.warning(
                self,
                "Thiếu Homebrew",
                "Không tìm thấy lệnh brew. Cài Homebrew tại "
                "https://brew.sh/ trước.",
            )
            return
        if (
            QMessageBox.question(
                self,
                "Cài BlackHole",
                "Tool sẽ chạy `brew install blackhole-2ch` trong Terminal mới. "
                "Cần password sudo. Tiếp tục?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        # Mở Terminal mới chạy lệnh để user nhập password
        script = (
            'tell application "Terminal" to do script '
            '"brew install blackhole-2ch"'
        )
        subprocess.run(["osascript", "-e", script], check=False)

    def _open_audio_midi(self) -> None:
        subprocess.run(["open", "-a", "Audio MIDI Setup"], check=False)

    def _open_sound_settings(self) -> None:
        subprocess.run(
            [
                "open",
                "x-apple.systempreferences:com.apple.preference.sound",
            ],
            check=False,
        )
