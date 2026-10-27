"""
famstack installer: the dependency checks, core, then the browser.

`./stack` on a new Mac checks Homebrew and Docker, writes stack.toml with
the time zone and language it detects, makes the admin password, starts
core and opens the dashboard, whose first run sets up the family. A script
installs with `stack install --family <file> --yes` instead, which asks
nothing at all and writes the family from the file. Both write the family
through `stack family set` (stack.family); everything else is added from
the dashboard or with `stack up`.
"""

import json
import os
import sys
import subprocess
from pathlib import Path

from .prompt import (
    ORANGE, TEAL, GREEN, RED, DIM, BOLD, RESET,
    clear, nl, out, dim, bold, done, warn,
    heading, banner, rule, Spinner, confirm, kv, status_list,
)
from .family import LANGUAGES


# ── Helpers ──────────────────────────────────────────────────────────────────

HELP_LINKS = (
    f"  {TEAL}Discord{RESET}  https://discord.com/invite/hfutdmmfBe",
    f"  {TEAL}GitHub{RESET}   https://github.com/famstack-dev/famstack",
    f"  {TEAL}Email{RESET}    hello@famstack.dev",
)


def get_help_text(detail=""):
    """Format an error footer with optional detail and help links."""
    lines = []
    if detail:
        lines.append("")
        for line in detail.split("\n"):
            lines.append(f"  {DIM}{line}{RESET}")
    lines.append("")
    lines.append("  Sorry for that. If it keeps happening, we'd love to help.")
    lines.append("  Please reach out so we can fix it faster:")
    lines.append("")
    lines.extend(HELP_LINKS)
    lines.append("")
    return "\n".join(lines)


def fail(msg, detail=""):
    """Print an error message with help links and exit."""
    nl()
    out(f"{RED}✗{RESET}  {msg}")
    print(get_help_text(detail))
    sys.exit(1)


def get_repo_root():
    here = Path(__file__).parent
    for candidate in [here.parent.parent, here.parent, Path.cwd()]:
        if (candidate / "stacklets").is_dir():
            return candidate
    return Path.cwd()


REPO_ROOT = get_repo_root()


def detect_timezone():
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/")[1]
    except Exception:
        pass
    return "UTC"


# Map timezone to primary language. Only de and en for now --
# taxonomy.yaml only has these two. Add more as we add translations.
_TZ_LANGUAGE = {
    "Europe/Berlin": "de", "Europe/Vienna": "de", "Europe/Zurich": "de",
}


def detect_language(timezone: str) -> str:
    """Guess the instance language from timezone. Defaults to English."""
    return _TZ_LANGUAGE.get(timezone, "en")


# (min_ram_gb, model_id, label)
# Qwen3.6 only shipped a 27B dense and a 35B-A3B MoE — no small variant — so
# the lightweight tier stays on Qwen3.5-9B (3.6's smallest 4bit is 16 GB of
# weights, too big for a 16 GB Mac).
MODEL_TIERS = [
    (32, "unsloth/Qwen3.6-35B-A3B-UD-MLX-4bit", "32 GB+ RAM"),
    (0,  "mlx-community/Qwen3.5-9B-MLX-4bit",    "16 GB+ RAM — lightweight"),
]


def _detect_ram_gb() -> float:
    try:
        ram_bytes = int(subprocess.check_output(
            ["sysctl", "-n", "hw.memsize"]).strip())
    except Exception:
        ram_bytes = 0
    return ram_bytes / (1024 ** 3)


def detect_default_model():
    """Pick a default LLM model based on system RAM."""
    ram_gb = _detect_ram_gb()
    for min_ram, model_id, _label in MODEL_TIERS:
        if ram_gb >= min_ram:
            return model_id
    return MODEL_TIERS[-1][1]


def _create_stack():
    """Create a Stack instance from the repo root."""
    from .cli import create_stack
    return create_stack(REPO_ROOT)


