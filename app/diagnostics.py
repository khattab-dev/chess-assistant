"""Quiet checks for a packaged executable; no dialogs or settings changes."""
import json
from pathlib import Path
import sys
import tkinter as tk
import traceback

import chess
import cv2
import mss
import numpy as np

from .engine import EngineWorker
from .gui import ChessAssistantApp
from .overlay import BoardArrowOverlay
from .paths import bundle_root, config_path, find_stockfish
from .recognition import PieceRecognizer
from .window_follow import WindowsWindowAPI


def run_self_test(report_path: str) -> int:
    report = {"ok": False, "frozen": bool(getattr(sys, "frozen", False)),
              "bundle_root": str(bundle_root()), "config_path": str(config_path())}
    root = overlay = worker = None
    try:
        # Exercise the native OpenCV, NumPy and screen-capture dependencies.
        assert cv2.cvtColor(np.zeros((8, 8, 3), np.uint8), cv2.COLOR_BGR2GRAY).shape == (8, 8)
        read = PieceRecognizer().read(np.zeros((256, 256, 3), np.uint8))
        assert len(read.symbols) == len(read.confidence) == 64
        report["piece_recognition"] = True
        with mss.mss() as capture:
            assert len(capture.monitors) > 1
            report["monitors"] = len(capture.monitors) - 1
        root = tk.Tk()
        root.withdraw()
        app = ChessAssistantApp.__new__(ChessAssistantApp)
        app.root = root
        app._build_ui()
        app._draw_board(chess.Board(), True, "e2e4")
        root.update_idletasks()
        window_api = WindowsWindowAPI()
        snapshot = window_api.snapshot(root.winfo_id())
        assert snapshot and snapshot.client.width > 0 and window_api.desktop().width > 0
        report["window_follow"] = True
        report["piece_icons"] = len(app.piece_renderer.images)
        assert report["piece_icons"] == 12
        assert len(app.board_canvas.find_withtag("piece")) == 32
        assert len(app.board_canvas.find_withtag("suggestion")) == 1
        overlay = BoardArrowOverlay()
        report["overlay"] = bool(overlay.layer.hwnd)
        assert report["overlay"]
        engine_path = find_stockfish()
        report["engine_path"] = engine_path
        assert engine_path, "Stockfish was not bundled"
        worker = EngineWorker(engine_path)
        event, data = worker.results.get(timeout=30)
        assert event == "engine_ready", data
        worker.submit(chess.STARTING_FEN, "Casual")
        event, data = worker.results.get(timeout=30)
        assert event == "eval", data
        assert chess.Move.from_uci(data["uci"]) in chess.Board().legal_moves
        report["suggestion"] = data["san"]
        worker.stop()
        assert not worker._thread.is_alive(), "Stockfish did not exit"
        report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if worker and worker._thread.is_alive():
            worker.stop()
        if overlay:
            overlay.close()
        if root:
            root.destroy()
        Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
