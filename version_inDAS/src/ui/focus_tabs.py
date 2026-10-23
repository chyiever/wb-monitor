"""Keep native tab behavior while omitting the dotted focus rectangle."""
from PyQt5.QtWidgets import QTabBar, QStyle, QStyleOptionTab, QStylePainter


class PlainTabBar(QTabBar):
    def paintEvent(self, event):
        painter = QStylePainter(self)
        order = [i for i in range(self.count()) if i != self.currentIndex()]
        if self.currentIndex() >= 0:
            order.append(self.currentIndex())
        for index in order:
            if hasattr(self, 'isTabVisible') and not self.isTabVisible(index):
                continue
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            option.state &= ~QStyle.State_HasFocus
            painter.drawControl(QStyle.CE_TabBarTab, option)
