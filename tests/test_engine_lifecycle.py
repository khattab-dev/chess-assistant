"""Packaging must keep Stockfish quiet and stop it before temp files go away."""
import os
import subprocess
import threading
from unittest.mock import Mock

import chess

from app.engine import EngineWorker


def test_engine_starts_without_console_and_quits(monkeypatch):
    engine = Mock()
    launch = Mock(return_value=engine)
    monkeypatch.setattr("app.engine.chess.engine.SimpleEngine.popen_uci", launch)
    worker = EngineWorker("stockfish.exe")
    assert worker.results.get(timeout=2) == ("engine_ready", None)
    worker.stop()
    assert not worker._thread.is_alive()
    expected = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    launch.assert_called_once_with("stockfish.exe", **expected)
    engine.quit.assert_called_once()


def test_shutdown_replaces_a_pending_analysis(monkeypatch):
    engine = Mock()
    entered = threading.Event()
    release = threading.Event()

    def analyse(*args):
        entered.set()
        assert release.wait(timeout=4)
        return {}

    engine.analyse.side_effect = analyse
    monkeypatch.setattr("app.engine.chess.engine.SimpleEngine.popen_uci", Mock(return_value=engine))
    worker = EngineWorker("stockfish.exe")
    assert worker.results.get(timeout=2) == ("engine_ready", None)
    worker.submit(chess.STARTING_FEN, "Casual")
    assert entered.wait(timeout=2)
    worker.submit(chess.STARTING_FEN, "Max")  # queue is full while analysing
    stopped = threading.Event()
    put = worker._requests.put_nowait

    def observe_stop(item):
        put(item)
        if item is None:
            stopped.set()

    monkeypatch.setattr(worker._requests, "put_nowait", observe_stop)
    stopper = threading.Thread(target=worker.stop)
    stopper.start()
    assert stopped.wait(timeout=2)
    release.set()
    stopper.join(timeout=4)
    assert not stopper.is_alive()
    assert not worker._thread.is_alive()
    engine.quit.assert_called_once()
    assert engine.analyse.call_count == 1
