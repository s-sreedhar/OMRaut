# PyInstaller single-folder app; Velopack packages and updates this directory.
from pathlib import Path
root = Path(SPECPATH)
a = Analysis([str(root / "src" / "omr_exam" / "app.py")], pathex=[str(root / "src")],
             binaries=[], datas=[(str(root / "config" / "test-config.schema.json"), "config"),
                                 (str(root / "config" / "update.json"), "config")],
             hiddenimports=["fitz", "openpyxl", "velopack"], hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="OMRaut", icon=str(root / "installer" / "OMRaut.ico"), debug=False,
          bootloader_ignore_signals=False, strip=False, upx=True, console=False,
          disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
          codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, upx_exclude=[], name="OMRaut")
