import unittest
from types import SimpleNamespace
import numpy as np
from tonality import estimate_key,pitch_classes,valid_key,key_name,MAJOR_PROFILE,MINOR_PROFILE

def track_for(notes):
    return SimpleNamespace(midi=np.array(notes,dtype=float),confidence=np.ones(len(notes)),hop=.01)

class Tonality(unittest.TestCase):
    def test_major_and_minor_profiles_in_all_keys(self):
        for mode,profile in [('major',MAJOR_PROFILE),('minor',MINOR_PROFILE)]:
            for root in range(12):
                notes=[]
                for step,weight in enumerate(profile):notes += [60+root+step]*round(weight*100)
                estimate=estimate_key(track_for(notes))
                self.assertEqual((estimate.tonic,estimate.mode),(root,mode))

    def test_cadence_and_short_ambiguous_input(self):
        notes=[]
        for note,count in [(60,100),(64,55),(67,65),(65,45),(62,35),(67,80),(71,35),(72,180)]:
            notes += [note]*count
        estimate=estimate_key(track_for(notes))
        self.assertEqual((estimate.tonic,estimate.mode),(0,'major'))
        self.assertIsNone(estimate_key(track_for([69]*200)))
        self.assertIsNone(estimate_key(track_for([60,64,67]*10)))
        self.assertEqual(pitch_classes(0,'major'),{0,2,4,5,7,9,11})
        self.assertEqual(pitch_classes(9,'minor'),{0,2,4,5,7,9,11})
        self.assertEqual(key_name(10,'major','flat'),'B♭ 大调')
        self.assertTrue(valid_key('auto'));self.assertTrue(valid_key('9:minor'))
        self.assertFalse(valid_key('12:minor'));self.assertFalse(valid_key(None))

if __name__=='__main__':unittest.main(verbosity=2)