def _install_global_command(stck) -> str:
    """Put `famstack` on PATH and return what to tell the admin to type.

    `famstack` when it now runs this checkout, `./stack` when another
    command of that name is in the way or there is nowhere to put it.
    The final screen prints whichever works on this Mac.
    """
    from . import global_command
    directory = global_command.bin_dir()
    if directory and global_command.install(stck.root, directory):
        return global_command.NAME
    return "./stack"


def _has_brew() -> bool:
    import shutil
    return shutil.which("brew") is not None


def _ensure_brew():
    if _has_brew():
        done("Homebrew installed")
        return

    # PATH is inherited from the parent shell at process start, so a brew
    # install completed in another window won't show up here even after a
    # "check again". Exit and have the user re-launch in a fresh tab.
    warn("Homebrew is not installed.")
    nl()
    out("famstack uses Homebrew to install dependencies.")
    out("Install it with this one-liner:")
    nl()
    dim('  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
    nl()
    out(f"Then open a {ORANGE}new terminal tab{RESET} ({BOLD}⌘T{RESET}) and re-run:")
    nl()
    dim("  ./stack")
    nl()
    sys.exit(1)


def _ensure_docker():
    from .docker import check_docker, init_runtime

    _, runtime_warn = init_runtime()
    ok, err = check_docker()

    if ok:
        done("Docker is running")
        if runtime_warn:
            warn(runtime_warn)
        return

    if "not installed" in (err or "").lower():
        warn("Docker is not installed.")
        nl()
        out("famstack needs a container runtime. We recommend OrbStack —")
        out("it's fast, lightweight, and built for macOS.")
        nl()

        if confirm("Install OrbStack via Homebrew?"):
            nl()
            subprocess.run(["brew", "install", "--cask", "orbstack"], timeout=300)
            nl()
            out("OrbStack is installed. Opening it for the first-time setup...")
            subprocess.run(["open", "-a", "OrbStack"], timeout=10)
            nl()
            out("Finish OrbStack's setup (allow Docker integration when it asks).")
            out(f"Then open a {ORANGE}new terminal tab{RESET} ({BOLD}⌘T{RESET}) and re-run:")
            nl()
            dim("  ./stack")
            nl()
            sys.exit(1)

        raise KeyboardInterrupt

    warn("Docker is not running.")
    nl()
    out("Start OrbStack from your Applications folder or menu bar.")
    nl()
    _wait_for_docker()


def _wait_for_docker():
    import time
    from .docker import check_docker, init_runtime

    for _ in range(5):
        time.sleep(2)
        init_runtime()
        ok, _ = check_docker()
        if ok:
            done("Docker is running")
            return

    while True:
        if not confirm("Check again?"):
            raise KeyboardInterrupt
        init_runtime()
        ok, _ = check_docker()
        if ok:
            done("Docker is running")
            return
        warn("Still not running.")
        nl()


# ── Config writers ───────────────────────────────────────────────────────────

def _model_comments(chosen: str) -> str:
    """Render model alternatives as TOML comments."""
    lines = ["# Models by RAM tier — uncomment one to switch, then run:"]
    lines.append("#   ./stack setup ai")
    lines.append("#   ./stack ai download <model>")
    for _min_ram, model_id, label in MODEL_TIERS:
        if model_id == chosen:
            lines.append(f'default = "{model_id}"  # ← {label} (selected)')
        else:
            lines.append(f'# default = "{model_id}"  # {label}')
    return "\n".join(lines)


def write_stack_toml(family_name, server_name, timezone, language="en", root=None):
    """Write a minimal stack.toml — just enough for messages.

    Into `root`, the instance directory, or the checkout's when not given.
    Empty names leave them for `stack family set` to fill in.
    """
    default_model = detect_default_model()
    model_block = _model_comments(default_model)
    content = f'''# stack.toml — generated by famstack installer
#
# Edit freely — this file is gitignored and won't conflict with updates.
# Run 'stack up <stacklet>' after changes to apply them.

[core]
# Product name. Drives CLI branding and the default paths derived from it
# (~/<name>-extensions). Without it the instance calls itself "stack".
name = "famstack"
stack_owner = "{family_name}"
# Your family's own web address, if it has one (smiths.com). It names the
# chat server and new people's email addresses. `stack family` shows it.
# Not the same as `domain` below, which serves the stack under a domain.
family_domain = ""
domain = ""
# Hostname used in port-mode URLs and the `{{ip}}` template var. Empty
# = auto-detect this machine's LAN IP (the LAN-friendly default).
# Set to "localhost" for single-machine testing, or to a custom
# hostname like "mac-mini.local" when the LAN IP shifts.
host = ""
data_dir = "~/famstack-data"
timezone = "{timezone}"
language = "{language}"
# Slug for the bucket inside the memory vault that holds institutional
# artifacts (documents, correspondents). Personal entities live at
# <vault>/<localpart>/; the shared bucket lives at <vault>/<slug>/.
# Defaults to "family"; rename for a non-family deployment ("office"), surname-based
# households, etc.
shared_bucket = "family"

[updates]
schedule = "0 0 3 * * *"

[ai]
provider = ""
openai_url = ""
openai_key = ""
# The language the stack speaks to you: the voice that reads answers
# aloud. Starts as the family language; it can differ. Transcription and
# document tags follow [core] language.
language = "{language}"
{model_block}

[messages]
# Permanent: every Matrix user ID ends with it (@you:server_name).
server_name = "{server_name}"
'''
    path = (root or REPO_ROOT) / "stack.toml"
    path.write_text(content)


# ── Existing config ──────────────────────────────────────────────────────────

def run_stack(*args):
    """Run a stack CLI command, return (success, output)."""
    cmd = [sys.executable, "-m", "stack"] + list(args)
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "lib")}
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                            cwd=str(REPO_ROOT), env=env)
    return result.returncode == 0, (result.stdout + result.stderr).strip()



