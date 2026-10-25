"""Synthetic vocal-like phrases for deterministic analysis and UI checks."""
import numpy as np
from pitch_core import frequency_for
from recording_analysis import AudioClip

def phrase(rate=48000):
    parts=[]
    expected=[]
    elapsed=0.
    for note,cents,length in [(60,-14.,.9),(64,8.,1.),(67,-21.,1.1),(69,3.,1.6),
                              (70,18.,.85),(69,-7.,1.05),(67,11.,1.3),(64,-5.,1.25)]:
        t=np.arange(round(rate*length))/rate
        vibrato=np.sin(2*np.pi*5*t)*18*np.minimum(1.,t/.2)
        frequency=frequency_for(note)*2**((cents+vibrato)/1200)
        phase=2*np.pi*np.cumsum(frequency)/rate
        envelope=np.minimum(1.,t/.025)*np.minimum(1.,(length-t)/.03)
        signal=envelope*(.17*np.sin(phase)+.22*np.sin(2*phase)+.09*np.sin(3*phase))
        parts.extend([signal,np.zeros(round(rate*.16))])
        expected.append((elapsed+.18,elapsed+length-.15,note,cents))
        elapsed+=length+.16
    audio=np.concatenate(parts).astype(np.float32).reshape(-1,1)
    return AudioClip('synthetic-vocal.wav',audio,rate),expected
