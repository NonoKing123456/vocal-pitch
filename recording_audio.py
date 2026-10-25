"""Recording to disk and sample-clock-synchronized playback."""
from collections import deque
from pathlib import Path
import queue
import threading
import time
import numpy as np
import sounddevice as sd
import soundfile as sf
from PySide6.QtCore import QObject, Signal
from recording_analysis import MAX_SECONDS, resample_audio
from audio_platform import audio_thread_context
from pitch_core import PitchDetector, Pitch

def clock_position(anchors, now, rate, fallback):
    """Map DAC clock to the source frame, including loops and buffered output."""
    if not anchors:
        return fallback
    entry = anchors[0]
    for candidate in anchors:
        if candidate[0] <= now:
            entry = candidate
        else:
            break
    dac_time, frame, length = entry
    elapsed = max(0., min(length/rate, now-dac_time))
    return (frame+elapsed*rate)/rate

class Recorder(QObject):
    started = Signal(int)
    completed = Signal(str)
    failed = Signal(str)
    measured = Signal(object)

    def __init__(self):
        super().__init__()
        self.thread = None
        self.stopped = threading.Event()
        self.frames = 0
        self.rate = 48000
        self.path = None
        self.recording = False
        self.gate = -50.

    @property
    def duration(self):
        return self.frames/self.rate

    def start(self, path, device=None, gate=-50.):
        if self.thread and self.thread.is_alive():
            raise RuntimeError('录音设备仍在关闭，请稍后重试。')
        self.stopped = threading.Event()
        self.frames = 0
        self.path = str(path)
        self.recording = True
        self.gate = gate
        self.thread = threading.Thread(target=self._run, args=(device,), daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()

    def _run(self, device):
        with audio_thread_context():
            self._capture(device)

    def _capture(self,device):
        blocks = queue.Queue(maxsize=256)
        overflow = threading.Event()
        def callback(data, frames, timing, status):
            if status:
                overflow.set()
            try:
                blocks.put_nowait(data.copy())
            except queue.Full:
                overflow.set()
        try:
            self.rate = int(sd.query_devices(device, 'input')['default_samplerate'])
            detector=PitchDetector(self.rate)
            window_size=4096 if self.rate<=48000 else 8192
            buffer=np.empty(0,dtype=np.float32)
            history=deque(maxlen=3)
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            with sf.SoundFile(self.path, mode='x', samplerate=self.rate, channels=1, subtype='PCM_24') as output:
                def write_block(block):
                    remaining=int(MAX_SECONDS*self.rate)-self.frames
                    if remaining>0:
                        piece=block[:remaining]
                        output.write(piece)
                        self.frames+=len(piece)
                with sd.InputStream(device=device, channels=1, samplerate=self.rate, dtype='float32',
                                    blocksize=1024, latency='high', callback=callback) as stream:
                    if self.stopped.is_set():
                        return
                    self.started.emit(self.rate)
                    last_data = time.monotonic()
                    while not self.stopped.is_set():
                        if overflow.is_set():
                            raise RuntimeError('录音发生丢帧，已停止并保留已录内容。请减轻系统负载后重试。')
                        try:
                            block = blocks.get(timeout=.2)
                        except queue.Empty:
                            if not stream.active or time.monotonic()-last_data > 2:
                                raise RuntimeError('录音设备已断开，已保留录到的内容。')
                            continue
                        last_data = time.monotonic()
                        write_block(block)
                        buffer=np.concatenate((buffer,block[:,0]))[-window_size:]
                        if len(buffer)==window_size:
                            reading=detector.detect(buffer,self.gate)
                            if reading.frequency is None:
                                history.clear()
                            else:
                                if history and abs(1200*np.log2(reading.frequency/history[-1]))>80:
                                    history.clear()
                                history.append(reading.frequency)
                                reading=Pitch(float(np.median(history)),reading.confidence,reading.db,reading.clipped)
                            self.measured.emit(reading)
                        if self.duration >= MAX_SECONDS:
                            self.stopped.set()
                while not blocks.empty():
                    block = blocks.get_nowait()
                    write_block(block)
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            self.recording = False
            self.completed.emit(self.path if self.frames else '')

class AudioPlayer(QObject):
    ended = Signal()
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.clip = None
        self.stream = None
        self.running = False
        self.current = 0.
        self.loop = None
        self.play_range = None
        self._generation = 0
        self._anchors = deque(maxlen=256)
        self._rate = 48000
        self._prepared = {}

    def set_clip(self, clip):
        self.pause()
        self.clip = clip
        self.current = 0.
        self.loop = None
        self.play_range = None
        self._prepared = {clip.rate: clip.audio}

    def position(self):
        if not self.running or self.stream is None:
            return self.current
        try:
            value = clock_position(list(self._anchors), self.stream.time, self._rate, self.current)
            return max(0., min(self.clip.duration, value))
        except Exception:
            return self.current

    def pause(self):
        self.current = self.position()
        self.running = False
        self._generation += 1
        if self.stream is not None:
            try:
                self.stream.abort()
            finally:
                self.stream.close()
                self.stream = None

    def seek(self, seconds):
        resume = self.running
        self.pause()
        self.current = float(np.clip(seconds, 0., self.clip.duration if self.clip else 0.))
        if resume:
            self.play()

    def play(self):
        if self.clip is None:
            return
        self.pause()
        try:
            info = sd.query_devices(None, 'output')
            channels = min(2, int(info['max_output_channels']))
            if channels < 1:
                raise RuntimeError('没有可用的播放设备。')
            rate = self.clip.rate
            try:
                sd.check_output_settings(samplerate=rate, channels=channels, dtype='float32')
            except Exception:
                rate = int(info['default_samplerate'])
            if rate not in self._prepared:
                self._prepared[rate] = resample_audio(self.clip.audio, self.clip.rate, rate)
            audio = self._prepared[rate]
            self._rate = rate
            end_frame = len(audio)
            if self.play_range is not None:
                end_frame=max(1,min(end_frame,round(self.play_range[1]*rate)))
            loop_start, loop_end = 0, end_frame
            looping = self.loop is not None and self.loop[1]-self.loop[0] >= .05
            if looping:
                loop_start = max(0, min(end_frame-1, round(self.loop[0]*rate)))
                loop_end = max(loop_start+1, min(end_frame, round(self.loop[1]*rate)))
            cursor = round(self.current*rate)
            if self.play_range is not None and not round(self.play_range[0]*rate)<=cursor<end_frame:
                cursor=max(0,round(self.play_range[0]*rate))
            if looping and not loop_start <= cursor < loop_end:
                cursor = loop_start
            if cursor >= end_frame:
                cursor = loop_start if looping else 0
            self.current = cursor/rate
            self._anchors.clear()
            generation = self._generation
            def callback(output, frames, timing, status):
                nonlocal cursor
                output.fill(0)
                written = 0
                while written < frames:
                    boundary = loop_end if looping else end_frame
                    if cursor >= boundary:
                        if looping:
                            cursor = loop_start
                        else:
                            raise sd.CallbackStop()
                    length = min(frames-written, boundary-cursor)
                    self._anchors.append((float(timing.outputBufferDacTime)+written/rate, cursor, length))
                    output[written:written+length] = audio[cursor:cursor+length, :channels]
                    cursor += length
                    written += length
                if not looping and cursor >= end_frame:
                    raise sd.CallbackStop()
            def finished():
                if generation == self._generation:
                    self.current = end_frame/rate
                    self.running = False
                    self.ended.emit()
            self.stream = sd.OutputStream(samplerate=rate, channels=channels, dtype='float32',
                                          blocksize=0, latency='low', callback=callback,
                                          finished_callback=finished)
            self.running = True
            self.stream.start()
        except Exception as error:
            self.running = False
            if self.stream:
                self.stream.close()
                self.stream = None
            self.failed.emit(str(error))
