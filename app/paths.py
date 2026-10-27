"""Locate bundled resources and persistent, per-user settings."""
import os
from pathlib import Path
import shutil
import sys


def bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def config_path() -> Path:
    if getattr(sys, "frozen", False):
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "ChessAssistant" / "config.json"
    return bundle_root() / "config.json"


def find_stockfish() -> str | None:
    override = os.environ.get("CHESS_ENGINE_PATH")
    if override and Path(override).is_file():
        return override
    roots = [bundle_root()]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).resolve().parent)
    for root in roots:
        for name in ("stockfish.exe", "stockfish"):
            engine = root / "engines" / name
            if engine.is_file():
                return str(engine)
    return shutil.which("stockfish")
