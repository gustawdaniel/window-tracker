#!/usr/bin/env python3
"""
window_tracker.py — Daemon śledzący aktywne okno, plik, stronę, katalog, czas skupienia i aplikacje.
Obsługuje wieloplatformowo Linux (Hyprland Wayland) oraz macOS (Darwin).

Architektura:
  Provider OS:
    - Linux: Hyprland IPC (socket / `hyprctl activewindow -j`) + evdev (mysz/klawisze/AFK)
    - macOS: Natywny pomocnik Swift / AppleScript (NSWorkspace / AXUIElement) + Quartz / IOHIDSystem (idle)
    -> Wzbogacanie metadanych (plik w IDE, URL/tab w przeglądarce, cwd w terminalu)
    -> Lokalna baza SQLite WAL (~/.local/share/window-tracker/windows.db) [100% offline-first]
    -> Asynchroniczny OTel Forwarder -> OTel Collector -> ClickHouse (signoz_logs.logs_v2)
"""

from __future__ import annotations
from pathlib import Path
import json
import os
import re
import select
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import traceback

try:
    import evdev  # type: ignore
except ImportError:
    evdev = None

# ---------- Konfiguracja ----------
HOSTNAME = os.getenv("HOST_NAME", socket.gethostname().split(".")[0])
OTLP_ENDPOINT = os.getenv("OTLP_LOGS_URL", "http://127.0.0.1:4318/v1/logs")
DATA_DIR = os.path.expanduser(os.getenv("WINDOW_TRACKER_DATA_DIR", "~/.local/share/window-tracker"))
DB_PATH = os.path.join(DATA_DIR, "windows.db")

POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "0.75"))         # co ile sekund sprawdzamy okno
MAX_CHUNK_SEC = float(os.getenv("MAX_CHUNK_SEC", "180.0"))        # maksymalna długość jednego bloku przed zrzutem do bazy
SYNC_INTERVAL = float(os.getenv("SYNC_INTERVAL", "1.0"))          # co ile sekund synchronizujemy z ClickHouse

os.makedirs(DATA_DIR, exist_ok=True)

# ---------- Baza SQLite ----------
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS window_activity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_start INTEGER NOT NULL,
        ts_end INTEGER NOT NULL,
        duration_ms INTEGER NOT NULL,
        host TEXT NOT NULL,
        app_class TEXT NOT NULL,
        window_title TEXT NOT NULL,
        workspace TEXT NOT NULL,
        monitor INTEGER NOT NULL,
        category TEXT DEFAULT '',
        project TEXT DEFAULT '',
        entity TEXT DEFAULT '',
        mouse_moves INTEGER DEFAULT 0,
        mouse_clicks INTEGER DEFAULT 0,
        mouse_scrolls INTEGER DEFAULT 0,
        keys INTEGER DEFAULT 0,
        is_idle INTEGER DEFAULT 0,
        synced INTEGER DEFAULT 0
    );
    """)
    # Migracja kolumn dla istniejącej bazy
    for col, col_type in [
        ("category", "TEXT DEFAULT ''"),
        ("project", "TEXT DEFAULT ''"),
        ("entity", "TEXT DEFAULT ''"),
        ("mouse_moves", "INTEGER DEFAULT 0"),
        ("mouse_clicks", "INTEGER DEFAULT 0"),
        ("mouse_scrolls", "INTEGER DEFAULT 0"),
        ("keys", "INTEGER DEFAULT 0"),
        ("is_idle", "INTEGER DEFAULT 0")
    ]:
        try:
            conn.execute(f"ALTER TABLE window_activity ADD COLUMN {col} {col_type};")
        except sqlite3.OperationalError:
            pass

    conn.execute("CREATE INDEX IF NOT EXISTS idx_window_synced ON window_activity(synced);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_window_ts ON window_activity(ts_start);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_window_app ON window_activity(app_class);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_window_cat ON window_activity(category);")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS git_commits_synced (
        commit_hash TEXT PRIMARY KEY,
        repo TEXT NOT NULL,
        synced_at INTEGER NOT NULL
    );
    """)
    conn.commit()
    return conn

# ---------- Rozpoznawanie ścieżek roboczych CWD i procesów potomnych ----------
def get_runtime_cwd_dir() -> str:
    """Zwraca ścieżkę do pamięci tmpfs zasilanej hookami powłok brain_cwd.*"""
    if os.getenv("XDG_RUNTIME_DIR"):
        return os.path.join(os.environ["XDG_RUNTIME_DIR"], "brain", "cwd")
    elif sys.platform == "darwin":
        tmp = os.getenv("TMPDIR", "/tmp")
        return os.path.join(tmp, f"brain-{os.getuid()}", "cwd")
    else:
        return f"/run/user/{os.getuid()}/brain/cwd"

