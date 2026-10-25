"""Package the portable Windows build with dependency licenses; exclude all user data."""
from importlib.metadata import distribution
from pathlib import Path
import hashlib
import json
import re
import shutil
import sys
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parent
VERSION=re.search(r"VERSION = '([^']+)'",(ROOT/'app.py').read_text(encoding='utf-8')).group(1)
BUILD=ROOT/'release'/'声准'
if not (BUILD/'声准.exe').is_file() or not (BUILD/'_internal').is_dir():
    raise SystemExit('Run build.ps1 -Portable first.')
LICENSES=BUILD/'licenses'
LICENSES.mkdir(parents=True,exist_ok=True)
manifest=[]
packages=['PySide6-Essentials','shiboken6','numpy','scipy','soundfile','sounddevice',
          'cffi','pycparser','typing-extensions','pyinstaller','packaging','setuptools']
for package in packages:
    dist=distribution(package)
    selected=[]
    for entry in dist.files or []:
        relative=Path(str(entry))
        if relative.is_absolute() or '..' in relative.parts:
            continue
        if relative.name.lower().startswith(('license','copying','copyright','notice')) or str(entry).replace('\\','/').endswith('licensing/license_notes.md'):
            if 'commercial' in str(entry).lower():
                continue
            source=Path(dist.locate_file(entry))
            if source.is_file() and source.suffix.lower() not in ('.pyc','.pyd','.dll'):
                target=LICENSES/package/relative
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(source,target)
                selected.append(target.relative_to(LICENSES).as_posix())
    manifest.append({'name':package,'version':dist.version,'notices':selected})

python_license=Path(sys.base_prefix)/'LICENSE.txt'
if python_license.exists():
    (LICENSES/'python').mkdir(exist_ok=True)
    shutil.copy2(python_license,LICENSES/'python'/'LICENSE.txt')
for name,urls in {
    'LGPL-3.0.txt':('https://raw.githubusercontent.com/spdx/license-list-data/main/text/LGPL-3.0-only.txt','https://www.gnu.org/licenses/lgpl-3.0.txt'),
    'GPL-3.0.txt':('https://raw.githubusercontent.com/spdx/license-list-data/main/text/GPL-3.0-only.txt','https://www.gnu.org/licenses/gpl-3.0.txt'),
    'LGPL-2.1.txt':('https://raw.githubusercontent.com/spdx/license-list-data/main/text/LGPL-2.1-only.txt','https://www.gnu.org/licenses/old-licenses/lgpl-2.1.txt'),
    'GPL-2.0.txt':('https://raw.githubusercontent.com/spdx/license-list-data/main/text/GPL-2.0-only.txt','https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt'),
    'PortAudio-MIT.txt':('https://raw.githubusercontent.com/PortAudio/portaudio/master/LICENSE.txt',),
}.items():
    target=LICENSES/name
    if not target.exists():
        for url in urls:
            try:
                with urllib.request.urlopen(url,timeout=20) as response:
                    content=response.read()
                if len(content)>100:
                    target.write_bytes(content)
                    break
            except OSError:
                continue
        else:
            raise RuntimeError(f'Could not download license: {name}')
    if target.stat().st_size<100:
        raise RuntimeError(f'License download failed: {name}')
(LICENSES/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
for name in ('使用说明.md','THIRD_PARTY_NOTICES.md'):
    shutil.copy2(ROOT/name,BUILD/name)
(BUILD/'README.txt').write_text('声准 Vocal Pitch '+VERSION+'\n\n解压整个文件夹，运行 声准.exe。请保留 _internal 文件夹。\n本工具使用 Qt/PySide6（LGPLv3）等组件，完整许可见 licenses 和 THIRD_PARTY_NOTICES.md。\n对应源码、替换动态库和构建说明见 THIRD_PARTY_NOTICES.md。\n\n项目：https://github.com/NonoKing123456/vocal-pitch\n',encoding='utf-8-sig')

archive=ROOT/'release'/f'VocalPitch-{VERSION}-windows-x64.zip'
excluded=('asio','libportaudio32bit','libportaudioarm64')
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as output:
    for source in sorted(BUILD.rglob('*')):
        if not source.is_file() or any(word in source.name.lower() for word in excluded):
            continue
        output.write(source,Path('VocalPitch')/source.relative_to(BUILD))
checksum=archive.with_suffix('.sha256')
checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n',encoding='ascii')
print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,'sha256':checksum.read_text().strip()},ensure_ascii=False))
