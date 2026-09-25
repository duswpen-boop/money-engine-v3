# -*- mode: python ; coding: utf-8 -*-
"""Windows onedir build. Writable data and logs live beside the EXE."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPEC).resolve().parent
frontend = root / "frontend" / "dist"
if not (frontend / "index.html").is_file():
    raise RuntimeError("Build frontend first: npm ci && npm run build")

a = Analysis(
    [str(root / "launcher.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(frontend), "frontend/dist")],
    hiddenimports=collect_submodules("uvicorn"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MoneyEngine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="MoneyEngine-Windows",
)