def get_process_children(pid: int) -> list[int]:
    """Zwraca listę bezpośrednich procesów potomnych dla danego PID."""
    if not pid:
        return []
    if sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["pgrep", "-P", str(pid)],
                stderr=subprocess.DEVNULL,
                timeout=0.2
            )
            return [int(x) for x in out.decode().split() if x.isdigit()]
        except Exception:
            return []
    else:
        children_file = f"/proc/{pid}/task/{pid}/children"
        if os.path.exists(children_file):
            try:
                with open(children_file, "r") as f:
                    return [int(x) for x in f.read().strip().split() if x.isdigit()]
            except Exception:
                pass
        return []

def get_all_descendant_pids(pid: int, depth: int = 3) -> list[int]:
    """Zwraca zagnieżdżone procesy potomne (np. Terminal -> login/shim -> zsh)."""
    pids = []
    current = [pid]
    for _ in range(depth):
        next_gen = []
        for p in current:
            ch = get_process_children(p)
            next_gen.extend(ch)
            pids.extend(ch)
        current = next_gen
        if not current:
            break
    return pids

# ---------- Ekstrakcja precyzyjnych metadanych (plik / strona / katalog / projekt) ----------
LOCKSCREEN_APPS = {"loginwindow", "hyprlock", "swaylock", "gtklock", "i3lock", "screensaverengine", "lockscreen"}

REPO_TO_PROJECT = {
    "fancy-cat": "tactile",
    "libvaxis-tactile": "tactile",
    "tactile": "tactile",
    "ghostty": "tactile",
    "google-api-key-demo-app": "google-api-key",
    "google-api-key-hack": "google-api-key",
    "google-api-key-push": "google-api-key",
    "open_cash_manager": "open-cash-manager",
    "landing_hajduk": "hajduk",
    "hajduk": "hajduk",
    "my-arch-i3-config": "dotfiles",
    "dotfiles": "dotfiles",
    "thesis": "master-thesis",
    "tpc-reco": "master-thesis",
}


def extract_project_from_path_or_text(text: str) -> str:
    if not text:
        return ""
    # 1. Ścieżki ~/pro/<repo>, /Users/<user>/pro/<repo>, /home/<user>/pro/<repo>
    m_pro = re.search(r'(?:~|/Users/[a-zA-Z0-9_.-]+|/home/[a-zA-Z0-9_.-]+|\.\.\.|…)/(?:pro|p)/([a-zA-Z0-9_.-]+)', text)
    if m_pro:
        repo = m_pro.group(1).strip()
        return REPO_TO_PROJECT.get(repo.lower(), repo.lower())

    # 2. Ścieżki ~/stud/<code>, /Users/<user>/stud/<code>, /home/<user>/stud/<code>
    m_stud = re.search(r'(?:~|/Users/[a-zA-Z0-9_.-]+|/home/[a-zA-Z0-9_.-]+|\.\.\.|…)/(?:stud|s)/([a-zA-Z0-9_.-]+)', text)
    if m_stud:
        code = m_stud.group(1).strip().lower()
        if code in ("thesis", "tpc-reco", "tpcreco"):
            return "master-thesis"
        return f"studies/{code}"

    # 3. Ścieżki ~/learn/<topic>, /Users/<user>/learn/<topic>, /home/<user>/learn/<topic>
    m_learn = re.search(r'(?:~|/Users/[a-zA-Z0-9_.-]+|/home/[a-zA-Z0-9_.-]+|\.\.\.|…)/learn/([a-zA-Z0-9_.-]+)', text)
    if m_learn:
        topic = m_learn.group(1).strip().lower()
        return f"learning/{topic}"

    t_low = text.lower()
    if any(k in t_low for k in ("tactile", "fancy-cat", "trackpad-logger", "pressure pilot")):
        return "tactile"
    if any(k in t_low for k in ("openinvoice", "invoice_invoice", "gustawdaniel/invoice", "ksef", "urzadskarbowy", "podatki", "login.mf.gov.pl")):
        return "invoice"
    if "open_cash_manager" in t_low or "open-cash-manager" in t_low:
        return "open-cash-manager"
    if any(k in t_low for k in ("aqmmbs", "many-body")):
        return "studies/aqmmbs"
    if "spns" in t_low or "stochastyczn" in t_low:
        return "studies/spns"
    if any(k in t_low for k in ("symulacje komputerowe", "/csp")):
        return "studies/csp"
    if "fpga" in t_low:
        return "studies/fpga"
    if any(k in t_low for k in ("materiałów kursu", "usos", "dziekanat", "krajewsk")):
        return "studies"
    if any(k in t_low for k in ("tpcreco", "tpc-reco", "warsawtpc", "kalinowski", "magisterk")):
        return "master-thesis"
    if any(k in t_low for k in ("ai-usagebar", "noctalia", "brain os", "gtd-engine", "window-tracker", "key-logger", "signoz", "beszel", "dozzle")):
        return "brain"
    if any(k in t_low for k in ("sketchybar", "yabai", "skhd", "karabiner")):
        return "dotfiles"
    if "hajduk" in t_low:
        return "hajduk"
    if "well-done" in t_low or "welldone" in t_low:
        return "well-done"
    if "net-pocket" in t_low or "netpocket" in t_low:
        return "net-pocket"
    return ""


