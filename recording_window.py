"""Recording review workflow around the piano roll and audio engine."""
from pathlib import Path
import threading
import logging
import numpy as np
import soundfile as sf
from PySide6.QtCore import Qt, QTimer, QObject, Signal, QUrl
from PySide6.QtGui import QFont, QKeySequence, QShortcut, QDesktopServices
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QProgressBar, QMessageBox, QSizePolicy)
from recording_analysis import load_audio, analyze, AnalysisCancelled
from recording_audio import AudioPlayer
from piano_roll import PianoRoll, time_label
from pitch_core import note_name
from ui_theme import theme_for, settings_style, ChoiceBox
from tonality import estimate_key, key_name, pitch_classes

class AnalysisJob(QObject):
    ready = Signal(object, object)
    progress = Signal(int)
    failed = Signal(str)

    def __init__(self, path, a4, gate):
        super().__init__()
        self.path, self.a4, self.gate = path, a4, gate
        self.cancelled = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        try:
            clip = load_audio(self.path)
            if self.cancelled.is_set():
                return
            track = analyze(clip,self.a4,self.gate,self.cancelled,self.progress.emit)
            if not self.cancelled.is_set():
                self.ready.emit(clip,track)
        except AnalysisCancelled:
            pass
        except Exception as error:
            if not self.cancelled.is_set():
                logging.exception('Recording analysis')
                self.failed.emit(str(error))

