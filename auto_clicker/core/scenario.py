"""Scenario engine - script-based auto click.

Scenario gồm 1 list các Step. Mỗi Step có type + params. Engine chạy tuần tự
nhưng hỗ trợ jump (goto / if_found_goto / if_not_found_goto), loop, sleep,
wait_for v.v...
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np

from .click_engine import ClickEngine, ClickMode, ClickType
from .image_matcher import ImageMatcher, MatchResult
from .window_manager import WindowManager


class StepType(str, Enum):
    FIND_CLICK = "find_click"  # Tìm template -> click nếu thấy
    WAIT_FOR = "wait_for"  # Chờ template xuất hiện (timeout)
    WAIT_GONE = "wait_gone"  # Chờ template biến mất (timeout)
    SLEEP = "sleep"  # Ngủ N giây
    IF_FOUND_GOTO = "if_found_goto"  # Nếu thấy template -> nhảy đến step idx
    IF_NOT_FOUND_GOTO = "if_not_found_goto"  # Ngược lại
    GOTO = "goto"  # Nhảy đến step idx
    LOOP_START = "loop_start"  # Bắt đầu loop (count lần, 0 = vô hạn)
    LOOP_END = "loop_end"  # Kết thúc loop
    ACTIVATE = "activate"  # Bring app to front
    LOG = "log"  # Ghi log message
    STOP = "stop"  # Dừng scenario


@dataclass
class Step:
    type: StepType = StepType.SLEEP
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)
    step_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @staticmethod
    def from_dict(d: dict) -> "Step":
        return Step(
            type=StepType(d.get("type", "sleep")),
            enabled=bool(d.get("enabled", True)),
            params=dict(d.get("params") or {}),
            step_id=str(d.get("step_id") or uuid.uuid4().hex[:8]),
        )

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "enabled": self.enabled,
            "params": self.params,
            "step_id": self.step_id,
        }

    def label(self, templates: dict[str, str] | None = None) -> str:
        """Render dòng tóm tắt cho UI."""
        templates = templates or {}
        p = self.params
        tname = lambda tid: templates.get(tid, tid or "—")
        if self.type == StepType.FIND_CLICK:
            return (
                f"Find&Click [{tname(p.get('template_id'))}] "
                f"thr={p.get('threshold', 0.85)} "
                f"click={p.get('click_type', 'left')}"
            )
        if self.type == StepType.WAIT_FOR:
            return (
                f"Wait For [{tname(p.get('template_id'))}] "
                f"timeout={p.get('timeout', 10)}s"
            )
        if self.type == StepType.WAIT_GONE:
            return (
                f"Wait Gone [{tname(p.get('template_id'))}] "
                f"timeout={p.get('timeout', 10)}s"
            )
        if self.type == StepType.SLEEP:
            return f"Sleep {p.get('seconds', 1.0)}s"
        if self.type == StepType.IF_FOUND_GOTO:
            return (
                f"If Found [{tname(p.get('template_id'))}] → "
                f"goto step #{p.get('target', 0) + 1}"
            )
        if self.type == StepType.IF_NOT_FOUND_GOTO:
            return (
                f"If NotFound [{tname(p.get('template_id'))}] → "
                f"goto step #{p.get('target', 0) + 1}"
            )
        if self.type == StepType.GOTO:
            return f"Goto step #{p.get('target', 0) + 1}"
        if self.type == StepType.LOOP_START:
            n = p.get("count", 0)
            n_str = "∞" if n <= 0 else str(n)
            return f"Loop ({n_str} lần)"
        if self.type == StepType.LOOP_END:
            return "End Loop"
        if self.type == StepType.ACTIVATE:
            return "Activate target window"
        if self.type == StepType.LOG:
            return f"Log: {p.get('message', '')}"
        if self.type == StepType.STOP:
            return "Stop scenario"
        return self.type.value


@dataclass
class TemplateRef:
    """Template lưu trong scenario (id, name, đường dẫn ảnh)."""

    template_id: str
    name: str
    path: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "TemplateRef":
        return TemplateRef(
            template_id=str(d["template_id"]),
            name=str(d.get("name", d["template_id"])),
            path=str(d.get("path", "")),
        )


@dataclass
class ScenarioConfig:
    name: str = "Untitled"
    window_id: int = 0
    pid: int = 0
    default_threshold: float = 0.85
    default_click_type: ClickType = ClickType.LEFT
    default_click_mode: ClickMode = ClickMode.HID_RESTORE
    default_click_jitter_px: int = 2
    default_poll_interval: float = 0.5
    activate_before_click: bool = True
    multi_scale: bool = True
    grayscale: bool = True
    templates: list[TemplateRef] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "default_threshold": self.default_threshold,
            "default_click_type": self.default_click_type.value,
            "default_click_mode": self.default_click_mode.value,
            "default_click_jitter_px": self.default_click_jitter_px,
            "default_poll_interval": self.default_poll_interval,
            "activate_before_click": self.activate_before_click,
            "multi_scale": self.multi_scale,
            "grayscale": self.grayscale,
            "templates": [t.to_dict() for t in self.templates],
            "steps": [s.to_dict() for s in self.steps],
        }

    @staticmethod
    def from_dict(d: dict) -> "ScenarioConfig":
        return ScenarioConfig(
            name=str(d.get("name", "Untitled")),
            default_threshold=float(d.get("default_threshold", 0.85)),
            default_click_type=ClickType(d.get("default_click_type", "left")),
            default_click_mode=ClickMode(
                d.get("default_click_mode", "hid_restore")
            ),
            default_click_jitter_px=int(d.get("default_click_jitter_px", 2)),
            default_poll_interval=float(d.get("default_poll_interval", 0.5)),
            activate_before_click=bool(d.get("activate_before_click", True)),
            multi_scale=bool(d.get("multi_scale", True)),
            grayscale=bool(d.get("grayscale", True)),
            templates=[
                TemplateRef.from_dict(t) for t in (d.get("templates") or [])
            ],
            steps=[Step.from_dict(s) for s in (d.get("steps") or [])],
        )

    def template_name_map(self) -> dict[str, str]:
        return {t.template_id: t.name for t in self.templates}

    def get_template(self, tid: str) -> Optional[TemplateRef]:
        for t in self.templates:
            if t.template_id == tid:
                return t
        return None

    def save(self, path: str) -> None:
        data = self.to_dict()
        # Convert templates path to relative if cùng thư mục
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @staticmethod
    def load(path: str) -> "ScenarioConfig":
        with open(path, "r", encoding="utf-8") as f:
            return ScenarioConfig.from_dict(json.load(f))


@dataclass
class ScenarioStats:
    started_at: float = 0.0
    steps_executed: int = 0
    clicks: int = 0
    last_step_idx: int = -1
    last_match: Optional[MatchResult] = None
    last_confidence: float = 0.0


@dataclass
class LogEvent:
    level: str  # info / warn / error / click / step
    message: str
    timestamp: float = field(default_factory=time.time)


class ScenarioEngine(threading.Thread):
    """Thread chạy 1 scenario."""

    def __init__(
        self,
        config: ScenarioConfig,
        on_log: Optional[Callable[[LogEvent], None]] = None,
        on_stats: Optional[Callable[[ScenarioStats], None]] = None,
        on_step: Optional[Callable[[int], None]] = None,  # current step idx
        on_finish: Optional[Callable[[], None]] = None,
    ):
        super().__init__(daemon=True)
        self.config = config
        self.stats = ScenarioStats()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._on_log = on_log
        self._on_stats = on_stats
        self._on_step = on_step
        self._on_finish = on_finish

        self._templates_cache: dict[str, np.ndarray] = {}
        self._matchers: dict[float, ImageMatcher] = {}
        self._loop_stack: list[tuple[int, int]] = []  # (start_pc, remaining)

    # --- control
    def stop(self) -> None:
        self._stop_event.set()
        self._pause_event.set()

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def toggle_pause(self) -> None:
        if self.is_paused():
            self.resume()
        else:
            self.pause()

    # --- helpers
    def _log(self, level: str, message: str) -> None:
        if self._on_log:
            try:
                self._on_log(LogEvent(level, message))
            except Exception:
                pass

    def _emit_stats(self) -> None:
        if self._on_stats:
            try:
                self._on_stats(self.stats)
            except Exception:
                pass

    def _emit_step(self, idx: int) -> None:
        if self._on_step:
            try:
                self._on_step(idx)
            except Exception:
                pass

    def _get_template(self, tid: str) -> Optional[np.ndarray]:
        if not tid:
            return None
        if tid in self._templates_cache:
            return self._templates_cache[tid]
        ref = self.config.get_template(tid)
        if ref is None:
            self._log("error", f"Template id={tid} không tồn tại")
            return None
        if not ref.path or not os.path.exists(ref.path):
            self._log("error", f"Template file không tồn tại: {ref.path}")
            return None
        img = cv2.imread(ref.path, cv2.IMREAD_COLOR)
        if img is None:
            self._log("error", f"Không đọc được template: {ref.path}")
            return None
        self._templates_cache[tid] = img
        return img

    def _get_matcher(self, threshold: float) -> ImageMatcher:
        key = round(threshold, 4)
        if key not in self._matchers:
            self._matchers[key] = ImageMatcher(
                threshold=threshold,
                multi_scale=self.config.multi_scale,
                grayscale=self.config.grayscale,
            )
        return self._matchers[key]

    def _capture(self) -> Optional[np.ndarray]:
        return WindowManager.capture_window(self.config.window_id)

    def _match(
        self, template: np.ndarray, threshold: float
    ) -> Optional[MatchResult]:
        screenshot = self._capture()
        if screenshot is None:
            return None
        m = self._get_matcher(threshold)
        return m.find(screenshot, template)

    def _interruptible_sleep(self, seconds: float, granularity: float = 0.05) -> bool:
        """Sleep nhưng có thể bị stop. Trả về True nếu bị stop."""
        end = time.time() + max(0.0, seconds)
        while time.time() < end:
            if self._stop_event.is_set():
                return True
            self._pause_event.wait()
            time.sleep(min(granularity, max(0.0, end - time.time())))
        return False

    # --- step executors. Trả về next pc (None = next, hoặc int).
    def _exec_step(self, idx: int, step: Step) -> Optional[int]:
        cfg = self.config
        p = step.params

        if step.type == StepType.SLEEP:
            secs = float(p.get("seconds", 1.0))
            self._log("step", f"#{idx + 1} Sleep {secs}s")
            if self._interruptible_sleep(secs):
                return None
            return None

        if step.type == StepType.LOG:
            self._log("info", f"#{idx + 1} {p.get('message', '')}")
            return None

        if step.type == StepType.ACTIVATE:
            if cfg.pid:
                ok = ClickEngine.activate_app(cfg.pid)
                self._log(
                    "step", f"#{idx + 1} Activate pid={cfg.pid} -> {ok}"
                )
            return None

        if step.type == StepType.GOTO:
            target = int(p.get("target", 0))
            self._log("step", f"#{idx + 1} Goto #{target + 1}")
            return target

        if step.type == StepType.STOP:
            self._log("step", f"#{idx + 1} Stop")
            return -1  # special: end scenario

        if step.type == StepType.LOOP_START:
            count = int(p.get("count", 0))
            # Nếu chưa push (vào lần đầu), push state
            if not self._loop_stack or self._loop_stack[-1][0] != idx:
                self._loop_stack.append((idx, count))  # count > 0 = N lần, <=0 = infinite
                self._log(
                    "step",
                    f"#{idx + 1} Loop start "
                    f"({'∞' if count <= 0 else count} lần)",
                )
            return None

        if step.type == StepType.LOOP_END:
            if not self._loop_stack:
                self._log("warn", f"#{idx + 1} Loop End nhưng không có Loop Start")
                return None
            start_pc, remaining = self._loop_stack[-1]
            if remaining <= 0:
                # infinite
                return start_pc + 1  # quay lại body (skip start)
            remaining -= 1
            if remaining > 0:
                self._loop_stack[-1] = (start_pc, remaining)
                return start_pc + 1
            self._loop_stack.pop()
            self._log("step", f"#{idx + 1} Loop end (đã hết)")
            return None

        # Steps liên quan template
        tid = p.get("template_id", "")
        threshold = float(p.get("threshold") or cfg.default_threshold)

        if step.type == StepType.FIND_CLICK:
            template = self._get_template(tid)
            if template is None:
                self._log("warn", f"#{idx + 1} skip vì template không có")
                return None
            res = self._match(template, threshold)
            if res is None:
                self._log("warn", f"#{idx + 1} không capture được window")
                return None
            self.stats.last_match = res
            self.stats.last_confidence = res.confidence
            if not res.found:
                self._log(
                    "step",
                    f"#{idx + 1} Find&Click → MISS "
                    f"(conf={res.confidence:.3f} < {threshold:.2f})",
                )
                return None
            # Click
            click_type = ClickType(
                p.get("click_type") or cfg.default_click_type.value
            )
            click_mode = ClickMode(
                p.get("click_mode") or cfg.default_click_mode.value
            )
            offx = int(p.get("offset_x", 0))
            offy = int(p.get("offset_y", 0))
            jitter = int(p.get("jitter_px", cfg.default_click_jitter_px))

            win = WindowManager.get_window(cfg.window_id)
            if win is None:
                self._log("warn", f"#{idx + 1} window không còn")
                return None

            cx_px, cy_px = res.center
            screenshot_h, screenshot_w = self._last_capture_size()
            local_x_pt = cx_px / screenshot_w * win.width + offx
            local_y_pt = cy_px / screenshot_h * win.height + offy
            if jitter > 0:
                local_x_pt += random.uniform(-jitter, jitter)
                local_y_pt += random.uniform(-jitter, jitter)

            if cfg.activate_before_click and cfg.pid:
                ClickEngine.activate_app(cfg.pid)
                time.sleep(0.05)

            gx, gy = ClickEngine.click_in_window(
                (win.x, win.y),
                local_x_pt,
                local_y_pt,
                pid=cfg.pid,
                click_type=click_type,
                mode=click_mode,
            )
            self.stats.clicks += 1
            self._log(
                "click",
                f"#{idx + 1} Click ({gx:.0f},{gy:.0f}) "
                f"conf={res.confidence:.3f}",
            )
            return None

        if step.type in (StepType.WAIT_FOR, StepType.WAIT_GONE):
            template = self._get_template(tid)
            if template is None:
                self._log("warn", f"#{idx + 1} skip vì template không có")
                return None
            timeout = float(p.get("timeout", 10.0))
            poll = float(p.get("poll_interval", cfg.default_poll_interval))
            on_timeout = p.get("on_timeout", "next")  # next | stop | goto
            on_timeout_target = int(p.get("on_timeout_target", 0))
            want_found = step.type == StepType.WAIT_FOR

            self._log(
                "step",
                f"#{idx + 1} {'WaitFor' if want_found else 'WaitGone'} "
                f"timeout={timeout}s",
            )
            deadline = time.time() + timeout
            while time.time() < deadline:
                if self._stop_event.is_set():
                    return None
                self._pause_event.wait()
                res = self._match(template, threshold)
                if res is not None:
                    self.stats.last_match = res
                    self.stats.last_confidence = res.confidence
                    if want_found and res.found:
                        return None
                    if not want_found and not res.found:
                        return None
                if self._interruptible_sleep(poll):
                    return None
            # Timeout
            self._log(
                "warn",
                f"#{idx + 1} {'WaitFor' if want_found else 'WaitGone'} TIMEOUT",
            )
            if on_timeout == "stop":
                return -1
            if on_timeout == "goto":
                return on_timeout_target
            return None

        if step.type in (StepType.IF_FOUND_GOTO, StepType.IF_NOT_FOUND_GOTO):
            template = self._get_template(tid)
            if template is None:
                self._log("warn", f"#{idx + 1} skip vì template không có")
                return None
            res = self._match(template, threshold)
            if res is None:
                return None
            self.stats.last_match = res
            self.stats.last_confidence = res.confidence
            target = int(p.get("target", 0))
            if step.type == StepType.IF_FOUND_GOTO and res.found:
                self._log(
                    "step",
                    f"#{idx + 1} IfFound conf={res.confidence:.3f} → "
                    f"goto #{target + 1}",
                )
                return target
            if step.type == StepType.IF_NOT_FOUND_GOTO and not res.found:
                self._log(
                    "step",
                    f"#{idx + 1} IfNotFound conf={res.confidence:.3f} → "
                    f"goto #{target + 1}",
                )
                return target
            return None

        return None

    # Cache last screenshot dimension cho step click
    def _last_capture_size(self) -> tuple[int, int]:
        ss = self._capture()
        if ss is None:
            return 1, 1
        return ss.shape[0], ss.shape[1]

    # --- main loop
    def run(self) -> None:
        try:
            self._run()
        except Exception as e:
            import traceback

            self._log("error", f"Engine crash: {e}\n{traceback.format_exc()}")
        finally:
            self._emit_stats()
            if self._on_finish:
                try:
                    self._on_finish()
                except Exception:
                    pass

    def _run(self) -> None:
        cfg = self.config
        steps = [s for s in cfg.steps if s.enabled]
        if not steps:
            self._log("warn", "Scenario không có step enabled, dừng.")
            return

        self.stats.started_at = time.time()
        self._log("info", f"Bắt đầu scenario '{cfg.name}' ({len(steps)} step)")

        pc = 0
        while pc < len(steps):
            if self._stop_event.is_set():
                break
            self._pause_event.wait()

            step = steps[pc]
            self.stats.last_step_idx = pc
            self._emit_step(pc)

            try:
                next_pc = self._exec_step(pc, step)
            except Exception as e:
                self._log("error", f"#{pc + 1} crash: {e}")
                next_pc = None

            self.stats.steps_executed += 1
            self._emit_stats()

            if next_pc == -1:
                break
            if next_pc is None:
                pc += 1
            else:
                if 0 <= next_pc < len(steps):
                    pc = next_pc
                else:
                    self._log(
                        "warn",
                        f"Goto target #{next_pc + 1} ngoài range, dừng.",
                    )
                    break

        self._log("info", "Scenario kết thúc.")


class ScenarioManager:
    """Quản lý 1 engine đang chạy."""

    def __init__(self):
        self._engine: Optional[ScenarioEngine] = None
        self._lock = threading.Lock()

    def start(
        self,
        config: ScenarioConfig,
        on_log: Optional[Callable[[LogEvent], None]] = None,
        on_stats: Optional[Callable[[ScenarioStats], None]] = None,
        on_step: Optional[Callable[[int], None]] = None,
        on_finish: Optional[Callable[[], None]] = None,
    ) -> ScenarioEngine:
        with self._lock:
            if self._engine and self._engine.is_alive():
                self._engine.stop()
                self._engine.join(timeout=2)
            engine = ScenarioEngine(
                config, on_log, on_stats, on_step, on_finish
            )
            self._engine = engine
            engine.start()
            return engine

    def stop(self) -> None:
        with self._lock:
            engine = self._engine
        if engine and engine.is_alive():
            engine.stop()
            engine.join(timeout=2)

    def current(self) -> Optional[ScenarioEngine]:
        return self._engine
