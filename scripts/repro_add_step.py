"""Reproduce crash khi mở StepEditorDialog headless."""

import faulthandler
import sys
import traceback

faulthandler.enable()

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from auto_clicker.core.scenario import ScenarioConfig, Step, StepType
from auto_clicker.ui.step_editor import StepEditorDialog


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    scenario = ScenarioConfig()
    step = Step(type=StepType.FIND_CLICK, params={})

    print("[*] Creating dialog...", flush=True)
    try:
        dlg = StepEditorDialog(step, scenario, 1, None)
        print("[*] Showing dialog...", flush=True)
        dlg.show()
        print("[*] OK, dialog shown", flush=True)
    except Exception:
        traceback.print_exc()
        return 1

    QTimer.singleShot(1500, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
