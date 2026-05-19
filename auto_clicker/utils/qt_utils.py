"""Helper convert giữa numpy ndarray (BGR) và Qt QImage/QPixmap."""
from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage, QPixmap


def ndarray_bgr_to_qpixmap(img: np.ndarray) -> QPixmap:
    if img is None or img.size == 0:
        return QPixmap()
    if img.ndim == 2:
        h, w = img.shape
        qimg = QImage(img.data, w, h, w, QImage.Format.Format_Grayscale8)
    else:
        h, w, ch = img.shape
        if ch == 3:
            # BGR -> RGB
            rgb = img[:, :, ::-1].copy()
            qimg = QImage(
                rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888
            )
        elif ch == 4:
            # BGRA -> RGBA
            rgba = img[:, :, [2, 1, 0, 3]].copy()
            qimg = QImage(
                rgba.data, w, h, 4 * w, QImage.Format.Format_RGBA8888
            )
        else:
            return QPixmap()
    return QPixmap.fromImage(qimg.copy())
