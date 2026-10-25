"""Test actual capture callback -> worker -> Qt signal pipeline with known audio."""
import threading
import time
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import QCoreApplication
from audio_engine import AudioEngine
from pitch_core import cents_from

class SyntheticMicrophone:
    def __init__(self, **kwargs):
        self.callback = kwargs['callback']
        self.active = True
        self.done = threading.Event()

    def __enter__(self):
        def produce():
            frequency = 440 * 2**(-27.2/1200)
            for i in range(36):
                if self.done.is_set():
                    return
                t = (np.arange(1024) + i*1024) / 48000
                block = .2*np.sin(2*np.pi*frequency*t) + .15*np.sin(4*np.pi*frequency*t)
                if i >= 22:
                    block = np.zeros(1024)
                self.callback(block.astype(np.float32).reshape(-1,1),1024,None,None)
                time.sleep(.01)
        self.thread = threading.Thread(target=produce)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.done.set()
        self.thread.join()
        self.active = False

application = QCoreApplication([])
engine = AudioEngine()
results, errors = [], []
engine.measured.connect(results.append)
engine.failed.connect(errors.append)
with patch('audio_engine.sd.query_devices', return_value={'default_samplerate':48000}), \
     patch('audio_engine.sd.InputStream', SyntheticMicrophone):
    engine.start()
    deadline = time.monotonic()+.6
    while time.monotonic() < deadline:
        application.processEvents()
        time.sleep(.005)
    engine.stop()
    application.processEvents()
voiced = [r for r in results if r.frequency]
assert not errors, errors
assert len(voiced) > 10, len(voiced)
assert max(abs(cents_from(r.frequency,69) + 27.2) for r in voiced[2:15]) < .5
assert all(r.frequency is None for r in results[-5:]), 'Stale pitch after silence'
assert not engine.thread
print('PASS: input callback, worker estimation, Qt delivery, cents accuracy, silence reset, clean shutdown')