def resolve_terminal_cwd(pid: int | None, title: str = "") -> str | None:
    runtime_cwd_dir = get_runtime_cwd_dir()
    home = os.path.expanduser("~")
    candidate_pids = ([pid] + get_all_descendant_pids(pid, depth=4)) if pid else []

    # 1. Sprawdź hooki powłoki dla procesów potomnych tego okna
    if candidate_pids and os.path.isdir(runtime_cwd_dir):
        for cpid in reversed(candidate_pids):
            hook_file = os.path.join(runtime_cwd_dir, str(cpid))
            if os.path.isfile(hook_file):
                try:
                    with open(hook_file, "r") as hf:
                        cand = hf.read().strip()
                        if os.path.isdir(cand):
                            return cand
                except Exception:
                    pass

    # 2. Odczyt bezpośrednio z jądra systemu (/proc/<pid>/cwd na Linux, lsof na macOS)
    if candidate_pids:
        if sys.platform != "darwin":
            fallback_cwd = None
            for cpid in reversed(candidate_pids):
                try:
                    cand = os.readlink(f"/proc/{cpid}/cwd")
                    if os.path.isdir(cand):
                        if cand not in (home, "/root", "/"):
                            return cand
                        if not fallback_cwd:
                            fallback_cwd = cand
                except Exception:
                    pass
            if fallback_cwd:
                return fallback_cwd
        else:
            try:
                pid_list = ",".join(str(p) for p in reversed(candidate_pids[:12]))
                out = subprocess.check_output(
                    ["lsof", "-a", "-p", pid_list, "-d", "cwd", "-Fn"],
                    stderr=subprocess.DEVNULL,
                    timeout=0.3
                ).decode("utf-8", errors="ignore")
                paths = [line[1:].strip() for line in out.splitlines() if line.startswith("n/")]
                for p in reversed(paths):
                    if os.path.isdir(p) and p not in (home, "/"):
                        return p
                if paths and os.path.isdir(paths[-1]):
                    if "tmux" not in title.lower():
                        return paths[-1]
            except Exception:
                pass

    # 3. Jeśli aktywny jest tmux, zapytaj aktywny pane tmuxa
    if "tmux" in title.lower():
        try:
            tmux_cwd = subprocess.check_output(
                ["tmux", "display-message", "-p", "#{pane_current_path}"],
                stderr=subprocess.DEVNULL,
                timeout=0.2
            ).decode("utf-8", errors="ignore").strip()
            if tmux_cwd and os.path.isdir(tmux_cwd):
                return tmux_cwd
        except Exception:
            pass

    # 4. Fallback na globalny wskaźnik ostatnio odwiedzonego katalogu w pamięci RAM
    if os.path.isdir(runtime_cwd_dir):
        last_file = os.path.join(runtime_cwd_dir, "last")
        if os.path.isfile(last_file):
            try:
                with open(last_file, "r") as lf:
                    cand = lf.read().strip()
                    if os.path.isdir(cand):
                        return cand
            except Exception:
                pass
    return None


