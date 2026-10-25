"""Validate the bar-first interface, dropdowns, theme preview, and save/cancel."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer, QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QAbstractSpinBox
import app as app_module
from app import PitchWindow, SettingsDialog, app_icon
from pitch_core import Pitch, cents_from, frequency_for, note_name
from ui_theme import THEMES

application = QApplication([])
application.setStyle('Fusion')
output = Path('verification/v2')
output.mkdir(parents=True, exist_ok=True)
with TemporaryDirectory() as folder, patch.object(app_module, 'CONFIG', Path(folder)/'settings.json'):
    app_module.CONFIG.write_text(json.dumps(dict(a4=440., gate=-50, tolerance=10,
        height=613, pin=False, device=None, locked=True, target=57)), encoding='utf-8')
    window = PitchWindow(capture=False)
    window.resize(300, 800)
    window.show()
    assert 'locked' not in window.config and 'target' not in window.config
    assert (window.BAR_BOTTOM-window.BAR_TOP)/window.BASE_H > .70
    window.listening = True
    for offset in [-50.1, -49.9, 49.9, 50.1]:
        window.on_pitch(Pitch(440*2**(offset/1200), .99, -20))
        assert abs(cents_from(window.result.frequency, window.current_note)) <= 50
    for key in THEMES:
        window.config['theme'] = key
        window.on_pitch(Pitch(440*2**(12.3/1200), .997, -21))
        application.processEvents()
        window.grab().save(str(output/f'window-{key}.png'))
    window.config['theme'] = 'graphite'
    window.on_pitch(Pitch(440*2**(-3.2/1200), .999, -25))
    application.processEvents()
    window.grab().save(str(output/'window-in-tune.png'))
    QTest.keyClick(window, Qt.Key.Key_L)
    QTest.keyClick(window, Qt.Key.Key_Up)
    assert 'locked' not in window.config and 'target' not in window.config
    assert set(window.BUTTONS)=={'record','review','settings'}
    before=window.paused
    QTest.keyClick(window, Qt.Key.Key_Space)
    assert window.paused==before
    previous_pin=window.config['pin']
    QTest.mouseClick(window,Qt.MouseButton.LeftButton,pos=window.PIN_RECT.center().toPoint())
    assert window.config['pin']!=previous_pin
    QTest.mouseClick(window,Qt.MouseButton.LeftButton,pos=window.PIN_RECT.center().toPoint())
    assert window.config['pin']==previous_pin
    for height in [560, 601, 800, 1000]:
        window.resize(400, height)
        application.processEvents()
        assert window.width() == round(height*3/8)
    window.resize(300, 800)
    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=QPoint(150, 790))
    QTest.mouseMove(window, QPoint(150, 720), delay=10)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=QPoint(150, 720))
    assert window.width() == round(window.height()*3/8)
    window.resize(300,800)

    dialog = SettingsDialog(window.config, window)
    assert len(dialog.findChildren(QComboBox)) == 6
    assert not dialog.findChildren(QLineEdit)
    assert not dialog.findChildren(QAbstractSpinBox)
    assert all(not box.isEditable() for box in dialog.findChildren(QComboBox))
    assert dialog.reference.currentData() == 440.
    assert dialog.tolerance.currentData() == 10
    assert dialog.theme.count() == 6
    assert dialog.notation.count() == 2
    assert dialog.notation.currentData() == 'sharp'
    dialog.notation.setCurrentIndex(dialog.notation.findData('flat'))
    dialog.show()
    application.processEvents()
    dialog.grab().save(str(output/'settings-graphite.png'))
    dialog.devices.showPopup()
    application.processEvents()
    viewport = dialog.devices.view().viewport()
    pointer = QPoint(60, 65)
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(pointer), QPointF(viewport.mapToGlobal(pointer)),
                       Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    application.sendEvent(viewport, move)
    application.processEvents()
    assert dialog.devices.hovered_row == 1, dialog.devices.hovered_row
    application.processEvents()
    dialog.devices.view().window().grab().save(str(output/'devices-hover.png'))
    QTest.mouseClick(dialog.devices.view().viewport(), Qt.MouseButton.LeftButton, pos=QPoint(60, 65))
    assert dialog.devices.currentIndex() == 1
    dialog.devices.setCurrentIndex(0)
    device_labels = [dialog.devices.itemText(i) for i in range(dialog.devices.count())]
    (output/'device-list.json').write_text(json.dumps(device_labels, ensure_ascii=False, indent=2), encoding='utf-8')
    dialog.devices.hidePopup()
    for key in THEMES:
        dialog.theme.setCurrentIndex(dialog.theme.findData(key))
        assert window.preview_theme == key
        assert window.colors == THEMES[key]
        application.processEvents()
        dialog.grab().save(str(output/f'settings-{key}.png'))
    dialog.reject()
    assert window.preview_theme is None and window.config['theme'] == 'graphite'
    assert window.config['tolerance'] == 10
    assert window.config['notation'] == 'sharp'

    def save_choices():
        active = application.activeModalWidget()
        active.theme.setCurrentIndex(active.theme.findData('porcelain'))
        active.tolerance.setCurrentIndex(active.tolerance.findData(3))
        active.reference.setCurrentIndex(active.reference.findData(442.))
        active.notation.setCurrentIndex(active.notation.findData('flat'))
        active.accept()
    QTimer.singleShot(100, save_choices)
    window.open_settings()
    assert window.config['theme'] == 'porcelain'
    assert window.config['tolerance'] == 3 and window.config['a4'] == 442.
    assert window.config['notation'] == 'flat'
    window.on_pitch(Pitch(frequency_for(61, 442.)*2**(7.3/1200), .999, -25))
    assert note_name(window.current_note, window.config['notation']) == 'D♭4'
    application.processEvents()
    window.grab().save(str(output/'window-flat.png'))
    assert abs(cents_from(window.result.frequency, window.current_note, 442.)-7.3) < .01
    persisted = json.loads(app_module.CONFIG.read_text(encoding='utf-8'))
    assert persisted['theme'] == 'porcelain' and 'locked' not in persisted
    assert persisted['notation'] == 'flat'
    window.on_error('Test device disconnect')
    application.processEvents()
    window.grab().save(str(output/'window-device-error.png'))
    window.close()
    restored = PitchWindow(capture=False)
    assert restored.config['theme'] == 'porcelain' and restored.config['tolerance'] == 3
    assert restored.config['notation'] == 'flat'
    restored.close()
    app_icon().pixmap(256,256).save('app.ico')
print('PASS: bar size, automatic notes, pin icon, three main buttons, fixed aspect, dropdowns, notation persistence, themes')
