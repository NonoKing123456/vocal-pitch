"""Short microphone and inaudible playback check using actual audio devices."""
import json
from pathlib import Path
import tempfile
import time
import numpy as np
import soundfile as sf
from PySide6.QtCore import QCoreApplication
from device_catalog import input_options,matching_option
from recording_analysis import AudioClip
from recording_audio import Recorder,AudioPlayer

application=QCoreApplication([])
data_dir=Path.home()/'AppData/Local/VocalPitch'
config=json.loads((data_dir/'settings.json').read_text(encoding='utf-8'))
option=matching_option(config.get('device'),input_options())
errors=[]
recorder=Recorder();recorder.failed.connect(errors.append)
output=Path('verification/v3').resolve();output.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=output) as folder:
    assert Path(folder).resolve().is_relative_to(output)
    path=Path(folder)/'mic-check.wav'
    recorder.start(path,option.index if option else None)
    deadline=time.monotonic()+6
    while recorder.duration<.3 and time.monotonic()<deadline:
        application.processEvents();time.sleep(.01)
    recorder.stop();recorder.thread.join(timeout=3);application.processEvents()
    assert not errors,errors
    assert recorder.frames>0 and not recorder.recording
    info=sf.info(path)
    assert info.frames==recorder.frames and info.subtype=='PCM_24'
    report=dict(recorded_frames=info.frames,recorded_rate=info.samplerate,subtype=info.subtype)

player=AudioPlayer();player.failed.connect(errors.append)
player.set_clip(AudioClip('silence',np.zeros((12000,1),np.float32),48000))
ended=[];player.ended.connect(lambda:ended.append(True))
player.play()
deadline=time.monotonic()+4
positions=[]
while not ended and time.monotonic()<deadline:
    application.processEvents();positions.append(player.position());time.sleep(.01)
assert not errors,errors
assert ended,'Audio device did not finish playback'
assert max(positions)>.05,'DAC clock did not advance'
player.pause()
report.update(silent_playback=True,dac_clock_max=max(positions))
(output/'hardware.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('PASS: actual microphone PCM24 recording, saved frame count, silent output playback, DAC synchronization clock')
