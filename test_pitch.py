import json
import math
from pathlib import Path
import unittest
import numpy as np
from pitch_core import PitchDetector, frequency_for, cents_from, note_name, midi_for

class PitchAccuracy(unittest.TestCase):
    def test_conversion(self):
        self.assertEqual(note_name(60), 'C4')
        self.assertEqual(note_name(69), 'A4')
        sharp = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B']
        flat = ['C', 'D♭', 'D', 'E♭', 'E', 'F', 'G♭', 'G', 'A♭', 'A', 'B♭', 'B']
        for octave in range(1, 7):
            for step in range(12):
                midi = (octave+1)*12+step
                self.assertEqual(note_name(midi, 'sharp'), f'{sharp[step]}{octave}')
                self.assertEqual(note_name(midi, 'flat'), f'{flat[step]}{octave}')
        self.assertAlmostEqual(frequency_for(69), 440)
        self.assertAlmostEqual(cents_from(440 * 2**(17.3 / 1200), 69), 17.3)
        self.assertAlmostEqual(cents_from(440 * 2**(-32.8 / 1200), 69), -32.8)
        self.assertAlmostEqual(cents_from(442, 69, 442), 0)
        self.assertAlmostEqual(cents_from(880, 69), 1200)

    def test_frequency_accuracy(self):
        rng = np.random.default_rng(629)
        errors = {'sine': [], 'harmonics': [], 'noise_30db': []}
        worst = None
        for rate in [44100, 48000, 96000]:
            detector = PitchDetector(rate)
            # More than two full cycles must fit the fixed comparison window.
            for midi in range(36, 92):
                for cents in [-43.7, -12.5, 0., 5.2, 38.9]:
                    freq = frequency_for(midi) * 2**(cents / 1200)
                    if not 62 <= freq <= 1580:
                        continue
                    t = np.arange(4096 if rate <= 48000 else 8192) / rate
                    phase = rng.uniform(0, 2*np.pi)
                    sine = .25 * np.sin(2*np.pi*freq*t + phase)
                    harmonic = sum(a * np.sin(2*np.pi*freq*k*t + phase/k)
                                   for k,a in [(1,.16),(2,.28),(3,.19),(4,.08),(5,.04)])
                    noise = sine + rng.normal(0, .25 / math.sqrt(2) / 31.62, len(t))
                    for name, signal in [('sine',sine),('harmonics',harmonic),('noise_30db',noise)]:
                        result = detector.detect(signal)
                        self.assertIsNotNone(result.frequency, f'{name}, {rate}, {freq}')
                        error = abs(1200 * math.log2(result.frequency / freq))
                        errors[name].append(error)
                        if worst is None or error > worst['error_cents']:
                            worst = dict(error_cents=error, kind=name, rate=rate, frequency=freq)
                        self.assertLess(error, 1.0, f'{name}, {rate}, {freq}, {result.frequency}')
        report = {key: dict(samples=len(values), maximum_cents=max(values),
                           mean_cents=float(np.mean(values)), p95_cents=float(np.percentile(values,95)))
                  for key, values in errors.items()}
        report['worst'] = worst
        Path('verification').mkdir(exist_ok=True)
        Path('verification/accuracy.json').write_text(json.dumps(report, indent=2))

    def test_silence_noise_and_clipping(self):
        detector = PitchDetector()
        self.assertIsNone(detector.detect(np.zeros(4096)).frequency)
        rng = np.random.default_rng(91)
        for _ in range(30):
            self.assertIsNone(detector.detect(rng.normal(0,.12,4096)).frequency)
        self.assertIsNone(detector.detect(.0001 * np.sin(np.arange(4096)*.1)).frequency)
        self.assertTrue(detector.detect(np.ones(4096)).clipped)

    def test_vibrato_and_transition(self):
        rate = 48000
        detector = PitchDetector(rate)
        t = np.arange(4096) / rate
        for instant in [0., .07, .14, .21]:
            local = t + instant
            f = 220 * 2**(25 * np.sin(2*np.pi*5*local) / 1200)
            phase = 2*np.pi*np.cumsum(f)/rate
            result = detector.detect(.2*np.sin(phase) + .1*np.sin(2*phase))
            self.assertIsNotNone(result.frequency)
            self.assertLess(abs(1200*math.log2(result.frequency/220)), 28)

if __name__ == '__main__':
    unittest.main(verbosity=2)
