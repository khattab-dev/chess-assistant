"""One-time setup: verify Python dependencies and download Stockfish.

Usage:  python setup.py
"""
import io
import json
import os
import platform
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
ENGINES = os.path.join(ROOT, "engines")
DEPS = ["chess", "mss", "cv2", "numpy", "PIL"]

# Preferred asset-name substrings per OS, most compatible first.
ASSET_PREFS = {
    "windows": ["windows-x86-64-universal.zip", "windows-x86-64-avx2.zip",
                "windows-x86-64-sse41-popcnt.zip", "windows-x86-64.zip"],
    "darwin": ["macos-universal.tar", "macos-x86-64-avx2.tar",
               "macos-x86-64.tar", "macos-arm64.tar"],
    "linux": ["linux-x86-64-universal.tar", "ubuntu-x86-64.tar",
              "linux-x86-64.tar"],
}


def check_python() -> bool:
    if sys.version_info < (3, 9):
        print(f"[x] Python 3.9+ required (found {sys.version.split()[0]})")
        return False
    print(f"[ok] Python {sys.version.split()[0]}")
    return True


def check_deps() -> bool:
    missing = []
    for mod in DEPS:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        print("[x] Missing packages: " + ", ".join(missing))
        print("    Run:  pip install -r requirements.txt")
        return False
    print("[ok] All Python packages installed")
    return True


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "chess-assistant-setup"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "chess-assistant-setup"})
    out = io.BytesIO()
    with urllib.request.urlopen(req, timeout=180) as r:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
    return out.getvalue()


def pick_asset(assets: list) -> dict | None:
    sysname = platform.system().lower()  # windows / darwin / linux
    for pref in ASSET_PREFS.get(sysname, []):
        for a in assets:
            name = a.get("name", "").lower()
            if name.endswith((".zip", ".tar", ".tar.gz")) and pref in name:
                return a
    return None


def install_stockfish() -> bool:
    exe = os.path.join(ENGINES, "stockfish.exe" if os.name == "nt" else "stockfish")
    if os.path.isfile(exe):
        print(f"[ok] Stockfish already present: {exe}")
        return True
    print("Looking up the latest Stockfish release...")
    try:
        rel = fetch_json("https://api.github.com/repos/official-stockfish/Stockfish/releases/latest")
        asset = pick_asset(rel.get("assets", []))
        if not asset:
            raise RuntimeError("no matching binary for this OS in the latest release")
        print(f"Downloading {asset['name']}...")
        data = download(asset["browser_download_url"])
        os.makedirs(ENGINES, exist_ok=True)
        tmp = os.path.join(ENGINES, "_dl")
        if asset["name"].endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                z.extractall(tmp)
        else:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as t:
                t.extractall(tmp)
        found = None
        for base, _dirs, files in os.walk(tmp):
            for f in files:
                if f.lower().startswith("stockfish") and f.endswith(".exe") == (os.name == "nt"):
                    found = os.path.join(base, f)
                    break
        if not found:
            raise RuntimeError("stockfish binary not found inside the archive")
        shutil.move(found, exe)
        shutil.rmtree(tmp, ignore_errors=True)
        if os.name != "nt":
            os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
        print(f"[ok] Installed Stockfish -> {exe}")
        return True
    except Exception as e:
        print(f"[x] Could not download Stockfish ({e})")
        print("    Download it manually from https://stockfishchess.org/download/")
        print(f"    and place the binary at: {exe}")
        return False


def main() -> None:
    ok = check_python()
    ok = check_deps() and ok
    ok = install_stockfish() and ok
    if ok:
        print("\nSetup complete. Start the app with:  python main.py")
    else:
        print("\nFix the issues above, then re-run:  python setup.py")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
