"""Stockfish wrapper.

Analysis runs on a dedicated thread; results arrive on a queue the GUI
polls, so a slow, busy or missing engine never blocks capture or UI.
"""
from __future__ import annotations

import os
import queue
import threading

import chess
import chess.engine

LEVELS = {
    "Casual":   {"skill": 3, "time": 0.25},
    "Balanced": {"skill": 10, "time": 0.5},
    "Max":      {"skill": 20, "time": 1.0},
}


def eval_white_pct(score_white: chess.engine.Score) -> float:
    """White's winning chances, 0-100 (lichess-style sigmoid)."""
    if score_white.is_mate():
        return 100.0 if score_white.mate() > 0 else 0.0
    cp = max(-1500.0, min(1500.0, float(score_white.score())))
    return 50.0 + 50.0 * (2.0 / (1.0 + 2.718281828 ** (-0.00368208 * cp)) - 1.0)


def format_eval(score_white: chess.engine.Score) -> str:
    if score_white.is_mate():
        m = abs(score_white.mate())
        return ("M" if score_white.mate() > 0 else "-M") + str(m)
    return f"{score_white.score() / 100.0:+.2f}"


class EngineWorker:
    """Single-consumer analysis thread.

    `submit(fen, level)` requests analysis; `results` yields
    ("eval", {...}) / ("engine_error", msg) / ("engine_ready", None) events.
    """

    def __init__(self, path: str):
        self.path = path
        self._requests: queue.Queue = queue.Queue(maxsize=1)
        self.results: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, fen: str, level: str) -> None:
        try:
            self._requests.put_nowait((fen, level))
        except queue.Full:
            try:  # a stale request is pending — replace it
                self._requests.get_nowait()
            except queue.Empty:
                pass
            try:
                self._requests.put_nowait((fen, level))
            except queue.Full:
                pass

    def stop(self) -> None:
        try:
            self._requests.put_nowait(None)
        except queue.Full:
            pass

    def _run(self) -> None:
        try:
            engine = chess.engine.SimpleEngine.popen_uci(self.path)
            engine.configure({"Threads": max(1, (os.cpu_count() or 2) // 2),
                              "Hash": 256})
        except Exception as e:
            self.results.put(("engine_error", f"could not start Stockfish: {e}"))
            return
        self.results.put(("engine_ready", None))
        level = None
        while True:
            item = self._requests.get()
            if item is None:
                break
            fen, want = item
            try:
                cfg = LEVELS.get(want, LEVELS["Balanced"])
                if want != level:
                    engine.configure({"Skill Level": cfg["skill"]})
                    level = want
                board = chess.Board(fen)
                if board.is_game_over():
                    continue
                info = engine.analyse(board, chess.engine.Limit(time=cfg["time"]))
                pv = info.get("pv") or []
                score = info.get("score")
                if not pv or score is None:
                    continue
                move = pv[0]
                sw = score.white()
                self.results.put(("eval", {
                    "fen": fen,
                    "san": board.san(move),
                    "uci": move.uci(),
                    "pct": eval_white_pct(sw),
                    "eval": format_eval(sw),
                }))
            except Exception as e:
                self.results.put(("engine_error", f"analysis failed: {e}"))
        try:
            engine.quit()
        except Exception:
            pass