def show_existing_config():
    """When config already exists, show what's there and how to change it."""
    import tomllib

    clear()
    banner("famstack")
    out("You already have a configuration. Here's what's set up:")
    nl()

    # stack.toml
    stack_path = REPO_ROOT / "stack.toml"
    try:
        with open(stack_path, "rb") as f:
            cfg = tomllib.load(f)
    except Exception:
        cfg = {}

    core = cfg.get("core", {})
    ai = cfg.get("ai", {})
    messages = cfg.get("messages", {})

    bold("  stack.toml")
    kv("Timezone", core.get("timezone", "UTC"))
    kv("Data dir", core.get("data_dir", "~/famstack-data"))
    if core.get("domain"):
        kv("Domain", core["domain"])
    if ai.get("openai_url"):
        kv("AI server", ai["openai_url"])
    if core.get("language"):
        kv("Language", core["language"])
    if ai.get("language"):
        kv("Voice", ai["language"])
    if messages.get("server_name"):
        kv("Chat server", messages["server_name"])
    nl()

    # users.toml
    users_path = REPO_ROOT / "users.toml"
    if users_path.exists():
        try:
            with open(users_path, "rb") as f:
                users_cfg = tomllib.load(f)
            users = users_cfg.get("users", [])
            out(f"  {BOLD}users.toml{RESET}")
            for u in users:
                role = f" {DIM}(admin){RESET}" if u.get("role") == "admin" else ""
                out(f"  {u.get('name', '?')}{role}  {DIM}{u.get('email', '')}{RESET}")
            nl()
        except Exception:
            pass

    # All stacklets and their status
    ok, output = run_stack("list", "--json")
    if ok:
        try:
            data = json.loads(output)
            status_list(data.get("stacklets", []))
        except Exception:
            pass

    # How to change things
    heading("To make changes")
    out(f"Edit {TEAL}stack.toml{RESET} to change settings like timezone or AI language.")
    out("Changes take effect on the next 'stack up'.")
    out(f"To use another AI server, run {TEAL}stack ai switch <url>{RESET}.")
    nl()
    out(f"Edit {TEAL}users.toml{RESET} to add or remove family members.")
    out("New accounts are created on the next 'stack up' for each service.")
    nl()
    out(f"To start fresh, run {TEAL}stack uninstall{RESET} and then {TEAL}stack install{RESET}.")
    nl()

    heading("Common commands")
    out(f"  {TEAL}stack up <name>{RESET}      Start a service")
    out(f"  {TEAL}stack down <name>{RESET}    Stop a service")
    out(f"  {TEAL}stack status{RESET}         See what's running")
    out(f"  {TEAL}stack destroy <name>{RESET} Remove a service and its data")
    out(f"  {TEAL}stack logs <name>{RESET}    View logs")
    nl()
    dim("Need help?")
    dim("  https://github.com/famstack-dev/famstack/issues")
    dim("  https://discord.gg/hfutdmmfBe")
    nl()


