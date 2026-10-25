import unittest
from unittest.mock import patch
from device_catalog import input_options, matching_option

class DeviceList(unittest.TestCase):
    def test_deduplicated_endpoints_and_legacy_selection(self):
        apis = [{'name': name} for name in ['MME', 'Windows DirectSound', 'Windows WASAPI', 'Windows WDM-KS']]
        devices = [dict(name='Microphone (Camera Audio)', hostapi=host, max_input_channels=2)
                   for host in range(4)]
        devices += [dict(name='Microphone (Camera Audio)', hostapi=2, max_input_channels=2),
                    dict(name='Microphone Array (Realtek Audio)', hostapi=2, max_input_channels=2),
                    dict(name='Speakers', hostapi=2, max_input_channels=0),
                    dict(name='Microsoft Sound Mapper - Input', hostapi=0, max_input_channels=2)]
        with patch('device_catalog.sd.query_devices', return_value=devices), patch('device_catalog.sd.query_hostapis', return_value=apis):
            options = input_options()
        self.assertEqual(len(options), 2)
        self.assertTrue(all(item.key.startswith('Windows WASAPI') for item in options))
        selected = matching_option('Windows WDM-KS | Microphone (Camera Audio)', options)
        self.assertIsNotNone(selected)
        self.assertIn('Camera Audio', selected.label)
        self.assertIsNone(matching_option(None, options))
        self.assertIsNone(matching_option('Missing mic', options))

    def test_fallback_without_wasapi(self):
        apis = [{'name':'MME'}]
        devices = [dict(name='Microphone (Camera)', hostapi=0, max_input_channels=1)]
        with patch('device_catalog.sd.query_devices', return_value=devices), patch('device_catalog.sd.query_hostapis', return_value=apis):
            self.assertEqual(len(input_options()), 1)

if __name__ == '__main__':
    unittest.main(verbosity=2)
