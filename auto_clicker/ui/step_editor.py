"""Dialog edit 1 step trong scenario."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.click_engine import ClickMode, ClickType
from ..core.scenario import ScenarioConfig, Step, StepType


_TYPE_LABELS = {
    StepType.FIND_CLICK: "Find & Click - tìm template, click nếu thấy",
    StepType.WAIT_FOR: "Wait For - chờ template xuất hiện",
    StepType.WAIT_GONE: "Wait Gone - chờ template biến mất",
    StepType.SLEEP: "Sleep - ngủ N giây",
    StepType.IF_FOUND_GOTO: "If Found Goto - nếu thấy template thì nhảy",
    StepType.IF_NOT_FOUND_GOTO: "If NotFound Goto - nếu không thấy thì nhảy",
    StepType.GOTO: "Goto - nhảy đến step số",
    StepType.LOOP_START: "Loop Start - mở vòng lặp",
    StepType.LOOP_END: "Loop End - đóng vòng lặp",
    StepType.ACTIVATE: "Activate - bring window to front",
    StepType.LOG: "Log - ghi log message",
    StepType.STOP: "Stop - dừng scenario",
}


class StepEditorDialog(QDialog):
    def __init__(
        self,
        step: Step,
        scenario: ScenarioConfig,
        total_steps: int,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Sửa step")
        self.resize(520, 460)
        self._scenario = scenario
        self._total_steps = total_steps
        self._step = Step(
            type=step.type,
            enabled=step.enabled,
            params=dict(step.params),
            step_id=step.step_id,
        )

        layout = QVBoxLayout(self)

        head = QFormLayout()
        self.type_combo = QComboBox()
        for t, label in _TYPE_LABELS.items():
            self.type_combo.addItem(label, t)
        idx = self.type_combo.findData(self._step.type)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        head.addRow("Loại step:", self.type_combo)

        self.enabled_chk = QCheckBox("Bật step này")
        self.enabled_chk.setChecked(self._step.enabled)
        head.addRow("", self.enabled_chk)
        layout.addLayout(head)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        # Build all panels
        self._panels: dict[StepType, QWidget] = {}
        self._fields: dict[str, dict] = {}  # type_value -> field map

        self._build_find_click_panel()
        self._build_wait_panel(StepType.WAIT_FOR)
        self._build_wait_panel(StepType.WAIT_GONE)
        self._build_sleep_panel()
        self._build_if_found_panel(StepType.IF_FOUND_GOTO)
        self._build_if_found_panel(StepType.IF_NOT_FOUND_GOTO)
        self._build_goto_panel()
        self._build_loop_start_panel()
        self._build_empty_panel(StepType.LOOP_END)
        self._build_empty_panel(StepType.ACTIVATE)
        self._build_log_panel()
        self._build_empty_panel(StepType.STOP)

        self._on_type_changed()

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    # ---------- panel builders
    def _add_panel(self, t: StepType, w: QWidget) -> None:
        self._panels[t] = w
        self.stack.addWidget(w)

    def _template_combo(self, current_id: str = "") -> QComboBox:
        c = QComboBox()
        c.addItem("(chưa chọn)", "")
        for ref in self._scenario.templates:
            c.addItem(ref.name, ref.template_id)
        idx = c.findData(current_id or "")
        if idx >= 0:
            c.setCurrentIndex(idx)
        return c

    def _step_target_spin(self, current: int = 0) -> QSpinBox:
        s = QSpinBox()
        s.setRange(1, max(1, self._total_steps))
        s.setValue(int(current) + 1)
        s.setSuffix(f"  /  {self._total_steps}")
        return s

    def _build_find_click_panel(self) -> None:
        w = QWidget()
        f = QFormLayout(w)
        p = self._step.params if self._step.type == StepType.FIND_CLICK else {}
        tpl = self._template_combo(p.get("template_id", ""))
        thr = QDoubleSpinBox()
        thr.setRange(0.5, 1.0)
        thr.setDecimals(2)
        thr.setSingleStep(0.01)
        thr.setSpecialValueText("(default)")
        thr.setValue(float(p.get("threshold") or 0.5))
        if not p.get("threshold"):
            thr.setValue(0.5)  # special

        click_t = QComboBox()
        click_t.addItem("(default)", "")
        for ct in ClickType:
            click_t.addItem(ct.value, ct.value)
        idx = click_t.findData(p.get("click_type", "") or "")
        click_t.setCurrentIndex(idx if idx >= 0 else 0)

        click_m = QComboBox()
        click_m.addItem("(default)", "")
        click_m.addItem("HID + Restore cursor", ClickMode.HID_RESTORE.value)
        click_m.addItem("Post tới PID", ClickMode.PID_POSTED.value)
        click_m.addItem("HID Tap", ClickMode.HID_TAP.value)
        idx = click_m.findData(p.get("click_mode", "") or "")
        click_m.setCurrentIndex(idx if idx >= 0 else 0)

        ox = QSpinBox()
        ox.setRange(-2000, 2000)
        ox.setValue(int(p.get("offset_x", 0)))
        oy = QSpinBox()
        oy.setRange(-2000, 2000)
        oy.setValue(int(p.get("offset_y", 0)))
        jit = QSpinBox()
        jit.setRange(0, 50)
        jit.setValue(int(p.get("jitter_px", 2)))
        jit.setSuffix(" px")

        f.addRow("Template:", tpl)
        f.addRow("Threshold:", thr)
        f.addRow("Click type:", click_t)
        f.addRow("Click mode:", click_m)
        f.addRow("Offset X:", ox)
        f.addRow("Offset Y:", oy)
        f.addRow("Jitter:", jit)

        self._fields[StepType.FIND_CLICK.value] = {
            "template_id": tpl,
            "threshold": thr,
            "click_type": click_t,
            "click_mode": click_m,
            "offset_x": ox,
            "offset_y": oy,
            "jitter_px": jit,
        }
        self._add_panel(StepType.FIND_CLICK, w)

    def _build_wait_panel(self, t: StepType) -> None:
        w = QWidget()
        f = QFormLayout(w)
        p = self._step.params if self._step.type == t else {}
        tpl = self._template_combo(p.get("template_id", ""))
        thr = QDoubleSpinBox()
        thr.setRange(0.5, 1.0)
        thr.setDecimals(2)
        thr.setSingleStep(0.01)
        thr.setSpecialValueText("(default)")
        thr.setValue(float(p.get("threshold") or 0.5))
        timeout = QDoubleSpinBox()
        timeout.setRange(0.5, 3600)
        timeout.setSuffix(" s")
        timeout.setValue(float(p.get("timeout", 10)))
        poll = QDoubleSpinBox()
        poll.setRange(0.05, 10)
        poll.setSingleStep(0.05)
        poll.setValue(float(p.get("poll_interval", 0.5)))
        poll.setSuffix(" s")

        on_to = QComboBox()
        on_to.addItem("Tiếp tục step kế", "next")
        on_to.addItem("Dừng scenario", "stop")
        on_to.addItem("Goto step...", "goto")
        idx = on_to.findData(p.get("on_timeout", "next"))
        on_to.setCurrentIndex(idx if idx >= 0 else 0)

        target = self._step_target_spin(int(p.get("on_timeout_target", 0)))

        f.addRow("Template:", tpl)
        f.addRow("Threshold:", thr)
        f.addRow("Timeout:", timeout)
        f.addRow("Poll interval:", poll)
        f.addRow("Khi timeout:", on_to)
        f.addRow("Goto step:", target)

        self._fields[t.value] = {
            "template_id": tpl,
            "threshold": thr,
            "timeout": timeout,
            "poll_interval": poll,
            "on_timeout": on_to,
            "on_timeout_target": target,
        }
        self._add_panel(t, w)

    def _build_sleep_panel(self) -> None:
        w = QWidget()
        f = QFormLayout(w)
        p = self._step.params if self._step.type == StepType.SLEEP else {}
        secs = QDoubleSpinBox()
        secs.setRange(0.05, 3600)
        secs.setSingleStep(0.1)
        secs.setSuffix(" s")
        secs.setValue(float(p.get("seconds", 1.0)))
        f.addRow("Thời gian:", secs)
        self._fields[StepType.SLEEP.value] = {"seconds": secs}
        self._add_panel(StepType.SLEEP, w)

    def _build_if_found_panel(self, t: StepType) -> None:
        w = QWidget()
        f = QFormLayout(w)
        p = self._step.params if self._step.type == t else {}
        tpl = self._template_combo(p.get("template_id", ""))
        thr = QDoubleSpinBox()
        thr.setRange(0.5, 1.0)
        thr.setDecimals(2)
        thr.setSingleStep(0.01)
        thr.setValue(float(p.get("threshold") or 0.5))
        target = self._step_target_spin(int(p.get("target", 0)))
        f.addRow("Template:", tpl)
        f.addRow("Threshold:", thr)
        f.addRow("Goto step:", target)
        self._fields[t.value] = {
            "template_id": tpl,
            "threshold": thr,
            "target": target,
        }
        self._add_panel(t, w)

    def _build_goto_panel(self) -> None:
        w = QWidget()
        f = QFormLayout(w)
        p = self._step.params if self._step.type == StepType.GOTO else {}
        target = self._step_target_spin(int(p.get("target", 0)))
        f.addRow("Goto step:", target)
        self._fields[StepType.GOTO.value] = {"target": target}
        self._add_panel(StepType.GOTO, w)

    def _build_loop_start_panel(self) -> None:
        w = QWidget()
        f = QFormLayout(w)
        p = self._step.params if self._step.type == StepType.LOOP_START else {}
        cnt = QSpinBox()
        cnt.setRange(0, 1_000_000)
        cnt.setSpecialValueText("Vô hạn")
        cnt.setValue(int(p.get("count", 0)))
        f.addRow("Số lần lặp:", cnt)
        self._fields[StepType.LOOP_START.value] = {"count": cnt}
        self._add_panel(StepType.LOOP_START, w)

    def _build_log_panel(self) -> None:
        w = QWidget()
        f = QFormLayout(w)
        p = self._step.params if self._step.type == StepType.LOG else {}
        msg = QLineEdit()
        msg.setText(str(p.get("message", "")))
        f.addRow("Message:", msg)
        self._fields[StepType.LOG.value] = {"message": msg}
        self._add_panel(StepType.LOG, w)

    def _build_empty_panel(self, t: StepType) -> None:
        w = QWidget()
        QFormLayout(w)  # empty form
        self._fields[t.value] = {}
        self._add_panel(t, w)

    # ---------- logic
    def _on_type_changed(self) -> None:
        t: StepType = self.type_combo.currentData()
        if t is None:
            return
        w = self._panels.get(t)
        if w is not None:
            self.stack.setCurrentWidget(w)

    def _collect(self) -> dict:
        t: StepType = self.type_combo.currentData()
        fields = self._fields.get(t.value, {})
        out: dict = {}
        for key, widget in fields.items():
            if isinstance(widget, QComboBox):
                data = widget.currentData()
                if data is None or data == "":
                    continue
                out[key] = data
            elif isinstance(widget, QDoubleSpinBox):
                # threshold "(default)" sentinel = 0.5 nhưng user có thể chọn 0.5 thật
                # Để đơn giản: nếu key là threshold và value < 0.51 thì coi là default
                if key == "threshold" and widget.value() <= 0.50:
                    continue
                out[key] = float(widget.value())
            elif isinstance(widget, QSpinBox):
                if key in ("target", "on_timeout_target"):
                    out[key] = int(widget.value()) - 1  # convert về 0-based
                else:
                    out[key] = int(widget.value())
            elif isinstance(widget, QLineEdit):
                out[key] = widget.text()
        return out

    def _accept(self) -> None:
        t: StepType = self.type_combo.currentData()
        self._step.type = t
        self._step.enabled = self.enabled_chk.isChecked()
        self._step.params = self._collect()
        self.accept()

    @property
    def step(self) -> Step:
        return self._step