# ── Install from a file ──────────────────────────────────────────────────────

def install_from_file(stck, path: Path, yes: bool) -> int:
    """`stack install --family <file> --yes`: the installer's steps, asking nothing.

    For scripts, provisioning tools and anything else without a terminal.
    The steps are the wizard's without its questions: the dependency
    checks, stack.toml as the installer writes it (time zone and language
    detected, as the wizard proposes them), the admin password, the family
    through `stack family set`, and core, which serves the dashboard.
    Messages is left to the dashboard's setup, where its server name is
    the one permanent choice.

    Nothing is written until the file and the checks pass, and a check
    that fails says what to do instead of offering to do it. Run again,
    it changes nothing. Without `yes` it asks one confirmation, and
    refuses where there is no terminal to ask in. Returns the exit code.
    """
    from . import family
    from .users import family_display_name

    try:
        text = path.read_text()
    except OSError as e:
        return _stop(f"Could not read {path}: {e.strerror}")
    data, found = family.load(text, path.name)
    if data is None or found:
        return _stop("Nothing was written: the family file has problems.", family.describe(found, data))
    name = family_display_name(data["name"])
    if not yes:
        if not sys.stdin.isatty():
            return _stop("There is no terminal to confirm in: add --yes to install without asking.")
        if not confirm(f"Install famstack for {name}?"):
            dim("Cancelled. Nothing was changed.")
            return 1
    if problem := _missing_dependency():
        return _stop(problem)

    stck = _write_config(stck)
    result = family.set_family(stck, data)
    if "error" in result:
        return _stop(result["error"], family.describe(result["problems"], data))
    if problem := _start_core(stck):
        return _stop(*problem)

    nl()
    done(f"{name} are set up, and core is running.")
    out(f"The dashboard: {TEAL}{stck._home_url()}/dashboard{RESET}")
    nl()
    return 0


def _write_config(stck):
    """stack.toml as the installer writes it, unless there is one, and the
    admin password, unless there is one. Returns the Stack to go on with:
    the data dir is read from stack.toml when a Stack is made, and there
    may have been none when `stck` was."""
    if not (stck.instance_dir / "stack.toml").exists():
        timezone = detect_timezone()
        write_stack_toml("", "", timezone, detect_language(timezone), root=stck.instance_dir)
    from .cli import create_stack
    stck = create_stack(stck.root, stck.instance_dir)
    if not stck.secrets.get("global", "ADMIN_PASSWORD"):
        stck.secrets.set("global", "ADMIN_PASSWORD", new_admin_password())
    return stck


def _start_core(stck) -> tuple[str, list[str]] | None:
    """The Docker network, the data dir and core. None when core runs, else
    what went wrong and what to do, for `_stop`."""
    from .cli import CLI
    from .docker import ensure_network
    _, err = ensure_network()
    if err:
        return "Could not create the Docker network.", [err]
    stck.data.mkdir(parents=True, exist_ok=True)
    started = CLI(stck).up("core")
    if not started.get("ok"):
        return (f"Core did not start: {started.get('error', 'unknown error')}",
                ["Run 'stack logs core' to see why, then 'stack up core' to try again."])
    # Up but not healthy is a finished run for `stack up`; here the dashboard
    # is the point, and its address is given, or the browser opened, only
    # once it answers (core's health check).
    if started.get("healthy") is False:
        return ("Core started, but its dashboard does not answer.",
                ["Run 'stack logs core' to see why, then 'stack up core' to try again."])
    return None


