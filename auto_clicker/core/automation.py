"""Automation engine - chạy job auto-click background."""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

import cv2
import numpy as np

from .click_engine import ClickEngine, ClickType
from .image_matcher import ImageMatcher, MatchResult
from .window_manager import WindowInfo, WindowManager


class JobStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass
class JobConfig:
    name: str = "Untitled"
    window_id: int = 0
    pid: int = 0
    template_path: str = ""
    threshold: float = 0.85
    click_type: ClickType = ClickType.LEFT
    interval_seconds: float = 1.0
    interval_jitter: float = 0.15  # ± jitter để giống người
    click_offset_x: int = 0  # offset từ center match
    click_offset_y: int = 0
    click_jitter_px: int = 2  # ± jitter pixel khi click
    max_clicks: int = 0  # 0 = unlimited
    stop_after_misses: int = 0  # 0 = không stop, >0 = stop sau N lần miss liên tiếp
    multi_scale: bool = True
    grayscale: bool = True
    enabled: bool = True


@dataclass
class JobStats:
    clicks: int = 0
    misses: int = 0
    last_confidence: float = 0.0
    last_match: Optional[MatchResult] = None
    started_at: float = 0.0
    last_click_at: float = 0.0


@dataclass
class LogEvent:
    level: str  # info / warn / error / click / miss
    message: str
    timestamp: float = field(default_factory=time.time)


