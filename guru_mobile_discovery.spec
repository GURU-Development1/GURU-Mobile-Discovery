# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

import os

from PyInstaller.utils.hooks import collect_all

# PyInstaller defines SPEC when loading this file
_spec_dir = os.path.dirname(os.path.abspath(SPEC))

block_cipher = None

_extra_datas: list = []
_extra_binaries: list = []
_hiddenimports: set[str] = set()

for _pkg in ("tzdata", "pillow_heif", "iphone_backup_decrypt", "cryptography"):
    try:
        d, b, h = collect_all(_pkg)
        _extra_datas += d
        _extra_binaries += b
        _hiddenimports.update(h)
    except Exception:
        pass

_manual_hidden = [
    "msgpack",
    "msgpack._cmsgpack",
    "PIL",
    "PIL._imaging",
    "PyQt6.QtSvg",
    "pillow_heif",
    "iphone_backup_decrypt",
    "iphone_backup_decrypt.google_iphone_dataprotection",
    "iphone_backup_decrypt.utils",
    "Crypto",
    "Crypto.Cipher",
    "Crypto.Cipher.AES",
    "zoneinfo",
    "plistlib",
    "sqlite3",
    "app",
    "app.backup_parser",
    "app.backup_parser.parser",
    "app.backup_parser.sms",
    "app.backup_parser.contacts",
]
for _h in _manual_hidden:
    _hiddenimports.add(_h)

_icon = os.path.join(_spec_dir, "assets", "app.ico")
_icons_dir = os.path.join(_spec_dir, "assets", "icons")
_datas = [
    (os.path.join(_spec_dir, "assets", "app.ico"), "assets"),
    (os.path.join(_spec_dir, "assets", "check.svg"), "assets"),
] + _extra_datas
if os.path.isdir(_icons_dir):
    for _name in os.listdir(_icons_dir):
        if _name.lower().endswith(".svg"):
            _datas.append((os.path.join(_icons_dir, _name), os.path.join("assets", "icons")))

_logo_dir = os.path.join(_spec_dir, "assets", "logo")
if os.path.isdir(_logo_dir):
    for _name in os.listdir(_logo_dir):
        if _name.lower().endswith(".png"):
            _datas.append((os.path.join(_logo_dir, _name), os.path.join("assets", "logo")))

a = Analysis(
    [os.path.join(_spec_dir, "main.py")],
    pathex=[_spec_dir],
    binaries=_extra_binaries,
    datas=_datas,
    hiddenimports=sorted(_hiddenimports),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GuruMobileDiscovery",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon if os.path.isfile(_icon) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="GuruMobileDiscovery",
)
