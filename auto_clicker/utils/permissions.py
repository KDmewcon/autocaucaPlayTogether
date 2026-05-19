"""Check permissions Screen Recording + Accessibility trên macOS."""
from __future__ import annotations

import subprocess

import Quartz


def check_screen_recording() -> bool:
    """Check Screen Recording permission bằng cách thử capture 1 pixel."""
    try:
        img = Quartz.CGWindowListCreateImage(
            Quartz.CGRectMake(0, 0, 1, 1),
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
            Quartz.kCGWindowImageDefault,
        )
        return img is not None
    except Exception:
        return False


def check_accessibility() -> bool:
    """Check Accessibility permission - cần để post mouse event."""
    try:
        from ApplicationServices import AXIsProcessTrusted

        return bool(AXIsProcessTrusted())
    except Exception:
        return False


def request_accessibility_prompt() -> bool:
    """Mở prompt yêu cầu Accessibility permission."""
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )

        options = {kAXTrustedCheckOptionPrompt: True}
        return bool(AXIsProcessTrustedWithOptions(options))
    except Exception:
        return False


def open_system_settings(panel: str = "accessibility") -> None:
    """Mở System Settings tới panel quyền tương ứng.

    panel: 'accessibility' hoặc 'screen'
    """
    if panel == "accessibility":
        url = (
            "x-apple.systempreferences:com.apple.preference.security"
            "?Privacy_Accessibility"
        )
    else:
        url = (
            "x-apple.systempreferences:com.apple.preference.security"
            "?Privacy_ScreenCapture"
        )
    subprocess.run(["open", url], check=False)
