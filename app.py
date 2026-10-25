"""声准: a bar-first, fixed-aspect-ratio vocal intonation meter for Windows."""
import json
import logging
import math
import os
from pathlib import Path
import sys
import time
from datetime import datetime
import uuid
from PySide6.QtCore import Qt, QRectF, QPointF, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QLinearGradient, QIcon, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget, QDialog, QFormLayout, QDialogButtonBox, QMessageBox
import sounddevice as sd
from audio_engine import AudioEngine
from pitch_core import Pitch, note_name, midi_for, cents_from
from device_catalog import input_options, matching_option
from ui_theme import THEMES, theme_for, settings_style, ChoiceBox
from recording_audio import Recorder

DATA_DIR = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'VocalPitch'
CONFIG = DATA_DIR / 'settings.json'
VERSION = '1.3.0'

def app_icon():
    image = QPixmap(128, 128)
    image.fill(QColor('#17181c'))
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor('#555a67'), 4))
    p.drawRoundedRect(QRectF(40, 13, 48, 102), 16, 16)
    p.fillRect(QRectF(42, 57, 44, 14), QColor('#a5baff'))
    p.setPen(QPen(QColor('#a5baff'), 5))
    p.drawLine(24, 64, 104, 64)
    p.end()
    return QIcon(image)

class SettingsDialog(QDialog):
    def __init__(self, config, parent):
        super().__init__(parent)
        self.setWindowTitle('声准 · 设置')
        self.setFixedWidth(460)
        form = QFormLayout(self)
        form.setContentsMargins(24, 26, 24, 24)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(18)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.devices = ChoiceBox()
        self.devices.addItem('系统默认麦克风', None)
        try:
            options = input_options()
        except Exception:
            options = []
        for option in options:
            self.devices.addItem(option.label, option.key)
        selected = matching_option(config.get('device'), options)
        if selected:
            self.devices.setCurrentIndex(self.devices.findData(selected.key))
        elif config.get('device'):
            self.devices.addItem('原设备不可用', config['device'])
            self.devices.setCurrentIndex(self.devices.count()-1)

        def number_choice(values, current, label):
            box = ChoiceBox()
            for value in sorted(set(values) | {current}):
                box.addItem(label(value), value)
            box.setCurrentIndex(box.findData(current))
            return box

        self.reference = number_choice([415., 415.3, 432., 435., 438., 439., 440.,
                                       441., 442., 443., 444., 445., 466.2],
                                      config['a4'], lambda value: f'{value:g} Hz')
        self.gate = number_choice(range(-75, -19, 5), config['gate'], lambda value: f'{value:g} dBFS')
        self.tolerance = number_choice([1, 2, 3, 5, 8, 10, 15, 20], config['tolerance'],
                                      lambda value: f'± {value:g} 音分')
        self.theme = ChoiceBox()
        for key, colors in THEMES.items():
            self.theme.addItem(colors['name'], key)
        self.theme.setCurrentIndex(self.theme.findData(config['theme']))
        self.notation = ChoiceBox()
        self.notation.addItem('Sharp · 升号 ♯', 'sharp')
        self.notation.addItem('Flat · 降号 ♭', 'flat')
        self.notation.setCurrentIndex(self.notation.findData(config['notation']))
        form.addRow('输入设备', self.devices)
        form.addRow('标准音 A4', self.reference)
        form.addRow('音名显示', self.notation)
        form.addRow('静音门限', self.gate)
        form.addRow('准音范围', self.tolerance)
        form.addRow('界面配色', self.theme)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存')
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setProperty('primary', True)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)
        self.theme.currentIndexChanged.connect(self.preview_theme)
        self.finished.connect(self.clear_preview)
        self.preview_theme()

    def preview_theme(self):
        key = self.theme.currentData()
        self.setStyleSheet(settings_style(key))
        for box in self.findChildren(ChoiceBox):
            box.colors = theme_for(key)
            box.view().viewport().update()
            box.update()
        self.parent().preview_theme = key
        self.parent().update()

    def clear_preview(self, result):
        self.parent().preview_theme = None
        self.parent().update()

    def values(self):
        return dict(device=self.devices.currentData(), a4=self.reference.currentData(),
                    gate=self.gate.currentData(), tolerance=self.tolerance.currentData(),
                    theme=self.theme.currentData(), notation=self.notation.currentData())