def parse_window_meta(app_class: str, title: str, pid: int = None) -> dict:
    project = ""
    entity = ""
    category = "system"
    
    app_lower = (app_class or "").lower()
    if app_lower in LOCKSCREEN_APPS:
        return {
            "category": "afk",
            "project": "",
            "entity": "AFK"
        }

    title = (title or "").strip()
    if not title:
        title = app_class or "Pulpit"

    norm_title = title.replace(" — ", " - ").replace(" – ", " - ")
    
    # 1. Kategoryzacja nadrzędna
    if any(k in app_lower for k in ['code', 'antigravity', 'electron', 'nvim', 'vim', 'ide', 'datagrip', 'pycharm', 'sublime', 'cursor']):
        category = "code"
    elif any(k in app_lower for k in ['zen', 'chrome', 'firefox', 'brave', 'epiphany', 'browser', 'chromium', 'safari', 'arc']):
        category = "browser"
    elif any(k in app_lower for k in ['term', 'kitty', 'alacritty', 'wezterm', 'foot', 'ghostty', 'iterm', 'trackpad-logger']):
        category = "terminal"
    elif any(k in app_lower for k in ['beeper', 'slack', 'discord', 'telegram', 'signal', 'whatsapp', 'messages']):
        category = "chat"
    elif any(k in app_lower for k in ['obsidian', 'notion', 'writer', 'document', 'pdf', 'preview']):
        category = "docs"
        
    # 2. Parsery specyficzne dla środowisk
    if category == "code":
        parts = norm_title.split(" - ")
        if len(parts) >= 1 and not parts[0].startswith("file://"):
            raw_proj = parts[0].strip()
            project = REPO_TO_PROJECT.get(raw_proj.lower(), raw_proj)

        file_match = re.search(r'file://(/[^ ]+)', title)
        if file_match:
            raw_path = file_match.group(1).split()[0]
            inferred = extract_project_from_path_or_text(raw_path)
            if inferred:
                project = inferred
            if "/pro/" in raw_path:
                entity = raw_path.split("/pro/")[-1]
            else:
                entity = os.path.basename(raw_path)
        elif len(parts) >= 3:
            entity = parts[-1].strip()
        elif len(parts) == 2:
            entity = parts[1].strip()
        else:
            entity = title

    elif "datagrip" in app_lower:
        category = "code"
        project = "databases"
        entity = norm_title.strip()

    elif category == "browser":
        # Czyścimy suffixy przeglądarek
        clean = re.sub(r' — (Zen Browser|Mozilla Firefox|Google Chrome|Chromium|Safari|Arc)$', '', title).strip()
        clean = re.sub(r' - (Zen Browser|Mozilla Firefox|Google Chrome|Chromium|Safari|Arc)$', '', clean).strip()
        
        project = extract_project_from_path_or_text(clean)
        # Wykrywanie platform i projektów (np. GitHub, YouTube, Grafana, SigNoz)
        if "github.com" in clean.lower() or "github" in clean.lower() or re.match(r'^gustawdaniel/[a-zA-Z0-9_.-]+', clean):
            project_m = re.search(r'([a-zA-Z0-9_-]+)/([a-zA-Z0-9_.-]+)', clean)
            if project_m:
                repo_name = project_m.group(2)
                project = REPO_TO_PROJECT.get(repo_name.lower(), repo_name)
            entity = f"GitHub: {clean}"
        elif "grafana" in clean.lower():
            project = project or "brain"
            entity = f"Grafana: {clean}"
        elif "signoz" in clean.lower():
            project = project or "brain"
            entity = f"SigNoz: {clean}"
        elif "clickhouse" in clean.lower():
            project = project or "brain"
            entity = clean
        elif "youtube" in clean.lower():
            project = project or "media"
            entity = clean
        else:
            entity = clean

    elif category == "terminal":
        # Najpierw sprawdź czy tytuł okna już zawiera ścieżkę lub nazwę projektu
        title_proj = extract_project_from_path_or_text(title)
        cwd = resolve_terminal_cwd(pid, title)

        if cwd:
            home = os.path.expanduser("~")
            rel_cwd = cwd.replace(home, "~")
            entity = rel_cwd if title in (app_class, "zsh", "fish", "bash", "~") else f"{rel_cwd} ({title[:50]})"
            cwd_proj = extract_project_from_path_or_text(cwd)
            if cwd_proj:
                project = cwd_proj
            elif title_proj:
                project = title_proj
            elif "Pobrane" in cwd or "Downloads" in cwd:
                project = "downloads"
            elif "/exp" in cwd or cwd.endswith("/exp"):
                project = "experiments"
            elif cwd in (home, "/root"):
                project = title_proj or "home"
            else:
                base = os.path.basename(cwd)
                project = REPO_TO_PROJECT.get(base.lower(), base) if base else "terminal"
        else:
            entity = title
            project = title_proj

    elif category == "chat":
        project = app_class
        entity = title

    else:
        entity = title
        project = extract_project_from_path_or_text(title)

    if not project:
        project = extract_project_from_path_or_text(f"{app_class} {title} {entity}")

    return {
        "category": category,
        "project": project,
        "entity": entity
    }

# ==============================================================================
# PLATFORM PROVIDERS (Linux Hyprland vs macOS Darwin)
# ==============================================================================

class BasePlatformProvider:
    scope_name = "window-tracker"

    def get_active_window(self) -> dict | None:
        raise NotImplementedError

    def is_idle(self) -> bool:
        return False

    def pop_input_stats(self) -> tuple[int, int, int, int]:
        return (0, 0, 0, 0)