class RecordingWindow(QWidget):
    closed = Signal()

    def __init__(self, host, recordings_dir):
        super().__init__(host,Qt.WindowType.Window)
        self.host = host
        self.setObjectName('reviewRoot')
        self.recordings_dir = Path(recordings_dir)
        self.setWindowTitle('声准 · 录音复盘')
        self.setWindowIcon(host.windowIcon())
        self.setMinimumSize(940,500)
        available = self.screen().availableGeometry()
        self.resize(min(1160,available.width()-40),min(740,available.height()-40))
        self.move(available.center()-self.rect().center())
        self.clip, self.track, self.job = None,None,None
        self._jobs = []
        self.busy = False
        self._closed = False
        self.key_estimate=None
        self.analysis_settings = (host.config['a4'],host.config['gate'])
        self.recorder = host.recorder
        self.player = AudioPlayer()
        self.recorder.started.connect(self.record_started)
        self.recorder.completed.connect(self.record_completed)
        self.player.ended.connect(self.playback_ended)
        self.player.failed.connect(self.audio_error)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16,16,16,12)
        layout.setSpacing(10)
        transport = QHBoxLayout()
        transport.setSpacing(8)
        self.record_button = self.button('● 录音',self.toggle_record)
        self.import_button = self.button('导入',self.import_audio)
        self.export_button = self.button('导出',self.export_audio)
        self.play_button = self.button('▶ 播放',self.toggle_play)
        self.stop_button = self.button('停止',self.stop_play)
        self.loop_button = self.button('循环',self.toggle_loop,True)
        self.follow_button = self.button('跟随',None,True)
        self.follow_button.setChecked(True)
        for button in (self.record_button,self.import_button,self.export_button,self.play_button,
                       self.stop_button,self.loop_button,self.follow_button):
            transport.addWidget(button)
        transport.addStretch()
        self.clock = QLabel('00:00.0 / 00:00.0')
        self.clock.setMinimumWidth(156)
        self.clock.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        transport.addWidget(self.clock)
        layout.addLayout(transport)

        tools = QHBoxLayout()
        tools.setSpacing(7)
        self.filename = QLabel('录音复盘')
        self.filename.setMinimumWidth(100)
        self.filename.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        tools.addWidget(self.filename,1)
        tools.addWidget(QLabel('调性'))
        self.key_choice=ChoiceBox()
        self.key_choice.setFixedWidth(190)
        self.key_choice.setFixedHeight(35)
        self.key_choice.currentIndexChanged.connect(self.key_changed)
        tools.addWidget(self.key_choice)
        tools.addWidget(self.button('录音文件',self.open_folder))
        self.clear_button = self.button('清除选区',self.clear_selection)
        tools.addWidget(self.clear_button)
        tools.addWidget(self.button('全览',self.fit_all))
        tools.addSpacing(12)
        tools.addWidget(QLabel('时间'))
        tools.addWidget(self.button('−',lambda:self.roll.zoom_horizontal(1/1.3),compact=True))
        tools.addWidget(self.button('+',lambda:self.roll.zoom_horizontal(1.3),compact=True))
        tools.addSpacing(12)
        tools.addWidget(QLabel('音高'))
        tools.addWidget(self.button('−',lambda:self.roll.zoom_vertical(1/1.3),compact=True))
        tools.addWidget(self.button('+',lambda:self.roll.zoom_vertical(1.3),compact=True))
        layout.addLayout(tools)
        self.roll = PianoRoll(self)
        self.roll.seekRequested.connect(self.seek)
        self.roll.selectionChanged.connect(self.selection_changed)
        self.roll.selectionFinished.connect(self.selection_finished)
        self.roll.inspected.connect(self.inspect)
        self.roll.playbackRequested.connect(self.playback_at)
        self.roll.cycleToggleRequested.connect(self.cycle_toggle_request)
        layout.addWidget(self.roll,1)
        footer = QHBoxLayout()
        self.reading = QLabel('—')
        self.reading.setMinimumWidth(280)
        self.reading.setTextFormat(Qt.TextFormat.PlainText)
        self.status = QLabel('')
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(150)
        self.progress.setTextVisible(False)
        self.progress.hide()
        footer.addWidget(self.reading,1)
        footer.addWidget(self.status,1)
        footer.addWidget(self.progress)
        layout.addLayout(footer)
        QShortcut(QKeySequence(Qt.Key.Key_Space),self,activated=self.toggle_play)
        QShortcut(QKeySequence('Alt+Space'),self,activated=lambda:self.playback_at(self.player.position(),True))
        QShortcut(QKeySequence('Ctrl+O'),self,activated=self.import_audio)
        QShortcut(QKeySequence('Ctrl+S'),self,activated=self.export_audio)
        QShortcut(QKeySequence('Home'),self,activated=self.fit_all)
        QShortcut(QKeySequence('Escape'),self,activated=self.clear_selection)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(30)
        self.apply_config()
        self.update_controls()

    def button(self,text,callback=None,checkable=False,compact=False):
        button = QPushButton(text)
        button.setCheckable(checkable)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if compact:
            button.setProperty('compact',True)
            button.setFixedWidth(32)
        if callback:
            button.clicked.connect(callback)
        return button

    def apply_config(self):
        t = theme_for(self.host.config['theme'])
        self.setStyleSheet(settings_style(self.host.config['theme'])+f'''
            QWidget#reviewRoot {{ background: {t['bg']}; }}
            QPushButton {{ padding: 8px 12px; min-width: 32px; }}
            QPushButton[compact="true"] {{ padding: 6px; min-width: 18px; font-size: 17px; }}
            QPushButton:checked {{ color: {t['accent']}; border-color: {t['accent']}; background: {t['hover']}; }}
            QPushButton:disabled {{ color: {t['secondary']}; border-color: {t['surface']}; background: {t['bg']}; }}
            QAbstractScrollArea {{ background: {t['bg']}; border: 1px solid {t['border']}; }}
            QProgressBar {{ background: {t['surface']}; border: none; border-radius: 3px; max-height: 6px; }}
            QProgressBar::chunk {{ background: {t['accent']}; border-radius: 3px; }}
            QScrollBar:horizontal {{ background: {t['surface']}; height: 10px; margin: 0; }}
            QScrollBar::handle:horizontal {{ background: {t['border']}; border-radius: 4px; min-width: 28px; }}
            QScrollBar::handle:horizontal:hover {{ background: {t['accent']}; }}
            QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal {{ width: 0; }}
            QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal {{ background: transparent; }}
        ''')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        self.roll.set_colors(t,self.host.config['notation'],self.host.config['tolerance'])
        self.key_choice.colors=t
        self.refresh_keys()
        settings = (self.host.config['a4'],self.host.config['gate'])
        if self.clip is not None and settings != self.analysis_settings and not self.recorder.recording:
            self.load_path(self.clip.path)
        self.update_reading(self.player.position())

    def update_controls(self):
        recording = self.recorder.recording
        ready = self.clip is not None and not self.busy and not recording
        self.record_button.setEnabled(not self.host._record_stopping)
        self.record_button.setText('保存中' if self.host._record_stopping else '■ 结束录音' if recording else '● 录音')
        self.import_button.setEnabled(not recording)
        self.export_button.setEnabled(ready)
        self.play_button.setEnabled(ready)
        self.stop_button.setEnabled(ready)
        self.loop_button.setEnabled(ready)
        self.clear_button.setEnabled(ready and self.roll.selection is not None)
        self.play_button.setText('Ⅱ 暂停' if self.player.running else '▶ 播放')

    def cancel_job(self):
        if self.job:
            self.job.cancelled.set()
            self.job = None

    def load_path(self,path):
        self.player.pause()
        self.cancel_job()
        self.busy = True
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText('正在分析…')
        self.analysis_settings = (self.host.config['a4'],self.host.config['gate'])
        job = AnalysisJob(str(path),*self.analysis_settings)
        self.job = job
        self._jobs = [other for other in self._jobs if other.thread.is_alive()]
        self._jobs.append(job)
        job.progress.connect(lambda value, current=job:self.analysis_progress(current,value))
        job.ready.connect(lambda clip,track,current=job:self.analysis_ready(current,clip,track))
        job.failed.connect(lambda message,current=job:self.analysis_failed(current,message))
        self.update_controls()
        job.thread.start()

    def analysis_progress(self,job,value):
        if job is self.job and not self._closed:
            self.progress.setValue(value)

    def analysis_ready(self,job,clip,track):
        if job is not self.job or self._closed:
            return
        self.job,self.busy=None,False
        self.clip,self.track=clip,track
        self.key_estimate=estimate_key(track)
        self.refresh_keys()
        self.player.set_clip(clip)
        self.roll.set_data(clip,track)
        self.filename.setText(Path(clip.path).name)
        self.filename.setToolTip(clip.path)
        self.progress.hide()
        self.loop_button.setChecked(False)
        self.status.setText(f'{len(track.segments)} 个音符段' if track.segments else '未检测到稳定音高')
        self.update_controls()
        self.update_reading(0.)
        self.clock.setText(f'00:00.0 / {time_label(clip.duration,True)}')

    def analysis_failed(self,job,message):
        if job is not self.job or self._closed:
            return
        self.job,self.busy=None,False
        self.progress.hide()
        self.status.setText('分析失败')
        self.update_controls()
        QMessageBox.warning(self,'无法分析录音',message)

    def import_audio(self):
        if self.recorder.recording:
            return
        path,_=QFileDialog.getOpenFileName(self,'导入录音',str(self.recordings_dir),
            '音频文件 (*.wav *.flac *.ogg *.mp3);;所有文件 (*)')
        if path:
            self.load_path(path)

    def open_folder(self):
        self.recordings_dir.mkdir(parents=True,exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.recordings_dir)))

    def export_audio(self):
        if self.clip is None or self.busy or self.recorder.recording:
            return
        path,_=QFileDialog.getSaveFileName(self,'导出录音',str(Path.home()/Path(self.clip.path).name),'WAV 音频 (*.wav)')
        if not path:
            return
        destination=Path(path).with_suffix('.wav')
        if destination.resolve() == Path(self.clip.path).resolve():
            self.status.setText('录音已保存在此文件')
            return
        try:
            sf.write(str(destination),self.clip.audio,self.clip.rate,subtype='PCM_24')
            self.status.setText('已导出录音')
        except Exception as error:
            QMessageBox.warning(self,'无法导出',str(error))

    def toggle_record(self):
        self.host.toggle_record()

    def record_started(self,rate):
        self.status.setText('录音中')
        self.update_controls()

    def record_completed(self,path):
        if not path:
            self.status.setText('未录到声音')
        self.update_controls()

    def audio_error(self,message):
        logging.warning('Review audio: %s',message)
        self.status.setText('音频设备异常')
        self.update_controls()
        if not self._closed:
            QMessageBox.warning(self,'音频设备',message)

    def toggle_play(self):
        if self.clip is None or self.busy or self.recorder.recording:
            return
        if self.player.running:
            self.player.pause()
        else:
            self.player.play_range=None
            self.sync_loop()
            self.player.play()
        self.update_controls()

    def stop_play(self):
        if self.clip:
            self.player.pause()
            self.player.seek(self.roll.selection[0] if self.loop_button.isChecked() and self.roll.selection else 0.)
            self.update_controls()
            self.tick()

    def seek(self,seconds):
        if self.clip and not self.busy and not self.recorder.recording:
            self.player.seek(seconds)
            self.tick()

    def sync_loop(self):
        self.player.loop=(self.roll.selection or (0.,self.clip.duration)) if self.loop_button.isChecked() and self.clip else None
        self.roll.cycle_active=self.loop_button.isChecked()
        self.roll.viewport().update()

    def cycle_toggle_request(self):
        self.loop_button.setChecked(not self.loop_button.isChecked())
        self.toggle_loop()

    def playback_at(self,seconds,selection_only=False):
        if self.clip is None or self.busy or self.recorder.recording:
            return
        if self.player.running:
            self.player.pause();self.player.seek(seconds)
        else:
            if selection_only and self.roll.selection:
                self.player.play_range=self.roll.selection
                self.player.loop=None
                self.player.current=self.roll.selection[0]
            else:
                self.player.play_range=None
                self.player.current=seconds
                self.sync_loop()
            self.player.play()
        self.update_controls();self.tick()

    def toggle_loop(self):
        if self.clip:
            playing=self.player.running
            self.player.pause()
            self.sync_loop()
            if playing:
                self.player.play()
            self.tick()
            self.update_controls()

    def selection_changed(self,selection):
        if selection:
            self.status.setText(f'选区 {time_label(selection[0],True)}–{time_label(selection[1],True)}')
        else:
            self.status.setText('')
        self.clear_button.setEnabled(selection is not None and not self.busy)
        # Avoid restarting the output stream on every pixel of a dragged selection.
        self.sync_loop()

    def clear_selection(self):
        self.roll.selection=None
        self.roll.viewport().update()
        self.selection_changed(None)
        if self.player.running and self.loop_button.isChecked():
            self.toggle_loop()

    def selection_finished(self):
        self.sync_loop()
        if self.player.running and self.loop_button.isChecked() and self.roll.selection:
            self.player.seek(self.roll.selection[0])
        if self.track and self.roll.selection:
            a,b=self.roll.selection
            for segment in self.track.segments:
                if abs(segment.start-a)<.02 and abs(segment.end-b)<.02:
                    self.status.setText(f'中心 {segment.cents:+.1f} 音分 · 波动 {segment.spread:.1f} 音分')
                    break

    def playback_ended(self):
        if not self._closed:
            self.update_controls()
            self.tick()

    def update_reading(self,seconds):
        if self.track is None:
            self.reading.setText('—')
            return
        value=self.track.at(seconds)
        if value:
            note,cents,frequency=value
            self.reading.setText(f'{time_label(seconds,True)}  {note_name(note,self.host.config["notation"])}  {cents:+.1f} 音分  ·  {frequency:.2f} Hz')
        else:
            self.reading.setText('—')

    def inspect(self,value):
        if value is not None and not self.player.running and not self.recorder.recording:
            self.update_reading(value[0])
        elif value is None:
            self.update_reading(self.player.position())

    def fit_all(self):
        self.roll.fit_all()

    def tick(self):
        if self.recorder.recording:
            self.clock.setText(f'{time_label(self.recorder.duration,True)}  录音')
        elif self.clip:
            position=self.player.position()
            self.roll.set_position(position,self.player.running and self.follow_button.isChecked())
            self.clock.setText(f'{time_label(position,True)} / {time_label(self.clip.duration,True)}')
            if self.player.running or self.roll.hover_time is None:
                self.update_reading(position)

    def closeEvent(self,event):
        if self._closed:
            event.accept()
        else:
            self.player.pause()
            self.timer.stop()
            self.hide()
            self.closed.emit()
            event.ignore()

    def showEvent(self,event):
        if not self._closed:
            self.timer.start(30)
        super().showEvent(event)

    def shutdown(self):
        self._closed=True
        self.cancel_job()
        self.player.pause()
        self.timer.stop()

    def refresh_keys(self):
        selected=self.host.config['key']
        self.key_choice.blockSignals(True)
        self.key_choice.clear()
        notation=self.host.config['notation']
        automatic='自动 · '+key_name(self.key_estimate.tonic,self.key_estimate.mode,notation) if self.key_estimate else '自动识别'
        self.key_choice.addItem(automatic,'auto')
        self.key_choice.addItem('全部音阶','none')
        for mode in ('major','minor'):
            for root in range(12):
                self.key_choice.addItem(key_name(root,mode,notation),f'{root}:{mode}')
        self.key_choice.setCurrentIndex(max(0,self.key_choice.findData(selected)))
        self.key_choice.blockSignals(False)
        self.update_key_highlight()

    def key_changed(self,index):
        value=self.key_choice.currentData()
        if value is not None:
            self.host.config['key']=value
            self.host.save()
            self.update_key_highlight()

    def update_key_highlight(self):
        value=self.host.config['key']
        tonic,mode=None,None
        if value=='auto' and self.key_estimate:
            tonic,mode=self.key_estimate.tonic,self.key_estimate.mode
        elif value not in ('auto','none'):
            root,mode=value.split(':')
            tonic=int(root)
        self.roll.set_key(tonic,mode)
        if value=='auto':
            self.key_choice.setToolTip('根据整段录音估计调性；短旋律、转调或关系大小调可能不确定，可手动更改。')
        else:
            self.key_choice.setToolTip('高亮所选调性的调内音；原始音高曲线保持可见。')
