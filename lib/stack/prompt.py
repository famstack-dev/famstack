"""Shared TUI primitives for hooks, installer, and CLI.

Provides consistent formatting and input for anything interactive
in stack — configure hooks, the installer wizard, CLI prompts.

Uses simple_term_menu (vendored, MIT) for menu selection.
Plain ANSI for everything else — no dependencies.
"""

import itertools
import os
import sys
import threading
import time


# ── Color detection ───────────────────────────────────────────────────────────

def _use_color():
    """Respect NO_COLOR convention (https://no-color.org)."""
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty"):
        return False
    return sys.stdout.isatty()


# ── Colors ────────────────────────────────────────────────────────────────────

if _use_color():
    ORANGE = "\033[38;5;208m"
    TEAL   = "\033[38;5;37m"
    GREEN  = "\033[38;5;78m"
    RED    = "\033[38;5;203m"
    DIM    = "\033[38;5;245m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"
else:
    ORANGE = TEAL = GREEN = RED = DIM = BOLD = RESET = ""


# ── Output ────────────────────────────────────────────────────────────────────

def clear():
    print("\033[2J\033[H", end="", flush=True)


def nl():
    print()


def out(msg="", indent=2):
    print(" " * indent + msg)


def orange(msg):
    out(f"{ORANGE}{msg}{RESET}")


def teal(msg):
    out(f"{TEAL}{msg}{RESET}")


def dim(msg):
    out(f"{DIM}{msg}{RESET}")


def bold(msg):
    out(f"{BOLD}{msg}{RESET}")


def done(msg):
    out(f"{GREEN}\u2713{RESET}  {msg}")


def warn(msg):
    out(f"{ORANGE}\u26a0{RESET}  {msg}")


def error(msg):
    out(f"{RED}\u2717{RESET}  {msg}")


def rule(width=44):
    out("\u2500" * width)


def kv(label, value, label_width=12):
    out(f"{DIM}{label:<{label_width}}{RESET}  {TEAL}{value}{RESET}")


def bullet(text):
    out(f"  \u2022 {text}")


# ── Status list ───────────────────────────────────────────────────────────────

def status_list(stacklets):
    """Render a compact stacklet status table.

    Six states: online, starting, degraded, failing, stopped, available.
    Degraded stacklets show health issue hints.
    """
    if not stacklets:
        return

    ordered = sorted(stacklets, key=lambda s: (
        not s.get("online"), not s.get("starting"), not s.get("degraded"),
        not s.get("failing"), not s.get("enabled"), s.get("id", "")))

    nl()
    for s in ordered:
        name = s.get("name", s.get("id", ""))
        sid = s.get("id", "")
        label = f"{name} {DIM}({sid}){RESET}" if sid and sid != name else name
        # Pad based on visible text length, not the label with ANSI codes
        visible_len = len(name) + len(f" ({sid})") if sid and sid != name else len(name)
        col = 28
        pad = max(1, col - visible_len)
        if s.get("degraded"):
            out(f"  {ORANGE}\u26a0{RESET} {label}{' ' * pad} {ORANGE}degraded{RESET}")
            for issue in s.get("health_issues", []):
                out(f"      {ORANGE}{issue}{RESET}")
        elif s.get("online"):
            port = s.get("port")
            url = f"  {DIM}localhost:{port}{RESET}" if port else ""
            out(f"  {GREEN}\u2713{RESET} {label}{' ' * pad} {GREEN}online{RESET}{url}")
        elif s.get("starting"):
            out(f"  {TEAL}\u25cb{RESET} {label}{' ' * pad} {TEAL}starting{RESET}")
        elif s.get("failing"):
            out(f"  {RED}\u2717{RESET} {label}{' ' * pad} {RED}failing{RESET}")
        elif s.get("enabled"):
            out(f"  {ORANGE}\u25cb{RESET} {label}{' ' * pad} {ORANGE}stopped{RESET}")
        else:
            out(f"  {DIM}\u2022{RESET} {label}{' ' * pad} {DIM}available{RESET}")
    nl()


