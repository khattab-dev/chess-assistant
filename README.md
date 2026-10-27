# Chess Assistant

A Python desktop tool that watches a chess game on your screen (chess.com,
lichess, or any other site/app), detects your color at the bottom, tracks the live
position, and suggests the best move with an arrow — powered by
Stockfish.

## How it works

- **Automatic color and tracking.** You always play the color at the bottom.
  The tool starts after a stable opening is visible and updates your color
  after new games, board flips and game imports. No color buttons are needed.
- **No FEN needed for a normal start.** A White bot can already have moved.
  The tool matches the starting layout or the first two
  half-moves against legal openings in either board orientation, retaining
  the detected moves and the correct side to move.
- **Automatic new-game detection.** When a tracked game returns to a fresh
  starting layout, the tool confirms it for about a second, clears the old
  history and result, and resumes automatic tracking. It also recognizes a
  new game if the first one or two half-moves already happened. Your color
  is detected again from the bottom side, including when you switch sides. New-game
  notices appear in green in **Activity**, without opening dialogs or
  restoring the control panel. Ordinary legal moves and board flips keep
  the existing game history.
- **Full piece recognition and midgame recovery.** If an opening cannot be
  matched, or legal-move/history recovery fails, a bundled local classifier
  reads all twelve piece types. Three consistent reads are required before
  an inline review appears beside the mini-board. Check the preview, click
  any square to correct its piece, and confirm your bottom color and whose
  turn it is. Orange outlines mark uncertain squares. Choose the remaining
  castling rights and any en passant target, then **Continue** to resume
  tracking and automatic suggestions without a FEN. The live board is checked
  again before committing, so alt-tabbing or making a move during review
  cannot silently adopt a stale image. No notification dialog is opened.
  **Read again** retries; **Cancel** suppresses the same proposed position.
  The classifier runs offline on the CPU through OpenCV; no screenshot is
  uploaded and no GPU or additional runtime is needed. The model comes from
  [fenshot](https://github.com/scoriiu/fenshot) under the MIT license; its
  pinned revision and license are included in `licenses/`.
- **Continuous screen sensing.** Between recoveries the tool senses which squares are occupied
  and by which color (background-color deviation per square, robust across
  themes and piece sets). Every sensed change is matched against the *legal
  moves* of the tracked game with `python-chess`, so captures, castling, en
  passant and promotions are all disambiguated by the rules of chess.
- **Recognizable mini-board pieces.** The preview draws distinct kings,
  queens, rooks, bishops, knights and pawns using the tracked position. Both
  colors have contrasting outlines, and the icons follow captures, castling,
  promotions and board orientation. Icons are rendered locally and cached;
  screen sensing continues to use occupancy and piece color.
- **Arrow on the actual board (Windows).** **Board arrow** enables a
  transparent, always-on-top, click-through arrow over the detected game.
  It follows the capture region and orientation, and appears only while the
  game window is active and the current suggestion matches your turn and
  the sensed position. Alt-tabbing, moving a piece, lost tracking and game
  results hide it automatically. The layer does not take focus and is
  excluded from screen capture so it does not interfere with sensing.
  This uses Windows 10 version 2004 or newer; other systems retain the
  mini-board arrow. The checkbox setting is saved between runs.
- **Custom arrows.** Click **Arrow style** beside **Board arrow** to expand
  the inline controls. Choose a preset or enter a custom `#RRGGBB` hex color,
  adjust **Opacity** from 0–100%, and **Thickness** from 50–200%. A live preview
  works even when no move is currently suggested. The same color, opacity and
  thickness apply to the game-window arrow and mini-board; thickness scales
  with board size. Opacity 0% hides the arrow. **Reset defaults** restores the
  amber arrow at 85% opacity and 100% thickness. Settings save automatically
  and survive restarts. Controls stay in the panel and open no settings dialogs.
- **Follow the game window (Windows).** The detected board is bound to its
  containing window, independently of the **Board arrow** checkbox. After
  a move, the tool updates the capture coordinates using the window's client
  offset. Resize, maximize, DPI changes, browser zoom and page scrolling trigger
  board detection inside that same window. Searches prefer the board's previous
  location and size and exclude the control panel. Arrows hide immediately when
  window geometry changes, then tracking resumes after the new grid and position
  settle. Moving between monitors, including monitors with negative coordinates,
  uses physical screen pixels. Minimizing, covering or moving the board partly
  offscreen pauses tracking quietly and preserves the game. Returning the visible
  board resumes it without a dialog or focus change. If the game window closes,
  use **Detect board again** to attach another window. The control panel itself
  stays where you placed it; only capture and the board arrow follow the game.
- Screen changes must settle for several frames before a move is accepted,
  so a piece animation is not immediately committed as a move.
- A missed frame is recovered by searching two consecutive moves, including
  a fast capture and recapture that leaves only the source squares empty.
- When the board shows something no legal move explains (window moved,
  page scrolled, animation glitch), the tool flags it and keeps retrying.
  If the board disappears from its capture region for several checks, the
  tool keeps the tracked game and looks for it quietly in the background.
  Alt-tabbing does not open a dialog or restore/focus the control panel.
  Tracking resumes when a visible board matches again. You can always
  use the inline piece review, or resync by pasting the complete move list or current FEN.
- **Activity panel instead of notification dialogs.** Warnings appear in
  orange, errors in red, and checkmate, draws and recovery notices in green.
  The panel replaces the move log, suppresses consecutive duplicate entries,
  and retains the latest 200 lines. Promotion choices appear inline too.
- During a position mismatch, suggestions and arrows are cleared. Recent
  takebacks or mistaken branches can be repaired when the observed board
  has a unique match in recent tracked history or its legal next moves.
  If recent history cannot resolve the mismatch, the piece review asks for
  game details instead of guessing whose turn it is.

## Setup

### Windows executable

Double-click `dist/ChessAssistant.exe` to run the standalone Windows app.
Python and a separate Stockfish installation are not required. This is a
64-bit Windows build with the engine, UI and screen-capture dependencies
included. The board overlay requires Windows 10 version 2004 or newer.
Settings are saved in `%LOCALAPPDATA%\ChessAssistant\config.json` so they
survive moving the executable or restarting it. No administrator access
is required. A single-file build takes a few seconds to unpack at startup.

To rebuild on Windows using the project's virtual environment:

Close the executable you intend to replace before rebuilding it.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe setup.py
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean ChessAssistant.spec
```

The result is `dist/ChessAssistant.exe`. The spec embeds `engines/stockfish.exe`,
the recognition model from `app/assets/`, and upstream license notices from
`licenses/`; it does not embed your
personal `config.json`. To verify the built app without opening its control
panel or changing settings:

```powershell
.\dist\ChessAssistant.exe --self-test "$env:TEMP\chess-assistant-check.json"
```

The JSON report covers bundled imports, custom arrow rendering and native styling,
native window geometry, recognition model inference,
screen-capture access, all twelve piece icons, the native overlay, a real
engine suggestion and engine shutdown.
This diagnostic requires an interactive Windows desktop.

### Run from Python

Requires Python 3.9+ on Windows (macOS/Linux also work).

```
cd chess-assistant
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python setup.py                   # downloads Stockfish into engines/
python main.py
```

If `setup.py` can't download Stockfish, grab it manually from
<https://stockfishchess.org/download/> and place the binary in `engines/`
(or set the `CHESS_ENGINE_PATH` environment variable). Stockfish is GPL-licensed;
this tool just launches it as a separate process.

## Usage

1. **Automatic board detection** — keep the board visible on your primary
   monitor when you launch the tool. It finds and calibrates the board
   automatically, checking fresh coordinates on every launch and **New game**.
   If detection fails, an orange warning appears in **Activity** and the
   tool retries quietly. You can click **Select board region** and drag a
   rectangle tightly around the 8x8 board (not the coordinates or side panels),
   or use **Detect board again** when it is visible.
   The control panel moves out of the board's way when screen space allows;
   keep the full board unobstructed for tracking.
2. **Automatic color** — keep your side at the bottom. The tool recognizes
   the starting layout or first two half-moves, detects White or Black at
   the bottom, and starts tracking automatically. A White bot can already
   have played its first move. No color selection or FEN is needed. If the
   game is further along, click **Import game…** and paste the **complete
   move list from the start**, such as `1. e4 e5 2. Nf3 Nc6`, or a PGN. FEN also remains
   supported. For an arbitrary midgame board, wait for the automatic piece
   review and confirm the missing game details. A screenshot cannot prove
   the side to move, historical castling rights, en passant or previous
   repetitions. Move number and draw counter can be entered when known;
   the defaults start a new position history at move 1 with counter 0.
3. Play. Suggestions run **automatically on your turn**, including your
   first move and after FEN resync or board recovery. The panel shows
   the suggested move and an arrow on the mini-board when analysis is ready.
   The suggestion clears after your move or when tracking is paused. Warnings
   and game results appear only in **Activity**. Move tracking runs at several
   frames per second.
4. **Copy FEN** copies the tracked position to the clipboard. If tracking
   cannot recover, use **Import game…** with the full move list, PGN, or
   current FEN. The bottom color is detected from the imported position and
   visible board; uncertain orientation does not trigger a guessed color.
   Suggestions run automatically; there
   is no manual suggestion button or visible FEN field.

Engine strength: **Casual / Balanced / Max** (Stockfish Skill Level and
think time).

## Tests

```
pip install -r requirements-dev.txt
python -m pytest tests/
```

The tracker tests replay games (castling, en passant, promotion, missed
frames, desyncs) through synthetic occupancy diffs; the sensor tests render
synthetic board screenshots in several site themes and check the sensing
round-trip. Detection tests check board boundaries, sizes, themes, unrelated
window borders, and manual fallback/recovery. For a real screenshot, run:

```
python tools/sense_test.py screenshot.png
```

## Troubleshooting

- **Suggestions wrong on hi-DPI screens** — DPI awareness is set at startup;
  make sure you launch via `python main.py` (not through some scaling shim).
- **Tracking desyncs every move** — the board region may be slightly off
  (grid misaligned). Re-select the region; drag exactly along the board edges.
- **Multiple monitors** — initial automatic detection and manual selection cover
  the primary monitor. Once bound, the game window can move to another monitor;
  following and local board searches use virtual-desktop coordinates.
- **Following pauses after switching tabs** — return to the game tab and keep
  the full board visible. The tool follows the containing window and uses the
  visible board image to detect changes inside it; it cannot read hidden tabs.
  If you close the window and open a new one, click **Detect board again**.
- **Tinted piece sets** — calibration learns the light and dark piece colors
  from their foreground, so White's pieces can be darker than the board.
  Pieces that blend almost completely into their square may still confuse
  occupancy sensing; use a clearer theme in that case.
- **Uncertain or incorrect recognized pieces** — inspect orange squares in
  the recovery preview and click a square to change its piece. Busy textures,
  unfamiliar artwork, small boards, overlays and animations can cause mistakes.
  The classifier is designed for clear 2D screen boards; it does not promise
  arbitrary 3D, camera or physical-board recognition. Nothing is committed
  until you confirm the review. Complete moves/PGN remain the best way to
  restore historical draw information.

## Fair play

Running an engine against human opponents violates the fair-play policies of
chess.com, lichess and every other serious chess platform, and can get
accounts banned. Use this tool against bots, in training, or to review your
games — not to cheat real players.

## Project layout

```
main.py              entry point
setup.py             dependency check + Stockfish download
app/capture.py       mss screen capture, snip overlay, automatic board detection
app/squares.py       8x8 grid mapping, occupancy + piece-color sensing
app/tracker.py       diff -> legal move matching, FEN state, desync detection
app/engine.py        Stockfish worker thread: best move + eval
app/gui.py           Tkinter control panel
app/overlay.py       Windows transparent click-through board arrow
app/arrows.py        shared arrow appearance, geometry and RGBA rendering
app/arrow_ui.py      inline color/opacity/thickness controls and live preview
app/window_follow.py read-only game-window binding and physical coordinates
app/pieces.py        cached recognizable mini-board piece icons
app/recognition.py   local ONNX piece classification and state validation
app/recognition_ui.py inline piece corrections and missing game details
app/assets/          bundled classifier model
tools/sense_test.py  debug the sensor on an image file
tests/               tracker and sensor test suites
```

## Roadmap ideas

- Camera-based physical board support
- Coaching mode: threat warnings, blunder alerts, alternate lines
