# Third-party components

Vocal Pitch uses **PySide6 and Qt under LGPL version 3**, and libsndfile and bundled audio codecs under their applicable LGPL/BSD terms. The corresponding license texts and dependency notices are included in the portable download's `licenses/` directory. These library licenses do not constitute a separate license for this project's original source code.

## Library replacement and rebuilding

The public Windows package is a directory distribution. Keep `声准.exe` beside `_internal/`. Qt/PySide6 DLLs are located under `_internal/PySide6/` and `_internal/shiboken6/`; libsndfile is under `_internal/_soundfile_data/`; PortAudio is under `_internal/_sounddevice_data/`. Recipients may replace these libraries with ABI-compatible modified versions. There are no library integrity checks, encrypted code or restrictions on reverse engineering for debugging modifications to these libraries.

To rebuild the application against replacement Python library packages, create `.venv`, install `requirements.txt`, install the replacement packages, and run `build.ps1 -Portable`. Use matching Python architecture and compatible ABIs. This repository provides the application sources and build instructions; the upstream source locations below provide the corresponding unmodified library sources and their build systems. No modifications to these upstream libraries were made for this release.

## Source locations and licenses

| Component | Version | License / corresponding source |
| --- | --- | --- |
| PySide6 Essentials / Shiboken6 | 6.12.0 | LGPL-3.0; [official source archive](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.12.0-src/) |
| Qt Base: Core, Gui, Widgets, Network and platform plugins | 6.12.0 | LGPL-3.0 and included third-party notices; [Qt source archives](https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/) / [tagged source](https://github.com/qt/qtbase/tree/v6.12.0) |
| Qt SVG / Qt Image Formats plugins | 6.12.0 | LGPL-3.0 and included third-party notices; same official Qt source archive directory, modules `qtsvg` and `qtimageformats` |
| CPython | 3.13.7 | PSF license and bundled dependency notices; [source](https://www.python.org/downloads/release/python-3137/) |
| NumPy | 2.5.4 | BSD-3-Clause plus bundled notices; [source](https://github.com/numpy/numpy/tree/v2.5.4) |
| SciPy | 1.18.1 | BSD-3-Clause plus bundled notices; [source](https://github.com/scipy/scipy/tree/v1.18.1) |
| sounddevice | 0.5.6 | MIT; [source](https://github.com/spatialaudio/python-sounddevice) |
| PortAudio | bundled by sounddevice 0.5.6 | MIT; [binary build sources](https://github.com/spatialaudio/portaudio-binaries), [PortAudio source](https://github.com/PortAudio/portaudio) |
| SoundFile | 0.14.0 | BSD-3-Clause; [source and bundled codec build/notice information](https://github.com/bastibe/python-soundfile/tree/0.14.0) |
| libsndfile | 1.2.2 | LGPL-2.1-or-later; [source](https://github.com/libsndfile/libsndfile/releases/tag/1.2.2) |
| Audio codecs bundled with libsndfile | supplied by SoundFile wheel | LGPL/BSD; see `licenses/soundfile/licensing/license_notes.md` for individual authors, licenses and corresponding-source locations |
| CFFI / pycparser | 2.1.1 / 3.12 | MIT / BSD; [CFFI](https://foss.heptapod.net/pypy/cffi), [pycparser](https://github.com/eliben/pycparser) |
| PyInstaller bootloader | 6.22.3 | GPL with bootloader distribution exception; [source and license](https://github.com/pyinstaller/pyinstaller) |

The license collection also retains NumPy/SciPy notices for bundled OpenBLAS, LAPACK and other components. Microsoft runtime DLLs, if included by upstream wheels, retain Microsoft's applicable runtime terms. The portable package omits unused ASIO-enabled PortAudio binaries.

Upstream component copyrights remain with their respective authors. Full notices in the distribution's `licenses/` directory take precedence over this summary.