class PitchWindow(QWidget):
    BASE_W, BASE_H = 300, 800
    BAR_TOP, BAR_BOTTOM = 158., 728.
    BUTTONS = {'record':QRectF(16,746,88,35),'review':QRectF(110,746,102,35),
               'settings':QRectF(218,746,66,35)}
    PIN_RECT = QRectF(176,7,30,30)

    def __init__(self, capture=True):
        super().__init__()
        self.config = dict(a4=440., gate=-50, tolerance=5, height=800,
                           pin=False, device=None, theme='graphite', notation='sharp', key='auto')
        try:
            saved = json.loads(CONFIG.read_text(encoding='utf-8'))
            for key in self.config:
                if key in saved:
                    self.config[key] = saved[key]
            self.config['a4'] = max(400., min(480., float(self.config['a4'])))
            self.config['gate'] = max(-75, min(-20, int(self.config['gate'])))
            self.config['tolerance'] = max(1, min(20, int(self.config['tolerance'])))
            self.config['height'] = max(560, min(1600, int(self.config['height'])))
            if self.config['theme'] not in THEMES:
                self.config['theme'] = 'graphite'
            if self.config['notation'] not in ('sharp', 'flat'):
                self.config['notation'] = 'sharp'
            from tonality import valid_key
            if not valid_key(self.config['key']):
                self.config['key']='auto'
        except (OSError, ValueError, TypeError):
            pass
        self.preview_theme = None
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle('声准 · 实时声乐音准')
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setMinimumSize(210, 560)
        self.setMaximumSize(600, 1600)
        available = QApplication.primaryScreen().availableGeometry()
        height = max(560, min(int(self.config['height']), available.height() - 50))
        self.resize(round(height*3/8), height)
        self.move(available.right()-self.width()-45, available.top()+max(15, (available.height()-height)//2))
        self.result = Pitch(None, 0., -100)
        self.last_reading = 0.
        self.paused = False
        self.error = ''
        self.listening = False
        self.current_note = 69
        self.hovered = None
        self.drag = None
        self.resize_drag = None
        self.capture_enabled = capture
        self.review = None
        self._record_stopping = False
        self._closing = False
        self.recordings_dir=DATA_DIR/'Recordings'
        self.recorder=Recorder()
        self.recorder.started.connect(self.record_started)
        self.recorder.completed.connect(self.record_completed)
        self.recorder.failed.connect(self.record_error)
        self.recorder.measured.connect(self.on_pitch)
        self.engine = AudioEngine()
        self.engine.measured.connect(self.on_pitch)
        self.engine.state.connect(self.on_state)
        self.engine.failed.connect(self.on_error)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)
        if self.config['pin']:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        if capture:
            QTimer.singleShot(250, self.start_audio)

    @property
    def colors(self):
        return theme_for(self.preview_theme or self.config['theme'])

    def resolve_device(self):
        selected = self.config.get('device')
        if selected is None:
            return None
        option = matching_option(selected, input_options())
        if option:
            self.config['device'] = option.key
            return option.index
        apis = sd.query_hostapis()
        for i, device in enumerate(sd.query_devices()):
            key = f"{apis[device['hostapi']]['name']} | {device['name']}"
            if device['max_input_channels'] > 0 and key == selected:
                return i
        raise RuntimeError('麦克风不可用，请重新选择输入设备。')

    def start_audio(self):
        if self._closing or self.recorder.recording or self._record_stopping or (self.review is not None and self.review.isVisible()):
            return
        self.error = ''
        self.listening = False
        self.result = Pitch(None, 0., -100)
        try:
            self.engine.start(self.resolve_device(), self.config['gate'])
        except Exception as error:
            self.on_error(str(error))

    def on_state(self, state):
        self.listening = True
        self.update()

    def on_error(self, error):
        self.error = error
        self.listening = False
        self.result = Pitch(None, 0., -100)
        logging.error('Audio: %s', error)
        self.update()

    def on_pitch(self, result):
        if self.paused:
            return
        self.result = result
        self.last_reading = time.monotonic()
        if result.frequency:
            self.current_note = int(math.floor(midi_for(result.frequency, self.config['a4'])+.5))
        self.update()

    def tick(self):
        if self.result.frequency and time.monotonic()-self.last_reading > .25:
            self.result = Pitch(None, 0., self.result.db)
        self.update()

    def save(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self.config['height'] = self.height()
            temporary = CONFIG.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(CONFIG)
        except OSError:
            logging.exception('Could not save preferences')

    def resizeEvent(self, event):
        expected = round(self.height()*3/8)
        if self.width() != expected:
            self.resize(expected, self.height())
        super().resizeEvent(event)

    def paintEvent(self, event):
        t = self.colors
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(self.width()/300., self.height()/800.)
        p.setPen(QPen(QColor(t['border']), 1))
        p.setBrush(QColor(t['bg']))
        p.drawRoundedRect(QRectF(.5, .5, 299, 799), 15, 15)

        def text(x, y, w, h, value, size=12, color=None, weight=QFont.Weight.Normal,
                 align=Qt.AlignmentFlag.AlignCenter):
            p.setPen(QColor(color or t['text']))
            font = QFont('Microsoft YaHei UI')
            font.setPixelSize(size)
            font.setWeight(weight)
            p.setFont(font)
            p.drawText(QRectF(x, y, w, h), align, value)

        def box(rect, color, radius=8):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(rect, radius, radius)

        text(20, 10, 90, 26, '声准', 15, weight=QFont.Weight.DemiBold,
             align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if self.recorder.recording or self._record_stopping:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor('#f27989'))
            p.drawEllipse(QPointF(85,23),3,3)
            seconds=int(self.recorder.duration)
            text(92,12,70,22,f'{seconds//60:02}:{seconds%60:02}',11,t['secondary'])
        if self.hovered=='pin' or self.config['pin']:
            box(self.PIN_RECT,t['hover'] if self.hovered=='pin' else t['surface'])
        p.save()
        p.translate(191,22)
        if not self.config['pin']:
            p.rotate(30)
        color=QColor(t['accent'] if self.config['pin'] else t['secondary'])
        p.setPen(QPen(color,1.5,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
        p.setBrush(color if self.config['pin'] else Qt.BrushStyle.NoBrush)
        pin=QPainterPath()
        pin.moveTo(-4,-7);pin.lineTo(4,-7);pin.lineTo(3,-1)
        pin.lineTo(6,3);pin.lineTo(-6,3);pin.lineTo(-3,-1);pin.closeSubpath()
        p.drawPath(pin);p.drawLine(QPointF(0,3),QPointF(0,9))
        p.restore()
        for name, x, label in [('minimize', 214, '−'), ('close', 250, '×')]:
            if self.hovered == name:
                box(QRectF(x, 7, 30, 30), t['hover'])
            text(x, 7, 30, 30, label, 19, t['secondary'])

        active = bool(self.result.frequency) and not self.paused and not self.error
        delta = cents_from(self.result.frequency, self.current_note, self.config['a4']) if active else None
        color = t['good'] if active and abs(delta) <= self.config['tolerance'] else t['warn']
        text(18, 47, 128, 62, note_name(self.current_note, self.config['notation']) if active else '—',
             46, weight=QFont.Weight.DemiBold)
        number = ('+0.0' if abs(delta) < .05 else f'{delta:+.1f}') if active else '—'
        text(144, 59, 138, 45, number, 31, color if active else t['secondary'], QFont.Weight.DemiBold)
        text(18, 111, 128, 22, f'{self.result.frequency:.2f} Hz' if active else '', 12, t['secondary'])
        text(144, 111, 138, 22, '音分', 12, t['secondary'])

        top, bottom = self.BAR_TOP, self.BAR_BOTTOM
        center, length = (top+bottom)/2, bottom-top
        box(QRectF(67, top, 152, length), t['track'], 19)
        gradient = QLinearGradient(67, 0, 219, 0)
        gradient.setColorAt(0, QColor(t['track']))
        gradient.setColorAt(.5, QColor(t['surface']))
        gradient.setColorAt(1, QColor(t['track']))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(gradient)
        p.drawRoundedRect(QRectF(70, top+3, 146, length-6), 17, 17)
        half_band = self.config['tolerance']/100*length
        box(QRectF(69, center-half_band, 148, half_band*2), t['band'], 5)
        for tick in range(-50, 51, 5):
            y = center-tick/100*length
            major = tick in (-50, -25, 0, 25, 50)
            p.setPen(QPen(QColor(t['secondary'] if major else t['border']), 1))
            p.drawLine(QPointF(219, y), QPointF(232 if major else 226, y))
            if major:
                text(239, y-11, 42, 22, '0' if tick == 0 else f'{tick:+}', 12, t['secondary'],
                     align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        p.setPen(QPen(QColor(t['good']), 1.6))
        p.drawLine(QPointF(58, center), QPointF(232, center))
        if active:
            y = center-max(-50., min(50., delta))/100*length
            box(QRectF(60, y-5, 166, 10), color, 5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawPolygon(QPolygonF([QPointF(50, y), QPointF(37, y-8), QPointF(37, y+8)]))
        if self.error or self.result.clipped:
            box(QRectF(32, 693, 236, 28), t['surface'], 7)
            text(32, 693, 236, 28, '麦克风不可用 · 设置' if self.error else '输入过载', 12, t['warn'])

        for name, rect in self.BUTTONS.items():
            selected = (name=='record' and (self.recorder.recording or self._record_stopping)) or (name=='review' and self.review is not None and self.review.isVisible())
            if self.hovered == name or selected:
                box(rect, t['hover'] if self.hovered == name else t['surface'])
            label = {'record':'保存中' if self._record_stopping else '结束录音' if self.recorder.recording else '录音',
                     'review':'关闭复盘' if self.review is not None and self.review.isVisible() else '打开复盘','settings':'设置'}[name]
            text(rect.x(), rect.y(), rect.width(), rect.height(), label, 12,
                 '#f27989' if name=='record' and selected else t['accent'] if selected else t['text'])
        p.setPen(QPen(QColor(t['border']), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(137, 791), QPointF(163, 791))
        p.end()

    def logical(self, position):
        return QPointF(position.x()*300/self.width(), position.y()*800/self.height())

    def hit_test(self, point):
        if point.y() >= 784:
            return 'resize'
        if point.y() < 43:
            if self.PIN_RECT.contains(point):
                return 'pin'
            return 'close' if point.x() >= 250 else 'minimize' if point.x() >= 214 else 'move'
        for name, rect in self.BUTTONS.items():
            if rect.contains(point):
                return name
        if self.error and QRectF(32, 693, 236, 28).contains(point):
            return 'settings'
        return None

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        action = self.hit_test(self.logical(event.position()))
        if action == 'resize':
            self.resize_drag = (event.globalPosition().y(), self.height())
            self.grabMouse()
        elif action == 'close':
            self.close()
        elif action == 'minimize':
            self.showMinimized()
        elif action == 'move':
            self.drag = event.globalPosition().toPoint()-self.frameGeometry().topLeft()
        elif action == 'record':
            self.toggle_record()
        elif action == 'settings':
            self.open_settings()
        elif action == 'review':
            self.toggle_review()
        elif action == 'pin':
            visible=self.review is not None and self.review.isVisible()
            self.config['pin'] = not self.config['pin']
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.config['pin'])
            self.show()
            if visible:
                self.review.showNormal()
            self.save()
        self.update()

    def mouseMoveEvent(self, event):
        if self.resize_drag:
            start_y, start_h = self.resize_drag
            screen = self.screen().availableGeometry()
            maximum = max(560, min(1600, screen.bottom()-self.y()+1))
            height = max(560, min(maximum, round(start_h+event.globalPosition().y()-start_y)))
            self.resize(round(height*3/8), height)
        elif self.drag is not None:
            self.move(event.globalPosition().toPoint()-self.drag)
        else:
            self.hovered = self.hit_test(self.logical(event.position()))
            cursor = Qt.CursorShape.ArrowCursor
            if self.hovered == 'resize':
                cursor = Qt.CursorShape.SizeVerCursor
            elif self.hovered == 'move':
                cursor = Qt.CursorShape.SizeAllCursor
            elif self.hovered:
                cursor = Qt.CursorShape.PointingHandCursor
            self.setCursor(cursor)
            self.setToolTip(('取消置顶' if self.config['pin'] else '置顶') if self.hovered=='pin' else '')
            self.update()

    def leaveEvent(self, event):
        self.hovered = None
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.resize_drag:
            self.releaseMouse()
            self.save()
        self.drag = None
        self.resize_drag = None

    def open_settings(self):
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            before = (self.config['device'], self.config['a4'], self.config['gate'])
            self.config.update(dialog.values())
            self.recorder.gate=self.config['gate']
            after = (self.config['device'], self.config['a4'], self.config['gate'])
            self.save()
            if before != after:
                self.result = Pitch(None, 0., -100)
            if (before != after or self.error) and not self.paused and self.capture_enabled:
                self.start_audio()
            if self.review is not None:
                self.review.apply_config()
            self.update()
        dialog.deleteLater()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_R:
            self.toggle_record()
        elif event.key() == Qt.Key.Key_V:
            self.toggle_review()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self._closing=True
        if self.recorder.recording:
            self._record_stopping=True
            self.recorder.stop()
            if self.review is not None:
                self.review.player.pause()
            event.ignore()
            return
        if self.review is not None:
            self.review.shutdown()
            self.review.close()
        self.timer.stop()
        self.engine.stop()
        self.save()
        event.accept()

    def open_review(self):
        if self._closing:
            return
        if not self.recorder.recording:
            self.paused=True
            self.engine.stop()
            self.result=Pitch(None,0.,-100)
        if self.review is None:
            from recording_window import RecordingWindow
            self.review=RecordingWindow(self,self.recordings_dir)
            self.review.closed.connect(self.review_closed)
        self.review.showNormal()
        self.review.raise_()
        self.review.activateWindow()
        self.update()

    def review_closed(self):
        self.paused=False
        if not self._closing and not self.recorder.recording and self.capture_enabled:
            self.start_audio()
        self.update()

    def toggle_review(self):
        if self.review is not None and self.review.isVisible():
            self.review.close()
        else:
            self.open_review()

    def toggle_record(self):
        if self._closing or self._record_stopping:
            return
        if self.recorder.recording:
            self._record_stopping=True
            self.recorder.stop()
            if self.review is not None:
                self.review.status.setText('正在保存录音…')
                self.review.update_controls()
        else:
            self.engine.stop()
            self.paused=False
            self.listening=False
            self.error=''
            self.result=Pitch(None,0.,-100)
            if self.review is not None:
                self.review.player.pause()
                self.review.cancel_job()
                self.review.busy=False
                self.review.progress.hide()
            try:
                name=datetime.now().strftime('录音-%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:5]+'.wav'
                self.recorder.start(self.recordings_dir/name,self.resolve_device(),self.config['gate'])
            except Exception as error:
                self.record_error(str(error))
                if self.capture_enabled:
                    self.start_audio()
            if self.review is not None:
                self.review.update_controls()
        self.update()

    def record_started(self,rate):
        self.listening=True
        self.update()

    def record_error(self,message):
        logging.error('Recording: %s',message)
        self.on_error(message)
        if not self._closing:
            QMessageBox.warning(self,'录音',message)

    def record_completed(self,path):
        self._record_stopping=False
        if self._closing:
            QTimer.singleShot(0,self.close)
        elif path:
            self.open_review()
            self.review.load_path(path)
        else:
            self.paused=False
            if self.capture_enabled:
                self.start_audio()
        self.update()

def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=DATA_DIR/'app.log', level=logging.WARNING,
                        format='%(asctime)s %(levelname)s %(message)s', encoding='utf-8')
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('VocalPitch.Desktop.1')
    application = QApplication(sys.argv)
    application.setApplicationName('声准')
    application.setOrganizationName('VocalPitch')
    application.setWindowIcon(app_icon())
    application.setStyle('Fusion')
    window = PitchWindow(capture=not any(flag in sys.argv for flag in ('--preview','--review-smoke','--workflow-smoke')))
    window.show()
    if '--review' in sys.argv:
        QTimer.singleShot(0,window.open_review)
    if '--workflow-smoke' in sys.argv:
        def workflow_check_start():
            window.recordings_dir=Path(sys.argv[sys.argv.index('--workflow-smoke')+1])
            window.toggle_record()
            deadline=time.monotonic()+20
            state={'stopped':False,'no_popup':True,'live':False}
            checker=QTimer(window)
            def check():
                if window.recorder.recording and not state['stopped']:
                    state['no_popup'] &= window.review is None or not window.review.isVisible()
                    state['live'] |= window.listening and not window.paused
                    if window.recorder.duration>=.3:
                        window.toggle_record();state['stopped']=True
                ready=window.review is not None and window.review.track is not None and not window.review.busy
                if not ready and time.monotonic()<deadline:return
                result=dict(version=VERSION,frames=window.recorder.frames,no_popup=state['no_popup'],
                    live=state['live'],loaded=ready,error=window.error)
                if ready:
                    view=window.review;clip=view.clip
                    result['automatic_window']=view.isVisible()
                    window.toggle_review();result['toggle_close']=not view.isVisible()
                    window.toggle_review();result['toggle_open']=view.isVisible() and view.clip is clip
                    result['key_options']=view.key_choice.count()
                    window.grab().save(str(DATA_DIR/'workflow-small.png'))
                (DATA_DIR/'workflow-smoke.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
                checker.stop()
                for widget in application.topLevelWidgets():
                    if widget.metaObject().className()=='QMessageBox':widget.close()
                window.close()
            checker.timeout.connect(check);checker.start(50)
        QTimer.singleShot(0,workflow_check_start)
    if '--review-smoke' in sys.argv:
        def review_check_start():
            window.open_review()
            review=window.review
            review.load_path(sys.argv[sys.argv.index('--review-smoke')+1])
            deadline=time.monotonic()+25
            checker=QTimer(window)
            def check():
                if review.busy and time.monotonic()<deadline:
                    return
                result=dict(version=VERSION,loaded=review.track is not None,error=review.status.text())
                if review.track:
                    anchor=review.roll.TOP+review.roll.grid_height/2
                    pitch=review.roll.midi_for_y(anchor)
                    height=review.roll.row_height
                    review.roll.zoom_vertical(1.5,anchor)
                    result.update(segments=len(review.track.segments),frames=len(review.track.times),
                        duration=review.clip.duration,zoom=review.roll.row_height>height,
                        anchored=abs(review.roll.midi_for_y(anchor)-pitch)<1/review.roll.row_height)
                    review.grab().save(str(DATA_DIR/'review-smoke.png'))
                (DATA_DIR/'review-smoke.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
                checker.stop()
                for widget in application.topLevelWidgets():
                    if widget.metaObject().className()=='QMessageBox':
                        widget.close()
                window.close()
            checker.timeout.connect(check)
            checker.start(100)
        QTimer.singleShot(0,review_check_start)
    if '--smoke-test' in sys.argv:
        def report():
            report_data = dict(listening=window.listening, error=window.error,
                               samplerate=window.engine.sample_rate, db=window.result.db,
                               size=[window.width(), window.height()], theme=window.config['theme'],
                               notation=window.config['notation'], version=VERSION)
            (DATA_DIR/'smoke-test.json').write_text(json.dumps(report_data, ensure_ascii=False), encoding='utf-8')
            window.close()
        QTimer.singleShot(4500, report)
    sys.exit(application.exec())

if __name__ == '__main__':
    main()
