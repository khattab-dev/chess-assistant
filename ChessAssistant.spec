# Build with: python -m PyInstaller --noconfirm --clean ChessAssistant.spec
from pathlib import Path

root = Path(SPECPATH)
engine = root / "engines" / "stockfish.exe"
if not engine.is_file():
    raise SystemExit("Run python setup.py first to install Stockfish.")

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=[(str(engine), "engines")],
    datas=[(str(root / "licenses"), "licenses"),
           (str(root / "app" / "assets"), "app/assets")],
    hiddenimports=["mss.windows"],
    excludes=["pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name="ChessAssistant",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
