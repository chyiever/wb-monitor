"""Small vector icons drawn locally; no platform emoji/font dependency."""
from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF


def tab_icon(name):
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(QPen(QColor('#167ca8'), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if name == 'View':
        painter.drawRoundedRect(3, 5, 26, 22, 4, 4)
        painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in [(6,17),(10,17),(13,10),(17,23),(21,14),(26,14)]]))
    elif name == 'eDAS':
        painter.drawRoundedRect(9, 9, 14, 14, 3, 3)
        for pos in (11, 16, 21):
            for a, b, c, d in ((pos,4,pos,8),(pos,24,pos,28),(4,pos,8,pos),(24,pos,28,pos)):
                painter.drawLine(a,b,c,d)
        painter.drawEllipse(13,13,6,6)
    elif name == 'FIP':
        painter.drawEllipse(12,3,8,8)
        painter.drawLine(16,11,16,18)
        painter.drawLine(6,18,26,18)
        painter.drawLine(6,18,6,22)
        painter.drawLine(26,18,26,22)
        painter.drawRoundedRect(2,22,8,7,2,2)
        painter.drawRoundedRect(22,22,8,7,2,2)
    elif name == 'Setting':
        for x, y in ((8,11),(16,21),(24,14)):
            painter.drawLine(x,4,x,28)
            painter.setBrush(QColor('#eef8fc'))
            painter.drawEllipse(QPointF(x,y),3.5,3.5)
    else:
        painter.drawEllipse(4,4,24,24)
        painter.drawEllipse(10,10,12,12)
        painter.drawLine(16,16,26,6)
    painter.end()
    return QIcon(pixmap)
