"""Tkinter control panel: automatic tracking, bottom-side color, suggestions."""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import simpledialog, ttk

import chess

from .capture import (BoardRegion, _looks_like_board, grab_region,
                      select_region_interactive, try_auto_detect)
from .engine import EngineWorker, LEVELS
from .overlay import BoardArrowOverlay
from .pieces import PieceRenderer
from .paths import config_path
from .recognition import PieceRecognizer, StableBoardReads
from .recognition_ui import RecognitionReview
from .squares import BoardSensor, chess_to_sensor_index, sensor_to_chess_index
from .tracker import PositionTracker, _game_over_text, board_to_sensor_states
from .window_follow import WindowsWindowAPI

CONFIG_PATH = str(config_path())

CELL = 34  # mini-board cell size, px
LIGHT_SQ, DARK_SQ = "#eeeed2", "#769656"
LOG_COLORS = {"info": "#444444", "warning": "#b85c00",
              "error": "#c62828", "success": "#187638"}
LOG_LIMIT = 200


class ChessAssistantApp:
    def __init__(self, root: tk.Tk, engine_path: str | None):
        self.root = root
        root.title("Chess Assistant")
        root.resizable(False, False)
        self.engine_path = engine_path
        self.engine_worker: EngineWorker | None = None

        self.sensor: BoardSensor | None = None
        self.tracker: PositionTracker | None = None
        self.user_color: chess.Color | None = None

        self._events: queue.Queue = queue.Queue()
        self._running = True
        self._paused = False          # set while a modal (promotion/resync) is open
        self._suggested_fen = ""
        self._last_states = None
        self._gameover_shown = False
        self._desync_count = 0
        self._tracking_uncertain = False
        self._board_misses = 0
        self._next_board_check = 0.0
        self._board_recovery_pending = False
        self._board_hidden = False
        self._next_recovery_scan = time.monotonic() + 2.0
        self._panel_region: BoardRegion | None = None
        self._last_log_entry = None
        self._promotion_tracker = None
        self.overlay = None
        self._overlay_move = None
        self._recognizer = PieceRecognizer()
        self._recognition_gate = StableBoardReads()
        self._recognition_source = None
        self._recognition_pending = None
        self._recognition_review = None
        self._next_recognition = 0.0
        self._window_api = None
        self._window_binding = None
        self._follow_moving = self._follow_waiting = False
        self._follow_geometry = self._follow_pending = None
        self._window_wait_reason = None

        self._build_ui()
        self._load_config()
        self._init_overlay()
        try:
            self._window_api = WindowsWindowAPI()
        except OSError:
            pass  # Screen-based recovery remains available on other platforms.
        if self.engine_path:
            self.engine_worker = EngineWorker(self.engine_path)
        else:
            self._set_status("Stockfish not found — run 'python setup.py'. "
                             "Tracking works, but no suggestions.", "red")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._poll)
        self.root.after(200, self._on_auto_detect)
        threading.Thread(target=self._capture_loop, daemon=True).start()

    # -- UI ------------------------------------------------------------------
    def _build_ui(self) -> None:
        pad = dict(padx=8, pady=4)
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        ttk.Button(top, text="Select board region",
                   command=self._on_select_region).pack(side="left")
        ttk.Button(top, text="Detect board again",
                   command=self._on_auto_detect).pack(side="left", padx=6)

        game = ttk.Frame(self.root)
        game.pack(fill="x", **pad)
        ttk.Button(game, text="Import game…",
                   command=self._resync_fen).pack(side="left")
        ttk.Button(game, text="New game",
                   command=self._on_new_game).pack(side="left", padx=6)
        ttk.Label(game, text="Engine:").pack(side="left", padx=(14, 2))
        self.level = tk.StringVar(value="Balanced")
        ttk.Combobox(game, textvariable=self.level, values=list(LEVELS),
                     state="readonly", width=9).pack(side="left")
        self.board_arrow = tk.BooleanVar(value=True)
        self.overlay_check = ttk.Checkbutton(game, text="Board arrow", variable=self.board_arrow,
                                             command=self._on_overlay_toggle)
        self.overlay_check.pack(side="left", padx=(12, 0))

        mid = ttk.Frame(self.root)
        mid.pack(fill="x", **pad)
        self._board_body = mid
        self.board_canvas = tk.Canvas(mid, width=CELL * 8, height=CELL * 8,
                                      highlightthickness=0, bg="#222")
        self.board_canvas.grid(row=0, column=0, padx=(0, 12))
        self.piece_renderer = PieceRenderer(self.root, CELL)
        self.suggest_lbl = tk.Label(mid, text="", font=("Segoe UI", 20, "bold"))
        self.suggest_lbl.grid(row=0, column=1, sticky="w")

        activity = ttk.LabelFrame(self.root, text="Activity")
        activity.pack(fill="x", **pad)
        log_body = ttk.Frame(activity)
        log_body.pack(fill="both", expand=True, padx=4, pady=4)
        self.activity_txt = tk.Text(log_body, height=5, width=72, wrap="word",
                                    state="disabled", font=("Consolas", 10))
        self.activity_txt.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_body, command=self.activity_txt.yview)
        scrollbar.pack(side="right", fill="y")
        self.activity_txt.config(yscrollcommand=scrollbar.set)
        for level, color in LOG_COLORS.items():
            self.activity_txt.tag_configure(level, foreground=color)
        self.promotion_row = ttk.Frame(activity)

        fenf = ttk.Frame(self.root)
        fenf.pack(fill="x", **pad)
        self.fen_var = tk.StringVar()
        ttk.Button(fenf, text="Copy FEN", width=10,
                   command=self._copy_fen).pack(side="left")
        self._draw_board(None, True, None)

    # -- config ---------------------------------------------------------------
    def _load_config(self) -> None:
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            self.level.set(cfg.get("strength", "Balanced"))
            self.board_arrow.set(cfg.get("board_arrow", True))
            # Screen coordinates can become stale between runs. Always detect
            # the current board after the window has initialized.
        except Exception:
            pass

    def _save_config(self) -> None:
        cfg = {"strength": self.level.get(), "board_arrow": self.board_arrow.get()}
        if self.sensor:
            cfg["region"] = self.sensor.region.as_dict()
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    # -- board region -----------------------------------------------------------
    def _apply_region(self, region: BoardRegion, save: bool = True,
                      preserve_game: bool = False) -> None:
        frame = grab_region(region)
        if not _looks_like_board(frame):
            raise ValueError("That area does not show a complete 8x8 board. "
                             "Keep the board visible and select its edges tightly.")
        sensor = BoardSensor(region)
        sensor.calibrate(frame)
        self.sensor = sensor
        self._close_recognition()
        self._recognition_source = None
        if not preserve_game:
            self.tracker = None
        if self.tracker is None:
            self.tracker = PositionTracker(follow_bottom=True)
            self.user_color = None
        self._suggested_fen = ""
        self._last_states = None
        self._desync_count = 0
        self._board_misses = 0
        self._board_hidden = False
        self._next_board_check = 0.0
        self._place_panel_away_from_board(region)
        self._hide_overlay()
        self._window_binding = None
        self._follow_moving = self._follow_waiting = False
        self._follow_geometry = self._follow_pending = None
        self._window_wait_reason = None
        if getattr(self, "_window_api", None):
            try:
                self.root.update_idletasks()
                self._window_binding = self._window_api.bind(region)
            except Exception as error:
                self._set_status(f"Could not bind the game window: {error}", "orange")
        if self.overlay:
            if self._window_binding:
                self.overlay.bind_region(region, owner=self._window_binding.hwnd)
            else:
                self.overlay.bind_region(region)
        if save:
            self._save_config()
        if self.tracker.board is not None:
            self.tracker.reset_sensing()
            self._draw_board(self.tracker.board.copy(stack=False), self.tracker.white_bottom, None)
            self._set_status("Board found. Checking the tracked position…"
                             if self._tracking_uncertain else
                             "Board found. Tracking resumed.",
                             "orange" if self._tracking_uncertain else "green")
            return
        self.tracker.reset_sensing()
        self._tracking_uncertain = False
        self._set_status("Board calibrated. Detecting the opening or reading the pieces "
                         "for midgame recovery.", "green")
        self._draw_board(None, True, None)

    def _place_panel_away_from_board(self, region: BoardRegion) -> None:
        """Keep the restored control panel from obscuring the capture area."""
        self.root.update_idletasks()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        width = max(self.root.winfo_width(), self.root.winfo_reqwidth()) + 16
        height = max(self.root.winfo_height(), self.root.winfo_reqheight()) + 40
        right, bottom = region.left + region.width, region.top + region.height
        if (x + width <= region.left or x >= right
                or y + height <= region.top or y >= bottom):
            return
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        fit_x, fit_y = max(0, min(x, sw - width)), max(0, min(y, sh - height))
        candidates = [(right + 12, fit_y), (region.left - width - 12, fit_y),
                      (fit_x, bottom + 12), (fit_x, region.top - height - 12)]
        candidates = [(cx, cy) for cx, cy in candidates
                      if 0 <= cx <= sw - width and 0 <= cy <= sh - height]
        if candidates:
            cx, cy = min(candidates, key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)
            self.root.geometry(f"+{cx}+{cy}")

    def _on_select_region(self) -> bool:
        self._close_recognition()
        self._hide_overlay(clear=False)
        was_paused = self._paused
        self._paused = True
        error = None
        try:
            self.root.withdraw()
            self.root.update_idletasks()
            time.sleep(0.1)  # let the desktop compositor finish hiding the window
            region = select_region_interactive(self.root)
            if region:
                self._apply_region(region, preserve_game=True)
                return True
        except Exception as e:
            error = e
        finally:
            self.root.deiconify()
            self._paused = was_paused
        if error:
            self._set_status(f"Calibration failed: {error}", "red")
        return False

    def _on_auto_detect(self) -> bool:
        """Explicit detection may hide the panel; failures are logged inline."""
        self._close_recognition()
        self._hide_overlay(clear=False)
        was_paused = self._paused
        self._paused = True
        self._set_status("Looking for a board…")
        try:
            # Hide our own mini-board so it cannot be mistaken for the game or
            # cover the real board during detection and calibration.
            self.root.withdraw()
            try:
                self.root.update_idletasks()
                time.sleep(0.1)
                region = try_auto_detect()
                if region:
                    self._apply_region(region, preserve_game=True)
                    return True
            except Exception as e:
                self._set_status(f"Board detection failed: {e}", "red")
            finally:
                self.root.deiconify()

            self._suggested_fen = ""
            self._board_hidden = True
            self._tracking_uncertain = True
            self._next_recovery_scan = time.monotonic() + 2.0
            self._clear_advice()
            self._set_status("Board not visible. Waiting for it to return. "
                             "You can also use 'Select board region'.", "orange")
            return False
        finally:
            self._paused = was_paused

    # -- game control -------------------------------------------------------------
    def _on_new_game(self) -> None:
        self._close_recognition()
        self._hide_overlay()
        self._clear_promotion()
        self._paused = False
        self.tracker = None
        self.user_color = None
        self._suggested_fen = ""
        self._gameover_shown = False
        self._tracking_uncertain = False
        self._desync_count = 0
        self.fen_var.set("")
        self.suggest_lbl.config(text="")
        self._draw_board(None, True, None)
        self._set_status("New game started.", "green")
        self._on_auto_detect()

    def _resync_fen(self) -> None:
        self._close_recognition()
        if not self.sensor:
            self._set_status("Wait for a visible board before importing a game.", "orange")
            return
        resume_paused = self._paused
        self._paused = True
        self._hide_overlay(clear=False)
        try:
            position = simpledialog.askstring(
                "Import game", "Paste the full move list from the start "
                "(for example 1. e4 e5 2. Nf3), or the current FEN:\n"
                "Your color will be detected from the bottom side.",
                parent=self.root)
            if not position:
                return
            if not self.tracker:
                self.tracker = PositionTracker(follow_bottom=True)
            self.tracker.follow_bottom = True
            err = self.tracker.set_position(position, self._last_states)
            if err:
                self._set_status(err, "red")
                return
            self.user_color = self.tracker.user_color
            self._suggested_fen = ""
            self._gameover_shown = False
            self._tracking_uncertain = False
            self._desync_count = 0
            self._clear_promotion()
            resume_paused = False
            self._post(("position", list(self.tracker.occ), self.tracker.white_bottom,
                        self.tracker.fen, list(self.tracker.san_history)))
            if self.user_color is None:
                self._set_status("Game imported. Waiting to determine the bottom color.", "orange")
            else:
                side = "White" if self.user_color == chess.WHITE else "Black"
                self._set_status(f"Game imported. Your color is {side} (bottom).", "green")
        finally:
            self._paused = resume_paused

    # -- capture loop -----------------------------------------------------------
    def _window_wait(self, sensor, reason) -> None:
        self._follow_waiting = True
        self._tracking_uncertain = self._board_hidden = True
        self._last_states = None
        if reason != getattr(self, "_window_wait_reason", None):
            self._window_wait_reason = reason
            self._post(("window_waiting", sensor, reason))

    def _follow_window(self, sensor, now) -> bool:
        """Capture-thread geometry checks also run during inline review."""
        binding = getattr(self, "_window_binding", None)
        if not binding or not sensor:
            return False
        if self._board_recovery_pending:
            return True
        current = binding.snapshot()
        if current is None:
            self._window_wait(sensor, "Game window closed. Use 'Detect board again' to attach another window.")
            return True
        if not current.visible or current.minimized or binding.search_area(current) is None:
            self._window_wait(sensor, "Game window hidden or minimized. Waiting for it to return.")
            return True
        predicted = binding.predicted(current)
        if predicted and not binding.unobstructed(predicted, current):
            self._window_wait(sensor, "Game board covered or outside the screen. Waiting for it to return.")
            return True
        if getattr(self, "_follow_waiting", False):
            self._follow_waiting = False
            self._window_wait_reason = None
            self._next_board_check = 0.0
        if current.geometry == binding.anchor.geometry:
            self._follow_geometry = None
            return False
        if not getattr(self, "_follow_moving", False):
            self._follow_moving = True
            self._tracking_uncertain = True
            self._last_states = None
            self._next_recovery_scan = min(self._next_recovery_scan, now)
            self._next_board_check = 0.0
            self._post(("window_moving", sensor))
        if current.geometry != getattr(self, "_follow_geometry", None):
            self._follow_geometry, self._follow_since = current.geometry, now
            return True
        if now - self._follow_since < 0.2:
            return True
        if predicted:
            try:
                if _looks_like_board(grab_region(predicted)):
                    self._queue_window_region(sensor, binding, predicted, current)
                    return True
            except Exception:
                pass
        self._scan_for_board(sensor, now)
        return True

    def _queue_window_region(self, sensor, binding, region, current) -> None:
        if sensor is self.sensor and binding is self._window_binding:
            self._board_recovery_pending = True
            self._follow_pending = (sensor, binding)
            self._post(("window_region", sensor, binding, region, current))

    def _ev_window_waiting(self, sensor, reason) -> None:
        if sensor is self.sensor and getattr(self, "_follow_waiting", False):
            self._clear_advice()
            self._set_status(reason, "orange")

    def _ev_window_moving(self, sensor) -> None:
        if sensor is self.sensor and getattr(self, "_follow_moving", False):
            self._clear_advice()
            self._set_status("Game window changed. Realigning the board…", "orange")

    def _ev_window_region(self, previous_sensor, binding, region, detected) -> None:
        if getattr(self, "_follow_pending", None) != (previous_sensor, binding):
            return
        try:
            if previous_sensor is not self.sensor or binding is not self._window_binding:
                return
            current = binding.snapshot()
            if (not current or current.geometry != detected.geometry
                    or not binding.unobstructed(region, current)):
                return  # The window moved again while the event was queued.
            frame = grab_region(region)
            if not _looks_like_board(frame):
                return
            sensor = BoardSensor(region)
            sensor.calibrate(frame)
            binding.reanchor(region, current)
            self.sensor = sensor
            self._follow_moving = self._follow_waiting = False
            self._follow_geometry = self._window_wait_reason = None
            self._board_hidden = False
            self._board_misses = 0
            self._next_board_check = 0.0
            self._recognition_pending = self._recognition_source = None
            self._last_states = None
            self._suggested_fen = ""
            self._tracking_uncertain = True
            if self.tracker:
                self.tracker.reset_sensing()
            self._clear_advice()
            if self.overlay:
                self.overlay.bind_region(region, owner=binding.hwnd)
            self._set_status("Board realigned. Checking the position…", "green")
        except Exception as error:
            self._set_status(f"Could not realign the board: {error}", "orange")
        finally:
            self._follow_pending = None
            self._board_recovery_pending = False

    def _scan_for_board(self, sensor, now: float) -> None:
        """Search without hiding, restoring or focusing the control panel."""
        if now < self._next_recovery_scan:
            return
        binding = getattr(self, "_window_binding", None)
        self._next_recovery_scan = now + (0.5 if binding else 2.0)
        try:
            if binding:
                current = binding.snapshot()
                search = binding.search_area(current) if current else None
                if search is None:
                    return
                region = try_auto_detect(exclude_region=self._panel_region, search_region=search,
                                         preferred_region=binding.search_hint(current))
                if region and binding.unobstructed(region, current):
                    self._queue_window_region(sensor, binding, region, current)
                return  # A bound game is never replaced by another window.
            region = try_auto_detect(exclude_region=self._panel_region)
        except Exception as e:
            self._set_status_event(f"Board search failed: {e}", "red")
            return
        promotion_waiting = self.tracker is not None and self._promotion_tracker is self.tracker
        if region and sensor is self.sensor and (not self._paused or promotion_waiting):
            self._board_recovery_pending = True
            self._post(("board_found", sensor, region))

    def _capture_loop(self) -> None:
        while self._running:
            time.sleep(0.1)
            sensor = self.sensor
            tr = self.tracker
            now = time.monotonic()
            if self._follow_window(sensor, now):
                continue
            promotion_waiting = tr is not None and self._promotion_tracker is tr
            if (self._paused and not promotion_waiting) or self._board_recovery_pending:
                if (self._paused and getattr(self, "_window_binding", None)
                        and not self._board_recovery_pending and now >= self._next_board_check):
                    self._next_board_check = now + 0.25
                    try:
                        if not _looks_like_board(grab_region(sensor.region)):
                            self._follow_moving = True
                            self._last_states = None
                            self._scan_for_board(sensor, now)
                    except Exception:
                        pass
                continue
            if not sensor:
                self._scan_for_board(None, now)
                continue
            check_board = now >= self._next_board_check
            if not tr and not check_board:
                continue
            try:
                frame = grab_region(sensor.region)
            except Exception:
                frame = None
            if check_board:
                binding = getattr(self, "_window_binding", None)
                self._next_board_check = now + (0.25 if binding else 1.0)
                if frame is None or not _looks_like_board(frame):
                    self._board_misses += 1
                    if binding:
                        self._follow_moving = True
                        self._tracking_uncertain = True
                        self._last_states = None
                        self._scan_for_board(sensor, now)
                    if self._board_misses == 3:
                        self._board_hidden = True
                        self._tracking_uncertain = True
                        self._board_recovery_pending = True
                        self._post(("board_lost", sensor))
                    elif self._board_misses > 3:
                        self._scan_for_board(sensor, now)
                    continue
                self._board_misses = 0
                if binding:
                    self._follow_moving = False
                if self._board_hidden:
                    self._board_hidden = False
                    if tr:
                        tr.reset_sensing()
                    self._post(("board_visible", sensor))
            if (frame is None or self._board_misses or not tr
                    or sensor is not self.sensor or (self._paused and not promotion_waiting)):
                continue
            try:
                states, _ = sensor.sense(frame)
            except Exception as e:
                self._set_status_event(f"Board sensing failed: {e}", "red")
                continue
            if (sensor is not self.sensor or tr is not self.tracker
                    or (self._paused and not promotion_waiting)):
                continue
            self._last_states = states
            res = tr.update_stable(states)
            fen = tr.fen
            if res.status == "started":
                self._desync_count = 0
                self._tracking_uncertain = False
                self._post(("tracking_started", tr))
                self._post(("position", list(tr.occ), tr.white_bottom,
                            fen, list(tr.san_history)))

            elif res.status in ("uninitialized", "orientation_unknown"):
                if not self._tracking_uncertain:
                    self._tracking_uncertain = True
                    self._post(("import_needed", tr, res.detail))
            elif res.status == "newgame":
                self._desync_count = 0
                self._tracking_uncertain = False
                self._post(("new_game", tr))
                self._post(("position", list(tr.occ), tr.white_bottom,
                            fen, list(tr.san_history)))
            elif res.status == "newgame_pending":
                if not self._tracking_uncertain:
                    self._tracking_uncertain = True
                    self._post(("new_game_pending", tr))
            elif res.status in ("moved", "gameover", "resynced"):
                was_uncertain = self._tracking_uncertain
                self._desync_count = 0
                self._tracking_uncertain = False
                if res.status == "resynced":
                    self._suggested_fen = ""
                who = "You" if res.mover == tr.user_color else "Opponent"
                msg = res.detail if res.status == "resynced" else f"{who} played {res.san}."
                if res.status == "gameover":
                    self._post(("gameover", res.detail))
                else:
                    if tr.board.turn == tr.user_color:
                        msg += " Your move — thinking…"
                    self._set_status_event(
                        msg, "green" if was_uncertain or res.status == "resynced" else None)
                self._post(("position", list(tr.occ), tr.white_bottom,
                            fen, list(tr.san_history)))
            elif res.status == "ambiguous":
                if not promotion_waiting:
                    self._paused = True
                    self._post(("ambiguous", res.candidates))
            elif res.status == "desync":
                self._desync_count += 1
                if not self._tracking_uncertain:
                    self._tracking_uncertain = True
                    self._post(("tracking_lost", tr))
            elif res.status == "none" and not res.detail and self._tracking_uncertain:
                self._tracking_uncertain = False
                self._desync_count = 0
                self._suggested_fen = ""
                self._set_status_event("Board matches again. Tracking resumed.", "green")
                self._post(("position", list(tr.occ), tr.white_bottom,
                            fen, list(tr.san_history)))

            if res.status in ("uninitialized", "orientation_unknown", "desync"):
                self._consider_recognition(sensor, tr, frame, now)
            elif res.status != "none" or not res.detail:
                self._recognition_source = None

    # -- full-piece recognition and inline recovery ----------------------------
    def _consider_recognition(self, sensor, tr, frame, now) -> None:
        if (self._paused or getattr(self, "_recognition_pending", None)
                or now < getattr(self, "_next_recognition", 0.0)):
            return
        self._next_recognition = now + 0.4
        source = (sensor, tr)
        if source != getattr(self, "_recognition_source", None):
            self._recognition_source = source
            self._recognition_gate = StableBoardReads()
            self._recognition_failure = None
        if not hasattr(self, "_recognizer"):
            self._recognizer = PieceRecognizer()
        try:
            read = self._recognizer.read(frame)
            if not read.plausible:
                detail = "Could not recognize both kings. Try a clear 2D piece set, or use 'Import game…'."
                if detail != self._recognition_failure:
                    self._set_status_event(detail, "orange")
                    self._recognition_failure = detail
                self._recognition_gate = StableBoardReads()
                return
            if (sensor is not self.sensor or tr is not self.tracker or self._paused
                    or not self._recognition_gate.offer(read, now)):
                return
            self._recognition_pending = source
            self._post(("recognition", sensor, tr, read))
        except Exception as error:
            detail = f"Piece recognition unavailable: {error} Use 'Import game…' to continue."
            if detail != self._recognition_failure:
                self._set_status_event(detail, "red")
                self._recognition_failure = detail

    def _ev_recognition(self, sensor, tr, read) -> None:
        if getattr(self, "_recognition_pending", None) != (sensor, tr):
            return
        self._recognition_pending = None
        if (sensor is not self.sensor or tr is not self.tracker or self._paused
                or not self._tracking_uncertain or self._board_hidden):
            return
        self._paused = True
        self._clear_advice()
        self.suggest_lbl.grid_remove()
        hint = tr.white_bottom if tr.orientation_known else None
        self._recognition_review = RecognitionReview(
            self._board_body, self.board_canvas, read, read.bottom_hint(hint),
            self._draw_board, self._confirm_recognition,
            self._retry_recognition, self._cancel_recognition, self._set_status)
        self._recognition_review.grid(row=0, column=1, sticky="nw")
        count = len(read.uncertain)
        detail = f" {count} uncertain squares are outlined in orange." if count else ""
        self._set_status("Pieces recognized. Review the board, bottom color, turn and game details; "
                         "then choose Continue." + detail, "orange")

    def _close_recognition(self, suppress: bool = False) -> None:
        review = getattr(self, "_recognition_review", None)
        if review:
            if suppress:
                self._recognition_gate.suppressed = review.read.symbols
            review.destroy()
            self._recognition_review = None
            self.suggest_lbl.grid()
            self._paused = False
        self._recognition_pending = None

    def _retry_recognition(self) -> None:
        self._close_recognition()
        self._recognition_gate = StableBoardReads()
        self._next_recognition = 0.0
        self._clear_advice()
        self._set_status("Reading the visible board again…", "orange")

    def _cancel_recognition(self) -> None:
        self._close_recognition(suppress=True)
        self._clear_advice()
        self._set_status("Recovery cancelled. Use 'Detect board again' to retry, "
                         "or import the game. Tracking remains paused until the board matches.", "orange")

    def _confirm_recognition(self, board, white_bottom) -> None:
        review = getattr(self, "_recognition_review", None)
        if not review or not self.sensor:
            return
        try:
            binding = getattr(self, "_window_binding", None)
            if binding and not binding.aligned(self.sensor.region):
                raise ValueError("Wait for the game window to realign before confirming the position.")
            # The game may have changed while the review was open, or the user
            # may have alt-tabbed. Never commit an old screenshot as live state.
            frame = grab_region(self.sensor.region)
            if not _looks_like_board(frame):
                raise ValueError("The board is hidden. Return to the game and choose Read again.")
            fresh = self._recognizer.read(frame)
            if fresh.symbols != review.read.symbols:
                raise ValueError("The board changed during review. Choose Read again before continuing.")
            self.sensor.calibrate(frame)
            states, _ = self.sensor.sense(frame)
            if states != board_to_sensor_states(board, white_bottom):
                raise ValueError("Some occupied squares or piece colors do not match the screen. "
                                 "Check your edits or try a clearer piece theme.")
            tracker = PositionTracker(follow_bottom=True)
            tracker.set_recognized(board, white_bottom)
        except Exception as error:
            self._set_status(str(error), "red")
            return
        self._close_recognition()
        self.tracker = tracker
        self.user_color = tracker.user_color
        self._last_states = states
        self._tracking_uncertain = False
        self._gameover_shown = False
        self._desync_count = 0
        self._suggested_fen = ""
        self._ev_position(list(tracker.occ), white_bottom, tracker.fen, [])
        side = "White" if white_bottom else "Black"
        self._set_status(f"Position confirmed. Your color is {side} (bottom). Tracking resumed.", "green")
        if board.is_game_over():
            self._ev_gameover(_game_over_text(board))

    def _maybe_suggest(self, tr: PositionTracker, fen: str) -> None:
        """Called on the UI thread so reading engine strength is safe in Tk."""
        b = tr.board
        if (self.sensor and tr is self.tracker and self.engine_worker
                and not getattr(self, "_follow_moving", False)
                and not getattr(self, "_follow_waiting", False)
                and not getattr(self, "_board_misses", 0)
                and not self._paused and not self._board_recovery_pending
                and not self._tracking_uncertain and not self._board_hidden
                and b is not None and fen and fen != self._suggested_fen
                and tr.user_color is not None and b.turn == tr.user_color
                and not b.is_game_over()):
            self._suggested_fen = fen
            self.suggest_lbl.config(text="")
            self.engine_worker.submit(fen, self.level.get())

    # -- click-through board arrow ---------------------------------------------
    def _init_overlay(self) -> None:
        try:
            self.overlay = BoardArrowOverlay()
        except Exception as e:
            self.board_arrow.set(False)
            self.overlay_check.state(["disabled"])
            self._set_status(f"Board arrow unavailable: {e}. Mini-board arrows still work.", "orange")

    def _hide_overlay(self, clear: bool = True) -> None:
        if clear:
            self._overlay_move = None
        if self.overlay:
            self.overlay.hide()

    def _on_overlay_toggle(self) -> None:
        self._refresh_overlay()
        self._save_config()

    def _refresh_overlay(self) -> None:
        if not self.overlay:
            return
        tr = self.tracker
        binding = getattr(self, "_window_binding", None)
        if (binding and (not self.sensor or not binding.aligned(self.sensor.region))
                or getattr(self, "_follow_moving", False) or getattr(self, "_follow_waiting", False)):
            self.overlay.hide()
            return
        move = self._overlay_move
        if (not self.board_arrow.get() or not self.sensor or not tr or tr.board is None
                or not move or self._paused or self._tracking_uncertain or self._board_hidden
                or self._board_recovery_pending or self._board_misses
                or move[0] != tr.fen or tr.board.turn != tr.user_color
                or tr.board.is_game_over()
                or self._last_states != board_to_sensor_states(tr.board, tr.white_bottom)):
            self.overlay.hide()
            return
        try:
            self.overlay.show(self.sensor.region, tr.white_bottom, move[1])
        except Exception as e:
            self.overlay.close()
            self.overlay = None
            self.board_arrow.set(False)
            self.overlay_check.state(["disabled"])
            self._set_status(f"Board arrow stopped: {e}", "red")

    # -- event plumbing -----------------------------------------------------------
    def _post(self, ev) -> None:
        self._events.put(ev)

    def _set_status_event(self, text, color=None) -> None:
        self._post(("status", text, color))

    def _poll(self) -> None:
        if not self._running:
            return
        # Only the UI thread reads Tk geometry; capture uses this snapshot to
        # exclude our mini-board from passive background detection.
        self._panel_region = (
            BoardRegion(self.root.winfo_rootx(), self.root.winfo_rooty(),
                        self.root.winfo_width(), self.root.winfo_height())
            if self.root.state() == "normal" else None)
        sources = [self._events]
        if self.engine_worker:
            sources.append(self.engine_worker.results)
        for source in sources:
            try:
                while True:
                    ev = source.get_nowait()
                    getattr(self, "_ev_" + ev[0])(*ev[1:])
            except queue.Empty:
                pass
        if self.tracker:
            # Covers automatic startup, opponent moves, new games,
            # imports, missed frames and board recovery without another click.
            self._maybe_suggest(self.tracker, self.tracker.fen)
        self._refresh_overlay()
        self.root.after(80, self._poll)

    def _ev_status(self, text, color=None) -> None:
        self._set_status(text, color)

    def _ev_tracking_started(self, tr) -> None:
        if tr is not self.tracker:
            return
        self.user_color = tr.user_color
        self._gameover_shown = False
        self._suggested_fen = ""
        side = "White" if tr.user_color == chess.WHITE else "Black"
        self._set_status(f"Your color detected: {side} (bottom). Tracking started.", "green")

    def _ev_import_needed(self, tr, detail) -> None:
        if tr is self.tracker and self._tracking_uncertain:
            self._clear_advice()
            self._set_status("Opening not recognized. Reading the pieces for midgame recovery. "
                             "You can also use 'Import game…'.", "orange")

    def _ev_new_game_pending(self, tr) -> None:
        if tr is self.tracker and self._tracking_uncertain:
            self._clear_advice()

    def _ev_new_game(self, tr) -> None:
        if tr is not self.tracker:
            return
        self._clear_promotion()
        self._paused = False
        self._gameover_shown = False
        self._desync_count = 0
        self._tracking_uncertain = False
        self._suggested_fen = ""
        self.user_color = tr.user_color
        self._clear_advice()
        if tr.user_color is None:
            detail = "Detecting the color at the bottom."
        else:
            side = "White" if tr.user_color == chess.WHITE else "Black"
            detail = f"Your color is {side} (bottom)."
        self._last_log_entry = None
        self._set_status("New game detected. " + detail, "green")

    def _ev_tracking_lost(self, tr) -> None:
        if tr is not self.tracker or not self._tracking_uncertain:
            return
        self._clear_advice()
        self._set_status("Tracking paused — checking history and reading the pieces for recovery.", "orange")

    def _ev_board_lost(self, sensor) -> None:
        try:
            if sensor is self.sensor:
                self._board_hidden = True
                self._tracking_uncertain = True
                self._clear_advice()
                self._set_status("Board not visible — tracking paused. Return to "
                                 "the game to resume; detection will retry quietly.", "orange")
        finally:
            self._board_recovery_pending = False

    def _ev_board_visible(self, sensor) -> None:
        if sensor is not self.sensor or self._board_hidden:
            return
        if self.tracker and self.tracker.board is not None:
            self._set_status("Board visible again. Checking the position…", "orange")
        else:
            self._tracking_uncertain = False
            self._set_status("Board visible again. Detecting the opening and your color.", "green")

    def _ev_board_found(self, previous_sensor, region) -> None:
        try:
            if previous_sensor is not self.sensor:
                return
            self._apply_region(region, preserve_game=True)
            if not self.tracker or self.tracker.board is None:
                self._tracking_uncertain = False
        except Exception as e:
            self._set_status(f"Could not recalibrate the board: {e}", "red")
        finally:
            self._board_recovery_pending = False

    def _ev_position(self, _occ, white_bottom, fen, _sans) -> None:
        if getattr(self, "_recognition_review", None):
            return
        tr = self.tracker
        if not tr or fen != tr.fen:
            return
        if self._promotion_tracker is tr:
            self._clear_promotion()
            self._paused = False
        if self.user_color != tr.user_color:
            self.user_color = tr.user_color
            self._suggested_fen = ""
            if self.user_color is not None:
                side = "White" if self.user_color == chess.WHITE else "Black"
                self._set_status(f"Your color is now {side} (bottom).", "green")
        self.fen_var.set(fen)
        self._hide_overlay()
        self._draw_board(chess.Board(fen), white_bottom, None)
        self.suggest_lbl.config(text="")

    def _ev_eval(self, data) -> None:
        tr = self.tracker
        if (not self.sensor or not tr or tr.board is None
                or getattr(self, "_follow_moving", False) or getattr(self, "_follow_waiting", False)
                or self._tracking_uncertain or self._board_hidden
                or data["fen"] != tr.fen or tr.board.turn != tr.user_color
                or tr.board.is_game_over()):
            return  # stale suggestion — the position moved on
        self.suggest_lbl.config(text=f"Play  {data['san']}")
        self._draw_board(chess.Board(data["fen"]), tr.white_bottom, data["uci"])
        self._overlay_move = (data["fen"], data["uci"])

    def _ev_ambiguous(self, candidates) -> None:
        self._ask_promotion(candidates)

    def _ev_gameover(self, text) -> None:
        if not self._gameover_shown:
            self._gameover_shown = True
            self._clear_advice()
            self._set_status(text, "green")

    def _ev_engine_ready(self, _none) -> None:
        self._log_event("Engine ready.", "success")

    def _ev_engine_error(self, msg) -> None:
        self._clear_advice()
        self._set_status("Engine: " + msg, "red")

    # -- inline promotion choice -------------------------------------------------
    def _ask_promotion(self, candidates) -> None:
        self._clear_promotion()
        self._paused = True
        self._promotion_tracker = self.tracker
        self.promotion_row.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Label(self.promotion_row, text="Promote to:").pack(side="left", padx=4)
        for san, mv in candidates:
            ttk.Button(self.promotion_row, text=san,
                       command=lambda mv=mv: self._choose_promotion(mv)).pack(side="left", padx=4)
        self._set_status("Promotion detected. Choose the promoted piece below.", "orange")

    def _clear_promotion(self) -> None:
        for child in self.promotion_row.winfo_children():
            child.destroy()
        self.promotion_row.pack_forget()
        self._promotion_tracker = None

    def _choose_promotion(self, move: chess.Move) -> None:
        tr = self.tracker
        if tr is not self._promotion_tracker or tr is None or move not in tr.board.legal_moves:
            return
        result = tr.apply_choice(move)
        self._clear_promotion()
        self._paused = False
        self._post(("position", list(tr.occ), tr.white_bottom, tr.fen, list(tr.san_history)))
        if result.status == "gameover":
            self._post(("gameover", result.detail))
        else:
            self._set_status("Promotion confirmed. Tracking resumed.", "green")

    # -- drawing -------------------------------------------------------------------
    def _draw_board(self, board: chess.Board | None, white_bottom, arrow_uci) -> None:
        cv = self.board_canvas
        cv.delete("all")
        pieces = board.piece_map() if board is not None else {}
        for i in range(64):
            r, c = divmod(i, 8)
            square = sensor_to_chess_index(i, white_bottom)
            x0, y0 = c * CELL, r * CELL
            light = (r + c) % 2 == 0
            cv.create_rectangle(x0, y0, x0 + CELL, y0 + CELL,
                                fill=LIGHT_SQ if light else DARK_SQ, outline="")
            piece = pieces.get(square)
            if piece is not None:
                self.piece_renderer.draw(cv, piece, square, x0 + CELL / 2, y0 + CELL / 2)
        if arrow_uci:
            frm = chess.parse_square(arrow_uci[:2])
            to = chess.parse_square(arrow_uci[2:4])
            r1, c1 = divmod(chess_to_sensor_index(frm, white_bottom), 8)
            r2, c2 = divmod(chess_to_sensor_index(to, white_bottom), 8)
            cv.create_line(c1 * CELL + CELL / 2, r1 * CELL + CELL / 2,
                           c2 * CELL + CELL / 2, r2 * CELL + CELL / 2,
                           fill="#f5b800", width=6, arrow=tk.LAST,
                           capstyle="round", arrowshape=(14, 16, 6), tags=("suggestion",))

    # -- misc ------------------------------------------------------------------------
    def _set_status(self, text, color=None) -> None:
        level = {"orange": "warning", "red": "error", "green": "success"}.get(color, "info")
        if level != "info":
            self._log_event(text, level)

    def _log_event(self, text: str, level: str) -> None:
        entry = (text, level)
        if entry == self._last_log_entry:
            return
        self._last_log_entry = entry
        self.activity_txt.config(state="normal")
        self.activity_txt.insert("end", f"{level.upper()}: {text}\n", level)
        count = int(self.activity_txt.index("end-1c").split(".")[0]) - 1
        if count > LOG_LIMIT:
            self.activity_txt.delete("1.0", f"{count - LOG_LIMIT + 1}.0")
        self.activity_txt.see("end")
        self.activity_txt.config(state="disabled")

    def _clear_advice(self) -> None:
        self._hide_overlay()
        self.suggest_lbl.config(text="")
        review = getattr(self, "_recognition_review", None)
        if review:
            review._redraw()
            return
        if self.tracker:
            board = self.tracker.board
            self._draw_board(board.copy(stack=False) if board is not None else None,
                             self.tracker.white_bottom, None)

    def _copy_fen(self) -> None:
        if getattr(self, "_recognition_review", None):
            self._set_status("Confirm the recognized position before copying its FEN.", "orange")
            return
        fen = self.fen_var.get()
        if fen:
            self.root.clipboard_clear()
            self.root.clipboard_append(fen)

    def _on_close(self) -> None:
        self._running = False
        self._close_recognition()
        if self.overlay:
            self.overlay.close()
            self.overlay = None
        if self.engine_worker:
            self.engine_worker.stop()
        self.root.destroy()
