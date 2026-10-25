"""Offline probabilistic YIN candidates, continuous-frequency refinement, Viterbi tracking."""
from dataclasses import dataclass
from pathlib import Path
import math
import threading
import numpy as np
import soundfile as sf
from pitch_core import PitchDetector

MAX_SECONDS = 20*60
ALGORITHM_VERSION = 1

class AnalysisCancelled(Exception):
    pass

@dataclass
class NoteSegment:
    start: float
    end: float
    note: int
    center_midi: float
    cents: float
    spread: float

@dataclass
class PitchTrack:
    times: np.ndarray
    frequencies: np.ndarray
    confidence: np.ndarray
    midi: np.ndarray
    segments: list
    duration: float
    a4: float
    hop: float

    def at(self, seconds):
        if len(self.times) == 0:
            return None
        index = int(np.clip(np.searchsorted(self.times, seconds), 0, len(self.times)-1))
        if index and abs(self.times[index-1]-seconds) < abs(self.times[index]-seconds):
            index -= 1
        if not np.isfinite(self.midi[index]):
            return None
        midi = float(self.midi[index])
        note = int(math.floor(midi+.5))
        return note, (midi-note)*100, float(self.frequencies[index])

@dataclass
class AudioClip:
    path: str
    audio: np.ndarray
    rate: int
    waveform: np.ndarray | None = None
    wave_step: float = .01

    @property
    def duration(self):
        return len(self.audio)/self.rate

def load_audio(path):
    info = sf.info(str(path))
    if info.frames <= 0:
        raise ValueError('音频文件没有声音数据。')
    if info.duration > MAX_SECONDS or info.frames*info.channels*4 > 512*1024*1024:
        raise ValueError('请导入 20 分钟以内、解码后小于 512 MB 的录音。')
    if info.samplerate < 8000 or info.samplerate > 192000:
        raise ValueError('支持 8–192 kHz 采样率的音频。')
    data, rate = sf.read(str(path), dtype='float32', always_2d=True)
    if not np.all(np.isfinite(data)):
        raise ValueError('音频包含无效采样值。')
    return AudioClip(str(Path(path).resolve()), np.ascontiguousarray(data[:, :2]), rate)

