"""Window themes and matching custom dropdowns."""
from PySide6.QtCore import Qt, QRectF, QSize, QEvent
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QListView, QStyledItemDelegate, QStyle, QSizePolicy

THEMES = {
    'graphite': dict(name='石墨黑', bg='#17181c', surface='#22242b', track='#292c34',
        border='#3a3d46', text='#f5f5f7', secondary='#b8bdc9', accent='#a5baff',
        good='#8cdfb8', warn='#ffc38f', hover='#353b4c', band='#334e43'),
    'porcelain': dict(name='瓷白蓝', bg='#f6f8fc', surface='#e9edf6', track='#e0e6f2',
        border='#b9c5db', text='#1c2c49', secondary='#526581', accent='#335ed4',
        good='#157a58', warn='#ac4c20', hover='#dbe5fc', band='#c4e4d5'),
    'paper': dict(name='暖纸色', bg='#f6f0e5', surface='#eae1d3', track='#e4d9c9',
        border='#c8b99f', text='#3c3025', secondary='#75624e', accent='#906437',
        good='#42694b', warn='#ae491e', hover='#e5d4ba', band='#cdd9c3'),
    'midnight': dict(name='午夜蓝', bg='#111a30', surface='#1c2946', track='#243354',
        border='#3c5075', text='#edf3ff', secondary='#b4c3df', accent='#8cb8ff',
        good='#7ee0cd', warn='#ffbb98', hover='#30456d', band='#294e55'),
    'forest': dict(name='森林绿', bg='#15251e', surface='#20372c', track='#2a4234',
        border='#486552', text='#eef4e9', secondary='#b5c9b8', accent='#d1db97',
        good='#b9df9b', warn='#f1bb85', hover='#3b5741', band='#496242'),
    'plum': dict(name='暮紫色', bg='#241c2e', surface='#33283f', track='#40334e',
        border='#635271', text='#f6eefb', secondary='#cdbbd8', accent='#d0b5fa',
        good='#b4e1cb', warn='#ffc2b1', hover='#574367', band='#3d5753'),
}

def theme_for(key):
    return THEMES.get(key, THEMES['graphite'])

def settings_style(key):
    t = theme_for(key)
    return f'''
        QDialog {{ background: {t['bg']}; color: {t['text']}; }}
        QLabel {{ color: {t['text']}; font: 13px "Microsoft YaHei UI"; background: transparent; }}
        QListView {{ background: {t['surface']}; border: 1px solid {t['border']};
            border-radius: 10px; padding: 5px; outline: none; }}
        QComboBoxPrivateContainer {{ background: {t['surface']}; border: none; }}
        QPushButton {{ background: {t['surface']}; color: {t['text']};
            border: 1px solid {t['border']}; border-radius: 8px;
            font: 13px "Microsoft YaHei UI"; padding: 10px 26px; min-width: 44px; }}
        QPushButton:hover {{ background: {t['hover']}; border-color: {t['accent']}; }}
        QPushButton:pressed {{ background: {t['track']}; }}
        QPushButton:focus {{ border-color: {t['accent']}; }}
        QPushButton[primary="true"] {{ background: {t['accent']}; color: {t['bg']}; border-color: {t['accent']}; }}
        QPushButton[primary="true"]:hover {{ background: {t['text']}; border-color: {t['text']}; }}
        QScrollBar:vertical {{ background: transparent; width: 9px; margin: 4px 0; }}
        QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 4px; min-height: 26px; }}
        QScrollBar::handle:vertical:hover {{ background: {t['accent']}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    '''

class OptionDelegate(QStyledItemDelegate):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner

    def sizeHint(self, option, index):
        return QSize(320, 42)

    def paint(self, painter, option, index):
        t = self.owner.colors
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(3, 2, -3, -2)
        current = index.row() == self.owner.currentIndex()
        highlighted = (index.row() == self.owner.hovered_row if self.owner.hovered_row is not None
                       else bool(option.state & QStyle.StateFlag.State_Selected))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t['hover'] if highlighted else t['track'] if current else t['surface']))
        painter.drawRoundedRect(rect, 7, 7)
        font = QFont('Microsoft YaHei UI')
        font.setPixelSize(13)
        painter.setFont(font)
        painter.setPen(QColor(t['accent'] if current else t['text']))
        label_rect = rect.adjusted(12, 0, -34, 0)
        label = painter.fontMetrics().elidedText(str(index.data()), Qt.TextElideMode.ElideRight, int(label_rect.width()))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        if current:
            painter.setPen(QPen(QColor(t['accent']), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            x, y = rect.right()-21, rect.center().y()
            painter.drawLine(int(x-4), int(y), int(x-1), int(y+3))
            painter.drawLine(int(x-1), int(y+3), int(x+5), int(y-4))
        painter.restore()

class ChoiceBox(QComboBox):
    def __init__(self):
        super().__init__()
        self.colors = theme_for('graphite')
        self.hovered_row = None
        self.setEditable(False)
        self.setMinimumHeight(45)
        self.setMinimumWidth(270)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(18)
        self.setMaxVisibleItems(7)
        view = QListView(self)
        view.setMouseTracking(True)
        view.viewport().setMouseTracking(True)
        view.viewport().installEventFilter(self)
        view.entered.connect(self.hover_option)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setView(view)
        self.setItemDelegate(OptionDelegate(self))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def hover_option(self, index):
        self.hovered_row = index.row()
        self.view().viewport().update()

    def eventFilter(self, watched, event):
        if watched is self.view().viewport() and event.type() == QEvent.Type.Leave:
            self.hovered_row = None
            self.view().viewport().update()
        return super().eventFilter(watched, event)

    def showPopup(self):
        self.hovered_row = None
        super().showPopup()
        self.update()

    def hidePopup(self):
        self.hovered_row = None
        super().hidePopup()
        self.update()

    def paintEvent(self, event):
        t = self.colors
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        emphasized = self.underMouse() or self.hasFocus() or self.view().isVisible()
        p.setPen(QPen(QColor(t['accent'] if emphasized else t['border']), 1.2))
        p.setBrush(QColor(t['hover'] if self.underMouse() else t['surface']))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 9, 9)
        font = QFont('Microsoft YaHei UI')
        font.setPixelSize(13)
        p.setFont(font)
        p.setPen(QColor(t['text']))
        label = p.fontMetrics().elidedText(self.currentText(), Qt.TextElideMode.ElideRight, self.width()-52)
        p.drawText(QRectF(14, 0, self.width()-52, self.height()), Qt.AlignmentFlag.AlignVCenter, label)
        p.setPen(QPen(QColor(t['accent'] if emphasized else t['secondary']), 1.7,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        x, y = self.width()-23, self.height()/2
        p.drawLine(int(x-4), int(y-2), int(x), int(y+2))
        p.drawLine(int(x), int(y+2), int(x+4), int(y-2))
        p.end()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)
