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
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.click_engine import ClickMode, ClickType
from ..core.scenario import ScenarioConfig, Step, StepType


_TYPE_LABELS = {
    StepType.FIND_CLICK: "Find & Click - tìm template, click nếu thấy",
    StepType.CLICK_AT: "Click At - click vào toạ độ cố định trong window",
    StepType.WAIT_FOR: "Wait For - chờ template xuất hiện",
    StepType.WAIT_GONE: "Wait Gone - chờ template biến mất",
    StepType.WAIT_FOR_SOUND: "Wait For Sound - chờ âm thanh capture qua loopback (đo RMS)",
    StepType.WAIT_FOR_AUDIO: "Wait For Audio Pattern - chờ đoạn mp3/wav xuất hiện qua loopback",
    StepType.WAIT_ANY: "Wait Any - chờ song song nhiều thứ, ai trigger trước thì goto",
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
        self.resize(560, 520)
        self._scenario = scenario
        self._total_steps = total_steps
        self._step = Step(
            type=step.type,
            enabled=step.enabled,
            params=dict(step.params),
            step_id=step.step_id,
            name=step.name,
        )

        layout = QVBoxLayout(self)

        head = QFormLayout()

        # Tên step
        self.name_edit = QLineEdit()
        self.name_edit.setText(self._step.name)
        self.name_edit.setPlaceholderText("(tự động theo loại)")
        head.addRow("Tên step:", self.name_edit)

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
        self._build_click_at_panel()
        self._build_wait_panel(StepType.WAIT_FOR)
        self._build_wait_panel(StepType.WAIT_GONE)
        self._build_wait_for_sound_panel()
        self._build_wait_for_audio_panel()
        self._build_wait_any_panel()
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

        # "Bước kế tiếp" footer (áp dụng cho mọi step trừ goto/if/loop_end/stop)
        next_form = QFormLayout()
        p = self._step.params
        self.next_step_combo = QComboBox()
        self.next_step_combo.addItem("(mặc định: step liền sau)", "")
        for i, s in enumerate(self._scenario.steps):
            type_short = s.type.value.replace("_", " ").title()
            display = s.name.strip() if s.name.strip() else type_short
            label = f"#{i + 1}  ·  {display}"
            self.next_step_combo.addItem(label, s.step_id)
        # restore
        next_id = p.get("next_step_id", "")
        if next_id:
            for i in range(self.next_step_combo.count()):
                if self.next_step_combo.itemData(i) == next_id:
                    self.next_step_combo.setCurrentIndex(i)
                    break
        next_form.addRow("Bước kế tiếp:", self.next_step_combo)
        layout.addLayout(next_form)

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
        seen: set[str] = set()
        # Library trước
        try:
            from ..core.media_library import MediaLibrary
            for ref in MediaLibrary.instance().list_templates():
                if ref.template_id in seen:
                    continue
                seen.add(ref.template_id)
                c.addItem(f"📚 {ref.name}", ref.template_id)
        except Exception:
            pass
        # Scenario-local sau
        for ref in self._scenario.templates:
            if ref.template_id in seen:
                continue
            seen.add(ref.template_id)
            c.addItem(f"🔒 {ref.name}", ref.template_id)
        idx = c.findData(current_id or "")
        if idx >= 0:
            c.setCurrentIndex(idx)
        return c

    def _audio_combo(self, current_id: str = "") -> QComboBox:
        c = QComboBox()
        c.addItem("(chưa chọn)", "")
        seen: set[str] = set()
        try:
            from ..core.media_library import MediaLibrary
            for ref in MediaLibrary.instance().list_audios():
                if ref.audio_id in seen:
                    continue
                seen.add(ref.audio_id)
                c.addItem(f"📚 {ref.name}", ref.audio_id)
        except Exception:
            pass
        for ref in self._scenario.audios:
            if ref.audio_id in seen:
                continue
            seen.add(ref.audio_id)
            c.addItem(f"🔒 {ref.name}", ref.audio_id)
        idx = c.findData(current_id or "")
        if idx >= 0:
            c.setCurrentIndex(idx)
        return c

    def _step_target_spin(self, current: int = 0) -> QSpinBox:
        """Legacy spinbox dạng số. Vẫn giữ cho backward-compat (Goto, IF_FOUND_GOTO)."""
        s = QSpinBox()
        s.setRange(1, max(1, self._total_steps))
        s.setValue(int(current) + 1)
        s.setSuffix(f"  /  {self._total_steps}")
        return s

    def _step_target_combo(self, current_id: str = "", current_idx: int = -1) -> QComboBox:
        """Combo liệt kê tên steps theo step_id (an toàn khi thêm/xóa).

        - current_id: ưu tiên match theo step_id
        - current_idx: fallback theo vị trí (cho file legacy)
        """
        c = QComboBox()
        steps = self._scenario.steps
        for i, s in enumerate(steps):
            type_short = s.type.value.replace("_", " ").title()
            label = f"#{i + 1}  ·  {type_short}"
            # Mô tả ngắn cho dễ nhận
            try:
                desc = s.label(self._scenario.template_name_map())
                if desc:
                    desc_short = desc[:42]
                    label += f"  ·  {desc_short}"
            except Exception:
                pass
            c.addItem(label, s.step_id)

        # Match theo step_id trước
        idx_to_select = -1
        if current_id:
            for i in range(c.count()):
                if c.itemData(i) == current_id:
                    idx_to_select = i
                    break
        # Fallback theo idx
        if idx_to_select < 0 and 0 <= current_idx < c.count():
            idx_to_select = current_idx
        if idx_to_select >= 0:
            c.setCurrentIndex(idx_to_select)
        elif c.count() > 0:
            c.setCurrentIndex(0)
        return c

    def _build_action_combo(self, current: str = "next") -> QComboBox:
        """Combo: next | stop | goto - dùng cho on_found / on_timeout / etc."""
        c = QComboBox()
        c.addItem("Tiếp tục step kế", "next")
        c.addItem("Dừng scenario", "stop")
        c.addItem("Goto step...", "goto")
        idx = c.findData(current)
        c.setCurrentIndex(idx if idx >= 0 else 0)
        return c

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

    def _build_click_at_panel(self) -> None:
        w = QWidget()
        f = QFormLayout(w)
        p = self._step.params if self._step.type == StepType.CLICK_AT else {}

        unit = QComboBox()
        unit.addItem("Point (pixel point trong window)", "point")
        unit.addItem("Phần trăm (% theo width/height)", "percent")
        idx = unit.findData(p.get("unit", "point"))
        unit.setCurrentIndex(idx if idx >= 0 else 0)

        x = QDoubleSpinBox()
        x.setRange(-10000, 10000)
        x.setDecimals(2)
        x.setValue(float(p.get("x", 0)))
        y = QDoubleSpinBox()
        y.setRange(-10000, 10000)
        y.setDecimals(2)
        y.setValue(float(p.get("y", 0)))

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

        jit = QSpinBox()
        jit.setRange(0, 50)
        jit.setValue(int(p.get("jitter_px", 0)))
        jit.setSuffix(" px")

        # Pick button - mở dialog chọn tọa độ trên screenshot
        pick_btn = QPushButton("📍  Chọn vị trí trên window...")

        def _pick():
            self._open_position_picker(x, y, unit)

        pick_btn.clicked.connect(_pick)

        f.addRow("Đơn vị:", unit)
        f.addRow("X:", x)
        f.addRow("Y:", y)
        f.addRow("", pick_btn)
        f.addRow("Click type:", click_t)
        f.addRow("Click mode:", click_m)
        f.addRow("Jitter:", jit)

        self._fields[StepType.CLICK_AT.value] = {
            "unit": unit,
            "x": x,
            "y": y,
            "click_type": click_t,
            "click_mode": click_m,
            "jitter_px": jit,
        }
        self._add_panel(StepType.CLICK_AT, w)

    def _build_wait_for_sound_panel(self) -> None:
        w = QWidget()
        f = QFormLayout(w)
        p = (
            self._step.params
            if self._step.type == StepType.WAIT_FOR_SOUND
            else {}
        )

        # Device chooser
        device = QComboBox()
        device.addItem("(Default input)", -1)
        try:
            from ..core.audio_monitor import list_input_devices
            for d in list_input_devices():
                device.addItem(d.display, d.index)
        except Exception:
            pass
        saved_dev = p.get("device", -1)
        try:
            saved_dev_int = int(saved_dev) if saved_dev is not None else -1
        except (TypeError, ValueError):
            saved_dev_int = -1
        idx_dev = device.findData(saved_dev_int)
        device.setCurrentIndex(idx_dev if idx_dev >= 0 else 0)

        thr = QDoubleSpinBox()
        thr.setRange(0.001, 1.0)
        thr.setDecimals(4)
        thr.setSingleStep(0.005)
        thr.setSpecialValueText("(default)")
        thr.setValue(float(p.get("threshold") or 0.001))
        timeout = QDoubleSpinBox()
        timeout.setRange(0.5, 36000)
        timeout.setDecimals(1)
        timeout.setSuffix(" s")
        timeout.setValue(float(p.get("timeout", 30.0)))
        sustain = QSpinBox()
        sustain.setRange(0, 5000)
        sustain.setValue(int(p.get("sustain_ms") or 100))
        sustain.setSuffix(" ms")
        sustain.setSpecialValueText("(default)")

        on_to = self._build_action_combo(p.get("on_timeout", "next"))
        target = self._step_target_combo(
            p.get("on_timeout_target_id", ""),
            int(p.get("on_timeout_target", -1)),
        )

        on_found_combo = self._build_action_combo(p.get("on_found", "next"))
        on_found_target = self._step_target_combo(
            p.get("on_found_target_id", ""),
            int(p.get("on_found_target", -1)),
        )

        helper = QLabel(
            "<i>Tool đo RMS từ <b>input device</b> đã chọn ở trên. "
            "Để 'nghe' audio đang phát ra loa, chọn loopback device như "
            "<b>BlackHole 2ch</b> (cần cài và setup Multi-Output, vào "
            "Tools → Setup Audio Capture xem hướng dẫn).</i>"
        )
        helper.setWordWrap(True)

        f.addRow("Input device:", device)
        f.addRow("Threshold (RMS):", thr)
        f.addRow("Sustain:", sustain)
        f.addRow("Timeout:", timeout)
        f.addRow("Khi phát hiện:", on_found_combo)
        f.addRow("  Goto step:", on_found_target)
        f.addRow("Khi timeout:", on_to)
        f.addRow("  Goto step:", target)
        f.addRow("", helper)

        self._fields[StepType.WAIT_FOR_SOUND.value] = {
            "device": device,
            "threshold": thr,
            "sustain_ms": sustain,
            "timeout": timeout,
            "on_found": on_found_combo,
            "on_found_target_id": on_found_target,
            "on_timeout": on_to,
            "on_timeout_target_id": target,
        }
        self._add_panel(StepType.WAIT_FOR_SOUND, w)

    def _build_wait_for_audio_panel(self) -> None:
        w = QWidget()
        f = QFormLayout(w)
        p = (
            self._step.params
            if self._step.type == StepType.WAIT_FOR_AUDIO
            else {}
        )
        audio = self._audio_combo(p.get("audio_id", ""))

        # Device chooser (giống WAIT_FOR_SOUND)
        device = QComboBox()
        device.addItem("(Default input)", -1)
        try:
            from ..core.audio_monitor import list_input_devices
            for d in list_input_devices():
                device.addItem(d.display, d.index)
        except Exception:
            pass
        saved_dev = p.get("device", -1)
        try:
            saved_dev_int = int(saved_dev) if saved_dev is not None else -1
        except (TypeError, ValueError):
            saved_dev_int = -1
        idx_dev = device.findData(saved_dev_int)
        device.setCurrentIndex(idx_dev if idx_dev >= 0 else 0)

        thr = QDoubleSpinBox()
        thr.setRange(0.3, 1.0)
        thr.setDecimals(3)
        thr.setSingleStep(0.01)
        thr.setSpecialValueText("(default)")
        thr.setValue(float(p.get("threshold") or 0.3))

        timeout = QDoubleSpinBox()
        timeout.setRange(0.5, 36000)
        timeout.setDecimals(1)
        timeout.setSuffix(" s")
        timeout.setValue(float(p.get("timeout", 30.0)))

        poll = QDoubleSpinBox()
        poll.setRange(0.05, 5)
        poll.setSingleStep(0.05)
        poll.setSuffix(" s")
        poll.setValue(float(p.get("poll_interval", 0.2)))

        on_to = self._build_action_combo(p.get("on_timeout", "next"))
        target = self._step_target_combo(
            p.get("on_timeout_target_id", ""),
            int(p.get("on_timeout_target", -1)),
        )

        on_found_combo = self._build_action_combo(p.get("on_found", "next"))
        on_found_target = self._step_target_combo(
            p.get("on_found_target_id", ""),
            int(p.get("on_found_target", -1)),
        )

        helper = QLabel(
            "<i>Tool so khớp đoạn audio reference với <b>input device</b>. "
            "macOS không cho capture output trực tiếp - cần cài "
            "<b>BlackHole</b> + Multi-Output Device để 'nghe' được audio "
            "đang phát. Vào Tools → Setup Audio Capture xem hướng dẫn. "
            "Threshold ~0.55-0.65 thường ổn.</i>"
        )
        helper.setWordWrap(True)

        f.addRow("Audio reference:", audio)
        f.addRow("Input device:", device)
        f.addRow("Threshold (similarity):", thr)
        f.addRow("Timeout:", timeout)
        f.addRow("Poll interval:", poll)
        f.addRow("Khi phát hiện:", on_found_combo)
        f.addRow("  Goto step:", on_found_target)
        f.addRow("Khi timeout:", on_to)
        f.addRow("  Goto step:", target)
        f.addRow("", helper)

        self._fields[StepType.WAIT_FOR_AUDIO.value] = {
            "audio_id": audio,
            "device": device,
            "threshold": thr,
            "timeout": timeout,
            "poll_interval": poll,
            "on_found": on_found_combo,
            "on_found_target_id": on_found_target,
            "on_timeout": on_to,
            "on_timeout_target_id": target,
        }
        self._add_panel(StepType.WAIT_FOR_AUDIO, w)

    def _build_wait_any_panel(self) -> None:
        from .wait_any_editor import WaitAnyBranchesWidget

        w = QWidget()
        f = QFormLayout(w)
        p = self._step.params if self._step.type == StepType.WAIT_ANY else {}

        timeout = QDoubleSpinBox()
        timeout.setRange(0.5, 36000)
        timeout.setDecimals(1)
        timeout.setSuffix(" s")
        timeout.setValue(float(p.get("timeout", 30.0)))

        poll = QDoubleSpinBox()
        poll.setRange(0.05, 5)
        poll.setSingleStep(0.05)
        poll.setSuffix(" s")
        poll.setValue(float(p.get("poll_interval", 0.2)))

        on_to = self._build_action_combo(p.get("on_timeout", "next"))
        target = self._step_target_combo(
            p.get("on_timeout_target_id", ""),
            int(p.get("on_timeout_target", -1)),
        )

        branches_widget = WaitAnyBranchesWidget(
            self._scenario, self._total_steps,
            initial_branches=p.get("branches") or [],
        )

        f.addRow("Timeout:", timeout)
        f.addRow("Poll interval:", poll)
        f.addRow("Khi timeout:", on_to)
        f.addRow("Goto step:", target)
        f.addRow(branches_widget)

        self._fields[StepType.WAIT_ANY.value] = {
            "timeout": timeout,
            "poll_interval": poll,
            "on_timeout": on_to,
            "on_timeout_target_id": target,
            "_branches_widget": branches_widget,
        }
        self._add_panel(StepType.WAIT_ANY, w)

    def _open_position_picker(self, x_field, y_field, unit_field) -> None:
        """Lấy ScenarioConfig để biết window_id, mở dialog cho user click."""
        from .position_picker import PositionPickerDialog

        win_id = self._scenario.window_id
        if not win_id:
            QMessageBox.warning(
                self, "Chưa có window", "Hãy chọn window target trước."
            )
            return
        dlg = PositionPickerDialog(win_id, self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        if dlg.picked is None:
            return
        local_x_pt, local_y_pt, win_w, win_h = dlg.picked
        unit = unit_field.currentData()
        if unit == "percent":
            x_field.setValue(local_x_pt / win_w * 100.0 if win_w else 0)
            y_field.setValue(local_y_pt / win_h * 100.0 if win_h else 0)
        else:
            x_field.setValue(local_x_pt)
            y_field.setValue(local_y_pt)

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

        on_to = self._build_action_combo(p.get("on_timeout", "next"))
        target = self._step_target_combo(
            p.get("on_timeout_target_id", ""),
            int(p.get("on_timeout_target", -1)),
        )

        on_found_combo = self._build_action_combo(p.get("on_found", "next"))
        on_found_target = self._step_target_combo(
            p.get("on_found_target_id", ""),
            int(p.get("on_found_target", -1)),
        )

        f.addRow("Template:", tpl)
        f.addRow("Threshold:", thr)
        f.addRow("Timeout:", timeout)
        f.addRow("Poll interval:", poll)
        f.addRow("Khi phát hiện:", on_found_combo)
        f.addRow("  Goto step:", on_found_target)
        f.addRow("Khi timeout:", on_to)
        f.addRow("  Goto step:", target)

        self._fields[t.value] = {
            "template_id": tpl,
            "threshold": thr,
            "timeout": timeout,
            "poll_interval": poll,
            "on_found": on_found_combo,
            "on_found_target_id": on_found_target,
            "on_timeout": on_to,
            "on_timeout_target_id": target,
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
        target = self._step_target_combo(
            p.get("target_id", ""),
            int(p.get("target", -1)),
        )
        f.addRow("Template:", tpl)
        f.addRow("Threshold:", thr)
        f.addRow("Goto step:", target)
        self._fields[t.value] = {
            "template_id": tpl,
            "threshold": thr,
            "target_id": target,
        }
        self._add_panel(t, w)

    def _build_goto_panel(self) -> None:
        w = QWidget()
        f = QFormLayout(w)
        p = self._step.params if self._step.type == StepType.GOTO else {}
        target = self._step_target_combo(
            p.get("target_id", ""),
            int(p.get("target", -1)),
        )
        f.addRow("Goto step:", target)
        self._fields[StepType.GOTO.value] = {"target_id": target}
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
        t = self.type_combo.currentData()
        if t is None:
            return
        if isinstance(t, str):
            t = StepType(t)
        w = self._panels.get(t)
        if w is not None:
            self.stack.setCurrentWidget(w)

    def _collect(self) -> dict:
        t = self.type_combo.currentData()
        if isinstance(t, str):
            t = StepType(t)
        fields = self._fields.get(t.value, {})
        out: dict = {}
        # Branches widget riêng (cho WAIT_ANY)
        from .wait_any_editor import WaitAnyBranchesWidget

        for key, widget in fields.items():
            if key.startswith("_"):
                # Special: branches widget
                if isinstance(widget, WaitAnyBranchesWidget):
                    out["branches"] = widget.collect()
                continue
            if isinstance(widget, QComboBox):
                data = widget.currentData()
                if data is None or data == "":
                    continue
                out[key] = data
            elif isinstance(widget, QDoubleSpinBox):
                # threshold sentinels:
                # - WAIT_FOR_SOUND: <=0.001 = use default
                # - WAIT_FOR_AUDIO: <=0.301 = use default
                # - khác (image): <=0.50 = use default
                if key == "threshold":
                    if t == StepType.WAIT_FOR_SOUND and widget.value() <= 0.001:
                        continue
                    if (
                        t == StepType.WAIT_FOR_AUDIO
                        and widget.value() <= 0.301
                    ):
                        continue
                    if (
                        t not in (StepType.WAIT_FOR_SOUND, StepType.WAIT_FOR_AUDIO)
                        and widget.value() <= 0.50
                    ):
                        continue
                out[key] = float(widget.value())
            elif isinstance(widget, QSpinBox):
                # sustain_ms 0 = default
                if key == "sustain_ms" and widget.value() == 0:
                    continue
                if key in ("target", "on_timeout_target", "on_found_target"):
                    out[key] = int(widget.value()) - 1  # convert về 0-based
                else:
                    out[key] = int(widget.value())
            elif isinstance(widget, QLineEdit):
                out[key] = widget.text()
        return out

    def _accept(self) -> None:
        t = self.type_combo.currentData()
        if isinstance(t, str):
            t = StepType(t)
        self._step.type = t
        self._step.enabled = self.enabled_chk.isChecked()
        self._step.name = self.name_edit.text().strip()
        params = self._collect()
        # Lưu next_step_id (rỗng = dùng mặc định)
        next_id = self.next_step_combo.currentData()
        if next_id:
            params["next_step_id"] = next_id
        else:
            params.pop("next_step_id", None)
        self._step.params = params
        self.accept()

    @property
    def step(self) -> Step:
        return self._step
