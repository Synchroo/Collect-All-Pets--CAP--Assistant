# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['bot_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('prompt_light_ref.png', '.'), ('prompt_rebirth_ref.png', '.'), ('prompt_hold_ref.png', '.'), ('prompt_hold_inner.png', '.'), ('egg_refs_7.npz', '.'), ('table_crystal_ref.png', '.'), ('altar_pillar_refs.npz', '.'), ('cap_assistant.ico', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CAP_Assistant',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['cap_assistant.ico'],
)
