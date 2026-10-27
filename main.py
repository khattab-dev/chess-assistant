"""Chess Assistant entry point.

Usage:  python main.py
"""
import os
import shutil
import tkinter as tk

from app.capture import make_dpi_aware
from app.gui import ChessAssistantApp


def find_stockfish() -> str | None:
    env = os.environ.get("CHESS_ENGINE_PATH")
    if env and os.path.isfile(env):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("stockfish.exe", "stockfish"):
        p = os.path.join(here, "engines", name)
        if os.path.isfile(p):
            return p
    return shutil.which("stockfish")


def main() -> None:
    make_dpi_aware()  # before any Tk window exists
    root = tk.Tk()
    ChessAssistantApp(root, find_stockfish())
    root.mainloop()


if __name__ == "__main__":
    main()
