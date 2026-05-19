"""Smoke test: import tất cả modules + test thuần logic không cần GUI."""
from __future__ import annotations

import sys
import traceback


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(msg: str, exc: BaseException) -> None:
    print(f"  ✗ {msg}")
    print("    " + "".join(traceback.format_exception_only(type(exc), exc)).strip())


def main() -> int:
    failures = 0
    print("== Imports ==")
    try:
        from auto_clicker.core.window_manager import WindowManager, WindowInfo
        _ok("window_manager")
    except Exception as e:
        failures += 1
        _fail("window_manager", e)

    try:
        from auto_clicker.core.click_engine import ClickEngine, ClickType
        _ok("click_engine")
    except Exception as e:
        failures += 1
        _fail("click_engine", e)

    try:
        from auto_clicker.core.image_matcher import ImageMatcher, MatchResult
        _ok("image_matcher")
    except Exception as e:
        failures += 1
        _fail("image_matcher", e)

    try:
        from auto_clicker.core.automation import (
            AutomationJob,
            AutomationManager,
            JobConfig,
            JobStatus,
        )
        _ok("automation")
    except Exception as e:
        failures += 1
        _fail("automation", e)

    try:
        from auto_clicker.utils.permissions import (
            check_screen_recording,
            check_accessibility,
        )
        _ok("permissions")
    except Exception as e:
        failures += 1
        _fail("permissions", e)

    try:
        from auto_clicker.utils.hotkey import HotkeyManager
        _ok("hotkey")
    except Exception as e:
        failures += 1
        _fail("hotkey", e)

    try:
        from auto_clicker.ui.main_window import MainWindow  # noqa: F401
        _ok("ui.main_window")
    except Exception as e:
        failures += 1
        _fail("ui.main_window", e)

    try:
        from auto_clicker.ui.region_selector import RegionSelectorDialog  # noqa: F401
        _ok("ui.region_selector")
    except Exception as e:
        failures += 1
        _fail("ui.region_selector", e)

    print("\n== Image matcher logic ==")
    try:
        import cv2
        import numpy as np
        from auto_clicker.core.image_matcher import ImageMatcher

        # Tạo haystack 400x400, vẽ rect đỏ tại (100,80)
        haystack = np.full((400, 400, 3), 240, dtype=np.uint8)
        cv2.rectangle(haystack, (100, 80), (180, 140), (50, 50, 220), -1)
        cv2.putText(
            haystack, "X", (130, 125), cv2.FONT_HERSHEY_SIMPLEX,
            1.5, (255, 255, 255), 3,
        )
        # Template = crop đúng vùng đó
        template = haystack[80:141, 100:181].copy()

        m = ImageMatcher(threshold=0.9)
        res = m.find(haystack, template)
        assert res.found, f"Không tìm thấy template (conf={res.confidence})"
        # center phải ở giữa rect
        cx, cy = res.center
        assert 130 <= cx <= 150, f"cx={cx} ngoài range"
        assert 100 <= cy <= 120, f"cy={cy} ngoài range"
        _ok(f"find: conf={res.confidence:.4f} center=({cx},{cy})")

        # Test multi-scale: scale template 0.85, vẫn phải tìm thấy
        small = cv2.resize(template, None, fx=0.85, fy=0.85)
        m2 = ImageMatcher(threshold=0.7, multi_scale=True)
        r2 = m2.find(haystack, small)
        assert r2.found, f"multi-scale fail conf={r2.confidence}"
        _ok(f"multi-scale: conf={r2.confidence:.4f}")
    except Exception as e:
        failures += 1
        _fail("image matcher logic", e)

    print("\n== WindowManager listing (non-GUI) ==")
    try:
        from auto_clicker.core.window_manager import WindowManager
        wins = WindowManager.list_windows()
        _ok(f"liệt kê được {len(wins)} window")
        for w in wins[:5]:
            print(f"      • {w.display_name} ({int(w.width)}x{int(w.height)}) pid={w.pid}")
    except Exception as e:
        failures += 1
        _fail("list_windows", e)

    print("\n== Permissions check (non-blocking) ==")
    try:
        from auto_clicker.utils.permissions import (
            check_accessibility,
            check_screen_recording,
        )
        sr = check_screen_recording()
        ax = check_accessibility()
        _ok(f"Screen Recording: {sr}")
        _ok(f"Accessibility:    {ax}")
    except Exception as e:
        failures += 1
        _fail("permissions check", e)

    print()
    if failures:
        print(f"FAILED: {failures} item(s)")
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
