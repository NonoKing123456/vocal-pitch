import math
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import numpy as np
import soundfile as sf
import sounddevice as sd
from PySide6.QtCore import QCoreApplication
from recording_analysis import AudioClip, analyze, load_audio, AnalysisCancelled
from recording_audio import AudioPlayer, Recorder, clock_position
from recording_fixture import phrase

application=QCoreApplication.instance() or QCoreApplication([])

class RecordingAnalysis(unittest.TestCase):
    def test_phrase_accuracy_notes_and_silence(self):
        clip,expected=phrase()
        track=analyze(clip)
        self.assertGreaterEqual(len(track.segments),8)
        self.assertLessEqual(len(track.segments),10)
        for start,end,note,cents in expected:
            values=track.midi[(track.times>=start)&(track.times<=end)]
            self.assertGreater(np.isfinite(values).mean(),.95)
            self.assertLess(abs((float(np.nanmedian(values))-note)*100-cents),4)
        self.assertTrue(any(segment.note==70 for segment in track.segments))
        rest=track.midi[(track.times>.96)&(track.times<1.01)]
        self.assertTrue(np.all(~np.isfinite(rest)))
        self.assertEqual(track.at(.4)[0],60)

    def test_precision_vibrato_and_antiphase_stereo(self):
        rate=44100
        t=np.arange(rate*2)/rate
        frequency=440*2**(12.3/1200)
        mono=(.2*np.sin(2*np.pi*frequency*t)).astype(np.float32)
        clip=AudioClip('test',np.column_stack((mono,-mono)),rate)
        track=analyze(clip)
        values=track.midi[(track.times>.2)&(track.times<1.8)]
        self.assertLess(abs((float(np.nanmedian(values))-69)*100-12.3),.5)
        f=440*2**((30*np.sin(2*np.pi*5*t))/1200)
        signal=(.2*np.sin(2*np.pi*np.cumsum(f)/rate)).astype(np.float32)
        vibrato=analyze(AudioClip('vibrato',signal[:,None],rate))
        pitch=vibrato.midi[(vibrato.times>.2)&(vibrato.times<1.8)]
        self.assertGreater((np.nanpercentile(pitch,95)-np.nanpercentile(pitch,5))*100,40)
        self.assertLessEqual(len(vibrato.segments),2)

    def test_io_cancel_noise_and_tuning(self):
        rate=48000
        t=np.arange(rate)/rate
        clip=AudioClip('note',(.2*np.sin(2*np.pi*442*t)).astype(np.float32)[:,None],rate)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'audio.wav'
            sf.write(path,clip.audio,rate,subtype='PCM_24')
            loaded=load_audio(path)
            self.assertEqual(loaded.audio.shape,clip.audio.shape)
            self.assertLess(float(np.max(np.abs(loaded.audio-clip.audio))),2e-7)
        track=analyze(clip,a4=442.)
        self.assertLess(abs(track.at(.5)[1]),.5)
        cancelled=threading.Event();cancelled.set()
        with self.assertRaises(AnalysisCancelled):
            analyze(clip,cancelled=cancelled)
        noise=np.random.default_rng(22).normal(0,.1,rate).astype(np.float32)
        noisy=analyze(AudioClip('noise',noise[:,None],rate))
        self.assertLess(np.isfinite(noisy.midi).mean(),.04)
        silent=analyze(AudioClip('silence',np.zeros((rate,1),np.float32),rate))
        self.assertFalse(np.any(np.isfinite(silent.midi)))

    def test_high_sample_rate_resampling(self):
        rate=96000
        t=np.arange(rate)/rate
        target=220*2**(-17.4/1200)
        signal=(.2*np.sin(2*np.pi*target*t)).astype(np.float32)
        track=analyze(AudioClip('96k',signal[:,None],rate))
        self.assertLess(abs(track.at(.5)[1]+17.4),.5)

class FakeOutput:
    def __init__(self,**kwargs):
        self.callback=kwargs['callback'];self.finished=kwargs['finished_callback']
        self.time=10.;self.active=False
    def start(self):self.active=True
    def abort(self):self.active=False;self.finished()
    def close(self):self.active=False

