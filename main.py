"""Chess Assistant entry point.

Usage:  python main.py
"""
import argparse
import multiprocessing
import tkinter as tk

from app.capture import make_dpi_aware
from app.gui import ChessAssistantApp
from app.paths import find_stockfish


def main() -> None:
    parser = argparse.ArgumentParser(description="Chess Assistant")
    parser.add_argument("--self-test", metavar="REPORT", help="check the build and write a JSON report")
    args = parser.parse_args()
    make_dpi_aware()  # before any Tk window exists
    if args.self_test:
        from app.diagnostics import run_self_test
        raise SystemExit(run_self_test(args.self_test))
    root = tk.Tk()
    ChessAssistantApp(root, find_stockfish())
    root.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
