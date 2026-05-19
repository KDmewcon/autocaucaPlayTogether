"""Click engine - 3 mode click để cover các app target khác nhau.

PID_POSTED: gửi event vào pid, cursor user KHÔNG động.
            Pros: zero intrusion. Cons: nhiều app không nhận.
HID_RESTORE: di chuyển cursor đến target qua HID tap, click, rồi move cursor
             trả về vị trí cũ. Cursor có chớp 1 cái nhưng universal.
HID_TAP: click qua HID tap, không restore. Đơn giản nhất, work với mọi app.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Optional

import Quartz
from Quartz import (
    CGEventCreate,
    CGEventCreateMouseEvent,
    CGEventGetLocation,
    CGEventPost,
    CGEventPostToPid,
    CGEventSetIntegerValueField,
    CGWarpMouseCursorPosition,
    kCGEventLeftMouseDown,
    kCGEventLeftMouseUp,
    kCGEventMouseMoved,
    kCGEventOtherMouseDown,
    kCGEventOtherMouseUp,
    kCGEventRightMouseDown,
    kCGEventRightMouseUp,
    kCGHIDEventTap,
    kCGMouseButtonCenter,
    kCGMouseButtonLeft,
    kCGMouseButtonRight,
    kCGMouseEventClickState,
)


class ClickType(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"
    DOUBLE = "double"


class ClickMode(str, Enum):
    PID_POSTED = "pid_posted"  # post vào PID - không di chuyển cursor
    HID_RESTORE = "hid_restore"  # HID tap, restore cursor về chỗ cũ
    HID_TAP = "hid_tap"  # HID tap, không restore


class ClickEngine:
    """Click engine với 3 mode."""

    @staticmethod
    def _post_to_pid(event, pid: int) -> None:
        CGEventPostToPid(pid, event)

    @staticmethod
    def _post_hid(event) -> None:
        CGEventPost(kCGHIDEventTap, event)

    @staticmethod
    def get_cursor_position() -> tuple[float, float]:
        """Trả về cursor position hiện tại (point)."""
        ev = CGEventCreate(None)
        loc = CGEventGetLocation(ev)
        return float(loc.x), float(loc.y)

    @classmethod
    def click(
        cls,
        x: float,
        y: float,
        pid: Optional[int] = None,
        click_type: ClickType = ClickType.LEFT,
        mode: ClickMode = ClickMode.HID_RESTORE,
        down_up_delay: float = 0.04,
    ) -> None:
        """Click tại (x, y) global point."""
        pos = (float(x), float(y))

        if click_type == ClickType.RIGHT:
            btn = kCGMouseButtonRight
            ev_down = kCGEventRightMouseDown
            ev_up = kCGEventRightMouseUp
        elif click_type == ClickType.MIDDLE:
            btn = kCGMouseButtonCenter
            ev_down = kCGEventOtherMouseDown
            ev_up = kCGEventOtherMouseUp
        else:
            btn = kCGMouseButtonLeft
            ev_down = kCGEventLeftMouseDown
            ev_up = kCGEventLeftMouseUp

        if mode == ClickMode.PID_POSTED:
            if not pid or pid <= 0:
                # Không có PID -> fallback HID
                mode = ClickMode.HID_TAP
            else:
                cls._click_via_pid(pos, pid, click_type, btn, ev_down, ev_up, down_up_delay)
                return

        if mode == ClickMode.HID_RESTORE:
            saved = cls.get_cursor_position()
            try:
                cls._click_via_hid(pos, click_type, btn, ev_down, ev_up, down_up_delay)
            finally:
                # Restore cursor về chỗ cũ
                CGWarpMouseCursorPosition(saved)
                # Sync HID stream để cursor cập nhật ngay
                Quartz.CGAssociateMouseAndMouseCursorPosition(True)
            return

        # HID_TAP
        cls._click_via_hid(pos, click_type, btn, ev_down, ev_up, down_up_delay)

    @staticmethod
    def _click_via_pid(
        pos: tuple[float, float],
        pid: int,
        click_type: ClickType,
        btn,
        ev_down,
        ev_up,
        delay: float,
    ) -> None:
        # Move event để app cập nhật hover state
        move = CGEventCreateMouseEvent(None, kCGEventMouseMoved, pos, btn)
        CGEventPostToPid(pid, move)
        time.sleep(0.01)

        if click_type == ClickType.DOUBLE:
            for state in (1, 2):
                d = CGEventCreateMouseEvent(
                    None, kCGEventLeftMouseDown, pos, kCGMouseButtonLeft
                )
                CGEventSetIntegerValueField(d, kCGMouseEventClickState, state)
                CGEventPostToPid(pid, d)
                time.sleep(delay)
                u = CGEventCreateMouseEvent(
                    None, kCGEventLeftMouseUp, pos, kCGMouseButtonLeft
                )
                CGEventSetIntegerValueField(u, kCGMouseEventClickState, state)
                CGEventPostToPid(pid, u)
                time.sleep(0.05)
        else:
            d = CGEventCreateMouseEvent(None, ev_down, pos, btn)
            CGEventPostToPid(pid, d)
            time.sleep(delay)
            u = CGEventCreateMouseEvent(None, ev_up, pos, btn)
            CGEventPostToPid(pid, u)

    @staticmethod
    def _click_via_hid(
        pos: tuple[float, float],
        click_type: ClickType,
        btn,
        ev_down,
        ev_up,
        delay: float,
    ) -> None:
        # Move trước (để app + hệ thống biết cursor ở đâu)
        move = CGEventCreateMouseEvent(None, kCGEventMouseMoved, pos, btn)
        CGEventPost(kCGHIDEventTap, move)
        time.sleep(0.01)

        if click_type == ClickType.DOUBLE:
            for state in (1, 2):
                d = CGEventCreateMouseEvent(
                    None, kCGEventLeftMouseDown, pos, kCGMouseButtonLeft
                )
                CGEventSetIntegerValueField(d, kCGMouseEventClickState, state)
                CGEventPost(kCGHIDEventTap, d)
                time.sleep(delay)
                u = CGEventCreateMouseEvent(
                    None, kCGEventLeftMouseUp, pos, kCGMouseButtonLeft
                )
                CGEventSetIntegerValueField(u, kCGMouseEventClickState, state)
                CGEventPost(kCGHIDEventTap, u)
                time.sleep(0.05)
        else:
            d = CGEventCreateMouseEvent(None, ev_down, pos, btn)
            CGEventPost(kCGHIDEventTap, d)
            time.sleep(delay)
            u = CGEventCreateMouseEvent(None, ev_up, pos, btn)
            CGEventPost(kCGHIDEventTap, u)

    @classmethod
    def click_in_window(
        cls,
        window_origin: tuple[float, float],
        local_x: float,
        local_y: float,
        pid: Optional[int] = None,
        click_type: ClickType = ClickType.LEFT,
        mode: ClickMode = ClickMode.HID_RESTORE,
    ) -> tuple[float, float]:
        """Click vào tọa độ local trong window. Trả về (gx, gy) global."""
        gx = window_origin[0] + local_x
        gy = window_origin[1] + local_y
        cls.click(gx, gy, pid=pid, click_type=click_type, mode=mode)
        return gx, gy

    @staticmethod
    def activate_app(pid: int) -> bool:
        """Bring process tới foreground để app nhận event."""
        try:
            from AppKit import NSRunningApplication, NSApplicationActivateIgnoringOtherApps

            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if app is None:
                return False
            return bool(app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))
        except Exception:
            return False
