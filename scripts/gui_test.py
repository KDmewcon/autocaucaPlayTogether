"""Test dựng MainWindow ở offscreen mode để bắt lỗi build UI."""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from auto_clicker.ui.main_window import MainWindow


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win.show()
    print(f"  ✓ MainWindow created, size {win.size().width()}x{win.size().height()}")
    print(f"  ✓ Window list: {win.window_list.count()} items rendered")

    # Quit sau 1.5s để timer/automation không leak
    QTimer.singleShot(1500, app.quit)
    code = app.exec()
    print(f"  ✓ Event loop exited cleanly (code={code})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
