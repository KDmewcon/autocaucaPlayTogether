"""Global hotkey listener cho macOS dùng NSEvent monitor (an toàn với Qt event loop).

Pynput GlobalHotKeys gây crash khi chạy chung với Qt trên macOS do cả hai đều
cần Cocoa event tap trong main thread. NSEvent.addGlobalMonitorForEventsMatchingMask
là API native macOS, work ổn định cùng Qt.
"""
from __future__ import annotations

from typing import Callable, Optional

try:
    from AppKit import NSEvent, NSKeyDownMask
    from Cocoa import (
        NSAlternateKeyMask,
        NSCommandKeyMask,
        NSControlKeyMask,
        NSShiftKeyMask,
    )

    _HAS_APPKIT = True
except Exception:  # pragma: no cover
    _HAS_APPKIT = False
    NSKeyDownMask = 1 << 10  # type: ignore
    NSCommandKeyMask = 1 << 20  # type: ignore
    NSShiftKeyMask = 1 << 17  # type: ignore
    NSAlternateKeyMask = 1 << 19  # type: ignore
    NSControlKeyMask = 1 << 18  # type: ignore


_MOD_MAP = {
    "cmd": NSCommandKeyMask,
    "command": NSCommandKeyMask,
    "shift": NSShiftKeyMask,
    "alt": NSAlternateKeyMask,
    "option": NSAlternateKeyMask,
    "ctrl": NSControlKeyMask,
    "control": NSControlKeyMask,
}


def _parse(spec: str) -> tuple[int, str]:
    """Parse 'cmd+shift+s' -> (mask, 's')."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    mask = 0
    key = ""
    for p in parts:
        if p in _MOD_MAP:
            mask |= _MOD_MAP[p]
        else:
            key = p
    return mask, key


class HotkeyManager:
    """Đăng ký hotkey toàn cục. Spec format: 'cmd+shift+s'."""

    # Bit nào trong modifierFlags ta quan tâm. Bỏ qua các bit "device-independent" khác.
    _MOD_MASK_RELEVANT = (
        NSCommandKeyMask | NSShiftKeyMask | NSAlternateKeyMask | NSControlKeyMask
    )

    def __init__(self):
        self._bindings: list[tuple[int, str, Callable[[], None]]] = []
        self._monitor = None
        self._local_monitor = None

    def set_binding(self, hotkey: str, callback: Callable[[], None]) -> None:
        """hotkey theo format 'cmd+shift+s'.

        Format pynput cũ '<cmd>+<shift>+s' cũng được hỗ trợ.
        """
        spec = hotkey.replace("<", "").replace(">", "")
        mask, key = _parse(spec)
        if not key:
            return
        self._bindings.append((mask, key, callback))

    def clear(self) -> None:
        self.stop()
        self._bindings.clear()

    def start(self) -> None:
        self.stop()
        if not _HAS_APPKIT or not self._bindings:
            return

        def _handler(event):
            try:
                chars = event.charactersIgnoringModifiers()
                if not chars:
                    return
                key = chars.lower()
                mods = int(event.modifierFlags()) & self._MOD_MASK_RELEVANT
                for mask, k, cb in self._bindings:
                    if k == key and mods == mask:
                        try:
                            cb()
                        except Exception as e:
                            print(f"[Hotkey] callback error: {e}")
                        break
            except Exception as e:
                print(f"[Hotkey] handler error: {e}")

        # Global = bắt event khi app KHÔNG có focus
        self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSKeyDownMask, _handler
        )
        # Local = bắt event khi app đang focus (NSEvent global không bắn cho app
        # đang focus). Local handler phải trả event để Qt vẫn nhận.
        def _local_handler(event):
            _handler(event)
            return event

        self._local_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSKeyDownMask, _local_handler
        )

    def stop(self) -> None:
        if not _HAS_APPKIT:
            return
        for mon in (self._monitor, self._local_monitor):
            if mon is not None:
                try:
                    NSEvent.removeMonitor_(mon)
                except Exception:
                    pass
        self._monitor = None
        self._local_monitor = None