class AutomationJob(threading.Thread):
    """Thread chạy 1 job auto-click."""

    def __init__(
        self,
        config: JobConfig,
        on_log: Optional[Callable[[LogEvent], None]] = None,
        on_stats: Optional[Callable[[JobStats], None]] = None,
        on_finish: Optional[Callable[[], None]] = None,
    ):
        super().__init__(daemon=True)
        self.config = config
        self.stats = JobStats()
        self._status = JobStatus.IDLE
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # set = không pause
        self._on_log = on_log
        self._on_stats = on_stats
        self._on_finish = on_finish

        self._template: Optional[np.ndarray] = None
        self._matcher: Optional[ImageMatcher] = None

    @property
    def status(self) -> JobStatus:
        return self._status

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

    def stop(self) -> None:
        self._stop_event.set()
        self._pause_event.set()  # un-pause để thread thoát

    def pause(self) -> None:
        self._pause_event.clear()
        self._status = JobStatus.PAUSED
        self._log("info", "Job paused")

    def resume(self) -> None:
        self._pause_event.set()
        self._status = JobStatus.RUNNING
        self._log("info", "Job resumed")

    def toggle_pause(self) -> None:
        if self._status == JobStatus.PAUSED:
            self.resume()
        elif self._status == JobStatus.RUNNING:
            self.pause()

    def run(self) -> None:
        try:
            self._run_loop()
        except Exception as e:
            self._log("error", f"Job crashed: {e}")
        finally:
            self._status = JobStatus.STOPPED
            self._emit_stats()
            if self._on_finish:
                try:
                    self._on_finish()
                except Exception:
                    pass

    def _run_loop(self) -> None:
        cfg = self.config
        # Load template
        self._template = ImageMatcher.load_template(cfg.template_path)
        if self._template is None:
            self._log("error", f"Cannot load template: {cfg.template_path}")
            return
        self._matcher = ImageMatcher(
            threshold=cfg.threshold,
            multi_scale=cfg.multi_scale,
            grayscale=cfg.grayscale,
        )

        self.stats.started_at = time.time()
        self._status = JobStatus.RUNNING
        self._log(
            "info",
            f"Job started - target window {cfg.window_id} pid {cfg.pid}",
        )

        consecutive_miss = 0

        while not self._stop_event.is_set():
            self._pause_event.wait()
            if self._stop_event.is_set():
                break

            # Lấy lại window info để có toạ độ + size hiện tại
            win = WindowManager.get_window(cfg.window_id)
            if win is None:
                self._log(
                    "warn",
                    "Target window không còn tồn tại, dừng job.",
                )
                break

            screenshot = WindowManager.capture_window(cfg.window_id)
            if screenshot is None:
                self._log("warn", "Không capture được window, retry...")
                self._sleep(cfg.interval_seconds, cfg.interval_jitter)
                continue

            result = self._matcher.find(screenshot, self._template)
            self.stats.last_confidence = result.confidence
            self.stats.last_match = result

            if result.found:
                consecutive_miss = 0
                self.stats.misses = 0  # reset chuỗi miss

                # Map từ pixel (screenshot) -> point (window local)
                cx_px, cy_px = result.center
                sw_px = screenshot.shape[1]
                sh_px = screenshot.shape[0]

                if sw_px == 0 or sh_px == 0:
                    self._log("warn", "Screenshot kích thước 0, skip.")
                    self._sleep(cfg.interval_seconds, cfg.interval_jitter)
                    continue

                local_x_pt = cx_px / sw_px * win.width
                local_y_pt = cy_px / sh_px * win.height

                # Apply offset + jitter (theo point)
                local_x_pt += cfg.click_offset_x
                local_y_pt += cfg.click_offset_y
                if cfg.click_jitter_px > 0:
                    local_x_pt += random.uniform(
                        -cfg.click_jitter_px, cfg.click_jitter_px
                    )
                    local_y_pt += random.uniform(
                        -cfg.click_jitter_px, cfg.click_jitter_px
                    )

                gx, gy = ClickEngine.click_in_window(
                    (win.x, win.y),
                    local_x_pt,
                    local_y_pt,
                    pid=cfg.pid,
                    click_type=cfg.click_type,
                )

                self.stats.clicks += 1
                self.stats.last_click_at = time.time()
                self._log(
                    "click",
                    f"#{self.stats.clicks} click ({gx:.0f},{gy:.0f}) "
                    f"conf={result.confidence:.3f}",
                )
                self._emit_stats()

                if cfg.max_clicks and self.stats.clicks >= cfg.max_clicks:
                    self._log("info", f"Đạt max_clicks={cfg.max_clicks}, dừng.")
                    break
            else:
                consecutive_miss += 1
                self.stats.misses += 1
                self._log(
                    "miss",
                    f"miss (conf={result.confidence:.3f} < "
                    f"{cfg.threshold:.2f})",
                )
                self._emit_stats()

                if (
                    cfg.stop_after_misses > 0
                    and consecutive_miss >= cfg.stop_after_misses
                ):
                    self._log(
                        "warn",
                        f"Miss {consecutive_miss} lần liên tiếp, dừng job.",
                    )
                    break

            self._sleep(cfg.interval_seconds, cfg.interval_jitter)

        self._log("info", "Job stopped.")

    def _sleep(self, base: float, jitter: float) -> None:
        """Sleep với jitter, có thể bị interrupt bởi stop_event."""
        if jitter > 0:
            wait = max(0.05, base + random.uniform(-jitter, jitter))
        else:
            wait = max(0.05, base)
        # Sleep nhỏ giọt để stop nhanh
        end = time.time() + wait
        while time.time() < end:
            if self._stop_event.is_set():
                return
            self._pause_event.wait()
            time.sleep(min(0.05, end - time.time()))


class AutomationManager:
    """Quản lý nhiều job."""

    def __init__(self):
        self._jobs: dict[str, AutomationJob] = {}
        self._lock = threading.Lock()

    def start_job(
        self,
        job_id: str,
        config: JobConfig,
        on_log: Optional[Callable[[LogEvent], None]] = None,
        on_stats: Optional[Callable[[JobStats], None]] = None,
        on_finish: Optional[Callable[[], None]] = None,
    ) -> AutomationJob:
        with self._lock:
            existing = self._jobs.get(job_id)
            if existing and existing.is_alive():
                existing.stop()
                existing.join(timeout=2)
            job = AutomationJob(config, on_log, on_stats, on_finish)
            self._jobs[job_id] = job
            job.start()
            return job

    def stop_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job:
            job.stop()
            job.join(timeout=2)

    def get_job(self, job_id: str) -> Optional[AutomationJob]:
        return self._jobs.get(job_id)

    def stop_all(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
        for j in jobs:
            j.stop()
        for j in jobs:
            j.join(timeout=2)
