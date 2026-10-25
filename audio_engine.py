"""Microphone capture and analysis, kept off the GUI thread."""
import queue
import threading
import time
from collections import deque
import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, Signal
from pitch_core import PitchDetector, Pitch
from audio_platform import audio_thread_context

class AudioEngine(QObject):
    measured = Signal(object)
    state = Signal(str)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.thread = None
        self.stop_event = threading.Event()
        self.gate = -50.
        self.stream = None
        self.sample_rate = 0

    def start(self, device=None, gate=-50.):
        self.stop()
        self.gate = gate
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(device, self.stop_event), daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.)
            self.thread = None

    def _run(self, device, stopped):
        with audio_thread_context():
            self._capture(device,stopped)

    def _capture(self,device,stopped):
        blocks = queue.Queue(maxsize=4)
        audio_warning = threading.Event()
        def callback(indata, frames, timing, status):
            if status:
                audio_warning.set()
            try:
                blocks.put_nowait(indata[:, 0].copy())
            except queue.Full:
                audio_warning.set()
        try:
            info = sd.query_devices(device, 'input')
            rate = int(info['default_samplerate'])
            detector = PitchDetector(rate)
            window_size = 4096 if rate <= 48000 else 8192
            history = deque(maxlen=3)
            buffer = np.empty(0, dtype=np.float32)
            self.sample_rate = rate
            with sd.InputStream(device=device, samplerate=rate, blocksize=1024,
                                channels=1, dtype='float32', latency='low', callback=callback) as stream:
                self.stream = stream
                self.state.emit('正在聆听')
                last_data = time.monotonic()
                while not stopped.is_set():
                    try:
                        block = blocks.get(timeout=.25)
                    except queue.Empty:
                        if not stream.active or time.monotonic() - last_data > 2:
                            raise RuntimeError('麦克风已断开，请在设置中重新选择输入设备。')
                        continue
                    last_data = time.monotonic()
                    if audio_warning.is_set():
                        buffer = np.empty(0, dtype=np.float32)
                        history.clear()
                        audio_warning.clear()
                    buffer = np.concatenate((buffer, block))[-window_size:]
                    if len(buffer) < window_size:
                        continue
                    result = detector.detect(buffer, self.gate)
                    if result.frequency is None:
                        history.clear()
                    else:
                        if history and abs(1200 * np.log2(result.frequency / history[-1])) > 80:
                            history.clear()
                        history.append(result.frequency)
                        result = Pitch(float(np.median(history)), result.confidence, result.db, result.clipped)
                    self.measured.emit(result)
        except Exception as error:
            if not stopped.is_set():
                self.failed.emit(str(error))
        finally:
            self.stream = None
