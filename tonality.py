"""Duration/confidence weighted key-profile correlation for monophonic recordings."""
from dataclasses import dataclass
import numpy as np
from pitch_core import NAMES, FLAT_NAMES

MAJOR_PROFILE=np.array([6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88])
MINOR_PROFILE=np.array([6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17])
SCALES={'major':(0,2,4,5,7,9,11),'minor':(0,2,3,5,7,8,10)}

@dataclass(frozen=True)
class KeyEstimate:
    tonic:int
    mode:str
    correlation:float
    margin:float

def valid_key(value):
    if value in ('auto','none'):
        return True
    return isinstance(value,str) and value in {f'{root}:{mode}' for root in range(12) for mode in SCALES}

def key_name(tonic,mode,notation='sharp'):
    names=FLAT_NAMES if notation=='flat' else NAMES
    return f'{names[tonic]} '+('大调' if mode=='major' else '小调')

def pitch_classes(tonic,mode):
    return {(tonic+step)%12 for step in SCALES[mode]}

def estimate_key(track):
    valid=np.isfinite(track.midi)&(track.confidence>=.75)
    if np.count_nonzero(valid)*track.hop<.8:
        return None
    notes=np.floor(track.midi[valid]+.5).astype(int)%12
    histogram=np.bincount(notes,weights=track.confidence[valid]**2,minlength=12).astype(float)
    if np.count_nonzero(histogram>histogram.sum()*.025)<3:
        return None
    centered=histogram-histogram.mean()
    norm=np.linalg.norm(centered)
    if norm<1e-10:
        return None
    candidates=[]
    for mode,profile in [('major',MAJOR_PROFILE),('minor',MINOR_PROFILE)]:
        centered_profile=profile-profile.mean()
        for tonic in range(12):
            correlation=float(np.dot(centered,np.roll(centered_profile,tonic))/(norm*np.linalg.norm(centered_profile)))
            candidates.append((correlation,tonic,mode))
    candidates.sort(reverse=True)
    best=candidates[0]
    if best[0]<.3:
        return None
    return KeyEstimate(best[1],best[2],best[0],best[0]-candidates[1][0])
