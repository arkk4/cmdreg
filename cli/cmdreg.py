#!/usr/bin/env python3
"""cmdreg — менеджер власних CLI-скриптів.
 
Реєструє bash/python/node/... скрипти як команди системи.
Для кожного скрипта створюється маленький shim (або alias), який запускає
правильний інтерпретатор (для Python — з venv, знайденого поруч зі скриптом).
Всі shim-и лежать в одній директорії, яку додано в PATH один раз.
 
Швидкий старт:
    python3 cmdreg.py install-self     # зареєструє сам cmdreg + налаштує PATH
    cmdreg                             # TUI
    cmdreg add ~/scripts/backup.py -n backup
"""
import argparse
import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path
 
try:
    import curses
except ImportError:  # Windows без windows-curses
    curses = None
 
APP = "cmdreg"
MARK = f"# managed by {APP}"
BEGIN, END = f"# >>> {APP} >>>", f"# <<< {APP} <<<"
 
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP
BIN_DIR = DATA_DIR / "bin"
CONFIG = CONFIG_DIR / "config.json"
ALIASES = CONFIG_DIR / "aliases.sh"
 
# ─── version ───
SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT  = SCRIPT_DIR.parent
LOGO_PATH  = REPO_ROOT / "public" / "logo.svg"
_vfile = REPO_ROOT / "version.json"
try:
    import json as _json
    _vdata = _json.loads(_vfile.read_text()) if _vfile.exists() else {}
    VERSION = _vdata.get("version", "1.0.0")
except Exception:
    VERSION = "1.0.0"
 
VENV_NAMES = (".venv", "venv", "env", ".env")
NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._+-]*$")
 
EXT_LANG = {
    ".py": "python", ".sh": "bash", ".bash": "bash", ".zsh": "zsh",
    ".js": "node", ".mjs": "node", ".cjs": "node", ".ts": "ts",
    ".rb": "ruby", ".pl": "perl", ".php": "php", ".lua": "lua",
}
SHEBANG_LANG = [("python", "python"), ("bash", "bash"), ("zsh", "zsh"),
                ("node", "node"), ("ruby", "ruby"), ("perl", "perl"),
                ("php", "php"), ("lua", "lua"), ("tsx", "ts"), ("deno", "ts")]
INTERPRETERS = {
    "python": ["python3"], "bash": ["bash"], "sh": ["sh"], "zsh": ["zsh"],
    "node": ["node"], "ts": ["tsx"], "ruby": ["ruby"], "perl": ["perl"],
    "php": ["php"], "lua": ["lua"],
}
 
 
class Err(Exception):
    pass
 
 
# ─── iTerm2 logo helpers ─────────────────────────────────────────
 
import base64 as _b64mod
import subprocess as _sp
 
 
def _is_iterm2() -> bool:
    return ("iTerm" in os.environ.get("TERM_PROGRAM", "") or
            "iTerm" in os.environ.get("LC_TERMINAL", ""))
 
 
_logo_cache: "str | None" = None
 
 
def _logo_png_b64() -> "str | None":
    global _logo_cache
    if _logo_cache is not None:
        return _logo_cache
    p = str(LOGO_PATH)
    if not os.path.exists(p):
        return None
    svg_bytes = open(p, "rb").read()
    try:
        r = _sp.run(["rsvg-convert", "-w", "128", "-f", "png"],
                    input=svg_bytes, capture_output=True, timeout=5)
        if r.returncode == 0 and r.stdout:
            _logo_cache = _b64mod.b64encode(r.stdout).decode("ascii")
            return _logo_cache
    except (FileNotFoundError, _sp.TimeoutExpired):
        pass
    _JS = ("import('@resvg/resvg-js').then(({Resvg})=>{" +
           "const png=new Resvg(process.stdin.read(),{fitTo:{mode:'width',value:128}}).render().asPng();" +
           "process.stdout.write(png);}).catch(()=>process.exit(1));")
    try:
        r = _sp.run(["node", "--input-type=module", "-e", _JS],
                    input=svg_bytes, capture_output=True, timeout=10)
        if r.returncode == 0 and r.stdout:
            _logo_cache = _b64mod.b64encode(r.stdout).decode("ascii")
            return _logo_cache
    except (FileNotFoundError, _sp.TimeoutExpired):
        pass
    return None
 
 
