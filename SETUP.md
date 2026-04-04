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

## Manual Launch

```bash
source bin/activate
python3 -m mes_intel
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
- **libomp** — `brew install libomp` (XGBoost acceleration; falls back to heuristic scorer without it)
- **HuggingFace models** — FinBERT downloads automatically on first news event

## Troubleshooting

- `ModuleNotFoundError: rapi` — Expected. Simulated feed kicks in automatically.
- `Populating font family aliases took X ms` — Harmless Qt font fallback warning.
- `No module named 'pyobjus'` — Harmless; desktop notifications fall back gracefully.
- High CPU — Normal during first ~30s (FinBERT loading + demo data seeding), then settles.