# ---------------- Linux / Hyprland Provider ----------------
class LinuxHyprlandProvider(BasePlatformProvider):
    scope_name = "hyprland-window-tracker"

    def __init__(self):
        self.afk_flag_file = os.getenv("AFK_FLAG_FILE", "/tmp/hypr_user_afk")
        self.moves = 0
        self.clicks = 0
        self.scrolls = 0
        self.keys = 0
        self.last_activity_ts = time.time()
        self.idle_timeout_sec = 90.0
        self.lock = threading.Lock()
        self.running = True
        
        if evdev is not None:
            self.thread = threading.Thread(target=self._run_evdev, daemon=True)
            self.thread.start()

    def _get_devices(self):
        devs = []
        if evdev is None:
            return devs
        for p in evdev.list_devices():
            try:
                d = evdev.InputDevice(p)
                name = d.name.lower()
                if any(k in name for k in ('mouse', 'touchpad', 'keyboard', 'kbrd', 'n-key')):
                    devs.append(d)
            except Exception:
                pass
        return devs

    def _run_evdev(self):
        while self.running:
            devs = self._get_devices()
            if not devs:
                time.sleep(3)
                continue
            fds = {d.fd: d for d in devs}
            try:
                while self.running:
                    r, _, _ = select.select(fds.keys(), [], [], 1.0)
                    now = time.time()
                    for fd in r:
                        dev = fds.get(fd)
                        if not dev:
                            continue
                        for ev in dev.read():
                            if ev.type == evdev.ecodes.EV_REL:
                                with self.lock:
                                    self.last_activity_ts = now
                                    if ev.code in (evdev.ecodes.REL_X, evdev.ecodes.REL_Y):
                                        self.moves += 1
                                    elif ev.code in (evdev.ecodes.REL_WHEEL, evdev.ecodes.REL_HWHEEL):
                                        self.scrolls += 1
                            elif ev.type == evdev.ecodes.EV_KEY:
                                with self.lock:
                                    self.last_activity_ts = now
                                    if ev.code in (
                                        evdev.ecodes.BTN_LEFT, evdev.ecodes.BTN_RIGHT,
                                        evdev.ecodes.BTN_MIDDLE, evdev.ecodes.BTN_SIDE,
                                        evdev.ecodes.BTN_EXTRA, evdev.ecodes.BTN_TOUCH
                                    ):
                                        if ev.value == 1:
                                            self.clicks += 1
                                    else:
                                        if ev.value == 1:
                                            self.keys += 1
            except Exception:
                time.sleep(1)

    def is_idle(self) -> bool:
        if os.path.exists(self.afk_flag_file):
            return True
        with self.lock:
            return (time.time() - self.last_activity_ts) > self.idle_timeout_sec

    def pop_input_stats(self) -> tuple[int, int, int, int]:
        with self.lock:
            res = (self.moves, self.clicks, self.scrolls, self.keys)
            self.moves = 0
            self.clicks = 0
            self.scrolls = 0
            self.keys = 0
            return res

    def ensure_hyprland_env(self):
        sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        uid = os.getuid()
        if sig:
            sock = Path(f"/run/user/{uid}/hypr/{sig}/.socket.sock")
            if sock.exists():
                return sig

        runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}")) / "hypr"
        if runtime_dir.is_dir():
            candidates = []
            for p in runtime_dir.iterdir():
                if p.is_dir() and (p / ".socket.sock").exists():
                    try:
                        candidates.append((p.stat().st_mtime, p.name))
                    except Exception:
                        pass
            if candidates:
                candidates.sort(reverse=True)
                chosen_sig = candidates[0][1]
                os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = chosen_sig
                return chosen_sig
        return None

    def get_active_window(self) -> dict | None:
        if self.is_idle():
            return {
                "class": "AFK",
                "title": "Bezczynność (Z dala od klawiatury)",
                "workspace": "0",
                "monitor": 0,
                "category": "afk",
                "project": "",
                "entity": "AFK"
            }

        sig = self.ensure_hyprland_env()
        data = None
        if sig:
            sock_path = f"/run/user/{os.getuid()}/hypr/{sig}/.socket.sock"
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(0.3)
                    s.connect(sock_path)
                    s.sendall(b"j/activewindow")
                    chunks = []
                    while True:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    raw = b"".join(chunks).decode("utf-8", errors="ignore")
                    if raw.strip().startswith("{"):
                        data = json.loads(raw)
            except Exception:
                data = None

        if data is None:
            try:
                raw = subprocess.check_output(
                    ["hyprctl", "activewindow", "-j"],
                    stderr=subprocess.DEVNULL,
                    timeout=1.5,
                    env=os.environ
                )
                data = json.loads(raw.decode("utf-8", errors="ignore"))
            except Exception:
                return None

        if not data or not isinstance(data, dict):
            return {
                "class": "Desktop",
                "title": "Pulpit",
                "workspace": "0",
                "monitor": 0,
                "category": "system",
                "project": "",
                "entity": "Pulpit"
            }
        
        app_class = data.get("class") or "Desktop"
        title = data.get("title") or "Workspace"
        workspace = str(data.get("workspace", {}).get("name", "1"))
        monitor = int(data.get("monitor", 0))
        pid = data.get("pid")

        meta = parse_window_meta(app_class, title, pid)

        return {
            "class": app_class,
            "title": title,
            "workspace": workspace,
            "monitor": monitor,
            "category": meta["category"],
            "project": meta["project"],
            "entity": meta["entity"]
        }


