"""Monophonic YIN estimator. No UI or audio-device dependencies."""
from dataclasses import dataclass
import math
import numpy as np

NAMES = ('C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B')
FLAT_NAMES = ('C', 'D♭', 'D', 'E♭', 'E', 'F', 'G♭', 'G', 'A♭', 'A', 'B♭', 'B')

def frequency_for(midi: int, a4: float = 440.0) -> float:
    return a4 * 2.0 ** ((midi - 69) / 12.0)

def midi_for(frequency: float, a4: float = 440.0) -> float:
    return 69.0 + 12.0 * math.log2(frequency / a4)

def note_name(midi: int, notation: str = 'sharp') -> str:
    names = FLAT_NAMES if notation == 'flat' else NAMES
    return f'{names[midi % 12]}{midi // 12 - 1}'

def cents_from(frequency: float, midi: int, a4: float = 440.0) -> float:
    return 1200.0 * math.log2(frequency / frequency_for(midi, a4))

@dataclass(frozen=True)
class Pitch:
    frequency: float | None
    confidence: float
    db: float
    clipped: bool = False

class PitchDetector:
    def __init__(self, sample_rate=48000, minimum=60.0, maximum=1600.0):
        self.sample_rate = sample_rate
        self.minimum = minimum
        self.maximum = maximum
        taps = max(31, int(sample_rate / 750) | 1)
        points = np.arange(taps) - (taps - 1) / 2
        kernel = np.sinc(2 * 2800 / sample_rate * points) * np.hamming(taps)
        self.filter = kernel / kernel.sum()

    def _yin_difference(self, samples):
        """Exact fixed-overlap YIN difference; shared by live/offline estimators."""
        x = np.asarray(samples, dtype=np.float64)
        x = np.convolve(x - x.mean(), self.filter, mode='valid')
        high = min(len(x) // 3, int(self.sample_rate / self.minimum) + 2)
        size = len(x) - high
        fft_size = 1 << (len(x) + size - 2).bit_length()
        convolution = np.fft.irfft(np.fft.rfft(x, fft_size) *
                                   np.fft.rfft(x[:size][::-1], fft_size), fft_size)
        cross = convolution[size - 1:size + high]
        energy = np.concatenate(([0.], np.cumsum(x * x)))
        lag = np.arange(high + 1)
        difference = np.maximum(energy[size] + energy[size + lag] - energy[lag] - 2 * cross, 0.)
        cmnd = np.ones(high + 1)
        cmnd[1:] = difference[1:] * np.arange(1, high + 1) / np.maximum(np.cumsum(difference[1:]), 1e-20)
        return difference, cmnd

    def _frequency_at(self, difference, tau):
        left, center, right = difference[tau - 1:tau + 2]
        divisor = left - 2 * center + right
        delta = .5 * (left - right) / divisor if abs(divisor) > 1e-20 else 0.
        return self.sample_rate / (tau + float(np.clip(delta, -.5, .5)))

    def detect(self, samples, gate_db=-50.0) -> Pitch:
        x = np.asarray(samples, dtype=np.float64)
        rms = float(np.sqrt(np.mean(x * x)))
        db = 20 * math.log10(max(rms, 1e-10))
        clipped = bool(np.max(np.abs(x)) >= .995)
        if db < gate_db or len(x) < 2048:
            return Pitch(None, 0., db, clipped)
        low = max(2, int(self.sample_rate / self.maximum))
        difference, cmnd = self._yin_difference(x)
        high = len(cmnd)-1
        candidates = np.flatnonzero(cmnd[low:high - 1] < .15)
        if not len(candidates):
            return Pitch(None, 0., db, clipped)
        tau = low + int(candidates[0])
        while tau + 1 < high and cmnd[tau + 1] < cmnd[tau]:
            tau += 1
        confidence = float(max(0., min(1., 1 - cmnd[tau])))
        frequency = self._frequency_at(difference, tau)
        if confidence < .85 or not self.minimum <= frequency <= self.maximum:
            return Pitch(None, confidence, db, clipped)
        return Pitch(float(frequency), confidence, db, clipped)
