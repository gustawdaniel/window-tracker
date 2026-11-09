# ⏱️ Cross-Platform Window Focus & Time Tracker

System rejestracji czasu skupienia i aktywności w oknach dla środowisk **Linux (Hyprland / Wayland)** oraz **macOS (Darwin)**, zaprojektowany w architekturze **offline-first** z asynchroniczną replikacją OTel do **ClickHouse** oraz dedykowanym dashboardem analitycznym w **SvelteKit**.

---

## 🏛️ Architektura Katalogów

Wzorem aplikacji `key-logger`, projekt został podzielony na niezależne moduły backendu (serwis systemowy) oraz frontendu:

```
apps/window-tracker/
├── service/                     # Daemon zbierający telemetrię
│   ├── window_tracker.py        # Demon (Hyprland IPC / macOS Cocoa -> SQLite WAL -> OTel ClickHouse)
│   ├── mac_window_helper.swift  # Natywny pomocnik Swift dla macOS (NSWorkspace + AXUIElement)
│   ├── window-tracker.service   # Jednostka systemd --user (Linux)
│   ├── com.daniel.window-tracker.plist # Agent launchd (macOS)
│   └── Makefile                 # Autodetekcja OS (install, start, logs, status)
│
├── shell/                       # Hooki powłok śledzące CWD w pamięci RAM
│   ├── brain_cwd.fish
│   ├── brain_cwd.zsh
│   └── brain_cwd.bash
│
├── svelte-dashboard/            # Interfejs analityczny (SvelteKit 5)
│   ├── src/
│   │   ├── lib/backend.js       # Adapter danych (SQLite lokalnie vs ClickHouse remote)
│   │   └── routes/
│   │       ├── +page.svelte     # Kokpit: hero active window, statystyki, 24h wykres, feed
│   │       └── api/             # API: /stats, /history, /stream (SSE), /timeline
│   ├── Dockerfile               # Konteneryzacja produkcyjna (adapter-node)
│   ├── .env.example             # Konfiguracja backendu (sqlite | clickhouse)
│   └── package.json
│
└── README.md
```

---

## 1. ⚙️ Daemon Śledzący (`service/`)

Demon `window_tracker.py` wykonuje ciągłe próbkowanie aktywnego okna i buforuje zdarzenia w lokalnej bazie SQLite w trybie **WAL**:
* **Linux (rog):** Odpytuje socket IPC Hyprlanda (`hyprctl activewindow -j`), a aktywność wejścia zlicza przez `evdev`.
* **macOS (mac):** Odpytuje natywne API Cocoa/Accessibility przez skompilowany pomocnik Swift (`mac_window_helper`), a bezczynność bada przez `Quartz / IOHIDSystem`.
* Ścieżka lokalnej bazy: `~/.local/share/window-tracker/windows.db`
* Tryb pracy: **100% offline-first**. Gdy serwer telemetrii jest niedostępny, wpisy czekają z flagą `synced = 0`.
* Forwarder: Asynchroniczny wątek w pętli co 1s dosyła zaległe bloki do OTLP HTTP (`http://localhost:4318/v1/logs` -> ClickHouse `signoz_logs.logs_v2`).

### Zarządzanie usługą:
`Makefile` automatycznie rozpoznaje system operacyjny (`Darwin` vs `Linux`):
```bash
cd apps/window-tracker/service

# Instalacja i start (launchd na macOS, systemd --user na Linuksie):
make install

# Podgląd logów na żywo:
make logs

# Status usługi:
make status
```

---

## 2. 📊 Dashboard SvelteKit (`svelte-dashboard/`)

Aplikacja analityczna pozwala na bieżąco monitorować skupienie, czas spędzony w poszczególnych programach i historię przełączeń okien.

### Funkcjonalności:
1. **Live Active Window (Hero Card):** Czas rzeczywisty w aktualnym oknie z sekundnikiem i identyfikacją aplikacji/obszaru.
2. **Kluczowe Wskaźniki (KPIs):** Łączny czas skupienia, liczba przełączeń okien, różnorodność narzędzi i Deep Work Ratio.
3. **Podział wg Aplikacji:** Wizualne paski postępu i udział procentowy dla każdego programu (Neovim, Zen Browser, Terminal itp.).
4. **Wykres Dobowy (24h Diurnal):** Histogram intensywności skupienia w każdej godzinie doby.
5. **Strumień Okien (SSE Live):** Dynamicznie aktualizowana lista zdarzeń bez przeładowywania strony.
6. **Bloki Skupienia (Deep Work Timeline):** Automatyczna detekcja nieprzerwanych sesji pracy.

### Tryby działania (Dual Backend):
* **`WINDOW_TRACKER_BACKEND=sqlite` (Domyślny lokalnie):**
  Odczytuje bezpośrednio plik `~/.local/share/window-tracker/windows.db`.
* **`WINDOW_TRACKER_BACKEND=clickhouse` (Produkcja):**
  Odpytuje endpoint HTTP ClickHouse (`http://localhost:8123` lub `http://signoz-telemetrystore-clickhouse-0-0:8123`).

### Uruchomienie lokalne:
```bash
cd apps/window-tracker/svelte-dashboard
pnpm install
pnpm run dev
# Dashboard dostępny pod adresem: http://localhost:3003
```

---

## 3. 🐳 Wdrożenie Kontenerowe (Docker)

Aplikacja posiada wieloetapowy `Dockerfile` optymalizowany pod produkcję z `@sveltejs/adapter-node`:

```bash
docker build -t window-tracker-dashboard apps/window-tracker/svelte-dashboard
docker run -d \
  --name window-tracker-dashboard \
  -p 3003:3003 \
  -e WINDOW_TRACKER_BACKEND=clickhouse \
  -e CLICKHOUSE_URL=http://clickhouse:8123 \
  window-tracker-dashboard
```

---

## 🔮 Planowane Usprawnienia Architektoniczne

Zgodnie z zapisem w [doc/ROADMAP_HYPER_PRODUCTIVITY.md](../../doc/ROADMAP_HYPER_PRODUCTIVITY.md#faza-1-śledzenie-czasu-i-aktywnych-okien-real-time--historia):
* **Rozbicie daemona na dwa niezależne procesy (jak w `key-logger`):**
  1. `window-collector.service`: czysty zbieracz IPC Hyprlanda -> SQLite WAL.
  2. `window-forwarder.service`: osobny proces czytający SQLite i wysyłający do OTLP / ClickHouse.
