# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
project_root = os.getcwd()

a = Analysis(
    ['app.py'],
    pathex=[project_root],
    binaries=[],
    datas=[
        (os.path.join(project_root, 'bot', 'bot.py'),        'bot'),
        (os.path.join(project_root, 'accounts.json'), '.'  ),
        (os.path.join(project_root, 'templates'),            'templates'),
        (os.path.join(project_root, 'static'),               'static'),
        (os.path.join(project_root, 'media'),                'media')
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='nozomi',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='samuraipeq.ico'
)


coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name='nozomi'
)
