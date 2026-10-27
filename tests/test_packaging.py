"""Frozen resources must work independently of the exe's working directory."""
import json
from pathlib import Path
import sys
from unittest.mock import Mock

from app import paths
from app.arrows import ArrowStyle
from app.gui import ChessAssistantApp


def frozen(monkeypatch, tmp_path):
    bundle = tmp_path / "extracted"
    bundle.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "installed" / "ChessAssistant.exe"))
    monkeypatch.delenv("CHESS_ENGINE_PATH", raising=False)
    return bundle


def put_engine(directory):
    engine = directory / "engines" / "stockfish.exe"
    engine.parent.mkdir(parents=True)
    engine.write_bytes(b"engine")
    return str(engine)


def test_frozen_engine_is_found_without_source_or_path(monkeypatch, tmp_path):
    bundle = frozen(monkeypatch, tmp_path)
    expected = put_engine(bundle)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "")
    assert paths.find_stockfish() == expected


def test_external_engine_next_to_exe_can_override_bundled_engine(monkeypatch, tmp_path):
    bundle = frozen(monkeypatch, tmp_path)
    put_engine(bundle)
    external = put_engine(Path(sys.executable).parent)
    assert paths.find_stockfish() == external


def test_explicit_engine_path_takes_priority(monkeypatch, tmp_path):
    bundle = frozen(monkeypatch, tmp_path)
    put_engine(bundle)
    custom = tmp_path / "custom.exe"
    custom.write_bytes(b"engine")
    monkeypatch.setenv("CHESS_ENGINE_PATH", str(custom))
    assert paths.find_stockfish() == str(custom)


def test_invalid_override_falls_back_to_bundle(monkeypatch, tmp_path):
    bundle = frozen(monkeypatch, tmp_path)
    expected = put_engine(bundle)
    monkeypatch.setenv("CHESS_ENGINE_PATH", str(tmp_path / "missing.exe"))
    assert paths.find_stockfish() == expected


def test_frozen_settings_survive_bundle_reextraction(monkeypatch, tmp_path):
    bundle = frozen(monkeypatch, tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user"))
    config = paths.config_path()
    assert config == tmp_path / "user" / "ChessAssistant" / "config.json"
    assert not config.is_relative_to(bundle)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "second-extraction"))
    assert paths.config_path() == config


def test_frozen_settings_have_a_home_fallback(monkeypatch, tmp_path):
    frozen(monkeypatch, tmp_path)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    assert paths.config_path() == tmp_path / "AppData" / "Local" / "ChessAssistant" / "config.json"


def test_source_settings_stay_in_project(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert paths.config_path() == Path(paths.__file__).resolve().parent.parent / "config.json"


def test_settings_create_the_user_directory_and_reload(monkeypatch, tmp_path):
    config = tmp_path / "new-user" / "ChessAssistant" / "config.json"
    monkeypatch.setattr("app.gui.CONFIG_PATH", str(config))
    app = ChessAssistantApp.__new__(ChessAssistantApp)
    app.level, app.board_arrow = Mock(), Mock()
    app.level.get.return_value = "Casual"
    app.board_arrow.get.return_value = False
    app.sensor = None
    app._save_config()
    assert json.loads(config.read_text()) == {"strength": "Casual", "board_arrow": False,
                                              "arrow_style": ArrowStyle().as_dict()}
    app._load_config()
    app.level.set.assert_called_once_with("Casual")
    app.board_arrow.set.assert_called_once_with(False)
