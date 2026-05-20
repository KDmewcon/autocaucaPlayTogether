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

from .audio_matcher import (
    AudioPattern,
    AudioStreamBuffer,
    SAMPLE_RATE as AUDIO_SR,
    build_pattern,
    compute_log_mel,
    match_pattern,
)
from .audio_monitor import AudioLevelMonitor
from .click_engine import ClickEngine, ClickMode, ClickType
from .image_matcher import ImageMatcher, MatchResult
from .window_manager import WindowManager


class StepType(str, Enum):
    FIND_CLICK = "find_click"  # Tìm template -> click nếu thấy
    CLICK_AT = "click_at"  # Click vào tọa độ cố định trong window
    WAIT_FOR = "wait_for"  # Chờ template xuất hiện (timeout)
    WAIT_GONE = "wait_gone"  # Chờ template biến mất (timeout)
    WAIT_FOR_SOUND = "wait_for_sound"  # Chờ âm thanh vượt threshold
    WAIT_FOR_AUDIO = "wait_for_audio"  # Chờ pattern audio (mp3/wav) match
    WAIT_ANY = "wait_any"  # Chờ song song nhiều condition - cái nào trigger trước thì goto
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
    name: str = ""  # Tên tùy chỉnh, để rỗng = dùng auto-label

    @staticmethod
    def from_dict(d: dict) -> "Step":
        return Step(
            type=StepType(d.get("type", "sleep")),
            enabled=bool(d.get("enabled", True)),
            params=dict(d.get("params") or {}),
            step_id=str(d.get("step_id") or uuid.uuid4().hex[:8]),
            name=str(d.get("name") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "enabled": self.enabled,
            "params": self.params,
            "step_id": self.step_id,
            "name": self.name,
        }

    def label(self, templates: dict[str, str] | None = None) -> str:
        """Render dòng tóm tắt cho UI. Nếu có name tùy chỉnh, prefix nó."""
        auto = self._auto_label(templates or {})
        if self.name and self.name.strip():
            return f"{self.name.strip()}  ·  {auto}"
        return auto

    def _auto_label(self, templates: dict[str, str]) -> str:
        p = self.params
        tname = lambda tid: templates.get(tid, tid or "—")
        if self.type == StepType.FIND_CLICK:
            return (
                f"Find&Click [{tname(p.get('template_id'))}] "
                f"thr={p.get('threshold', 0.85)} "
                f"click={p.get('click_type', 'left')}"
            )
        if self.type == StepType.CLICK_AT:
            x = p.get("x", 0)
            y = p.get("y", 0)
            unit = p.get("unit", "point")  # point | percent
            ct = p.get("click_type", "left")
            return f"Click@({x},{y} {unit}) [{ct}]"
        if self.type == StepType.WAIT_FOR_SOUND:
            return (
                f"Wait Sound thr={p.get('threshold', 0.05)} "
                f"timeout={p.get('timeout', 30)}s"
            )
        if self.type == StepType.WAIT_FOR_AUDIO:
            return (
                f"Wait Audio [{tname(p.get('audio_id'))}] "
                f"thr={p.get('threshold', 0.7)} "
                f"timeout={p.get('timeout', 30)}s"
            )
        if self.type == StepType.WAIT_ANY:
            branches = p.get("branches") or []
            n = len(branches)
            parts: list[str] = []
            for b in branches[:3]:
                bt = b.get("type", "?")
                if bt == "image":
                    parts.append(f"img[{tname(b.get('template_id'))}]→#{b.get('goto', 0) + 1}")
                elif bt == "image_gone":
                    parts.append(f"img_gone[{tname(b.get('template_id'))}]→#{b.get('goto', 0) + 1}")
                elif bt == "audio":
                    parts.append(f"aud[{tname(b.get('audio_id'))}]→#{b.get('goto', 0) + 1}")
                elif bt == "sound":
                    parts.append(f"sound→#{b.get('goto', 0) + 1}")
                else:
                    parts.append(f"?→#{b.get('goto', 0) + 1}")
            extra = f" +{n - 3}" if n > 3 else ""
            return (
                f"Wait Any ({n}) "
                + " | ".join(parts)
                + extra
                + f"  timeout={p.get('timeout', 30)}s"
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
class AudioRef:
    """Audio reference lưu trong scenario."""

    audio_id: str
    name: str
    path: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "AudioRef":
        return AudioRef(
            audio_id=str(d["audio_id"]),
            name=str(d.get("name", d["audio_id"])),
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
    audio_device: int = -1  # -1 = default
    audio_threshold: float = 0.05  # RMS threshold mặc định cho wait_for_sound
    audio_sustain_ms: int = 100  # Phải vượt threshold liên tục N ms
    audio_match_threshold: float = 0.6  # Cosine threshold cho wait_for_audio
    audio_buffer_seconds: float = 5.0  # Buffer rolling cho audio matching
    templates: list[TemplateRef] = field(default_factory=list)
    audios: list[AudioRef] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    loop_forever: bool = False  # Lặp vô hạn khi start

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
            "audio_device": self.audio_device,
            "audio_threshold": self.audio_threshold,
            "audio_sustain_ms": self.audio_sustain_ms,
            "audio_match_threshold": self.audio_match_threshold,
            "audio_buffer_seconds": self.audio_buffer_seconds,
            "templates": [t.to_dict() for t in self.templates],
            "audios": [a.to_dict() for a in self.audios],
            "steps": [s.to_dict() for s in self.steps],
            "loop_forever": self.loop_forever,
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
            audio_device=int(d.get("audio_device", -1)),
            audio_threshold=float(d.get("audio_threshold", 0.05)),
            audio_sustain_ms=int(d.get("audio_sustain_ms", 100)),
            audio_match_threshold=float(d.get("audio_match_threshold", 0.6)),
            audio_buffer_seconds=float(d.get("audio_buffer_seconds", 5.0)),
            templates=[
                TemplateRef.from_dict(t) for t in (d.get("templates") or [])
            ],
            audios=[AudioRef.from_dict(a) for a in (d.get("audios") or [])],
            steps=[Step.from_dict(s) for s in (d.get("steps") or [])],
            loop_forever=bool(d.get("loop_forever", False)),
        )

    def template_name_map(self) -> dict[str, str]:
        m = {t.template_id: t.name for t in self.templates}
        for a in self.audios:
            m[a.audio_id] = a.name
        # Merge library (scenario local override library nếu trùng id)
        try:
            from .media_library import MediaLibrary
            lib = MediaLibrary.instance()
            for t in lib.list_templates():
                m.setdefault(t.template_id, t.name)
            for a in lib.list_audios():
                m.setdefault(a.audio_id, a.name)
        except Exception:
            pass
        return m

    def get_template(self, tid: str) -> Optional[TemplateRef]:
        for t in self.templates:
            if t.template_id == tid:
                return t
        return None

    def get_audio(self, aid: str) -> Optional[AudioRef]:
        for a in self.audios:
            if a.audio_id == aid:
                return a
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
        self._audio: Optional[AudioLevelMonitor] = None
        self._audio_buffer: Optional[AudioStreamBuffer] = None
        self._audio_stream = None  # sounddevice InputStream khi cần buffer
        self._audio_buffer_device = "__none__"  # device hiện tại của stream
        self._audio_patterns: dict[str, AudioPattern] = {}

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
        # Throttle: chỉ emit nếu cách lần trước >= 200ms
        now = time.time()
        last = getattr(self, "_last_stats_emit", 0.0)
        if now - last < 0.2:
            return
        self._last_stats_emit = now
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
        # Ưu tiên scenario.templates (legacy), fallback shared library
        ref = self.config.get_template(tid)
        if ref is None:
            try:
                from .media_library import MediaLibrary
                ref = MediaLibrary.instance().get_template(tid)
            except Exception:
                pass
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

    def _get_audio(self, device: Optional[int] = None) -> Optional[AudioLevelMonitor]:
        # Nếu step truyền device riêng, ưu tiên dùng nó.
        if device is None:
            dev = self.config.audio_device if self.config.audio_device >= 0 else None
        else:
            dev = device if device >= 0 else None

        # Nếu monitor hiện tại đang chạy đúng device thì tái dùng
        current_dev = getattr(self._audio, "device", None) if self._audio else "__none__"
        if self._audio is not None and current_dev == dev:
            return self._audio

        # Khác device -> stop cũ, tạo mới
        if self._audio is not None:
            try:
                self._audio.stop()
            except Exception:
                pass
            self._audio = None

        self._audio = AudioLevelMonitor(device=dev)
        if not self._audio.start():
            self._log("error", f"Audio start failed: {self._audio.error}")
            self._audio = None
            return None
        return self._audio

    def _ensure_audio_buffer(self, device: Optional[int] = None) -> bool:
        """Mở stream audio + buffer rolling cho audio pattern matching.

        - Dùng AudioBus shared để N scenario cùng device không xung đột.
        - Nếu device chỉ định không hợp lệ, fallback default input.
        """
        # Resolve target device
        if device is None:
            target_dev = (
                self.config.audio_device
                if self.config.audio_device >= 0
                else None
            )
        else:
            target_dev = device if device >= 0 else None

        # Đã subscribe đúng device thì OK
        current_dev = getattr(self, "_audio_buffer_device", "__none__")
        if (
            self._audio_buffer is not None
            and current_dev == target_dev
            and getattr(self, "_audio_bus_callback", None) is not None
        ):
            return True

        # Khác device hoặc chưa init -> stop subscription cũ
        self._stop_audio_buffer()

        # Validate device
        try:
            import sounddevice as sd
            try:
                if target_dev is None:
                    info = sd.query_devices(kind="input")
                else:
                    info = sd.query_devices(target_dev)
                if isinstance(info, dict):
                    if int(info.get("max_input_channels", 0)) < 1:
                        self._log(
                            "warn",
                            f"Device {target_dev} không có input, fallback default",
                        )
                        target_dev = None
            except Exception:
                target_dev = None
        except Exception as e:
            self._log("error", f"sounddevice không khả dụng: {e}")
            return False

        self._audio_buffer = AudioStreamBuffer(
            capacity_seconds=self.config.audio_buffer_seconds
        )

        def _push(samples: np.ndarray) -> None:
            try:
                self._audio_buffer.append(samples)
            except Exception:
                pass

        from .audio_bus import AudioBus
        bus = AudioBus.instance()
        ok = bus.subscribe(target_dev, _push, samplerate=AUDIO_SR)
        if not ok:
            self._log("error", "AudioBus không mở được stream")
            self._audio_buffer = None
            return False

        self._audio_bus_callback = _push
        self._audio_buffer_device = target_dev
        self._log(
            "info",
            f"Audio buffer subscribed bus device={target_dev} "
            f"(buffer={self.config.audio_buffer_seconds}s)",
        )
        return True

    def _stop_audio_buffer(self) -> None:
        cb = getattr(self, "_audio_bus_callback", None)
        dev = getattr(self, "_audio_buffer_device", "__none__")
        if cb is not None and dev != "__none__":
            try:
                from .audio_bus import AudioBus
                AudioBus.instance().unsubscribe(dev, cb)
            except Exception:
                pass
        self._audio_bus_callback = None
        self._audio_stream = None
        self._audio_buffer = None
        self._audio_buffer_device = "__none__"

    def _get_audio_pattern(self, audio_id: str) -> Optional[AudioPattern]:
        if audio_id in self._audio_patterns:
            return self._audio_patterns[audio_id]
        ref = self.config.get_audio(audio_id)
        if ref is None:
            try:
                from .media_library import MediaLibrary
                ref = MediaLibrary.instance().get_audio(audio_id)
            except Exception:
                pass
        if ref is None or not ref.path or not os.path.exists(ref.path):
            self._log("error", f"Audio ref không tồn tại: {audio_id}")
            return None
        try:
            pat = build_pattern(audio_id, ref.name, ref.path)
        except Exception as e:
            self._log("error", f"Build pattern lỗi cho {ref.path}: {e}")
            return None
        self._audio_patterns[audio_id] = pat
        return pat

    def _capture(self) -> Optional[np.ndarray]:
        # Dùng CaptureWorker - share frame giữa các scenario song song
        try:
            from .capture_worker import CaptureWorker
            return CaptureWorker.instance().get_or_capture(self.config.window_id)
        except Exception:
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
    def _resolve_branch(
        self, p: dict, prefix: str, default_action: str = "next"
    ) -> Optional[int]:
        """Resolve một branch action (on_found/on_timeout/...).

        Trả:
        - None = chạy tiếp step kế (next)
        - -1 = stop scenario
        - int >= 0 = goto step idx đó

        Ưu tiên `<prefix>_target_id` (theo step_id, ổn định khi reorder),
        fallback `<prefix>_target` (legacy, theo index).
        """
        action = p.get(f"{prefix}", default_action)
        if action == "stop":
            return -1
        if action == "goto":
            target_id = p.get(f"{prefix}_target_id")
            if target_id:
                idx = self._step_id_to_index(target_id)
                if idx >= 0:
                    return idx
                self._log(
                    "warn",
                    f"Branch goto '{prefix}': step_id '{target_id}' không tồn tại",
                )
                return None
            return int(p.get(f"{prefix}_target", 0))
        return None

    def _step_id_to_index(self, step_id: str) -> int:
        """Tìm index của enabled step có step_id tương ứng. -1 nếu không thấy."""
        if not step_id:
            return -1
        idx = 0
        for s in self.config.steps:
            if not s.enabled:
                continue
            if s.step_id == step_id:
                return idx
            idx += 1
        return -1

    def _resolve_target(self, p: dict, key_id: str = "target_id", key_legacy: str = "target") -> int:
        """Resolve target step: ưu tiên *_id, fallback *_int (legacy)."""
        tid = p.get(key_id)
        if tid:
            idx = self._step_id_to_index(tid)
            if idx >= 0:
                return idx
            self._log("warn", f"target step_id '{tid}' không tồn tại")
        return int(p.get(key_legacy, 0))

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
            target = self._resolve_target(p)
            self._log("step", f"#{idx + 1} Goto #{target + 1}")
            return target

        if step.type == StepType.STOP:
            self._log("step", f"#{idx + 1} Stop")
            return -1  # special: end scenario

        if step.type == StepType.CLICK_AT:
            win = WindowManager.get_window(cfg.window_id)
            if win is None:
                self._log("warn", f"#{idx + 1} window không còn")
                return None
            unit = p.get("unit", "point")  # "point" hoặc "percent"
            x = float(p.get("x", 0))
            y = float(p.get("y", 0))
            if unit == "percent":
                local_x = x / 100.0 * win.width
                local_y = y / 100.0 * win.height
            else:
                local_x = x
                local_y = y
            jitter = int(p.get("jitter_px", cfg.default_click_jitter_px))
            if jitter > 0:
                local_x += random.uniform(-jitter, jitter)
                local_y += random.uniform(-jitter, jitter)
            click_type = ClickType(
                p.get("click_type") or cfg.default_click_type.value
            )
            click_mode = ClickMode(
                p.get("click_mode") or cfg.default_click_mode.value
            )
            if cfg.activate_before_click and cfg.pid:
                ClickEngine.activate_app(cfg.pid)
                time.sleep(0.05)
            gx, gy = ClickEngine.click_in_window(
                (win.x, win.y),
                local_x,
                local_y,
                pid=cfg.pid,
                click_type=click_type,
                mode=click_mode,
            )
            self.stats.clicks += 1
            self._log(
                "click",
                f"#{idx + 1} ClickAt ({gx:.0f},{gy:.0f}) "
                f"local=({local_x:.0f},{local_y:.0f})",
            )
            return None

        if step.type == StepType.WAIT_FOR_SOUND:
            step_device = p.get("device")
            try:
                step_device_int = int(step_device) if step_device is not None else None
            except (TypeError, ValueError):
                step_device_int = None
            audio = self._get_audio(device=step_device_int)
            if audio is None:
                self._log("error", f"#{idx + 1} không init được audio")
                return None
            threshold = float(p.get("threshold") or cfg.audio_threshold)
            timeout = float(p.get("timeout", 30.0))
            sustain_ms = int(p.get("sustain_ms") or cfg.audio_sustain_ms)
            poll = 0.02  # 20ms để bắt nhanh
            self._log(
                "step",
                f"#{idx + 1} WaitForSound thr={threshold:.3f} "
                f"timeout={timeout}s sustain={sustain_ms}ms",
            )
            deadline = time.time() + timeout
            sustain_start: Optional[float] = None
            sustain_secs = sustain_ms / 1000.0
            while time.time() < deadline:
                if self._stop_event.is_set():
                    return None
                self._pause_event.wait()
                rms = audio.rms
                if rms >= threshold:
                    if sustain_start is None:
                        sustain_start = time.time()
                    elif time.time() - sustain_start >= sustain_secs:
                        self._log(
                            "step",
                            f"#{idx + 1} Sound triggered "
                            f"rms={rms:.4f} ≥ {threshold:.3f}",
                        )
                        return self._resolve_branch(p, "on_found")
                else:
                    sustain_start = None
                if self._interruptible_sleep(poll):
                    return None
            # timeout
            self._log("warn", f"#{idx + 1} WaitForSound TIMEOUT")
            return self._resolve_branch(p, "on_timeout")

        if step.type == StepType.WAIT_FOR_AUDIO:
            audio_id = p.get("audio_id", "")
            pattern = self._get_audio_pattern(audio_id)
            if pattern is None:
                self._log("warn", f"#{idx + 1} skip vì audio pattern không có")
                return None
            # Device per-step (override cfg)
            step_device = p.get("device")
            try:
                step_device_int = int(step_device) if step_device is not None else None
            except (TypeError, ValueError):
                step_device_int = None
            if not self._ensure_audio_buffer(device=step_device_int):
                self._log("error", f"#{idx + 1} không init được audio buffer")
                return None
            threshold = float(
                p.get("threshold") or cfg.audio_match_threshold
            )
            timeout = float(p.get("timeout", 30.0))
            poll = float(p.get("poll_interval", 0.2))

            self._log(
                "step",
                f"#{idx + 1} WaitForAudio [{pattern.name}] "
                f"thr={threshold:.2f} timeout={timeout}s "
                f"(pat {pattern.duration_s:.2f}s)",
            )

            # Đợi buffer fill ít nhất bằng pattern length
            min_buf_seconds = pattern.duration_s + 0.2
            t0 = time.time()
            while True:
                if self._stop_audio_buffer is None:
                    break
                snap = self._audio_buffer.snapshot() if self._audio_buffer else None
                buf_seconds = (snap.size / AUDIO_SR) if snap is not None else 0
                if buf_seconds >= min_buf_seconds:
                    break
                if time.time() - t0 > 2.0:
                    break  # đừng đợi mãi
                if self._interruptible_sleep(0.05):
                    return None

            deadline = time.time() + timeout
            best_conf = 0.0
            min_samples = int(AUDIO_SR * (pattern.duration_s + 0.05))
            while time.time() < deadline:
                if self._stop_event.is_set():
                    return None
                self._pause_event.wait()
                if self._audio_buffer is None:
                    break
                # Dùng cached log-mel để tránh recompute toàn bộ 5s buffer
                buf_lm = self._audio_buffer.snapshot_log_mel()
                if buf_lm is not None and buf_lm.shape[1] >= pattern.n_frames:
                    res = match_pattern(pattern, buf_lm, threshold=threshold)
                    if res.confidence > best_conf:
                        best_conf = res.confidence
                    self.stats.last_confidence = res.confidence
                    if res.matched:
                        self._log(
                            "step",
                            f"#{idx + 1} Audio matched! "
                            f"conf={res.confidence:.3f} ≥ {threshold:.2f}",
                        )
                        return self._resolve_branch(p, "on_found")
                if self._interruptible_sleep(poll):
                    return None
            # timeout
            self._log(
                "warn",
                f"#{idx + 1} WaitForAudio TIMEOUT "
                f"(best conf={best_conf:.3f})",
            )
            return self._resolve_branch(p, "on_timeout")

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
                        return self._resolve_branch(p, "on_found")
                    if not want_found and not res.found:
                        return self._resolve_branch(p, "on_found")
                if self._interruptible_sleep(poll):
                    return None
            # Timeout
            self._log(
                "warn",
                f"#{idx + 1} {'WaitFor' if want_found else 'WaitGone'} TIMEOUT",
            )
            return self._resolve_branch(p, "on_timeout")

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
            target = self._resolve_target(p)
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

        if step.type == StepType.WAIT_ANY:
            return self._exec_wait_any(idx, step)

        return None

    def _exec_wait_any(self, idx: int, step: Step) -> Optional[int]:
        """Chờ song song nhiều branch. Branch đầu tiên trigger thắng -> goto.

        Mỗi branch có thể là:
          - {type:'image',  template_id, threshold?, goto}
          - {type:'image_gone', template_id, threshold?, goto}
          - {type:'audio',  audio_id, threshold?, goto}
          - {type:'sound',  threshold?, sustain_ms?, goto}
        """
        cfg = self.config
        p = step.params
        branches = p.get("branches") or []
        if not branches:
            self._log("warn", f"#{idx + 1} WaitAny không có branch")
            return None
        timeout = float(p.get("timeout", 30.0))
        poll = float(p.get("poll_interval", 0.2))

        # Pre-resolve resources cho từng branch
        prepared: list[dict] = []
        needs_audio_buffer = False
        needs_sound = False
        for b in branches:
            bt = b.get("type", "")
            entry: dict = {"type": bt, "branch": b}
            if bt in ("image", "image_gone"):
                tid = b.get("template_id", "")
                tmpl = self._get_template(tid)
                if tmpl is None:
                    self._log("warn", f"#{idx + 1} branch image template thiếu, skip")
                    continue
                entry["template"] = tmpl
                entry["threshold"] = float(
                    b.get("threshold") or cfg.default_threshold
                )
            elif bt == "audio":
                aid = b.get("audio_id", "")
                pat = self._get_audio_pattern(aid)
                if pat is None:
                    self._log("warn", f"#{idx + 1} branch audio pattern thiếu, skip")
                    continue
                entry["pattern"] = pat
                entry["threshold"] = float(
                    b.get("threshold") or cfg.audio_match_threshold
                )
                needs_audio_buffer = True
            elif bt == "sound":
                entry["threshold"] = float(
                    b.get("threshold") or cfg.audio_threshold
                )
                entry["sustain_secs"] = (
                    int(b.get("sustain_ms") or cfg.audio_sustain_ms)
                    / 1000.0
                )
                entry["sustain_start"] = None
                needs_sound = True
            else:
                self._log("warn", f"#{idx + 1} branch type lạ: {bt}, skip")
                continue
            # Resolve goto: ưu tiên goto_id (theo step_id), fallback goto (legacy index)
            entry["goto"] = self._resolve_target(b, key_id="goto_id", key_legacy="goto")
            prepared.append(entry)

        if not prepared:
            self._log("warn", f"#{idx + 1} WaitAny không có branch hợp lệ")
            return None

        # Init audio resources nếu cần
        if needs_audio_buffer and not self._ensure_audio_buffer():
            self._log("error", f"#{idx + 1} không init được audio buffer")
            return None
        if needs_sound and self._get_audio() is None:
            self._log("error", f"#{idx + 1} không init được audio level monitor")
            return None

        # Branch summary
        summary = " | ".join(
            f"{e['type']}->#{e['goto'] + 1}" for e in prepared
        )
        self._log(
            "step",
            f"#{idx + 1} WaitAny ({len(prepared)} branches) [{summary}] "
            f"timeout={timeout}s",
        )

        # Đợi audio buffer fill nếu có audio branch
        if needs_audio_buffer:
            min_buf = max(
                (
                    e["pattern"].duration_s + 0.05
                    for e in prepared
                    if e["type"] == "audio"
                ),
                default=0.0,
            )
            t0 = time.time()
            while True:
                if self._stop_event.is_set():
                    return None
                snap = self._audio_buffer.snapshot() if self._audio_buffer else None
                buf_seconds = (snap.size / AUDIO_SR) if snap is not None else 0
                if buf_seconds >= min_buf:
                    break
                if time.time() - t0 > 2.0:
                    break
                if self._interruptible_sleep(0.05):
                    return None

        deadline = time.time() + timeout
        last_screenshot: Optional[np.ndarray] = None
        last_buf_log_mel: Optional[np.ndarray] = None
        last_image_check_at: float = 0.0

        while time.time() < deadline:
            if self._stop_event.is_set():
                return None
            self._pause_event.wait()

            now = time.time()
            # Capture screenshot 1 lần cho tất cả image branch (rate limit ~poll)
            need_image = any(e["type"] in ("image", "image_gone") for e in prepared)
            if need_image and (now - last_image_check_at) >= poll:
                last_screenshot = self._capture()
                last_image_check_at = now

            # Capture audio buffer log-mel 1 lần cho tất cả audio branch
            need_audio = any(e["type"] == "audio" for e in prepared)
            if need_audio and self._audio_buffer is not None:
                # Dùng cached log-mel để tránh recompute mỗi poll
                last_buf_log_mel = self._audio_buffer.snapshot_log_mel()

            # Check từng branch
            for e in prepared:
                bt = e["type"]
                if bt == "image" and last_screenshot is not None:
                    matcher = self._get_matcher(e["threshold"])
                    res = matcher.find(last_screenshot, e["template"])
                    self.stats.last_match = res
                    self.stats.last_confidence = res.confidence
                    if res.found:
                        self._log(
                            "step",
                            f"#{idx + 1} WaitAny → image triggered "
                            f"conf={res.confidence:.3f} → goto #{e['goto'] + 1}",
                        )
                        return e["goto"]
                elif bt == "image_gone" and last_screenshot is not None:
                    matcher = self._get_matcher(e["threshold"])
                    res = matcher.find(last_screenshot, e["template"])
                    self.stats.last_match = res
                    self.stats.last_confidence = res.confidence
                    if not res.found:
                        self._log(
                            "step",
                            f"#{idx + 1} WaitAny → image_gone triggered "
                            f"conf={res.confidence:.3f} → goto #{e['goto'] + 1}",
                        )
                        return e["goto"]
                elif bt == "audio" and last_buf_log_mel is not None:
                    pat = e["pattern"]
                    if last_buf_log_mel.shape[1] >= pat.n_frames:
                        res = match_pattern(
                            pat, last_buf_log_mel, threshold=e["threshold"]
                        )
                        self.stats.last_confidence = res.confidence
                        if res.matched:
                            self._log(
                                "step",
                                f"#{idx + 1} WaitAny → audio triggered "
                                f"conf={res.confidence:.3f} → goto #{e['goto'] + 1}",
                            )
                            return e["goto"]
                elif bt == "sound" and self._audio is not None:
                    rms = self._audio.rms
                    if rms >= e["threshold"]:
                        if e["sustain_start"] is None:
                            e["sustain_start"] = time.time()
                        elif time.time() - e["sustain_start"] >= e["sustain_secs"]:
                            self._log(
                                "step",
                                f"#{idx + 1} WaitAny → sound triggered "
                                f"rms={rms:.4f} → goto #{e['goto'] + 1}",
                            )
                            return e["goto"]
                    else:
                        e["sustain_start"] = None

            if self._interruptible_sleep(min(poll, 0.05)):
                return None

        # Timeout
        on_timeout = p.get("on_timeout", "next")
        on_timeout_target = int(p.get("on_timeout_target", 0))
        self._log("warn", f"#{idx + 1} WaitAny TIMEOUT")
        if on_timeout == "stop":
            return -1
        if on_timeout == "goto":
            return on_timeout_target
        return None

    # Cache last screenshot dimension cho step click
    def _last_capture_size(self) -> tuple[int, int]:
        ss = self._capture()
        if ss is None:
            return 1, 1
        return ss.shape[0], ss.shape[1]

    # --- main loop
    def run(self) -> None:
        # Subscribe capture worker
        try:
            from .capture_worker import CaptureWorker
            if self.config.window_id:
                CaptureWorker.instance().subscribe(self.config.window_id)
        except Exception:
            pass
        try:
            self._run()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._log("error", f"Engine crash: {e}")
            for line in tb.splitlines():
                self._log("error", f"  {line}")
        finally:
            if self._audio is not None:
                try:
                    self._audio.stop()
                except Exception:
                    pass
                self._audio = None
            try:
                self._stop_audio_buffer()
            except Exception:
                pass
            try:
                from .capture_worker import CaptureWorker
                if self.config.window_id:
                    CaptureWorker.instance().unsubscribe(self.config.window_id)
            except Exception:
                pass
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
        self._log("info", f"Bắt đầu scenario '{cfg.name}' ({len(steps)} step)"
                  + (" [LOOP ∞]" if cfg.loop_forever else ""))

        iteration = 0
        while True:
            iteration += 1
            if iteration > 1:
                self._log("info", f"--- Loop lần {iteration} ---")

            pc = 0
            should_stop = False
            while pc < len(steps):
                if self._stop_event.is_set():
                    should_stop = True
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
                    should_stop = True
                    break
                if next_pc is None:
                    # Check next_step_id override
                    next_id = step.params.get("next_step_id", "")
                    if next_id:
                        ni = self._step_id_to_index(next_id)
                        if ni >= 0:
                            pc = ni
                            continue
                    pc += 1
                else:
                    if 0 <= next_pc < len(steps):
                        pc = next_pc
                    else:
                        self._log(
                            "warn",
                            f"Goto target #{next_pc + 1} ngoài range, dừng.",
                        )
                        should_stop = True
                        break

            if should_stop or not cfg.loop_forever:
                break

        self._log("info", "Scenario kết thúc.")


class ScenarioManager:
    """Quản lý nhiều scenario engine chạy song song.

    Mỗi engine được tag bằng 1 key (mặc định = config.name + id()). Có thể
    start/stop/list theo key. Backward-compatible: `start()` không truyền key
    sẽ replace engine "default" như cũ.
    """

    DEFAULT_KEY = "default"

    def __init__(self):
        self._engines: dict[str, ScenarioEngine] = {}
        self._lock = threading.Lock()

    def start(
        self,
        config: ScenarioConfig,
        on_log: Optional[Callable[[LogEvent], None]] = None,
        on_stats: Optional[Callable[[ScenarioStats], None]] = None,
        on_step: Optional[Callable[[int], None]] = None,
        on_finish: Optional[Callable[[], None]] = None,
        key: Optional[str] = None,
    ) -> ScenarioEngine:
        """Start scenario. Nếu cùng key đã chạy thì stop nó trước.

        - key = None → tự sinh key duy nhất theo (name + id) để cho phép song song.
          Nếu user muốn behaviour cũ ("một-tại-một-thời-điểm"), truyền
          key=ScenarioManager.DEFAULT_KEY.
        """
        with self._lock:
            if key is None:
                # Tự sinh key duy nhất → cho phép chạy song song
                base = config.name or "scenario"
                k = base
                i = 1
                while k in self._engines and self._engines[k].is_alive():
                    i += 1
                    k = f"{base} #{i}"
                key = k
            else:
                # Stop engine cũ với cùng key (replace)
                old = self._engines.get(key)
                if old and old.is_alive():
                    old.stop()
                    old.join(timeout=2)

            # Wrap on_finish để cleanup khỏi dict
            def _wrapped_finish(_key=key, _orig=on_finish):
                with self._lock:
                    cur = self._engines.get(_key)
                    if cur and not cur.is_alive():
                        self._engines.pop(_key, None)
                if _orig:
                    try:
                        _orig()
                    except Exception:
                        pass

            engine = ScenarioEngine(
                config, on_log, on_stats, on_step, _wrapped_finish
            )
            self._engines[key] = engine
            engine.start()
            return engine

    def stop(self, key: Optional[str] = None) -> None:
        """Stop 1 engine theo key, hoặc tất cả nếu key=None."""
        with self._lock:
            if key is None:
                engines = list(self._engines.values())
            else:
                eng = self._engines.get(key)
                engines = [eng] if eng else []
        for e in engines:
            if e and e.is_alive():
                e.stop()
                e.join(timeout=2)
        if key is None:
            with self._lock:
                self._engines.clear()
        else:
            with self._lock:
                self._engines.pop(key, None)

    def stop_all(self) -> None:
        self.stop(key=None)

    def list_running(self) -> list[tuple[str, ScenarioEngine]]:
        """Trả về list (key, engine) đang chạy."""
        with self._lock:
            return [(k, e) for k, e in self._engines.items() if e.is_alive()]

    def get(self, key: str) -> Optional[ScenarioEngine]:
        with self._lock:
            return self._engines.get(key)

    def is_running(self, key: Optional[str] = None) -> bool:
        with self._lock:
            if key is None:
                return any(e.is_alive() for e in self._engines.values())
            e = self._engines.get(key)
            return bool(e and e.is_alive())

    def current(self) -> Optional[ScenarioEngine]:
        """Backward-compat: trả engine đầu tiên đang chạy (nếu có)."""
        with self._lock:
            for e in self._engines.values():
                if e.is_alive():
                    return e
            return None
