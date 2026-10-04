# Third-party notices

Apollo bundles the unmodified Figtree variable font, Copyright 2022 The Figtree
Project Authors, under the SIL Open Font License 1.1. The copyright and full
license travel with the font in [assets/fonts/OFL.txt](assets/fonts/OFL.txt).
Source: https://github.com/google/fonts/tree/main/ofl/figtree

Apollo's own source is MIT licensed. The application uses unmodified Qt 6,
PySide6 Essentials and Shiboken6 from the Qt for Python Community Edition,
Copyright The Qt Company Ltd. and other contributors, under LGPL version 3.
The Windows binary uses version 6.11.2. QtCore, QtGui, QtWidgets and their
runtime dependencies are dynamically loaded; no Qt source modifications are made.

- Project and license information: https://doc.qt.io/qtforpython-6/
- Qt source: https://code.qt.io/cgit/qt/qtbase.git/?h=v6.11.2
- PySide/Shiboken source: https://code.qt.io/cgit/pyside/pyside-setup.git/?h=v6.11.2
- Source downloads: https://download.qt.io/official_releases/QtForPython/
- LGPL/GPL texts: [licenses/](licenses/)

Apollo's complete application source and PyInstaller spec are available in this
repository. Users may modify and rebuild Apollo, including with modified or
replacement compatible Qt/PySide libraries. No Apollo restriction prohibits reverse
engineering for debugging changes to those libraries. See docs/development.md and
packaging/build-exe.bat for the build procedure. Build requirements do not require
proprietary tools or a paid Qt license.

Other runtime dependencies retain their upstream licenses: NumPy (BSD), Pillow
(MIT-CMU), Requests (Apache-2.0), keyboard/mouse (MIT), pyperclip (BSD), pystray
(LGPL-3.0), sounddevice (MIT), PortAudio (MIT), comtypes (MIT), certifi (MPL-2.0),
charset-normalizer (MIT), idna (BSD) and urllib3 (MIT). See the respective installed
package metadata for full copyright and license details.
