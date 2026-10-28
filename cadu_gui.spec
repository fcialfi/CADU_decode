# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec of the CADU Decoder window: the folder dist/CADU-Decoder/ with CADU-Decoder.exe, no console.
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

# Build mode. ONEFILE = False (default) builds a folder, dist/CADU-Decoder/, with CADU-Decoder.exe next to its
# libraries (in _internal/): it starts quickly because nothing has to be unpacked, and is distributed as a zip
# of the whole folder. ONEFILE = True builds a single .exe that unpacks numpy and the other libraries to a temp
# folder at every start: slower, much slower with an antivirus scanning the files.
ONEFILE = False
NAME = 'CADU-Decoder'

# Splash screen, shown by the bootloader as soon as the .exe is started, before any Python code can run (in
# one-file mode, while the bundle is being unpacked). cadu_gui.py closes it when the window is up.
splash = Splash(
    'assets/splash.png',
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(40, 262),
    text_size=10,
    text_color='#b8c4d0',
    minify_script=True,
    always_on_top=False,
)

_exe_options = dict(
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX-compressed files are decompressed at every start and often flagged by antivirus programs: off.
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/exostaff_icon.ico'],
)

if ONEFILE:
    exe = EXE(pyz, a.scripts, splash, splash.binaries, a.binaries, a.datas, [], runtime_tmpdir=None, **_exe_options)
else:
    exe = EXE(pyz, a.scripts, splash, [], exclude_binaries=True, **_exe_options)
    coll = COLLECT(exe, splash.binaries, a.binaries, a.datas, strip=False, upx=False, name=NAME)
