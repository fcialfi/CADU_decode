# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec of the CADU Decoder window: one CADU-Decoder.exe, no console.
#
#     pyinstaller cadu_gui.spec      (or build.bat on Windows)

a = Analysis(
    ['cadu_gui.py'],
    pathex=[SPECPATH],
    binaries=[],
    # Exostaff logo and icon; also the licence public key (assets/license_public_key.pem) once
    # license_tool.py keygen has created it, which turns the licence check on (see README).
    datas=[('assets', 'assets')],
    # numpy is imported inside a function (the Reed-Solomon check), cryptography inside licensing.py:
    # listed so they are never left out.
    hiddenimports=['numpy', 'cadu_decode', 'rs_ccsds', 'summary_view', 'detail_view', 'version', 'licensing',
                   'cryptography.exceptions', 'cryptography.hazmat.primitives.serialization',
                   'cryptography.hazmat.primitives.asymmetric.ed25519'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Not used: keeps the executable small.
    excludes=['matplotlib', 'scipy', 'pandas', 'PIL', 'pytest', 'IPython'],
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
    name='CADU-Decoder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX-compressed files are often flagged by antivirus programs: off.
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/exostaff_icon.ico'],
)