def print_startup_banner() -> None:
    """Print cmdreg logo + version header (iTerm2 inline image if available)."""
    if not _is_iterm2():
        print(f"  \033[1m{APP}\033[0m \033[2mv{VERSION}\033[0m")
        return
    logo_b64 = _logo_png_b64()
    if logo_b64:
        nb = _b64mod.b64encode(b"logo").decode()
        esc = f"\033]1337;File=name={nb};width=4;height=2;preserveAspectRatio=1;inline=1:{logo_b64}\a"
        sys.stdout.write(esc + "\033[1A" + f"  \033[1m{APP}\033[0m \033[2mv{VERSION}\033[0m\n\n")
        sys.stdout.flush()
    else:
        print(f"  \033[1m{APP}\033[0m \033[2mv{VERSION}\033[0m")
 
 
# ───────────────────────────── конфіг ─────────────────────────────
 
def load():
    if CONFIG.exists():
        try:
            cfg = json.loads(CONFIG.read_text())
            cfg.setdefault("base_dir", str(Path.home()))
            cfg.setdefault("entries", {})
            return cfg
        except json.JSONDecodeError:
            pass
    return {"base_dir": str(Path.home()), "entries": {}}
 
 
def save(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    tmp.replace(CONFIG)
 
 
# ───────────────────────────── детекція ─────────────────────────────
 
def read_shebang(p):
    try:
        with open(p, "rb") as f:
            line = f.readline(256)
    except OSError:
        return None
    if not line.startswith(b"#!"):
        return None
    parts = line[2:].decode(errors="replace").split()
    if not parts:
        return None
    prog = os.path.basename(parts[0])
    if prog == "env":
        rest = [x for x in parts[1:] if not x.startswith("-")]
        prog = rest[0] if rest else ""
    return prog
 
 
def detect_lang(p):
    p = Path(p)
    sb = read_shebang(p)
    if sb:
        if sb in ("sh", "dash", "ash"):
            return "sh"
        for key, lang in SHEBANG_LANG:
            if sb.startswith(key):
                return lang
    if p.suffix.lower() in EXT_LANG:
        return EXT_LANG[p.suffix.lower()]
    if os.access(p, os.X_OK):
        return "binary"
    return "unknown"
 
 
def venv_python(d):
    """Якщо d — venv (є pyvenv.cfg і bin/python), повертає шлях до python."""
    py = d / "bin" / "python"
    if (d / "pyvenv.cfg").is_file() and py.exists():
        return py
    return None
 
 
def find_venv(script, base=None):
    """Шукає venv за структурою (а не за назвою): у папці скрипта, потім вище,
    але не далі за базову директорію (якщо скрипт поза нею — лише його папка).
    Спершу пробує типові назви, потім будь-яку іншу підпапку."""
    d = Path(script).parent
    stop = None
    if base:
        b = Path(os.path.abspath(Path(base).expanduser()))
        try:
            d.relative_to(b)
            stop = b
        except ValueError:
            stop = d  # поза базовою директорією — тільки поточна папка
    else:
        stop = d
    while True:
        try:
            subdirs = [x for x in d.iterdir() if x.is_dir()]
        except OSError:
            subdirs = []
        subdirs.sort(key=lambda x: (x.name not in VENV_NAMES, x.name))
        for sd in subdirs:
            py = venv_python(sd)
            if py:
                return py
        if d == stop or d.parent == d:
            return None
        d = d.parent
 
 
def default_interpreter(path, lang, base=None):
    """→ (список-інтерпретатор | None, нотатка)"""
    if lang == "python":
        venv = find_venv(path, base)
        if venv:
            return [str(venv)], f"venv: {venv.parent.parent}"
        return ["python3"], "системний python3 (venv не знайдено)"
    return INTERPRETERS.get(lang), ""
 
 
def command_for(e):
    return [*(e.get("interpreter") or []), e["path"]]
 
 
# ───────────────────────────── shim / alias ─────────────────────────────
 
def shim_text(e):
    cmd = " ".join(shlex.quote(x) for x in command_for(e))
    return f'#!/bin/sh\n{MARK}\nexec {cmd} "$@"\n'
 
 
def apply(cfg):
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    want = {n for n, e in cfg["entries"].items() if e.get("mode", "shim") == "shim"}
    for f in BIN_DIR.iterdir():
        if f.name not in want and f.is_file() and MARK in f.read_text(errors="ignore"):
            f.unlink()
    for n in want:
        f = BIN_DIR / n
        f.write_text(shim_text(cfg["entries"][n]))
        f.chmod(0o755)
    lines = [MARK]
    for n, e in sorted(cfg["entries"].items()):
        if e.get("mode") == "alias":
            cmd = " ".join(shlex.quote(x) for x in command_for(e))
            lines.append(f"alias {n}={shlex.quote(cmd)}")
    ALIASES.write_text("\n".join(lines) + "\n")
 
 
def path_ok():
    return any(Path(d) == BIN_DIR for d in os.environ.get("PATH", "").split(os.pathsep) if d)
 
 
def path_conflict(name):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d or Path(d) == BIN_DIR:
            continue
        c = Path(d) / name
        if c.exists() and os.access(c, os.X_OK):
            return str(c)
    return None
 
 
def rc_target():
    sh = os.path.basename(os.environ.get("SHELL", ""))
    home = Path.home()
    if sh == "zsh":
        return home / ".zshrc", "posix"
    if sh == "fish":
        return home / ".config/fish/conf.d/cmdreg.fish", "fish"
    if sh == "bash":
        return (home / ".bash_profile" if sys.platform == "darwin" else home / ".bashrc"), "posix"
    return home / ".profile", "posix"
 
 
def setup_path():
    rc, kind = rc_target()
    if rc.exists() and BEGIN in rc.read_text(errors="ignore"):
        return f"PATH уже налаштовано в {rc}"
    q = shlex.quote
    if kind == "fish":
        block = f"{BEGIN}\nfish_add_path {q(str(BIN_DIR))}\n{END}\n"
    else:
        block = (f'{BEGIN}\nexport PATH={q(str(BIN_DIR))}:"$PATH"\n'
                 f"[ -f {q(str(ALIASES))} ] && . {q(str(ALIASES))}\n{END}\n")
    rc.parent.mkdir(parents=True, exist_ok=True)
    with rc.open("a") as f:
        f.write("\n" + block)
    return f"✓ Додано блок у {rc}. Перезапустіть термінал або: source {rc}"
 
 
# ───────────────────────────── операції ─────────────────────────────
 
def resolve_path(raw, cfg):
    p = Path(raw).expanduser()
    if p.is_absolute():
        return Path(os.path.abspath(p))
    for root in (Path.cwd(), Path(cfg["base_dir"]).expanduser()):
        if (root / p).exists():
            return Path(os.path.abspath(root / p))
    return Path(os.path.abspath(Path.cwd() / p))
 
 
def check_name(name, cfg, ignore=None, replace=False):
    if not NAME_RE.match(name or ""):
        raise Err(f"Некоректна назва команди: {name!r} (дозволено A-Z a-z 0-9 _ . + -)")
    if name in cfg["entries"] and name != ignore and not replace:
        raise Err(f"Команда '{name}' уже існує (змініть назву або використайте --force)")
 
 
def add_entry(cfg, raw_path, name=None, mode="shim", interpreter=None, replace=False):
    p = resolve_path(raw_path, cfg)
    if not p.is_file():
        raise Err(f"Файл не знайдено: {p}")
    lang = detect_lang(p)
    name = name or p.stem
    check_name(name, cfg, replace=replace)
    interp, note = default_interpreter(p, lang, cfg["base_dir"])
    if interpreter is not None:
        interp, note = interpreter, "інтерпретатор задано вручну"
    if interp is None and not os.access(p, os.X_OK):
        raise Err(f"Мову не визначено ({lang}) і файл не виконуваний — вкажіть інтерпретатор (-i)")
    cfg["entries"][name] = {"path": str(p), "lang": lang, "interpreter": interp, "mode": mode}
    save(cfg)
    apply(cfg)
    msg = f"✓ {name} → {p}  [{lang}{'; ' + note if note else ''}]"
    other = path_conflict(name)
    if other and mode == "shim":
        msg += f"\n⚠ затінює існуючу команду {other}"
    if mode == "shim" and not path_ok():
        msg += "\n⚠ shim-директорії немає в PATH — виконайте: cmdreg setup-path"
    return msg
 
 
def get_entry(cfg, name):
    if name not in cfg["entries"]:
        raise Err(f"Невідома команда: {name}")
    return cfg["entries"][name]
 
 
def remove_entry(cfg, name):
    get_entry(cfg, name)
    del cfg["entries"][name]
    save(cfg)
    apply(cfg)
    return f"✓ Видалено {name} (сам скрипт не чіпали)"
 
 
def rename_entry(cfg, old, new):
    get_entry(cfg, old)
    check_name(new, cfg, ignore=old)
    if new != old:
        cfg["entries"][new] = cfg["entries"].pop(old)
        save(cfg)
        apply(cfg)
    return f"✓ {old} → {new}"
 
 
def edit_entry(cfg, name, interpreter=None, mode=None):
    e = get_entry(cfg, name)
    if interpreter is not None:
        if interpreter.strip() in ("", "auto"):
            e["interpreter"], _ = default_interpreter(Path(e["path"]), e["lang"], cfg["base_dir"])
        else:
            e["interpreter"] = shlex.split(interpreter)
    if mode:
        e["mode"] = mode
    save(cfg)
    apply(cfg)
    return f"✓ {name}: {shlex.join(command_for(e))}  [{e['mode']}]"
 
 
def set_base(cfg, d):
    p = Path(d).expanduser()
    if not p.is_dir():
        raise Err(f"Не директорія: {p}")
    cfg["base_dir"] = str(p.resolve())
    save(cfg)
    return f"✓ Базова директорія: {cfg['base_dir']}"
 
 
def install_self(cfg):
    msg = add_entry(cfg, __file__, name=APP, interpreter=[sys.executable], replace=True)
    return msg + "\n" + setup_path()
 
 
def doctor(cfg):
    problems = []
    if not path_ok():
        problems.append(f"{BIN_DIR} відсутня в PATH (cmdreg setup-path)")
    for n, e in sorted(cfg["entries"].items()):
        if not Path(e["path"]).exists():
            problems.append(f"{n}: файл зник: {e['path']}")
        interp = (e.get("interpreter") or [None])[0]
        if interp and not (shutil.which(interp) or Path(interp).exists()):
            problems.append(f"{n}: інтерпретатор не знайдено: {interp}")
        if e.get("mode", "shim") == "shim" and not (BIN_DIR / n).exists():
            problems.append(f"{n}: shim відсутній (cmdreg refresh)")
    return problems
 
 
# ───────────────────────────── CLI ─────────────────────────────
 
def cmd_list(args, cfg):
    print(f"base: {cfg['base_dir']}\nshim dir: {BIN_DIR}" + ("" if path_ok() else "   ⚠ не в PATH"))
    if not cfg["entries"]:
        print("\n(порожньо) — cmdreg add <скрипт>")
        return 0
    print()
    w = max(len(n) for n in cfg["entries"])
    for n, e in sorted(cfg["entries"].items()):
        mark = " " if Path(e["path"]).exists() else "✗"
        print(f"{mark} {n:<{w}}  {e['lang']:<7} {e.get('mode', 'shim'):<5} {e['path']}")
        if e.get("interpreter"):
            print(f"  {'':<{w}}  ↳ {shlex.join(e['interpreter'])}")
    return 0
 
 
def cmd_add(a, cfg):
    interp = shlex.split(a.interpreter) if a.interpreter else None
    print(add_entry(cfg, a.path, a.name, a.mode, interp, a.force))
    return 0
 
 
def cmd_rm(a, cfg):
    print(remove_entry(cfg, a.name))
    return 0
 
 
def cmd_rename(a, cfg):
    print(rename_entry(cfg, a.old, a.new))
    return 0
 
 
def cmd_edit(a, cfg):
    print(edit_entry(cfg, a.name, a.interpreter, a.mode))
    return 0
 
 
def cmd_base(a, cfg):
    print(set_base(cfg, a.dir) if a.dir else cfg["base_dir"])
    return 0
 
 
def cmd_refresh(a, cfg):
    apply(cfg)
    print("✓ shim-и й aliases перегенеровано")
    return 0
 
 
def cmd_doctor(a, cfg):
    probs = doctor(cfg)
    print("\n".join("⚠ " + p for p in probs) if probs else "✓ все гаразд")
    return 1 if probs else 0
 
 
def cmd_self(a, cfg):
    print(install_self(cfg))
    return 0
 
 
def cmd_path(a, cfg):
    print(setup_path())
    return 0
 
 
def cmd_tui(a, cfg):
    if curses is None:
        print("curses недоступний — запускаю REPL")
        return repl()
    print_startup_banner()
    os.environ.setdefault("ESCDELAY", "25")
    curses.wrapper(tui)
    return 0
 
 
def cmd_repl(a, cfg):
    return repl()
 
 
def build_parser():
    p = argparse.ArgumentParser(prog=APP, description="Менеджер CLI-скриптів: реєструє їх як команди.")
    sub = p.add_subparsers(dest="cmd")
 
    def mk(name, fn, help_, aliases=()):
        sp = sub.add_parser(name, aliases=list(aliases), help=help_)
        sp.set_defaults(fn=fn)
        return sp
 
    mk("tui", cmd_tui, "повноекранний інтерфейс (за замовчуванням)")
    mk("repl", cmd_repl, "простий текстовий REPL")
    mk("list", cmd_list, "показати зареєстровані команди", ["ls"])
    a = mk("add", cmd_add, "зареєструвати скрипт")
    a.add_argument("path", help="шлях до скрипта (відносний — шукається в cwd, потім у базовій директорії)")
    a.add_argument("-n", "--name", help="назва команди (за замовчуванням — ім'я файла без розширення)")
    a.add_argument("-m", "--mode", choices=["shim", "alias"], default="shim")
    a.add_argument("-i", "--interpreter", help="свій інтерпретатор, напр. '/opt/venv/bin/python'")
    a.add_argument("-f", "--force", action="store_true", help="перезаписати існуючу")
    r = mk("rm", cmd_rm, "видалити команду", ["remove"])
    r.add_argument("name")
    rn = mk("rename", cmd_rename, "перейменувати команду")
    rn.add_argument("old")
    rn.add_argument("new")
    e = mk("edit", cmd_edit, "змінити інтерпретатор / режим")
    e.add_argument("name")
    e.add_argument("-i", "--interpreter", help="новий інтерпретатор, або 'auto' для повторного пошуку venv")
    e.add_argument("-m", "--mode", choices=["shim", "alias"])
    b = mk("base", cmd_base, "показати / задати базову директорію")
    b.add_argument("dir", nargs="?")
    mk("refresh", cmd_refresh, "перегенерувати shim-и")
    mk("doctor", cmd_doctor, "перевірити биті шляхи/інтерпретатори")
    mk("install-self", cmd_self, "зареєструвати сам cmdreg + налаштувати PATH")
    mk("setup-path", cmd_path, "додати shim-директорію в PATH (rc-файл шелла)")
    return p
 
 
def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        cfg = load()
        if not getattr(args, "fn", None):
            if sys.stdin.isatty() and sys.stdout.isatty():
                return cmd_tui(args, cfg)
            return cmd_list(args, cfg)
        return args.fn(args, cfg)
    except Err as ex:
        print(f"✗ {ex}", file=sys.stderr)
        return 1
 
 
def repl():
    print_startup_banner()
    print("cmdreg REPL — 'help' для довідки, 'q' для виходу")
    help_ = build_parser().format_help()
    while True:
        try:
            line = input("cmdreg> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("q", "quit", "exit"):
            return 0
        if line in ("?", "help"):
            print(help_)
            continue
        try:
            argv = shlex.split(line)
            if argv[0] in ("repl", "tui"):
                continue
            main(argv)
        except (SystemExit, ValueError):
            pass
 
 
# ───────────────────────────── TUI ─────────────────────────────
 
def put(scr, y, x, s, attr=0):
    h, w = scr.getmaxyx()
    if y < 0 or y >= h or x >= w - 1:
        return
    try:
        scr.addstr(y, x, s[: w - x - 1], attr)
    except curses.error:
        pass
 
 
def prompt(scr, label, default=""):
    buf = list(default)
    curses.curs_set(1)
    try:
        while True:
            h, w = scr.getmaxyx()
            scr.move(h - 1, 0)
            scr.clrtoeol()
            text = f"{label}: {''.join(buf)}"
            put(scr, h - 1, 0, text, curses.A_BOLD)
            scr.move(h - 1, min(len(text), w - 2))
            ch = scr.get_wch()
            if ch in ("\n", "\r") or ch == curses.KEY_ENTER:
                return "".join(buf)
            if ch == "\x1b":
                return None
            if ch in ("\x7f", "\b") or ch == curses.KEY_BACKSPACE:
                if buf:
                    buf.pop()
            elif ch == "\x15":
                buf.clear()
            elif isinstance(ch, str) and ch.isprintable():
                buf.append(ch)
    finally:
        curses.curs_set(0)
 
 
def confirm(scr, text):
    h, _ = scr.getmaxyx()
    scr.move(h - 1, 0)
    scr.clrtoeol()
    put(scr, h - 1, 0, f"{text} (y/N)", curses.A_BOLD)
    scr.refresh()
    ch = scr.get_wch()
    return ch in ("y", "Y")
 
 
def pick_file(scr, start, base):
    cur = Path(start).expanduser()
    if not cur.is_dir():
        cur = Path.home()
    sel = top = 0
    hidden = False
    while True:
        try:
            items = sorted(cur.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            items = []
        if not hidden:
            items = [i for i in items if not i.name.startswith(".")]
        rows = [cur.parent] + items
        sel = max(0, min(sel, len(rows) - 1))
        h, w = scr.getmaxyx()
        avail = max(1, h - 5)
        if sel < top:
            top = sel
        if sel >= top + avail:
            top = sel - avail + 1
        scr.erase()
        put(scr, 0, 0, f" Оберіть скрипт — {cur}".ljust(w - 1), curses.A_REVERSE)
        for i, p in enumerate(rows[top: top + avail]):
            idx = top + i
            if idx == 0:
                label, tag = "../", ""
            elif p.is_dir():
                label, tag = p.name + "/", ""
            else:
                label, tag = p.name, detect_lang(p)
            attr = curses.A_REVERSE if idx == sel else 0
            if idx and p.is_dir():
                attr |= curses.A_BOLD
            put(scr, 2 + i, 1, label.ljust(max(10, w - 14)), attr)
            put(scr, 2 + i, max(12, w - 12), tag, curses.A_DIM)
        put(scr, h - 1, 0, " ↑↓ рух  →/Enter відкрити/обрати  ← вгору  . приховані  t шлях  b база  ~ home  Esc скасувати")
        scr.refresh()
        try:
            ch = scr.get_wch()
        except KeyboardInterrupt:
            return None
        if ch in (curses.KEY_UP, "k"):
            sel -= 1
        elif ch in (curses.KEY_DOWN, "j"):
            sel += 1
        elif ch == curses.KEY_PPAGE:
            sel -= avail
        elif ch == curses.KEY_NPAGE:
            sel += avail
        elif ch in (curses.KEY_LEFT, curses.KEY_BACKSPACE, "\x7f", "h"):
            cur, sel, top = cur.parent, 0, 0
        elif ch in (curses.KEY_RIGHT, curses.KEY_ENTER, "\n", "\r", "l"):
            target = rows[sel]
            if sel == 0 or target.is_dir():
                cur, sel, top = target, 0, 0
            else:
                return target
        elif ch == ".":
            hidden = not hidden
        elif ch == "~":
            cur, sel, top = Path.home(), 0, 0
        elif ch == "b":
            cur, sel, top = Path(base).expanduser(), 0, 0
        elif ch == "t":
            s = prompt(scr, "Шлях", str(cur) + os.sep)
            if s:
                p = Path(s).expanduser()
                if p.is_dir():
                    cur, sel, top = p, 0, 0
                elif p.is_file():
                    return p
        elif ch in ("\x1b", "q"):
            return None
 
 
def tui(scr):
    curses.curs_set(0)
    scr.keypad(True)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_RED, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
    green, red, yellow = curses.color_pair(1), curses.color_pair(2), curses.color_pair(3)
    sel = top = 0
    status = ""
    while True:
        cfg = load()
        names = sorted(cfg["entries"])
        sel = max(0, min(sel, len(names) - 1))
        scr.erase()
        h, w = scr.getmaxyx()
        if h < 12 or w < 40:
            put(scr, 0, 0, "Збільште вікно термінала")
            scr.refresh()
            if scr.get_wch() == "q":
                return
            continue
        put(scr, 0, 0, " cmdreg — менеджер команд".ljust(w - 1), curses.A_REVERSE)
        put(scr, 1, 1, f"база: {cfg['base_dir']}   shim-dir: {BIN_DIR}", curses.A_DIM)
        if not path_ok():
            put(scr, 2, 1, "⚠ shim-директорії немає в PATH — натисніть p", yellow)
        nw = min(24, max([len(n) for n in names] + [4]) + 2)
        put(scr, 4, 3, f"{'КОМАНДА':<{nw}} {'МОВА':<8} {'РЕЖИМ':<6} ШЛЯХ", curses.A_BOLD)
        avail = h - 11
        if sel < top:
            top = sel
        if sel >= top + avail:
            top = sel - avail + 1
        if not names:
            put(scr, 6, 3, "(порожньо — натисніть a, щоб додати скрипт)", curses.A_DIM)
        for i, n in enumerate(names[top: top + avail]):
            e = cfg["entries"][n]
            exists = Path(e["path"]).exists()
            line = f"{n:<{nw}} {e['lang']:<8} {e.get('mode', 'shim'):<6} {e['path']}"
            attr = (green if exists else red) | (curses.A_REVERSE if top + i == sel else 0)
            put(scr, 5 + i, 1, ("▶ " if top + i == sel else "  ") + line, attr)
        if names:
            e = cfg["entries"][names[sel]]
            put(scr, h - 5, 1, f"шлях:  {e['path']}", curses.A_DIM)
            put(scr, h - 4, 1, f"запуск: {shlex.join(command_for(e))}", curses.A_DIM)
            if not Path(e["path"]).exists():
                put(scr, h - 3, 1, "✗ файл не існує", red)
        put(scr, h - 2, 1, status.replace("\n", "  "), yellow)
        put(scr, h - 1, 0, " a додати  e інтерпр.  r назва  m shim/alias  d видалити  b база  s себе  p PATH  R оновити  q вихід",
            curses.A_REVERSE)
        scr.refresh()
        try:
            ch = scr.get_wch()
        except KeyboardInterrupt:
            return
        status = ""
        try:
            if ch in (curses.KEY_UP, "k"):
                sel -= 1
            elif ch in (curses.KEY_DOWN, "j"):
                sel += 1
            elif ch in ("q", "Q"):
                return
            elif ch == "a":
                f = pick_file(scr, cfg["base_dir"], cfg["base_dir"])
                if f:
                    lang = detect_lang(f)
                    _, note = default_interpreter(f, lang, cfg["base_dir"])
                    name = prompt(scr, f"Назва команди [{lang}; {note or 'виконуваний файл'}]", f.stem)
                    if name:
                        status = add_entry(cfg, str(f), name)
                        sel = sorted(load()["entries"]).index(name)
            elif ch == "e" and names:
                e = cfg["entries"][names[sel]]
                v = prompt(scr, "Інтерпретатор (порожньо = авто)", shlex.join(e.get("interpreter") or []))
                if v is not None:
                    status = edit_entry(cfg, names[sel], v if v.strip() else "auto")
            elif ch == "r" and names:
                v = prompt(scr, "Нова назва", names[sel])
                if v:
                    status = rename_entry(cfg, names[sel], v)
            elif ch == "m" and names:
                cur = cfg["entries"][names[sel]].get("mode", "shim")
                status = edit_entry(cfg, names[sel], mode="alias" if cur == "shim" else "shim")
                if cur == "shim":
                    status += "  (alias працює лише в інтерактивному шеллі)"
            elif ch == "d" and names:
                if confirm(scr, f"Видалити '{names[sel]}'?"):
                    status = remove_entry(cfg, names[sel])
            elif ch == "b":
                v = prompt(scr, "Базова директорія", cfg["base_dir"])
                if v:
                    status = set_base(cfg, v)
            elif ch == "s":
                status = install_self(cfg)
            elif ch == "p":
                status = setup_path()
            elif ch == "R":
                apply(cfg)
                status = "✓ оновлено"
        except Err as ex:
            status = f"✗ {ex}"
 
 
if __name__ == "__main__":
    sys.exit(main())
 
