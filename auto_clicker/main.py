"""Entry point cho Auto Clicker."""
from __future__ import annotations

import faulthandler
import signal
import sys

# Bật faulthandler để in Python stack khi segfault
faulthandler.enable()

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow


def main() -> int:
    # Cho phép Ctrl+C ở terminal kill app sạch sẽ
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("AutoClicker")
    app.setOrganizationName("AutoClicker")

    # Dark style nhẹ
    app.setStyleSheet(
        """
        QGroupBox {
            font-weight: 600;
            border: 1px solid #444;
            border-radius: 6px;
            margin-top: 10px;
            padding-top: 8px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px;
        }
        """
    )

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
