"""Image matcher - tìm template image trong screenshot bằng OpenCV."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass
class MatchResult:
    found: bool
    confidence: float
    # Top-left của match trong tọa độ ảnh haystack (pixel)
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


class ImageMatcher:
    """Template matching với multi-scale + grayscale option để robust hơn."""

    def __init__(
        self,
        threshold: float = 0.85,
        multi_scale: bool = True,
        scales: tuple[float, ...] = (1.0, 0.9, 1.1, 0.8, 1.2, 0.75, 1.25),
        grayscale: bool = True,
    ):
        self.threshold = threshold
        self.multi_scale = multi_scale
        self.scales = scales
        self.grayscale = grayscale

    @staticmethod
    def load_template(path: str) -> Optional[np.ndarray]:
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        return img

    def find(
        self,
        haystack: np.ndarray,
        template: np.ndarray,
        roi: Optional[tuple[int, int, int, int]] = None,
    ) -> MatchResult:
        """Tìm template trong haystack.

        Args:
            haystack: ảnh BGR.
            template: ảnh BGR.
            roi: (x, y, w, h) - giới hạn vùng tìm trong haystack.

        Returns:
            MatchResult.
        """
        if haystack is None or template is None:
            return MatchResult(False, 0.0, 0, 0, 0, 0)

        offset_x = offset_y = 0
        search = haystack
        if roi is not None:
            rx, ry, rw, rh = roi
            rx = max(0, rx)
            ry = max(0, ry)
            rw = min(haystack.shape[1] - rx, rw)
            rh = min(haystack.shape[0] - ry, rh)
            if rw <= 0 or rh <= 0:
                return MatchResult(False, 0.0, 0, 0, 0, 0)
            search = haystack[ry : ry + rh, rx : rx + rw]
            offset_x, offset_y = rx, ry

        if self.grayscale:
            search_p = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
            template_p = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        else:
            search_p = search
            template_p = template

        best = MatchResult(False, 0.0, 0, 0, 0, 0)

        scales = self.scales if self.multi_scale else (1.0,)
        for s in scales:
            if s == 1.0:
                t = template_p
            else:
                new_w = max(8, int(template_p.shape[1] * s))
                new_h = max(8, int(template_p.shape[0] * s))
                if (
                    new_w >= search_p.shape[1]
                    or new_h >= search_p.shape[0]
                ):
                    continue
                t = cv2.resize(
                    template_p,
                    (new_w, new_h),
                    interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC,
                )
            if t.shape[0] >= search_p.shape[0] or t.shape[1] >= search_p.shape[1]:
                continue
            try:
                res = cv2.matchTemplate(search_p, t, cv2.TM_CCOEFF_NORMED)
            except cv2.error:
                continue
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val > best.confidence:
                best = MatchResult(
                    found=max_val >= self.threshold,
                    confidence=float(max_val),
                    x=int(max_loc[0]) + offset_x,
                    y=int(max_loc[1]) + offset_y,
                    width=t.shape[1],
                    height=t.shape[0],
                )

        return best

    def find_all(
        self,
        haystack: np.ndarray,
        template: np.ndarray,
        max_results: int = 20,
        roi: Optional[tuple[int, int, int, int]] = None,
    ) -> list[MatchResult]:
        """Tìm tất cả vị trí > threshold (chỉ dùng scale 1.0 để đơn giản)."""
        if haystack is None or template is None:
            return []

        offset_x = offset_y = 0
        search = haystack
        if roi is not None:
            rx, ry, rw, rh = roi
            search = haystack[ry : ry + rh, rx : rx + rw]
            offset_x, offset_y = rx, ry

        if self.grayscale:
            search_p = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
            template_p = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        else:
            search_p = search
            template_p = template

        if (
            template_p.shape[0] >= search_p.shape[0]
            or template_p.shape[1] >= search_p.shape[1]
        ):
            return []

        res = cv2.matchTemplate(search_p, template_p, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= self.threshold)
        candidates = sorted(
            ((float(res[y, x]), int(x), int(y)) for x, y in zip(xs, ys)),
            key=lambda c: -c[0],
        )

        results: list[MatchResult] = []
        h, w = template_p.shape[:2]
        for conf, x, y in candidates:
            # NMS đơn giản: skip nếu quá gần kết quả đã có
            too_close = any(
                abs(x - r.x) < w * 0.5 and abs(y - r.y) < h * 0.5
                for r in results
            )
            if too_close:
                continue
            results.append(
                MatchResult(True, conf, x + offset_x, y + offset_y, w, h)
            )
            if len(results) >= max_results:
                break
        return results