class Playback(unittest.TestCase):
    def test_clock_follows_dac_and_loop_wrapping(self):
        self.assertEqual(clock_position([],0,48000,.4),.4)
        self.assertAlmostEqual(clock_position([(10.,48000,480),(10.01,48480,480)],10.015,48000,0),1.015)
        self.assertAlmostEqual(clock_position([(10.,48000,480),(10.01,24000,480)],10.015,48000,0),.505)

    def test_callback_mono_stereo_loop_seek_pause(self):
        data=np.arange(20,dtype=np.float32)[:,None]/20
        clip=AudioClip('buffer',data,48000)
        player=AudioPlayer();player.set_clip(clip)
        with patch('recording_audio.sd.query_devices',return_value={'max_output_channels':2,'default_samplerate':48000}), \
             patch('recording_audio.sd.check_output_settings'),patch('recording_audio.sd.OutputStream',FakeOutput):
            player.current=4/48000;player.loop=(3/48000,7/48000)
            # Use a longer buffer to exercise the public 50ms minimum loop duration.
            clip.audio=np.repeat(data,1000,axis=0)
            player.set_clip(clip);player.current=4000/48000;player.loop=(3000/48000,7000/48000)
            player.play()
            output=np.zeros((5000,2),np.float32)
            timing=type('Time',(),{'outputBufferDacTime':11.})()
            player.stream.callback(output,5000,timing,None)
            self.assertTrue(np.allclose(output[:3000,0],clip.audio[4000:7000,0]))
            self.assertTrue(np.allclose(output[3000:,0],clip.audio[3000:5000,0]))
            self.assertTrue(np.array_equal(output[:,0],output[:,1]))
            player.stream.time=11+3500/48000
            self.assertAlmostEqual(player.position(),3500/48000,places=6)
            player.seek(.1)
            self.assertTrue(player.running)
            player.pause();self.assertFalse(player.running)

    def test_selection_playback_stops_at_selected_end(self):
        clip=AudioClip('selection',np.zeros((48000,1),np.float32),48000)
        player=AudioPlayer();player.set_clip(clip);player.play_range=(.1,.2)
        with patch('recording_audio.sd.query_devices',return_value={'max_output_channels':2,'default_samplerate':48000}), \
             patch('recording_audio.sd.check_output_settings'),patch('recording_audio.sd.OutputStream',FakeOutput):
            player.play()
            timing=type('Time',(),{'outputBufferDacTime':11.})()
            with self.assertRaises(sd.CallbackStop):
                player.stream.callback(np.zeros((5000,2),np.float32),5000,timing,None)
            player.stream.finished()
            self.assertAlmostEqual(player.current,.2)
            player.pause()

class FakeInput:
    def __init__(self,**kwargs):
        self.callback=kwargs['callback'];self.active=True;self.stopped=threading.Event()
    def __enter__(self):
        def produce():
            for i in range(20):
                if self.stopped.is_set():break
                data=np.full((1024,1),.125,np.float32)
                self.callback(data,1024,None,None)
                time.sleep(.006)
        self.thread=threading.Thread(target=produce);self.thread.start();return self
    def __exit__(self,*args):
        self.stopped.set();self.thread.join();self.active=False

class Capture(unittest.TestCase):
    def test_pcm_saved_and_drained_on_stop(self):
        recorder=Recorder();results=[];errors=[]
        recorder.completed.connect(results.append);recorder.failed.connect(errors.append)
        with tempfile.TemporaryDirectory() as folder, \
             patch('recording_audio.sd.query_devices',return_value={'default_samplerate':48000}), \
             patch('recording_audio.sd.InputStream',FakeInput):
            path=Path(folder)/'录音.wav';recorder.start(path)
            deadline=time.monotonic()+1
            while recorder.frames<1024*10 and time.monotonic()<deadline:
                application.processEvents();time.sleep(.005)
            recorder.stop();recorder.thread.join(timeout=2);application.processEvents()
            self.assertFalse(recorder.recording)
            self.assertFalse(errors,errors)
            self.assertTrue(results)
            audio,rate=sf.read(path,always_2d=True)
            self.assertEqual(rate,48000)
            self.assertEqual(len(audio),recorder.frames)
            self.assertGreaterEqual(len(audio),1024*10)
            self.assertTrue(np.allclose(audio,.125))

if __name__=='__main__':
    unittest.main(verbosity=2)