# ── Headings ──────────────────────────────────────────────────────────────────

def heading(msg):
    nl()
    bold(msg)
    out(f"{ORANGE}\u2500\u2500\u2500{RESET}")
    nl()


def section(name, description=""):
    """Stacklet or wizard step header — colored name, dim description."""
    nl()
    out(f"{ORANGE}\u2500\u2500{RESET} {ORANGE}{BOLD}{name}{RESET}")
    if description:
        out(f"   {TEAL}{description}{RESET}")
    nl()


def banner(product, subtitle=""):
    """Product branding — large name in stack orange."""
    nl()
    out(f"{ORANGE}{BOLD}{product}{RESET}")
    if subtitle:
        dim(subtitle)
    nl()


# ── Spinner ───────────────────────────────────────────────────────────────────

_BRAILLE = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"


class Spinner:
    """Minimal braille spinner for long operations."""

    def __init__(self, msg):
        self.msg = msg
        self.running = False
        self.thread = None
        self.ok = True

    def __enter__(self):
        self.running = True
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.running = False
        self.thread.join()
        icon = f"{GREEN}\u2713{RESET}" if self.ok else f"{RED}\u2717{RESET}"
        print(f"\r  {icon}  {self.msg}" + " " * 10)

    def fail(self):
        self.ok = False

    def _spin(self):
        for frame in itertools.cycle(_BRAILLE):
            if not self.running:
                break
            print(f"\r  {ORANGE}{frame}{RESET}  {self.msg}", end="", flush=True)
            time.sleep(0.08)


# ── Input ─────────────────────────────────────────────────────────────────────

def ask(prompt, default="", validate=None):
    """Text input with optional default and validation.

    Returns the entered string, or None on EOF.
    Ctrl+C propagates as KeyboardInterrupt.
    """
    if default:
        display = f"  {ORANGE}\u203a{RESET} {prompt} {DIM}[{default}]{RESET} "
    else:
        display = f"  {ORANGE}\u203a{RESET} {prompt} "

    while True:
        try:
            value = input(display).strip() or default
            if validate:
                err = validate(value)
                if err:
                    dim(f"  {err}")
                    continue
            return value
        except EOFError:
            return None


def confirm(question, default=True):
    """Yes/no with sensible default. Ctrl+C propagates as KeyboardInterrupt."""
    hint = "Y/n" if default else "y/N"
    try:
        answer = input(
            f"  {ORANGE}?{RESET} {question} {DIM}[{hint}]{RESET} "
        ).strip().lower()
        if not answer:
            return default
        return answer in ("y", "yes")
    except EOFError:
        return False


def choose(title, options, hint=None):
    """Single-selection menu. Returns selected index, or None on quit."""
    from simple_term_menu import TerminalMenu

    nl()
    bold(title)
    if hint:
        dim(hint)
    nl()

    menu = TerminalMenu(
        options,
        menu_cursor="\u203a ",
        menu_cursor_style=("fg_yellow", "bold"),
        menu_highlight_style=("fg_yellow", "bold"),
    )
    return menu.show()


def choose_many(title, options, preselected=None):
    """Multi-select menu. Returns tuple of selected indices, or None."""
    from simple_term_menu import TerminalMenu

    nl()
    bold(title)
    dim("space = toggle, enter = confirm")
    nl()

    menu = TerminalMenu(
        options,
        menu_cursor="\u203a ",
        menu_cursor_style=("fg_yellow", "bold"),
        menu_highlight_style=("fg_yellow", "bold"),
        multi_select=True,
        multi_select_cursor="[x] ",
        multi_select_cursor_brackets_style=("fg_yellow",),
        multi_select_select_on_accept=False,
        show_multi_select_hint=False,
        preselected_entries=preselected,
    )
    return menu.show()