# ---------------- macOS / Darwin Provider ----------------
class DarwinPlatformProvider(BasePlatformProvider):
    scope_name = "macos-window-tracker"

    def __init__(self):
        self.helper_bin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "mac_window_helper")
        self.idle_timeout_sec = 90.0
        self.last_idle_sec = 0.0
        self.active_polls = 0

    def _get_macos_idle_sec(self) -> float:
        try:
            out = subprocess.check_output(
                ["ioreg", "-c", "IOHIDSystem"],
                stderr=subprocess.DEVNULL,
                timeout=0.5
            ).decode("utf-8", errors="ignore")
            for line in out.splitlines():
                if "HIDIdleTime" in line:
                    val = line.split("=")[-1].strip()
                    return int(val) / 1_000_000_000.0
        except Exception:
            pass
        return 0.0

    def is_idle(self) -> bool:
        return self.last_idle_sec > self.idle_timeout_sec

    def pop_input_stats(self) -> tuple[int, int, int, int]:
        # Na macOS akumulujemy próbki aktywne z całego interwału, aby uniknąć fałszywego is_idle przy zmianie okna
        polls = self.active_polls
        self.active_polls = 0
        if polls > 0 or self.last_idle_sec < 5.0:
            return (max(1, polls), 0, 0, 0)
        return (0, 0, 0, 0)

    def _query_swift_helper(self) -> dict | None:
        if os.path.isfile(self.helper_bin) and os.access(self.helper_bin, os.X_OK):
            try:
                raw = subprocess.check_output([self.helper_bin], timeout=0.8)
                data = json.loads(raw.decode("utf-8", errors="ignore"))
                return data
            except Exception:
                pass
        return None

    def _query_yabai(self) -> dict | None:
        try:
            raw = subprocess.check_output(
                ["yabai", "-m", "query", "--windows", "--window"],
                stderr=subprocess.DEVNULL,
                timeout=0.5
            )
            data = json.loads(raw.decode("utf-8", errors="ignore"))
            if isinstance(data, dict) and data.get("app"):
                return {
                    "app": data.get("app", ""),
                    "title": data.get("title", ""),
                    "pid": int(data.get("pid") or 0),
                    "workspace": str(data.get("space", "mac")),
                    "monitor": int(data.get("display", 0)),
                }
        except Exception:
            pass
        return None

    def _query_applescript_fallback(self) -> dict:
        idle_sec = self._get_macos_idle_sec()
        yb = self._query_yabai()
        if yb:
            yb["idle_sec"] = idle_sec
            return yb
        return {
            "app": "Desktop",
            "title": "Pulpit",
            "pid": 0,
            "workspace": "mac",
            "monitor": 0,
            "idle_sec": idle_sec
        }

    def get_active_window(self) -> dict | None:
        data = self._query_swift_helper()
        if not data:
            data = self._query_applescript_fallback()
        elif not data.get("title"):
            yb = self._query_yabai()
            if yb and yb.get("title"):
                data["title"] = yb["title"]
                if yb.get("space") or yb.get("workspace"):
                    data["workspace"] = yb.get("workspace", "mac")
                if yb.get("monitor") is not None:
                    data["monitor"] = yb["monitor"]

        self.last_idle_sec = float(data.get("idle_sec", 0.0))
        app_class = data.get("app") or "Desktop"

        if self.is_idle() or app_class.lower() in LOCKSCREEN_APPS:
            return {
                "class": "AFK",
                "title": "Bezczynność (Z dala od klawiatury)",
                "workspace": "0",
                "monitor": 0,
                "category": "afk",
                "project": "",
                "entity": "AFK"
            }

        if self.last_idle_sec < 5.0:
            self.active_polls += 1

        title = data.get("title") or app_class
        workspace = str(data.get("workspace", "mac"))
        monitor = int(data.get("monitor", 0))
        pid = data.get("pid")

        meta = parse_window_meta(app_class, title, pid)

        return {
            "class": app_class,
            "title": title,
            "workspace": workspace,
            "monitor": monitor,
            "category": meta["category"],
            "project": meta["project"],
            "entity": meta["entity"]
        }


# ---------- Wybór platformy na starcie ----------
def create_provider() -> BasePlatformProvider:
    if sys.platform == "darwin":
        return DarwinPlatformProvider()
    else:
        return LinuxHyprlandProvider()

provider = create_provider()

