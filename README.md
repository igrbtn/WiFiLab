# WiFiLab

Wi-Fi scanner, site survey and reporting tool for macOS. WiFiLab scans with the Mac's own Wi-Fi radio
(CoreWLAN), keeps a scan history, draws channel graphs, finds access points by signal, builds coverage heatmaps
over a floor plan from points you measure by walking around with the Mac, and writes survey reports
(printable HTML/PDF, Word, CSV, JSON).

It is a standalone fork of the Wi-Fi features of FieldTab (a field engineer tablet and its Mac companion): the
analysis logic, the engineering heatmaps and the `fieldtab.plan/1` floor plan format are shared, and FieldTab tablet
survey exports open in WiFiLab directly.

The UI is a local web app at `http://127.0.0.1:8097/`, started from a small menubar app (`WiFiLab.app`).

## Features

- **Scanner** (CoreWLAN via pyobjc): SSID, BSSID, RSSI, noise, channel, band (2.4 / 5 / 6 GHz), channel width,
  security (Open, OWE, WEP, WPA, WPA2, WPA3, Enterprise, transition modes), PHY modes (11a/b/g/n/ac/ax/be), country,
  beacon interval, ad hoc networks; current connection with Tx rate, SNR and MCS.
- **Background scanning** with a configurable interval and a scan history in SQLite
  (`~/Library/Application Support/WiFiLab`), pruned after 14 days by default. Clear errors when Wi-Fi is off or the
  radio is busy (falls back to the system's cached scan).
- **Live AP table** with search, band / security / signal filters, sortable columns, vendor by MAC prefix (bundled
  OUI subset, extendable), per-AP details and a signal-over-time chart.
- **Channel graphs** for 2.4, 5 and 6 GHz: every AP as an arc over the channels it really occupies (40/80/160 MHz
  blocks), coloured per SSID; channel load bars; recommendations (least loaded of 1/6/11, best non-DFS 20/40/80 MHz
  on 5 GHz, best PSC channel on 6 GHz); co-channel congestion and partially overlapping pairs.
- **Find AP**: a big RSSI meter with trend and min/avg/max while you walk.
- **History**: every BSSID seen in a window with min/avg/max, and signal curves for up to 8 APs.
- **Surveys**: a floor plan per survey (editor with walls, doors, windows, beams, snapping, undo, background image;
  JSON import; an LLM prompt that turns a photo of a plan into the JSON). Click where you stand: the Mac scans and
  stores the point. Place access points on the plan and bind them to the measured radios.
- **Engineering heatmaps** (inverse-distance weighting with fade-out): signal of the strongest AP, one SSID, one AP,
  an AP group; serving AP zones; SNR; AP count; channel overlap. PNG export.
- **Reports**: summary, issues found (weak and missing coverage, low SNR, co-channel congestion, partial overlap,
  open / WEP / WPA1 networks, hidden SSIDs, 40 MHz and off-1/6/11 channels on 2.4 GHz), heatmaps, channel plan and AP
  inventory. Print to PDF from the browser, Word (.docx), CSV and JSON.
- **FieldTab import**: tablet `wifi-survey` JSON/CSV, `wifi-map` JSON (metre maps and older cell grids), session
  documents and `fieldtab.plan/1` plans; export back to the FieldTab map format.
- **Fake scanner** (a simulated floor) for tests, demos and non-macOS hosts.

## Screenshots

Placeholders, to be added:

- `docs/screenshots/live.png`: live AP table with connection card
- `docs/screenshots/channels.png`: 2.4 / 5 GHz channel graphs and load bars
- `docs/screenshots/survey.png`: survey heatmap with placed APs
- `docs/screenshots/plan.png`: floor plan editor
- `docs/screenshots/report.png`: printable report

## Requirements

- macOS 13 or newer (developed on macOS 26/27, Apple Silicon); Python 3.11+ (3.12 recommended).
- Xcode Command Line Tools (`clang`, `codesign`, `iconutil`) to build the app bundle.
- Node.js only to run the JavaScript unit tests.

## Install and run

Build the menubar app (creates `~/.venvs/wifilab` if missing, installs WiFiLab there, builds
`/Applications/WiFiLab.app`):

```bash
git clone https://github.com/igrbtn/WiFiLab.git
cd WiFiLab
./scripts/build_app.sh
open /Applications/WiFiLab.app
```

The app sits in the menu bar as "WL", opens the UI in your browser and asks for Location Services (see below).
Log: `~/Library/Logs/WiFiLab.log`.

From source, without the bundle:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"            # or: pip install ".[dev]"
wifilab web                        # UI only, http://127.0.0.1:8097/
wifilab scan                       # one scan printed in the terminal
WIFILAB_SCANNER=fake wifilab web   # simulated floor, works anywhere
```

Configuration is optional, through environment variables (see `.env.example`): port, data folder, scanner backend,
scan interval, history retention.

## Location Services permission

Since macOS 14, Wi-Fi scans return network names (SSID) and access point addresses (BSSID) only to apps the user
allowed Location Services. Without it every network comes back anonymous: WiFiLab counts them and shows a banner
explaining what to do.

- `WiFiLab.app` declares `NSLocationWhenInUseUsageDescription` and asks at start (or from the menu, "Allow Location
  Services..."). Allow it once in the prompt or in System Settings > Privacy & Security > Location Services.
- When you run `wifilab web` from a terminal, macOS judges the terminal app, which usually has no location access,
  so scans stay anonymous. Use the app bundle for real surveys.
- WiFiLab does not read, store or send your location; the permission only unlocks the scan fields.

The app bundle is signed ad hoc. Rebuilding it with a different launcher version or plist makes macOS ask again.

## Survey workflow

1. Surveys > New survey, then Floor plan: set width and length, draw walls, doors and windows, or import a plan
   (JSON, a FieldTab plan, or an LLM answer made from a photo with the provided prompt). Optionally add a background
   image.
2. Measure & heatmaps: stand at a spot, click the same spot on the plan. The Mac scans (a few seconds) and the
   point appears. Repeat in a walking pattern, roughly every 2-4 m.
3. Place AP: click where an access point hangs and pick the measured radio it is. This enables the serving AP and
   per-AP views.
4. Report: choose the SSID the coverage is judged for, then Print / PDF, Word, CSV or JSON.

## Development

```bash
pip install -e ".[dev]"
pytest                 # Python tests + node tests of the browser helpers (skipped without node)
ruff check .
```

Layout: `src/wifilab/` (FastAPI app `web.py`, `scanner/` backends, `analysis.py`, `store.py`, `projects.py`,
`report.py`, `fieldtab.py` import), `src/wifilab/static/` (vanilla JS UI, no build step), `tests/`,
`scripts/build_app.sh` (app bundle). Floor plan format: [docs/PLAN_FORMAT.md](docs/PLAN_FORMAT.md).

## License

MIT, see [LICENSE](LICENSE). Copyright (c) 2026 Igor Batin.
