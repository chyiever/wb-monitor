"""
`src/plot_interaction.py` 提供统一的实时绘图交互辅助组件，核心是 `ZoomablePlotViewBox`。

这个模块不处理业务数据，而是把多个图之间应保持一致的交互语义集中起来。当前约定包括左键框选放大、`Shift + 左键拖拽` 横向平移、滚轮缩放以及右键 `View All` 恢复全视图。主窗口中的时域图、监测图和 Time-Space 图都复用这一交互层。

把这些规则抽成独立模块，能显著降低维护成本，也能避免不同图之间出现不一致的交互行为。
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import QAction, QMenu
import pyqtgraph as pg


class ZoomablePlotViewBox(pg.ViewBox):
    """ViewBox with stable rectangle-zoom behavior for realtime plots."""

    sigManualRangeChange = pyqtSignal()
    sigViewAllRequested = pyqtSignal()

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("enableMenu", False)
        super().__init__(*args, **kwargs)
        self.setMouseMode(self.RectMode)

    def wheelEvent(self, ev, axis=None):
        super().wheelEvent(ev, axis=axis)
        self.sigManualRangeChange.emit()

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() == Qt.LeftButton and (ev.modifiers() & Qt.ShiftModifier):
            last_pos = self.mapToView(ev.lastPos())
            current_pos = self.mapToView(ev.pos())
            delta = last_pos - current_pos
            self.translateBy(x=delta.x(), y=0.0)
            ev.accept()
            self.sigManualRangeChange.emit()
            return

        super().mouseDragEvent(ev, axis=axis)
        if ev.button() == Qt.LeftButton and ev.isFinish():
            self.sigManualRangeChange.emit()

    def mouseClickEvent(self, ev):
        if ev.button() != Qt.RightButton:
            super().mouseClickEvent(ev)
            return

        menu = QMenu()
        view_all_action = QAction("View All", menu)
        menu.addAction(view_all_action)
        selected = menu.exec_(QCursor.pos())
        if selected is view_all_action:
            self.sigViewAllRequested.emit()
        ev.accept()
