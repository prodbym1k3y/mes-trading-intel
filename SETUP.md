# MES Trading Intelligence System — Setup

## Quick Start (macOS)

```bash
git clone <repo-url> trading
cd trading
python3 -m venv .
source bin/activate
brew install libomp              # required for XGBoost
pip install -r requirements.txt  # ~10 min first time
cp .env.example .env             # optional: add API keys
./run.sh                         # launches the app
```

## Quick Start (Windows)

```powershell
git clone <repo-url> trading
cd trading
python -m venv .
Scripts\activate.bat
pip install -r requirements.txt  # ~10 min first time
copy .env.example .env           # optional: add API keys
run.bat                          # launches the app
```

**Windows notes:**
- Use `python` (not `python3`) — Windows Python installer registers as `python`
- No `brew install libomp` needed — XGBoost ships with OpenMP on Windows
- If `simpleaudio` fails to install, install [Visual C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) first, or skip it (audio alerts will fall back to system beeps)
- Venv lives in `Scripts/` (not `bin/`)

## Manual Launch

```bash
# macOS
source bin/activate && python3 -m mes_intel

# Windows
Scripts\activate.bat && python -m mes_intel
```

## API Keys (all optional)

The app runs fully in simulated mode with no keys. Add keys to `.env` for:

| Key | Purpose |
|-----|---------|
| `ANTHROPIC_API_KEY` | AI Assistant tab (Claude) |
| `RITHMIC_USER` / `RITHMIC_PASSWORD` | Live MES market data via AMP Futures |
| `FINNHUB_KEY` | Real news feed + dark pool data |
| `TWITTER_BEARER_TOKEN` | Real-time tweet streaming |

## What Gets Auto-Created on First Run

- `var/mes_intel/mes_intel.db` — SQLite database (schema auto-migrates)
- `var/mes_intel/config.json` — App settings (editable via Settings tab)
- `var/mes_intel/models/` — ML model checkpoints
- `~/.cache/huggingface/` — FinBERT model (~440 MB, downloaded once)

## External Dependencies

- **Python 3.12+** (tested on 3.14)
- **macOS only:** `brew install libomp` (XGBoost acceleration; falls back to heuristic scorer without it)
- **HuggingFace models** — FinBERT downloads automatically on first news event

## Troubleshooting

- `ModuleNotFoundError: rapi` — Expected. Simulated feed kicks in automatically.
- `Populating font family aliases took X ms` — Harmless Qt font fallback warning.
- `No module named 'pyobjus'` — Harmless; desktop notifications fall back gracefully.
- High CPU — Normal during first ~30s (FinBERT loading + demo data seeding), then settles.
- **Windows:** If you get `DLL load failed` for torch/xgboost, install the latest [Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe).
