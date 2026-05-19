"""Click engine - gửi mouse event vào process target mà không di chuyển cursor thật."""
from __future__ import annotations

import time
from enum import Enum

import Quartz
from Quartz import (
    CGEventCreateMouseEvent,
    CGEventPost,
    CGEventPostToPid,
    CGEventSetIntegerValueField,
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


class ClickEngine:
    """Gửi mouse click bằng CGEventPostToPid để không chiếm cursor thật.

    Ý tưởng:
    - Tạo CGEvent với toạ độ global screen (point, không phải pixel).
    - Post vào PID của process target -> app target nhận event như là user click.
    - Cursor thật của user không bị di chuyển.
    """

    @staticmethod
    def _post(event, pid: int | None) -> None:
        if pid and pid > 0:
            CGEventPostToPid(pid, event)
        else:
            CGEventPost(kCGHIDEventTap, event)

    @classmethod
    def click(
        cls,
        x: float,
        y: float,
        pid: int | None = None,
        click_type: ClickType = ClickType.LEFT,
        down_up_delay: float = 0.03,
    ) -> None:
        """Click tại (x, y) - global screen point.

        Args:
            x, y: tọa độ point trong screen (origin = top-left, không phải pixel).
            pid: PID của target process. Nếu None -> post tới HID tap (chiếm chuột).
            click_type: loại click.
            down_up_delay: delay giữa down và up (giây).
        """
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

        # Một số app cần move event đến vị trí đó trước khi click để cập nhật hover state.
        move_ev = CGEventCreateMouseEvent(None, kCGEventMouseMoved, pos, btn)
        cls._post(move_ev, pid)

        if click_type == ClickType.DOUBLE:
            # Double click = 2 lần down/up với clickState = 1 và 2
            for state in (1, 2):
                down = CGEventCreateMouseEvent(
                    None, kCGEventLeftMouseDown, pos, kCGMouseButtonLeft
                )
                CGEventSetIntegerValueField(down, kCGMouseEventClickState, state)
                cls._post(down, pid)
                time.sleep(down_up_delay)
                up = CGEventCreateMouseEvent(
                    None, kCGEventLeftMouseUp, pos, kCGMouseButtonLeft
                )
                CGEventSetIntegerValueField(up, kCGMouseEventClickState, state)
                cls._post(up, pid)
                time.sleep(0.04)
        else:
            down = CGEventCreateMouseEvent(None, ev_down, pos, btn)
            cls._post(down, pid)
            time.sleep(down_up_delay)
            up = CGEventCreateMouseEvent(None, ev_up, pos, btn)
            cls._post(up, pid)

    @classmethod
    def click_in_window(
        cls,
        window_origin: tuple[float, float],
        local_x: float,
        local_y: float,
        pid: int | None = None,
        click_type: ClickType = ClickType.LEFT,
    ) -> tuple[float, float]:
        """Click vào tọa độ local trong window. Trả về (gx, gy) global đã click."""
        gx = window_origin[0] + local_x
        gy = window_origin[1] + local_y
        cls.click(gx, gy, pid=pid, click_type=click_type)
        return gx, gy