# ---------- Synchronizacja commitów Git do ClickHouse ----------
def sync_git_commits(conn):
    """Skanuje ~/pro, ~/stud, ~/learn pod kątem nowych commitów z ostatnich 14 dni i wysyła je do ClickHouse."""
    cur = conn.cursor()
    cur.execute("SELECT commit_hash FROM git_commits_synced")
    known = {r[0] for r in cur.fetchall()}

    home = Path.home()
    new_commits = []
    for area in ("pro", "stud", "learn"):
        base = home / area
        if not base.is_dir():
            continue
        for repo in sorted(base.iterdir()):
            if not (repo.is_dir() and (repo / ".git").exists()):
                continue
            try:
                res = subprocess.run(
                    [
                        "git", "log", "--all", "-n", "200",
                        "-i", "--author=daniel\\|gustaw", "--no-merges",
                        "--since=14 days ago", "--pretty=format:%H\t%h\t%ct\t%s"
                    ],
                    cwd=str(repo), capture_output=True, text=True, timeout=5
                )
                for line in res.stdout.splitlines():
                    parts = line.strip().split("\t", 3)
                    if len(parts) < 4:
                        continue
                    full_h, short_h, ts_str, subj = parts
                    if full_h in known or not ts_str.isdigit():
                        continue
                    known.add(full_h)
                    project_slug = extract_project_from_path_or_text(f"/{area}/{repo.name}") or repo.name
                    new_commits.append((full_h, short_h, int(ts_str), subj, repo.name, area, project_slug))
            except Exception:
                continue

    if not new_commits:
        return

    # Wysyłamy w paczkach po 100
    for i in range(0, len(new_commits), 100):
        batch = new_commits[i:i + 100]
        log_records = []
        for full_h, short_h, ts_sec, subj, repo_name, area, project_slug in batch:
            time_nano = str(ts_sec * 1_000_000_000)
            log_records.append({
                "timeUnixNano": time_nano,
                "observedTimeUnixNano": str(int(time.time() * 1e9)),
                "severityNumber": 9,
                "severityText": "INFO",
                "body": {"stringValue": f"{short_h} {subj}"},
                "attributes": [
                    {"key": "log_type", "value": {"stringValue": "git_commit"}},
                    {"key": "repo", "value": {"stringValue": repo_name}},
                    {"key": "area", "value": {"stringValue": area}},
                    {"key": "project", "value": {"stringValue": project_slug}},
                    {"key": "commit.hash", "value": {"stringValue": short_h}},
                    {"key": "commit.full_hash", "value": {"stringValue": full_h}},
                    {"key": "commit.subject", "value": {"stringValue": subj}}
                ]
            })
        payload = {
            "resourceLogs": [{
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "git-tracker"}},
                        {"key": "host.name", "value": {"stringValue": HOSTNAME}}
                    ]
                },
                "scopeLogs": [{
                    "scope": {"name": "git-tracker"},
                    "logRecords": log_records
                }]
            }]
        }
        req = urllib.request.Request(
            OTLP_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if resp.status in (200, 202):
                    now_sec = int(time.time())
                    cur.executemany(
                        "INSERT OR IGNORE INTO git_commits_synced (commit_hash, repo, synced_at) VALUES (?, ?, ?)",
                        [(c[0], c[4], now_sec) for c in batch]
                    )
                    conn.commit()
        except Exception as e:
            sys.stderr.write(f"[WARN] Git commit OTel sync error: {e}\n")
            break


# ---------- Wątek synchronizacji do ClickHouse (OTel HTTP) ----------
running = True
sync_trigger = threading.Event()

def sync_worker():
    global running
    conn = get_db()
    last_git_sync = 0.0
    
    while running:
        try:
            now_ts = time.time()
            if now_ts - last_git_sync >= 180.0:
                sync_git_commits(conn)
                last_git_sync = now_ts

            cur = conn.cursor()
            cur.execute("""
                SELECT id, ts_start, ts_end, duration_ms, host, app_class, window_title, workspace, monitor, category, project, entity, mouse_moves, mouse_clicks, mouse_scrolls, keys, is_idle
                FROM window_activity
                WHERE synced = 0
                ORDER BY id ASC
                LIMIT 50
            """)
            rows = cur.fetchall()
            
            if rows:
                log_records = []
                ids = []
                for r in rows:
                    r_id, ts_start, ts_end, duration_ms, host, app_class, window_title, workspace, monitor, category, project, entity, m_moves, m_clicks, m_scrolls, m_keys, is_idle = r
                    ids.append(r_id)
                    
                    time_nano = str(int(ts_end * 1_000_000) + (1_000_000 if duration_ms == 0 else 0))
                    if duration_ms == 0:
                        desc_body = f"[FOCUS_START] {app_class}: {entity or window_title}"
                        ev_type = "focus_start"
                    else:
                        desc_body = f"[{category.upper()}] {app_class}: {entity or window_title}"
                        ev_type = "interval"

                    if project:
                        desc_body += f" ({project})"

                    log_records.append({
                        "timeUnixNano": time_nano,
                        "observedTimeUnixNano": str(int(time.time() * 1e9)),
                        "severityNumber": 9,
                        "severityText": "INFO",
                        "body": {"stringValue": desc_body},
                        "attributes": [
                            {"key": "app.class", "value": {"stringValue": app_class}},
                            {"key": "window.title", "value": {"stringValue": window_title}},
                            {"key": "duration.ms", "value": {"intValue": duration_ms}},
                            {"key": "workspace", "value": {"stringValue": workspace}},
                            {"key": "monitor", "value": {"intValue": monitor}},
                            {"key": "category", "value": {"stringValue": category or "system"}},
                            {"key": "project", "value": {"stringValue": project or ""}},
                            {"key": "entity", "value": {"stringValue": entity or ""}},
                            {"key": "event.type", "value": {"stringValue": ev_type}},
                            {"key": "mouse.moves", "value": {"intValue": int(m_moves or 0)}},
                            {"key": "mouse.clicks", "value": {"intValue": int(m_clicks or 0)}},
                            {"key": "mouse.scrolls", "value": {"intValue": int(m_scrolls or 0)}},
                            {"key": "keys.count", "value": {"intValue": int(m_keys or 0)}},
                            {"key": "mouse.active", "value": {"boolValue": bool((m_moves or 0) + (m_clicks or 0) + (m_scrolls or 0) > 0)}},
                            {"key": "user.active", "value": {"boolValue": bool((m_moves or 0) + (m_clicks or 0) + (m_scrolls or 0) + (m_keys or 0) > 0 and app_class != 'AFK')}},
                            {"key": "is.idle", "value": {"boolValue": bool(is_idle or app_class == 'AFK')}}
                        ]
                    })
                
                payload = {
                    "resourceLogs": [{
                        "resource": {
                            "attributes": [
                                {"key": "service.name", "value": {"stringValue": "window-tracker"}},
                                {"key": "host.name", "value": {"stringValue": HOSTNAME}}
                            ]
                        },
                        "scopeLogs": [{
                            "scope": {"name": provider.scope_name},
                            "logRecords": log_records
                        }]
                    }]
                }
                
                req = urllib.request.Request(
                    OTLP_ENDPOINT,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                
                try:
                    with urllib.request.urlopen(req, timeout=4.0) as resp:
                        if resp.status in (200, 202):
                            placeholders = ",".join("?" for _ in ids)
                            cur.execute(f"UPDATE window_activity SET synced = 1 WHERE id IN ({placeholders})", ids)
                            conn.commit()
                except Exception as net_err:
                    # Brak sieci / offline — dane są bezpieczne w SQLite WAL
                    sys.stderr.write(f"[WARN] OTel forwarder offline/network error: {net_err}\n")

        except Exception as e:
            sys.stderr.write(f"[ERROR] Sync worker critical error: {e}\n")
            traceback.print_exc(file=sys.stderr)
        
        sync_trigger.wait(timeout=SYNC_INTERVAL)
        sync_trigger.clear()
    
    conn.close()

# ---------- Pętla główna śledzenia okna ----------
def main():
    global running
    
    def handle_signal(sig, frame):
        global running
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    
    conn = get_db()
    
    sync_thread = threading.Thread(target=sync_worker, daemon=True)
    sync_thread.start()
    
    print(f"[*] Window & Activity Tracker started on host '{HOSTNAME}' ({provider.scope_name}).")
    print(f"[*] SQLite Database: {DB_PATH}")
    print(f"[*] OTLP ClickHouse Endpoint: {OTLP_ENDPOINT}")
    
    current_win = None
    interval_start = time.time()
    
    def save_focus_start(win, start_sec):
        if not win:
            return
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO window_activity (ts_start, ts_end, duration_ms, host, app_class, window_title, workspace, monitor, category, project, entity, mouse_moves, mouse_clicks, mouse_scrolls, keys, is_idle, synced)
            VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?, 0)
        """, (
            int(start_sec * 1000),
            int(start_sec * 1000),
            HOSTNAME,
            win["class"],
            win["title"],
            win["workspace"],
            win["monitor"],
            win.get("category", "system"),
            win.get("project", ""),
            win.get("entity", ""),
            1 if win["class"] == "AFK" else 0
        ))
        conn.commit()

    def save_interval(win, start_sec, end_sec):
        if not win:
            return
        duration_ms = int((end_sec - start_sec) * 1000)
        if duration_ms < 200: # pomijamy mrugnięcia < 0.2s
            return
            
        m_moves, m_clicks, m_scrolls, m_keys = provider.pop_input_stats()
        is_idle = 1 if (win["class"] == "AFK" or (m_moves == 0 and m_clicks == 0 and m_scrolls == 0 and m_keys == 0)) else 0

        cur = conn.cursor()
        cur.execute("""
            INSERT INTO window_activity (ts_start, ts_end, duration_ms, host, app_class, window_title, workspace, monitor, category, project, entity, mouse_moves, mouse_clicks, mouse_scrolls, keys, is_idle, synced)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            int(start_sec * 1000),
            int(end_sec * 1000),
            duration_ms,
            HOSTNAME,
            win["class"],
            win["title"],
            win["workspace"],
            win["monitor"],
            win.get("category", "system"),
            win.get("project", ""),
            win.get("entity", ""),
            m_moves,
            m_clicks,
            m_scrolls,
            m_keys,
            is_idle
        ))
        conn.commit()

    while running:
        try:
            now = time.time()
            active = provider.get_active_window()
            
            if active is None:
                time.sleep(POLL_INTERVAL)
                continue

            if current_win is None and active is not None:
                current_win = active
                interval_start = now
                save_focus_start(current_win, interval_start)
                sync_trigger.set()
            elif current_win is not None:
                is_same = (
                    active is not None and
                    active["class"] == current_win["class"] and
                    active["title"] == current_win["title"] and
                    active["workspace"] == current_win["workspace"] and
                    active["monitor"] == current_win["monitor"] and
                    active.get("entity") == current_win.get("entity")
                )
                
                if not is_same:
                    save_interval(current_win, interval_start, now)
                    current_win = active
                    interval_start = now
                    if current_win is not None:
                        save_focus_start(current_win, interval_start)
                    sync_trigger.set()
                elif (now - interval_start) >= MAX_CHUNK_SEC:
                    save_interval(current_win, interval_start, now)
                    interval_start = now
                    sync_trigger.set()
                    
            time.sleep(POLL_INTERVAL)
            
        except Exception as e:
            sys.stderr.write(f"[ERROR] Main tracker loop error: {e}\n")
            time.sleep(0.5)
            
    if current_win:
        save_interval(current_win, interval_start, time.time())
    
    conn.close()
    print("[*] Window Tracker stopped cleanly.")

if __name__ == "__main__":
    main()