def new_admin_password() -> str:
    """A new admin password. It starts with a letter, because one starting
    with a dash breaks tools that read it as a flag (register_new_matrix_user)."""
    import secrets
    return "s" + secrets.token_urlsafe(8)


def _missing_dependency() -> str | None:
    """What stops an install that cannot ask: Homebrew or Docker missing, or Docker stopped."""
    if not _has_brew():
        return ("Homebrew is not installed. Install it as https://brew.sh says, "
                "then run this again in a new terminal.")
    from .docker import check_docker, init_runtime
    init_runtime()
    ok, err = check_docker()
    if ok:
        return None
    if "not installed" in (err or "").lower():
        return ("Docker is not installed. Install OrbStack with 'brew install --cask orbstack', "
                "open it once, then run this again.")
    return "Docker is not running. Start OrbStack, then run this again."


def _stop(message: str, lines=()) -> int:
    """Say why the install stopped, on stderr, and return its exit code."""
    print(f"\n  {RED}\u2717{RESET}  {message}", file=sys.stderr)
    for line in lines:
        print(f"      {line}", file=sys.stderr)
    print(file=sys.stderr)
    return 1


# ── In a terminal ───────────────────────────────────────────────────────────

# Where the dashboard is on this Mac: core's tools server publishes it there
# in every mode (core/docker-compose.yml), and a browser on this Mac reaches
# it without knowing the LAN address.
DASHBOARD_HERE = "http://localhost:42000/dashboard"


def install():
    """`./stack` on a new Mac: the dependency checks, core, then the browser.

    It asks nothing but what the checks need (installing OrbStack, starting
    Docker). It writes stack.toml with the time zone and the language it
    detects, makes the admin password, starts core and opens the dashboard
    on this Mac, where the first run sets up the family (`stack family set`
    through the host API). Run again, it starts what is missing and opens
    the dashboard again. Returns None.
    """
    if (REPO_ROOT / "stack.toml").exists() and (REPO_ROOT / "users.toml").exists():
        show_existing_config()
        return None

    clear()
    banner("famstack", "Your family's private server")
    out("Everything stays on your network. Nothing leaves this machine.")
    nl()
    warn(f"{RED}{BOLD}Local network only.{RESET} famstack runs plain HTTP and trusts your LAN.")
    out("   Never install it on a machine reachable from the public internet:")
    out("   no VPS, no forwarded ports. For remote access, use a VPN (e.g. Tailscale).")
    nl()

    _ensure_brew()
    _ensure_docker()
    nl()

    stck = _create_stack()
    with Spinner("Writing stack.toml and the admin password"):
        stck = _write_config(stck)
    language = stck._cfg("core", "language", "en")
    dim(f"Time zone {stck._cfg('core', 'timezone')}, language {LANGUAGES.get(language, language)}. "
        "The browser's language decides on the first run.")
    nl()

    out(f"  Bringing up {TEAL}Core{RESET}...\n")
    if problem := _start_core(stck):
        fail(problem[0], "\n".join(problem[1]))
    command = _install_global_command(stck)

    _open(DASHBOARD_HERE)
    clear()
    nl()
    out(f"{ORANGE}{BOLD}famstack{RESET}")
    out(f"{GREEN}Core is running.{RESET} The rest happens in your browser.")
    nl()
    rule()
    nl()
    bold("Your dashboard, on this Mac")
    out(f"   {BOLD}{TEAL}{DASHBOARD_HERE}{RESET}")
    dim(f"   From another computer: {stck._home_url()}/dashboard")
    nl()
    out("   It asks your name, then who else is at home.")
    nl()
    rule()
    nl()
    out(f"  {TEAL}{command} status{RESET}   See what's running")
    out(f"  {TEAL}{command} family{RESET}   See the family once it is set up")
    nl()
    return None


def _open(url: str) -> None:
    """Open `url` in this Mac's browser; the address is printed either way."""
    subprocess.run(["open", url], timeout=10, check=False)
