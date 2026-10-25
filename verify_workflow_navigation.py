"""Regression checks for direct recording, persistent review, key shading and Melodyne navigation."""
import json
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import patch
import numpy as np
import soundfile as sf
from PySide6.QtCore import Qt,QPoint,QPointF,QEvent
from PySide6.QtGui import QMouseEvent,QWheelEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import app as app_module
from app import PitchWindow
from recording_fixture import phrase
from recording_analysis import analyze
from tonality import pitch_classes

application=QApplication([])
application.setStyle('Fusion')
output=Path('verification/v4');output.mkdir(parents=True,exist_ok=True)

def wait_until(condition,timeout=6):
    deadline=time.monotonic()+timeout
    while not condition() and time.monotonic()<deadline:
        application.processEvents();time.sleep(.005)
    application.processEvents()
    assert condition(),'Timed out waiting for UI state'

def mouse(widget,kind,point,button=Qt.MouseButton.LeftButton,buttons=Qt.MouseButton.LeftButton,modifiers=Qt.KeyboardModifier.NoModifier):
    application.sendEvent(widget,QMouseEvent(kind,QPointF(point),QPointF(widget.mapToGlobal(point.toPoint())),button,buttons,modifiers))

def drag(widget,start,end,modifiers=Qt.KeyboardModifier.NoModifier):
    mouse(widget,QEvent.Type.MouseButtonPress,start,modifiers=modifiers)
    mouse(widget,QEvent.Type.MouseMove,end,button=Qt.MouseButton.NoButton,modifiers=modifiers)
    mouse(widget,QEvent.Type.MouseButtonRelease,end,buttons=Qt.MouseButton.NoButton,modifiers=modifiers)

class VoiceInput:
    def __init__(self,**kwargs):
        self.callback=kwargs['callback'];self.active=True;self.stop=threading.Event()
    def __enter__(self):
        def produce():
            for i in range(150):
                if self.stop.is_set():break
                t=(np.arange(1024)+i*1024)/48000
                block=(.2*np.sin(2*np.pi*440*2**(12.3/1200)*t)).astype(np.float32)[:,None]
                self.callback(block,1024,None,None);time.sleep(.007)
        self.thread=threading.Thread(target=produce);self.thread.start();return self
    def __exit__(self,*args):self.stop.set();self.thread.join();self.active=False

