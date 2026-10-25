"""Initialize Windows COM in each thread that owns a WASAPI stream."""
from contextlib import contextmanager
import sys

@contextmanager
def audio_thread_context():
    initialized=False
    if sys.platform=='win32':
        import ctypes
        result=ctypes.windll.ole32.CoInitializeEx(None,0)
        initialized=result in (0,1)
    try:
        yield
    finally:
        if initialized:
            ctypes.windll.ole32.CoUninitialize()
