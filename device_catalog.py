"""List input endpoints without duplicate audio interfaces."""
from dataclasses import dataclass
import re
import sounddevice as sd

@dataclass(frozen=True)
class InputOption:
    label: str
    key: str
    index: int

def identity(name):
    return re.sub(r'[^\w]', '', name.casefold())

def display_name(name):
    for prefix, translated in [('Microphone Array (', '麦克风阵列'), ('Microphone (', '麦克风')]:
        if name.startswith(prefix) and name.endswith(')'):
            return f'{translated} · {name[len(prefix):-1]}'
    return name

def input_options():
    devices, apis = sd.query_devices(), sd.query_hostapis()
    available = [(i, d, apis[d['hostapi']]['name']) for i, d in enumerate(devices)
                 if d['max_input_channels'] > 0
                 and not d['name'].startswith(('Microsoft Sound Mapper', 'Primary Sound Capture'))]
    for preferred in ['Windows WASAPI', 'Windows DirectSound', 'MME', 'Windows WDM-KS']:
        group = [entry for entry in available if entry[2] == preferred]
        if group:
            available = group
            break
    seen, result = set(), []
    for i, device, api in available:
        key = identity(device['name'])
        if key not in seen:
            seen.add(key)
            result.append(InputOption(display_name(device['name']), f"{api} | {device['name']}", i))
    return sorted(result, key=lambda entry: entry.label.casefold())

def matching_option(key, options):
    if key is None:
        return None
    for option in options:
        if key == option.key:
            return option
    old_name = identity(key.split(' | ', 1)[-1])
    for option in options:
        name = identity(option.key.split(' | ', 1)[-1])
        if min(len(name), len(old_name)) >= 12 and (name.startswith(old_name) or old_name.startswith(name)):
            return option
    return None
