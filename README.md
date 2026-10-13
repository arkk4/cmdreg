# cmdreg

> Register your scripts as system commands — shims, aliases, venv auto-detection.

![cmdreg badge](public/badge.svg)

**cmdreg** is a lightweight CLI/TUI manager that registers any bash/python/node/ruby/... script as a named system command. It creates small *shims* (or shell aliases) in a single `~/.local/share/cmdreg/bin/` directory you add to `$PATH` once.

---

## ✨ Features

- **Zero config** — auto-detects language from shebang and extension
- **Python venv support** — finds `.venv` / `venv` next to the script automatically
- **Full-screen TUI** (curses) and a **REPL** fallback
- **shim** or **alias** mode — aliases work in interactive shells only
- **doctor** command — detects broken paths and missing interpreters
- **Self-registration** — register cmdreg itself with one command

---

## 🚀 Quick Start

```bash
# Register cmdreg itself and add shim-dir to PATH:
python3 cli/cmdreg.py install-self

# Then use as a command:
cmdreg                              # Open full-screen TUI
cmdreg add ~/scripts/backup.py -n backup
cmdreg add ~/scripts/deploy.sh
cmdreg list
cmdreg rm backup
```

---

## 📋 Commands

| Command | Description |
|---|---|
| `cmdreg` | Open full-screen TUI (default when stdin is a TTY) |
| `cmdreg list` | List all registered commands |
| `cmdreg add <path> [-n name]` | Register a script |
| `cmdreg rm <name>` | Remove a command |
| `cmdreg rename <old> <new>` | Rename a command |
| `cmdreg edit <name> [-i interp]` | Change interpreter |
| `cmdreg doctor` | Check for broken entries |
| `cmdreg refresh` | Regenerate all shims |
| `cmdreg install-self` | Register cmdreg + setup PATH |
| `cmdreg setup-path` | Add shim-dir to shell rc |

### TUI Key Bindings

| Key | Action |
|---|---|
| `↑`/`↓` or `j`/`k` | Navigate entries |
| `a` | Add new script (file picker) |
| `e` | Edit interpreter |
| `r` | Rename |
| `m` | Toggle shim ↔ alias |
| `d` | Delete |
| `b` | Change base directory |
| `s` | Register cmdreg itself |
| `p` | Setup PATH |
| `R` | Refresh shims |
| `q` | Quit |

---

## 📁 Structure

```
cmdreg/
├── cli/
│   └── cmdreg.py         # Main CLI/TUI script
├── tools/
│   ├── generate_logo.mjs # Generate public/logo.svg + badge.svg
│   └── bump.mjs          # Bump version, regenerate assets, commit
├── public/
│   ├── logo.svg          # Generated logo (rose-pine · mirror)
│   └── badge.svg         # Generated badge
├── vendor/
│   └── logogen/          # Git submodule — SVG logo generator
├── Makefile
├── version.json
└── README.md
```

---

## 🔧 Makefile

```bash
make setup          # Init submodules + install git hooks
make badge          # Regenerate logo and badge
make bump           # Bump version and commit
make bump V=1.1.0   # Bump to specific version
```

---

## ⚙️ How It Works

1. `cmdreg add /path/to/script.py -n mycommand`
2. cmdreg detects language (`python`) and finds venv if present
3. Creates a shim: `~/.local/share/cmdreg/bin/mycommand`
4. The shim runs `VENV/bin/python /path/to/script.py "$@"`

Shim directory is added to `$PATH` once via `cmdreg install-self` or `cmdreg setup-path`.

---

## 📄 License

MIT
