from __future__ import annotations

from functools import lru_cache

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


@lru_cache(maxsize=256)
def make_icon(name: str, color: str, size: int = 32) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen_width = max(1.5, size * 2.5 / 32)
    pen = QPen(QColor(color))
    pen.setWidthF(pen_width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    m = size * 0.15  # 여백
    inner = size - 2 * m

    if name == "refresh":
        rect = QRectF(m, m, inner, inner)
        painter.drawArc(rect, 40 * 16, 280 * 16)
        # 화살촉
        end_angle_rad = 0.698  # 40도(rad)
        cx, cy, r = size / 2, size / 2, inner / 2
        import math
        ax = cx + r * math.cos(end_angle_rad)
        ay = cy - r * math.sin(end_angle_rad)
        p1 = QPointF(ax, ay)
        p2 = QPointF(ax - size * 0.14, ay - size * 0.02)
        p3 = QPointF(ax - size * 0.02, ay + size * 0.14)
        painter.setBrush(QColor(color))
        painter.drawPolygon(p1, p2, p3)

    elif name == "plus":
        cx, cy = size / 2, size / 2
        painter.drawLine(QPointF(cx, m), QPointF(cx, size - m))
        painter.drawLine(QPointF(m, cy), QPointF(size - m, cy))

    elif name == "gear":
        cx, cy, r = size / 2, size / 2, inner / 2.6
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), r, r)
        painter.drawEllipse(QPointF(cx, cy), r * 0.35, r * 0.35)
        import math
        for i in range(6):
            ang = math.radians(i * 60)
            x1 = cx + r * math.cos(ang)
            y1 = cy + r * math.sin(ang)
            x2 = cx + (r + size * 0.14) * math.cos(ang)
            y2 = cy + (r + size * 0.14) * math.sin(ang)
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    elif name == "play":
        painter.setBrush(QColor(color))
        pts = [QPointF(m + inner * 0.1, m), QPointF(m + inner * 0.1, size - m),
               QPointF(size - m, size / 2)]
        painter.drawPolygon(*pts)

    elif name == "stop":
        painter.setBrush(QColor(color))
        r = size * 0.18
        painter.drawRoundedRect(QRectF(m, m, inner, inner), r, r)

    elif name == "log":
        rect = QRectF(m, m * 0.8, inner, size - 2 * m * 0.8)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, size * 0.06, size * 0.06)
        for i in range(3):
            y = rect.top() + rect.height() * (0.3 + i * 0.22)
            painter.drawLine(QPointF(rect.left() + rect.width() * 0.18, y),
                              QPointF(rect.right() - rect.width() * 0.18, y))

    elif name == "dots":
        cx = size / 2
        painter.setBrush(QColor(color))
        r = size * 0.06
        for i in (-1, 0, 1):
            cy = size / 2 + i * size * 0.24
            painter.drawEllipse(QPointF(cx, cy), r, r)

    elif name == "monitor":
        rect = QRectF(m, m, inner, inner * 0.7)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, size * 0.05, size * 0.05)
        cx = size / 2
        stand_top = rect.bottom()
        stand_bottom = size - m * 0.6
        painter.drawLine(QPointF(cx, stand_top), QPointF(cx, stand_bottom))
        painter.drawLine(QPointF(cx - inner * 0.22, stand_bottom),
                          QPointF(cx + inner * 0.22, stand_bottom))

    elif name == "server":
        h = inner * 0.34
        for i in range(2):
            y = m + i * (h + inner * 0.12)
            rect = QRectF(m, y, inner, h)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, size * 0.04, size * 0.04)
            dot_cx = rect.left() + rect.width() * 0.18
            dot_cy = rect.top() + rect.height() * 0.5
            painter.setBrush(QColor(color))
            painter.drawEllipse(QPointF(dot_cx, dot_cy), size * 0.025, size * 0.025)
            painter.setBrush(Qt.BrushStyle.NoBrush)

    elif name == "trash":
        top = m * 1.3
        body = QRectF(m * 1.2, top, size - m * 2.4, size - top - m * 0.6)
        painter.drawRect(body)
        painter.drawLine(QPointF(m * 0.7, top), QPointF(size - m * 0.7, top))
        lid_w = inner * 0.4
        painter.drawLine(QPointF(size / 2 - lid_w / 2, top),
                          QPointF(size / 2 - lid_w / 2, top - size * 0.08))
        painter.drawLine(QPointF(size / 2 + lid_w / 2, top),
                          QPointF(size / 2 + lid_w / 2, top - size * 0.08))
        painter.drawLine(QPointF(size / 2 - lid_w / 2, top - size * 0.08),
                          QPointF(size / 2 + lid_w / 2, top - size * 0.08))

    elif name == "arrow_right":
        cy = size / 2
        painter.drawLine(QPointF(m, cy), QPointF(size - m, cy))
        head = inner * 0.35
        painter.drawLine(QPointF(size - m, cy), QPointF(size - m - head, cy - head))
        painter.drawLine(QPointF(size - m, cy), QPointF(size - m - head, cy + head))

    elif name == "arrow_down":
        cx = size / 2
        painter.drawLine(QPointF(cx, m), QPointF(cx, size - m))
        head = inner * 0.35
        painter.drawLine(QPointF(cx, size - m), QPointF(cx - head, size - m - head))
        painter.drawLine(QPointF(cx, size - m), QPointF(cx + head, size - m - head))

    elif name == "folder":
        path = QPainterPath()
        left, top = m, m + inner * 0.12
        w, h = inner, inner * 0.75
        path.moveTo(left, top)
        path.lineTo(left + w * 0.35, top)
        path.lineTo(left + w * 0.45, top - inner * 0.12)
        path.lineTo(left + w, top - inner * 0.12)
        path.lineTo(left + w, top + h)
        path.lineTo(left, top + h)
        path.closeSubpath()
        painter.drawPath(path)

    painter.end()
    return QIcon(pixmap)