with tempfile.TemporaryDirectory() as directory,patch.object(app_module,'CONFIG',Path(directory)/'settings.json'), \
     patch('app.QMessageBox.warning',side_effect=lambda *args:(_ for _ in ()).throw(AssertionError(str(args)))):
    host=PitchWindow(capture=False);host.config.update(pin=False,key='auto',device=None)
    host.recordings_dir=Path(directory)/'takes';host.resize(300,800);host.show()
    with patch('recording_audio.sd.InputStream',VoiceInput), \
         patch('recording_audio.sd.query_devices',return_value={'default_samplerate':48000}):
        QTest.mouseClick(host,Qt.MouseButton.LeftButton,pos=host.BUTTONS['record'].center().toPoint())
        assert host.review is None and host.recorder.recording
        wait_until(lambda:host.recorder.frames>=10240 and host.result.frequency is not None)
        assert not host.paused and abs(1200*np.log2(host.result.frequency/440)-12.3)<.5
        host.grab().save(str(output/'01-small-recording.png'))
        QTest.mouseClick(host,Qt.MouseButton.LeftButton,pos=host.BUTTONS['record'].center().toPoint())
        wait_until(lambda:host.review is not None and host.review.track is not None and not host.review.busy)
        review=host.review;assert review.isVisible()
        saved_path=Path(review.clip.path)
        assert sf.info(saved_path).frames==host.recorder.frames
        original_clip=review.clip;original_track=review.track
        QTest.mouseClick(host,Qt.MouseButton.LeftButton,pos=host.BUTTONS['review'].center().toPoint())
        assert not review.isVisible() and not host.paused
        QTest.mouseClick(host,Qt.MouseButton.LeftButton,pos=host.BUTTONS['review'].center().toPoint())
        assert review.isVisible() and review.clip is original_clip and review.track is original_track
        QTest.mouseClick(host,Qt.MouseButton.LeftButton,pos=host.PIN_RECT.center().toPoint())
        assert host.config['pin'] and review.isVisible()
        QTest.mouseClick(host,Qt.MouseButton.LeftButton,pos=host.PIN_RECT.center().toPoint())
        host.toggle_review();assert not review.isVisible()
        host.toggle_record();assert not review.isVisible()
        wait_until(lambda:host.recorder.frames>=10240)
        active_path=host.recorder.path
        host.close();wait_until(lambda:not host.isVisible())
        assert not review.isVisible() and sf.info(active_path).frames==host.recorder.frames

    host=PitchWindow(capture=False);host.config.update(pin=False,key='auto',device=None)
    host.resize(300,800);host.show();host.open_review();review=host.review
    clip,_=phrase();track=analyze(clip)
    token=object();review.job=token
    review.analysis_ready(token,clip,track)
    roll=review.roll;viewport=roll.viewport()
    assert review.key_estimate and review.key_choice.count()==26
    review.key_choice.setCurrentIndex(review.key_choice.findData('2:major'))
    assert roll.scale_pcs==pitch_classes(2,'major')
    assert host.config['key']=='2:major'
    persisted=json.loads(app_module.CONFIG.read_text(encoding='utf-8'))
    assert persisted['key']=='2:major'
    application.processEvents();review.grab().save(str(output/'02-manual-key.png'))
    frame=viewport.grab().toImage()
    x=viewport.width()-25
    assert frame.pixelColor(x,round(roll.y_for_midi(62)-roll.row_height*.2)).lightness() > \
           frame.pixelColor(x,round(roll.y_for_midi(60)-roll.row_height*.2)).lightness()
    review.key_choice.setCurrentIndex(review.key_choice.findData('none'));assert roll.scale_pcs is None
    review.key_choice.setCurrentIndex(review.key_choice.findData('auto'))
    assert roll.scale_pcs==pitch_classes(review.key_estimate.tonic,review.key_estimate.mode)
    application.processEvents();review.grab().save(str(output/'03-auto-key.png'))
    host.grab().save(str(output/'04-small-pin.png'))

    center=QPointF(roll.LEFT+roll.grid_width/2,roll.TOP+roll.grid_height/2)
    roll.zoom_horizontal(3);roll.zoom_vertical(2)
    roll.horizontalScrollBar().setValue(roll.horizontalScrollBar().maximum()//2)
    roll.verticalScrollBar().setValue(roll.verticalScrollBar().maximum()//2)
    def wheel(angle,pixels=QPoint(),modifiers=Qt.KeyboardModifier.NoModifier,buttons=Qt.MouseButton.NoButton):
        event=QWheelEvent(center,QPointF(viewport.mapToGlobal(center.toPoint())),pixels,angle,
            buttons,modifiers,Qt.ScrollPhase.NoScrollPhase,False)
        application.sendEvent(viewport,event)
    horizontal=roll.horizontalScrollBar();vertical=roll.verticalScrollBar()
    old=horizontal.value();height=roll.row_height;scale=roll.pixels_per_second
    wheel(QPoint(120,0));assert horizontal.value()==old-100 and roll.row_height==height
    old=horizontal.value();wheel(QPoint(0,120),modifiers=Qt.KeyboardModifier.ShiftModifier)
    assert horizontal.value()==old-100 and roll.pixels_per_second==scale
    old=horizontal.value();wheel(QPoint(),QPoint(30,0));assert horizontal.value()==old-30
    old=horizontal.value();wheel(QPoint(0,120),buttons=Qt.MouseButton.BackButton);assert horizontal.value()==old-100
    wheel(QPoint(0,120),modifiers=Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.AltModifier)
    assert roll.row_height>height and roll.pixels_per_second>scale
    before_x,before_y=horizontal.value(),vertical.value();origin=review.pos()
    old_selection=roll.selection
    drag(viewport,center,center-QPointF(40,25),Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.ShiftModifier)
    assert horizontal.value()==before_x+40 and vertical.value()==before_y+25
    assert review.pos()==origin and roll.selection==old_selection
    scale,height=roll.pixels_per_second,roll.row_height
    drag(viewport,center,center+QPointF(20,-20),Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.AltModifier)
    assert roll.pixels_per_second>scale and roll.row_height>height
    roll.fit_all();state=roll.view_state();segment=track.segments[3]
    point=QPointF(roll.x_for_time((segment.start+segment.end)/2),roll.y_for_midi(segment.center_midi))
    mouse(viewport,QEvent.Type.MouseButtonDblClick,point,modifiers=Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.AltModifier)
    assert roll.previous_zoom is not None and roll.view_state()!=state
    mouse(viewport,QEvent.Type.MouseButtonDblClick,QPointF(roll.LEFT+10,roll.TOP+4),
        modifiers=Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.AltModifier)
    assert roll.view_state()==state
    # Thumb ends zoom the corresponding axis; double-clicking the thumb fits it.
    slider,_=horizontal.parts();point=QPointF(slider.right()-1,horizontal.height()/2)
    scale=roll.pixels_per_second
    drag(horizontal,point,point-QPointF(50,0));assert roll.pixels_per_second>scale
    mouse(horizontal,QEvent.Type.MouseButtonDblClick,QPointF(horizontal.width()/2,horizontal.height()/2))
    assert abs(roll.pixels_per_second-state[0])<.01
    slider,_=vertical.parts();point=QPointF(vertical.width()/2,slider.bottom()-1)
    height=roll.row_height
    drag(vertical,point,point-QPointF(0,10));assert roll.row_height>height
    roll.fit_all()
    drag(viewport,QPointF(roll.x_for_time(2),35),QPointF(roll.x_for_time(4),35))
    assert roll.selection and abs(roll.selection[0]-2)<.02 and abs(roll.selection[1]-4)<.02
    mouse(viewport,QEvent.Type.MouseButtonDblClick,QPointF(roll.x_for_time(3),35))
    assert review.loop_button.isChecked() and roll.cycle_active
    with patch.object(review.player,'play') as play:
        mouse(viewport,QEvent.Type.MouseButtonDblClick,QPointF(roll.x_for_time(1),12),modifiers=Qt.KeyboardModifier.AltModifier)
        assert play.called and review.player.play_range==roll.selection
    host.close();application.processEvents()
print('PASS: direct recording without popup, live pitch while recording, auto review on stop, preserve/reopen, pin, safe app close, auto/manual keys and shading, side-wheel, Shift-wheel, smooth trackpad, Ctrl+Shift pan, Ctrl+Alt zoom/focus, scrollbar edge zoom, cycle ruler, selection playback')