def resample_audio(audio, source_rate, target_rate):
    if source_rate == target_rate:
        return audio
    from scipy.signal import resample_poly
    divisor = math.gcd(source_rate, target_rate)
    return np.ascontiguousarray(resample_poly(audio, target_rate//divisor, source_rate//divisor, axis=0), dtype=np.float32)

def prepare_waveform(clip):
    energies = np.einsum('ij,ij->j', clip.audio, clip.audio, dtype=np.float64)
    mono = clip.audio[:, int(np.argmax(energies))]
    step = max(1, round(clip.rate*.01))
    padded = np.pad(mono, (0, (-len(mono)) % step))
    groups = padded.reshape(-1, step)
    clip.waveform = np.column_stack((groups.min(axis=1), groups.max(axis=1)))
    clip.wave_step = step/clip.rate

class OfflineDetector(PitchDetector):
    """pYIN-style threshold integration; refinement uses the non-quantized YIN minimum."""
    def __init__(self, rate):
        super().__init__(rate)
        self.thresholds = np.linspace(.01, 1., 100)
        cdf = 1-(1-self.thresholds)**18*(1+18*self.thresholds)
        self.threshold_weights = np.diff(np.r_[0., cdf])

    def candidates(self, samples, gate):
        db = 20*np.log10(max(float(np.sqrt(np.mean(samples*samples))), 1e-10))
        if db < gate:
            return []
        difference, cmnd = self._yin_difference(samples)
        low = max(2, int(self.sample_rate/self.maximum))
        high = len(cmnd)-2
        indices = np.arange(low, high+1)
        minima = indices[(cmnd[indices] <= cmnd[indices-1]) & (cmnd[indices] < cmnd[indices+1])]
        minima = minima[cmnd[minima] < .65]
        if not len(minima):
            return []
        eligible = cmnd[minima][None,:] < self.thresholds[:,None]
        ranks = np.maximum(0,np.cumsum(eligible,axis=1)-1)
        preferences = np.where(eligible,np.exp(-2*ranks),0.)
        preferences /= np.maximum(preferences.sum(axis=1,keepdims=True),1e-20)
        weights = (preferences*self.threshold_weights[:,None]).sum(axis=0)
        voiced = float(np.clip(1-float(np.min(cmnd[minima])), 0., 1.))
        weights += .001
        weights /= weights.sum()
        result = []
        for i in np.argsort(weights)[::-1][:7]:
            lag = int(minima[i])
            frequency = self._frequency_at(difference, lag)
            if self.minimum <= frequency <= self.maximum:
                result.append((frequency, float(weights[i]*voiced**2), float(1-cmnd[lag])))
        return result

def segment_notes(times, midi, confidence, duration, hop):
    result = []
    start, anchor, gap_start, pending_start, pending_note = None, None, None, None, None

    def append(first, last, note):
        if first is None or last < first or times[last]-times[first]+hop < .06:
            return
        values = midi[first:last+1]
        valid = np.isfinite(values) & (confidence[first:last+1] >= .65)
        values = values[valid]
        if len(values) < 4:
            return
        if len(values) > 25:
            values = values[4:-4]
        center = float(np.median(values))
        result.append(NoteSegment(max(0., float(times[first]-hop/2)),
            min(duration, float(times[last]+hop/2)), int(note), center,
            (center-note)*100, float(np.percentile(values,95)-np.percentile(values,5))*100))

    for i, value in enumerate(midi):
        if not np.isfinite(value):
            if start is not None:
                gap_start = i if gap_start is None else gap_start
                if i-gap_start >= 2:
                    append(start, gap_start-1, anchor)
                    start, gap_start, pending_start = None, None, None
            continue
        gap_start = None
        if start is None:
            start, anchor = i, int(math.floor(value+.5))
            pending_start = None
            continue
        candidate = int(math.floor(value+.5))
        if abs(value-anchor) > .60 and candidate != anchor:
            if pending_start is None or pending_note != candidate:
                pending_start, pending_note = i, candidate
            if i-pending_start >= 6:
                append(start, pending_start-1, anchor)
                start, anchor, pending_start = pending_start, pending_note, None
        else:
            pending_start = None
    if start is not None:
        last = (gap_start-1) if gap_start is not None else len(times)-1
        append(start, last, anchor)
    return result

def analyze(clip, a4=440., gate=-50., cancelled=None, progress=None):
    cancelled = cancelled or threading.Event()
    if len(clip.audio) == 0:
        raise ValueError('录音为空。')
    # Choose the strongest input channel; averaging anti-phase stereo can erase the voice.
    energies = np.einsum('ij,ij->j', clip.audio, clip.audio, dtype=np.float64)
    mono = clip.audio[:, int(np.argmax(energies))]
    if clip.waveform is None:
        prepare_waveform(clip)
    rate = min(48000, clip.rate)
    mono = resample_audio(mono, clip.rate, rate)
    window = max(2048, 1 << int(math.ceil(math.log2(rate*.08))))
    hop_samples = max(1, round(rate*.01))
    centers = np.arange(0, len(mono), hop_samples)
    times = centers/rate
    padded = np.pad(mono, (window//2, window//2))
    count, states = len(times), 8
    frequencies = np.full((count, states), np.nan)
    probabilities = np.full((count, states), 1e-9)
    confidence = np.zeros((count, states))
    detector = OfflineDetector(rate)
    for i, position in enumerate(centers):
        if cancelled.is_set():
            raise AnalysisCancelled()
        candidates = detector.candidates(padded[position:position+window], gate)
        for j, (frequency, weight, certainty) in enumerate(candidates):
            frequencies[i,j], probabilities[i,j], confidence[i,j] = frequency, max(weight,1e-8), certainty
        voiced = max((entry[2] for entry in candidates), default=0.)
        probabilities[i,-1] = max(.005, 1-voiced**2) if candidates else 1.
        if progress and (i % 64 == 0 or i == count-1):
            progress(round((i+1)/count*75))

    pitches = np.full_like(frequencies, np.nan)
    valid = np.isfinite(frequencies)
    pitches[valid] = 69+12*np.log2(frequencies[valid]/a4)
    emissions = -np.log(probabilities)
    back = np.zeros((count, states), dtype=np.int8)
    cost = emissions[0].copy()
    for i in range(1, count):
        if cancelled.is_set():
            raise AnalysisCancelled()
        previous, current = pitches[i-1,:,None], pitches[i,None,:]
        voiced_pair = np.isfinite(previous) & np.isfinite(current)
        distances = np.abs(np.nan_to_num(previous-current))
        transitions = np.where(voiced_pair, np.minimum(distances*.65,12.), 2.8)
        transitions[-1,-1] = 0.
        alternatives = cost[:,None]+transitions
        back[i] = np.argmin(alternatives, axis=0)
        cost = emissions[i]+np.min(alternatives, axis=0)
        cost -= cost.min()
    chosen = np.zeros(count, dtype=np.int8)
    chosen[-1] = int(cost.argmin())
    for i in range(count-1,0,-1):
        chosen[i-1] = back[i,chosen[i]]
    f0 = frequencies[np.arange(count), chosen]
    certainty = confidence[np.arange(count), chosen]
    f0[certainty < .65] = np.nan
    midi = np.full(count, np.nan)
    voiced = np.isfinite(f0)
    midi[voiced] = 69+12*np.log2(f0[voiced]/a4)
    if progress:
        progress(90)
    segments = segment_notes(times, midi, certainty, clip.duration, hop_samples/rate)
    if progress:
        progress(100)
    return PitchTrack(times, f0, certainty, midi, segments, clip.duration, a4, hop_samples/rate)
