# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller --noconfirm Parser.spec
#
# Ядро parser_2gis (лицензия LGPLv3) кладётся в сборку обычными .py-файлами,
# а не прячется в архив PYZ: так пользователь может заменить его своей версией,
# как требует LGPL. Ваш код из app/ компилируется в архив как обычно.
import os
import sys

sys.path.insert(0, os.path.abspath('.'))
from app import brand  # noqa: E402

APP = brand.APP_NAME
ICON_WIN = 'assets/icon.ico'
ICON_MAC = 'assets/icon.icns'

# Зависимости ядра: раз ядро исключено из анализа, перечисляем их явно.
core_hidden = [
    'pychrome', 'pychrome.exceptions', 'pychrome.tab', 'pychrome.browser',
    'websocket', 'psutil', 'requests', 'requests.exceptions',
    'xlsxwriter', 'xlsxwriter.workbook', 'pydantic', 'pydantic_core',
    'argparse', 'base64', 'codecs', 'csv', 'ctypes', 'dataclasses', 'html',
    'app.yandex', 'json', 'queue', 'shlex', 'socket', 'tempfile', 'urllib.parse', 'warnings',
]
if sys.platform.startswith('win'):
    core_hidden.append('winreg')


def _core_datas():
    out = []
    for root, dirs, files in os.walk('parser_2gis'):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for f in files:
            if f.endswith(('.pyc', '.pyo')):
                continue
            out.append((os.path.join(root, f), root))
    return out


datas = _core_datas() + [
    ('app/ui', 'app/ui'),
    ('LICENSE-LGPL.txt', '.'),
    ('NOTICE.txt', '.'),
]

a = Analysis(
    ['launcher.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=core_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=['parser_2gis', 'tkinter', 'PySimpleGUI', 'flask', 'pytest'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP,
    debug=False,
    strip=False,
    upx=False,
    console=False,          # без чёрного окна консоли
    icon=ICON_WIN if sys.platform.startswith('win') else ICON_MAC,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name=APP + '.app',
        icon=ICON_MAC,
        bundle_identifier=brand.APP_ID,
        version=brand.APP_VERSION,
        info_plist={
            'CFBundleDisplayName': APP,
            'CFBundleShortVersionString': brand.APP_VERSION,
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '11.0',
        },
    )
