"""Keep draft-dialog combo popups inside their owner and recognize popup clicks."""
from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication, QComboBox, QDialog


class DialogComboBox(QComboBox):
    def showPopup(self):
        owner = self.window()
        super().showPopup()
        if not isinstance(owner, QDialog):
            return
        bounds = owner.rect().translated(owner.mapToGlobal(QPoint(0, 0))).adjusted(6, 6, -6, -6)
        screen = QApplication.screenAt(self.mapToGlobal(self.rect().center()))
        if screen is not None:
            bounds = bounds.intersected(screen.availableGeometry())
        if bounds.isEmpty():
            return
        popup = self.view().window()
        popup.setMaximumSize(bounds.size())
        popup.resize(min(popup.width(), bounds.width()), min(popup.height(), bounds.height()))
        popup.move(max(bounds.left(), min(popup.x(), bounds.right() - popup.width() + 1)),
                   max(bounds.top(), min(popup.y(), bounds.bottom() - popup.height() + 1)))


def contains_owned_combo_popup(dialog, global_position):
    # Native popup windows are not consistently reported as QWidget descendants
    # of the dialog. The editor's view gives us its explicit ownership instead.
    return any(combo.view().isVisible() and combo.view().window().frameGeometry().contains(global_position)
               for combo in dialog.findChildren(QComboBox))
