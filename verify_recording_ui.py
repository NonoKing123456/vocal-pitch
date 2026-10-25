"""End-to-end GUI checks for asynchronous analysis and piano-roll navigation."""
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
import soundfile as sf
from PySide6.QtCore import Qt,QPoint,QPointF,QEvent
from PySide6.QtGui import QMouseEvent,QWheelEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import app as app_module
from app import PitchWindow
from recording_fixture import phrase

application=QApplication([])
from test_recording import FakeInput
application.setStyle('Fusion')
output=Path('verification/v3');output.mkdir(parents=True,exist_ok=True)
clip,_=phrase()
path=output/'demo-vocal.wav'
sf.write(path,clip.audio,clip.rate,subtype='PCM_24')
errors=[]
with tempfile.TemporaryDirectory() as folder,patch.object(app_module,'CONFIG',Path(folder)/'settings.json'), \
     patch('recording_window.QMessageBox.warning',side_effect=lambda *args:errors.append(str(args[-1]))):
    host=PitchWindow(capture=False)
    host.config.update(pin=False,theme='graphite',notation='flat')
    host.show()
    host.open_review()
    review=host.review
    assert review is not None and review.isVisible()
    with patch('recording_window.QFileDialog.getOpenFileName',return_value=(str(path),'WAV')):
        review.import_button.click()
    deadline=time.monotonic()+30
    while review.busy and time.monotonic()<deadline:
        application.processEvents()
        time.sleep(.01)
    assert not errors,errors
    assert review.track is not None and len(review.track.segments)>=8
    exported=Path(folder)/'导出.wav'
    with patch('recording_window.QFileDialog.getSaveFileName',return_value=(str(exported),'WAV')):
        review.export_button.click()
    assert sf.info(exported).frames==len(clip.audio)
    review.seek(4.8)
    application.processEvents()
    review.grab().save(str(output/'01-piano-roll.png'))
    roll=review.roll
    anchor=roll.TOP+roll.grid_height/2
    before=roll.midi_for_y(anchor)
    original=roll.row_height
    roll.zoom_vertical(2.,anchor)
    assert roll.row_height>original
    assert abs(roll.midi_for_y(anchor)-before)<1/roll.row_height
    roll.zoom_vertical(.5,anchor)
    assert abs(roll.row_height-original)<.01
    viewport=roll.viewport()
    mouse=QPointF(roll.LEFT+roll.grid_width/2,roll.TOP+roll.grid_height/2)
    global_pos=viewport.mapToGlobal(mouse.toPoint())
    wheel=QWheelEvent(mouse,QPointF(global_pos),QPoint(),QPoint(0,120),Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.AltModifier,Qt.ScrollPhase.NoScrollPhase,False)
    application.sendEvent(viewport,wheel)
    assert roll.row_height>original
    roll.fit_all()
    old_seconds=roll.time_for_x(mouse.x())
    roll.zoom_horizontal(2.,mouse.x())
    assert abs(roll.time_for_x(mouse.x())-old_seconds)<1/roll.pixels_per_second
    roll.fit_all()
    def send(kind,point,button,buttons):
        application.sendEvent(viewport,QMouseEvent(kind,point,QPointF(viewport.mapToGlobal(point.toPoint())),
            button,buttons,Qt.KeyboardModifier.NoModifier))
    first=QPointF(roll.x_for_time(2.),roll.TOP+100)
    last=QPointF(roll.x_for_time(4.),roll.TOP+100)
    send(QEvent.Type.MouseButtonPress,first,Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton)
    send(QEvent.Type.MouseMove,last,Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton)
    send(QEvent.Type.MouseButtonRelease,last,Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton)
    assert roll.selection and abs(roll.selection[0]-2.)<.02 and abs(roll.selection[1]-4.)<.02
    review.loop_button.setChecked(True);review.toggle_loop()
    assert review.player.loop==roll.selection
    review.clear_selection()
    assert roll.selection is None and review.player.loop==(0.,clip.duration)
    review.loop_button.setChecked(False);review.toggle_loop()
    review.seek(1.5)
    assert abs(review.player.current-1.5)<.01
    host.config['theme']='porcelain';review.apply_config()
    application.processEvents()
    review.grab().save(str(output/'02-piano-roll-light.png'))
    roll.zoom_vertical(2.5)
    application.processEvents()
    review.grab().save(str(output/'03-vertical-zoom.png'))
    review.close()
    application.processEvents()
    assert host.review is review and not review.isVisible()
    # Record -> stop -> analyze, then close the entire app during another take.
    with patch('recording_audio.sd.InputStream',FakeInput), \
         patch('recording_audio.sd.query_devices',return_value={'default_samplerate':48000}):
        host.open_review();capture=host.review
        host.recordings_dir=Path(folder)/'takes'
        capture.recordings_dir=host.recordings_dir
        capture.toggle_record()
        deadline=time.monotonic()+2
        while capture.recorder.frames<10240 and time.monotonic()<deadline:
            application.processEvents();time.sleep(.005)
        capture.toggle_record()
        deadline=time.monotonic()+4
        while (capture.recorder.recording or capture.busy or capture.clip is None) and time.monotonic()<deadline:
            application.processEvents();time.sleep(.005)
        assert capture.clip is not None and Path(capture.clip.path).exists()
        assert sf.info(capture.clip.path).subtype=='PCM_24'
        capture.toggle_record();recorder=capture.recorder
        deadline=time.monotonic()+2
        while recorder.frames<10240 and time.monotonic()<deadline:
            application.processEvents();time.sleep(.005)
        saved_path=recorder.path
        host.close()
        deadline=time.monotonic()+4
        while host.isVisible() and time.monotonic()<deadline:
            application.processEvents();time.sleep(.005)
        application.processEvents()
        assert not host.isVisible()
        assert sf.info(saved_path).frames==recorder.frames and recorder.frames>0
    assert not errors,errors
clip96,_=phrase(96000)
sf.write(output/'demo-96k.wav',clip96.audio,clip96.rate,subtype='PCM_24')
print('PASS: recording window entry, import/export, asynchronous analysis, piano roll, zoom, selection, loop, themes, recording/save/close')
