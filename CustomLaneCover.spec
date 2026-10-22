# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec - build.py 가 생성한다. 직접 고치지 말 것."""

import os

ROOT = 'C:\\Users\\xor09\\Desktop\\custom-lane-cover'
ONEFILE = True

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[(os.path.join(ROOT, "assets", "icon.ico"), "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'cv2', 'matplotlib', 'scipy', 'pandas', 'IPython', 'jupyter', 'notebook', 'nbformat', 'nbconvert', 'ipykernel', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'wx', 'pytest', 'setuptools', 'pip', 'PIL.ImageQt'],
    noarchive=False,
)

pyz = PYZ(a.pure)

_common = dict(
    name='CustomLaneCover',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "assets", "icon.ico"),
    manifest=os.path.join(ROOT, "CustomLaneCover.manifest"),
)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        runtime_tmpdir=None,
        **_common,
    )
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **_common)
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name='CustomLaneCover',
    )
