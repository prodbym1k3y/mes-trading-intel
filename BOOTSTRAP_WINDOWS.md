# Bootstrap: Windows PC Setup

Mirror of the Mac side. Goal: PC for charting/trades, MacBook for indices/commodities/notes, both reading the same SQLite DB, Obsidian vault, and Claude memory via iCloud Drive.

> **Run order matters.** Don't skip ahead — the symlink steps (#5) require iCloud to have finished syncing the Brain folder first.

---

## 1. Install prerequisites

Download and install in this order:

1. **iCloud for Windows** — https://support.apple.com/en-us/HT204283 (Microsoft Store version is fine)
   - Sign in with the same Apple ID as the Mac
   - Enable **iCloud Drive**
2. **Git for Windows** — https://git-scm.com/download/win
3. **Python 3.14** — https://www.python.org/downloads/windows/ (check "Add to PATH" in the installer)
4. **GitHub CLI** — https://cli.github.com/ or `winget install GitHub.cli`

---

## 2. GitHub auth

```cmd
gh auth login
```

Pick GitHub.com → HTTPS → "Login with a web browser". Authenticate as `prodbym1k3y`.

```cmd
gh auth setup-git
```

This makes git use the gh credential helper (avoids stale Keychain/Credential-Manager tokens — the same trap that bit us on the Mac).

---

## 3. Clone the repo

```cmd
mkdir C:\trading
cd C:\trading
git clone https://github.com/prodbym1k3y/mes-trading-intel.git .
```

You'll be on `main`. There's also a `mac-experimental` branch with ~1,500 lines of in-progress work from the Mac (autonomous optimizer agent + cross-asset config + UI changes) — preserved but not merged. To inspect it later:

```cmd
git fetch origin
git log --oneline origin/mac-experimental
```

Decide whether to merge, cherry-pick, or rebuild on the PC.

---

## 4. Python environment

```cmd
cd C:\trading
python -m venv .
Scripts\activate
pip install -r requirements.txt
```

Note: XGBoost installs cleanly on Windows from PyPI. Mac needed `brew install libomp` first; you don't need that on Windows.

---

## 5. Wait for iCloud sync, verify Brain

Before symlinking anything, **wait for iCloud Drive to fully sync the Brain folder from the Mac**. Open File Explorer → `iCloud Drive` and look for a folder called `Brain`. The cloud icon next to files should become a green checkmark when fully downloaded.

Verify from cmd:

```cmd
dir "%USERPROFILE%\iCloudDrive\Brain"
```

You should see three subfolders:

- `mes-state\`     — contains `config.json`, `mes_intel.db`, `models\`
- `obsidian\`      — contains `Obsidian Vault\`
- `claude-memory\` — contains `MEMORY.md` and the per-project memory files

If any are missing or empty, wait longer or right-click → **Always keep on this device** to force a download.

---

## 6. Create the symlinks

Open **cmd** or **PowerShell as Administrator** (symlink creation requires elevation on Windows by default).

### State directory (SQLite DB + config)

```cmd
mkdir C:\trading\var
mklink /D "C:\trading\var\mes_intel" "%USERPROFILE%\iCloudDrive\Brain\mes-state"
```

### Obsidian vault

Don't symlink the user-facing vault path — instead, point Obsidian at the Brain location directly:

1. Open Obsidian → "Open folder as vault"
2. Navigate to `%USERPROFILE%\iCloudDrive\Brain\obsidian\Obsidian Vault`
3. Open it. Obsidian works fine with paths inside iCloud Drive on Windows.

### Claude Code memory

This one is timing-sensitive. Claude Code creates the per-project memory dir the first time you open the project on a given machine. So:

1. Open Claude Code on Windows once with `C:\trading` as the working directory (just open and close).
2. Find the project hash dir under `%USERPROFILE%\.claude\projects\` — it'll be named something like `-C--trading` (the path with separators replaced).
3. From that dir, delete the auto-created `memory\` folder and symlink it to Brain:

```cmd
rmdir /S /Q "%USERPROFILE%\.claude\projects\-C--trading\memory"
mklink /D "%USERPROFILE%\.claude\projects\-C--trading\memory" "%USERPROFILE%\iCloudDrive\Brain\claude-memory"
```

Adjust the `-C--trading` part to whatever Claude Code actually created. From now on Claude memory entries written on either machine will appear on both.

---

## 7. The lockfile rule (important)

`mes_intel/main.py` writes `RUNNING.lock` to the state dir (which is symlinked to Brain) at startup. The lockfile records hostname, pid, and ISO timestamp. Behavior:

- **Fresh lock from a different host (< 2h old)** → app refuses to start. Prevents both machines from writing the SQLite DB at the same time, which can corrupt it.
- **Stale lock (> 2h old)** → automatically cleared.
- **Same-host lock** → cleared (covers crashes).
- **Clean exit** (Cmd-Q, Ctrl-C, SIGTERM) → lockfile removed.

**If the Mac crashed mid-session and you want to start on PC** (or vice versa):

```cmd
del "%USERPROFILE%\iCloudDrive\Brain\mes-state\RUNNING.lock"
```

…then start the app normally. Just make sure the other machine actually isn't running it.

---

## 8. Run the app

```cmd
cd C:\trading
Scripts\activate
python -m mes_intel
```

You should see in the logs:

- "Database initialized: ..." — pointing at the symlinked path
- No lockfile warnings (or "Reclaiming stale local lockfile" if your previous run didn't clean up)

---

## 9. Daily workflow

- **PC** = charting, order entry, trade execution. Most of the time, the MES app runs here.
- **MacBook** = monitoring indices/commodities, notes in Obsidian. Run the MES app only when not running on the PC.
- The Brain folder syncs in the background via iCloud — usually a few seconds to a few minutes. SQLite writes are atomic, so as long as only one machine has the app open at a time (the lockfile enforces this), you're safe.
- Obsidian notes you take on the Mac appear on the PC after iCloud syncs. Same for Claude memory updates.

If something feels desynced, check iCloud Drive's status icon in the system tray — it'll show pending uploads/downloads.

---

## 10. Pushing code changes from PC

Standard flow:

```cmd
cd C:\trading
git add <files>
git commit -m "..."
git push
```

If you want to fold in the Mac's `mac-experimental` work:

```cmd
git fetch origin
git checkout main
git merge origin/mac-experimental
# resolve conflicts, then:
git push
```

Or selectively cherry-pick from it.
