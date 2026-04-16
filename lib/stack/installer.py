#!/usr/bin/env python3
"""
famstack installer — interactive config wizard.

Gathers preferences, writes stack.toml and users.toml, runs stack init,
then tells the user what to do next. Does NOT start stacklets — the user
runs `stack up <name>` for each one, in their own time.
"""

import os
import secrets
import subprocess
import sys
import json
from pathlib import Path

from .prompt import (
    ORANGE, TEAL, GREEN, RED, DIM, BOLD, RESET,
    clear, nl, out, dim, bold, done, warn,
    heading, section, banner, rule, kv,
    Spinner, ask, confirm, choose, choose_many,
)


# ── Validation ────────────────────────────────────────────────────────────────

def validate_email(value):
    if not value:
        return "Email is required"
    if "@" not in value:
        return "Needs an @ symbol"
    return None


def validate_name(value):
    if not value or len(value) < 2:
        return "Name is required"
    return None


# ── Stacklets ─────────────────────────────────────────────────────────────────

def load_stacklets(repo_root):
    """Load stacklet definitions from stacklet.toml files."""
    from ._compat import tomllib

    stacklets_dir = repo_root / "stacklets"
    stacklets = {}
    core = None

    for path in stacklets_dir.glob("*/stacklet.toml"):
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)

            sid = data.get("id")
            if not sid:
                continue

            info = {
                "name": data.get("name", sid),
                "description": data.get("description", ""),
                "category": data.get("category", "other"),
                "always_on": data.get("always_on", False),
            }

            if data.get("always_on"):
                core = info
            else:
                stacklets[sid] = info

        except Exception:
            continue

    return stacklets, core


def get_repo_root():
    """Find repo root by looking for stacklets/ directory."""
    here = Path(__file__).parent
    for candidate in [here.parent, here.parent.parent, Path.cwd()]:
        if (candidate / "stacklets").is_dir():
            return candidate
    return Path.cwd()


REPO_ROOT = get_repo_root()
STACKLETS, CORE = load_stacklets(REPO_ROOT)


# ── Helpers ───────────────────────────────────────────────────────────────────

def detect_timezone():
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/")[1]
    except Exception:
        pass
    return "UTC"


def email_from_name(name):
    first = name.split()[0] if name else "user"
    clean = "".join(c for c in first.lower() if c.isalnum())
    return f"{clean}@home.local"


def generate_password():
    """Human-friendly password for local services."""
    words = ["sun", "moon", "star", "rain", "wind", "leaf", "tree", "bird",
             "fish", "wave", "fire", "lake", "hill", "rock", "sand", "snow"]
    return "-".join(secrets.choice(words) for _ in range(3))


def _has_brew() -> bool:
    """Check if Homebrew is installed."""
    import shutil
    return shutil.which("brew") is not None


def _ensure_brew():
    """Verify Homebrew is installed. Guide the user if not."""
    if _has_brew():
        done("Homebrew installed")
        return

    warn("Homebrew is not installed.")
    nl()
    out("famstack uses Homebrew to install dependencies.")
    out("Install it with this one-liner:")
    nl()
    dim('  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
    nl()

    while True:
        if not confirm("Check again?"):
            raise KeyboardInterrupt
        if _has_brew():
            done("Homebrew installed")
            return
        warn("Still not found.")
        nl()


def _ensure_docker():
    """Verify Docker (OrbStack) is installed and running.

    Checks in order: installed → running. Offers to install via brew
    if missing, guides the user to start it if stopped.
    """
    from .docker import check_docker, init_runtime

    _, runtime_warn = init_runtime()
    ok, err = check_docker()

    if ok:
        done("Docker is running")
        if runtime_warn:
            warn(runtime_warn)
        return

    # ── Not installed — offer brew install ───────────────────────
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

            # OrbStack needs to be launched once to set up Docker
            out("OrbStack is installed. Starting it for the first time...")
            dim("This opens OrbStack so it can set up Docker integration.")
            nl()
            subprocess.run(["open", "-a", "OrbStack"], timeout=10)

            # Wait for Docker to become available
            _wait_for_docker()
            return

        raise KeyboardInterrupt

    # ── Installed but not running ────────────────────────────────
    warn("Docker is not running.")
    nl()
    out("Start OrbStack from your Applications folder or menu bar.")
    nl()

    _wait_for_docker()


def _wait_for_docker():
    """Poll until Docker is running, with user prompts."""
    import time
    from .docker import check_docker, init_runtime

    # Give it a moment to start up
    for _ in range(5):
        time.sleep(2)
        init_runtime()
        ok, _ = check_docker()
        if ok:
            done("Docker is running")
            return

    # Still not up — ask the user
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


def _announce(service_name: str):
    """Short voice announcement after a successful stacklet setup.

    Silently does nothing if TTS isn't available yet.
    """
    try:
        sys.path.insert(0, str(REPO_ROOT / "stacklets" / "ai"))
        from speech import speak
        speak(f"{service_name} is ready.")
    except Exception:
        pass


def run_stack(*args):
    """Run a stack CLI command, return (success, output)."""
    cmd = [sys.executable, "-m", "stack"] + list(args)
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "lib")}
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                            cwd=str(REPO_ROOT), env=env)
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def run_stack_live(*args, confirmed=False):
    """Run a stack CLI command with live output and stdin passthrough.

    confirmed=True sets STACK_SETUP_CONFIRMED=1 so stacklet configure
    hooks skip interactive prompts the installer already handled.

    Returns True on success, False on failure.
    """
    cmd = [sys.executable, "-m", "stack"] + list(args)
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "lib")}
    if confirmed:
        env["STACK_SETUP_CONFIRMED"] = "1"
    result = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env)
    return result.returncode == 0


def print_status(only=None):
    """Show compact status of stacklets. Pass only=set to filter."""
    from .prompt import status_list
    ok, output = run_stack("list", "--json")
    if not ok:
        return
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return
    stacklets = data.get("stacklets", [])
    if only is not None:
        stacklets = [s for s in stacklets if s.get("id") in only]
    status_list(stacklets)


# ── Config writers ────────────────────────────────────────────────────────────

def write_stack_toml(config):
    """Write stack.toml from gathered config."""
    tz = config["timezone"]
    data_dir = config["data_dir"]
    language = config["language"]
    provider = config.get("provider", "")
    openai_url = config.get("openai_url", "")
    openai_key = config.get("openai_key", "")

    content = f'''# stack.toml — generated by famstack installer
#
# Edit freely — this file is gitignored and won't conflict with updates.
# Run 'stack up <stacklet>' after changes to apply them.

[core]
domain = ""
data_dir = "{data_dir}"
timezone = "{tz}"
language = "{language}"

[updates]
schedule = "0 0 3 * * *"

[ai]
provider = "{provider}"
openai_url = "{openai_url}"
openai_key = "{openai_key}"
whisper_url = "http://localhost:42062/v1"
language = "{language}"
default = "Qwen3.5-9B-MLX-8bit"
'''
    path = REPO_ROOT / "stack.toml"
    path.write_text(content)


def write_users_toml(users):
    """Write users.toml from the gathered user list."""
    from .users import user_id
    lines = [
        "# users.toml — generated by famstack installer",
        "#",
        "# The admin account is used for all services (Photos, Docs, Chat, etc.).",
        "# Family members get their own accounts where supported.",
        "",
    ]

    for u in users:
        lines.append("[[users]]")
        lines.append(f'id       = "{user_id(u)}"')
        lines.append(f'name     = "{u["name"]}"')
        lines.append(f'email    = "{u["email"]}"')
        lines.append(f'role     = "{u["role"]}"')
        lines.append(f'stacklets = ["photos", "docs", "messages"]')
        lines.append("")

    path = REPO_ROOT / "users.toml"
    path.write_text("\n".join(lines))


# ── Wizard ────────────────────────────────────────────────────────────────────

def show_existing_config():
    """When config already exists, show what's there and how to change it."""
    from ._compat import tomllib

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
        kv("LLM", ai["openai_url"])
    if ai.get("language"):
        kv("AI language", ai["language"])
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
    from .prompt import status_list
    ok, output = run_stack("list", "--json")
    if ok:
        try:
            data = json.loads(output)
            status_list(data.get("stacklets", []))
        except Exception:
            pass

    # How to change things
    heading("To make changes")
    out(f"Edit {TEAL}stack.toml{RESET} to change settings like timezone, LLM endpoint,")
    out(f"or AI language. Changes take effect on the next 'stack up'.")
    nl()
    out(f"Edit {TEAL}users.toml{RESET} to add or remove family members.")
    out(f"New accounts are created on the next 'stack up' for each service.")
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
    dim("  https://discord.gg/rwyrBRun")
    nl()


def wizard():
    # If config already exists, show it instead of re-running the wizard
    has_config = (REPO_ROOT / "stack.toml").exists()
    has_users = (REPO_ROOT / "users.toml").exists()
    if has_config and has_users:
        show_existing_config()
        return None

    clear()

    # ── Welcome ────────────────────────────────────────────────────────────

    banner("famstack", "Your family's private server")
    out("Photos, messages, documents, and AI that runs entirely")
    out("on your Mac. No cloud, no subscriptions.")
    nl()

    _ensure_brew()
    _ensure_docker()
    nl()

    out("This wizard sets up your configuration, then walks you")
    out("through each service one by one.")
    nl()

    if not confirm("Ready?"):
        return None

    # ── You ────────────────────────────────────────────────────────────────

    section("About You", "Create your admin account")

    out("This creates your admin account. You'll use it to log into")
    out("Photos, Chat, and other services.")
    nl()

    name = ask("Your name", validate=validate_name)
    if not name:
        return None

    email = ask("Email", default=email_from_name(name), validate=validate_email)
    dim("Used as login username. Doesn't need to be a real email.")
    if not email:
        return None

    from .users import user_id
    admin = {"name": name, "email": email, "role": "admin"}
    done(f"{name} (id: {user_id(admin)})")

    # ── Family ─────────────────────────────────────────────────────────────

    section("Family", "Add family member accounts")
    out("Add family members now and we create their accounts")
    out("in Photos, Chat, and other services automatically.")
    dim("You can always add more people later in users.toml.")
    nl()

    users = [admin]

    while True:
        member_name = ask("Name (leave empty to continue)", validate=None)
        if not member_name:
            break

        member_email = ask("Email", default=email_from_name(member_name), validate=validate_email)
        if not member_email:
            break

        member = {"name": member_name, "email": member_email, "role": "member"}
        users.append(member)
        done(f"{member_name} (id: {user_id(member)})")
        nl()

    # ── Services ───────────────────────────────────────────────────────────

    section("Services", "Pick what to set up")
    out("You can always add more later with 'stack up <name>'.")
    nl()

    if CORE:
        dim(f"{CORE['name']} is always included ({CORE['description']})")
        nl()

    INSTALL_STACKLETS = ["photos", "messages", "docs", "ai", "chatai", "bots"]
    stacklet_ids = [sid for sid in INSTALL_STACKLETS if sid in STACKLETS]
    options = [
        f"{STACKLETS[sid]['name']}  {STACKLETS[sid]['description']}"
        for sid in stacklet_ids
    ]

    preselected = list(range(len(stacklet_ids)))
    selected = choose_many("Services", options, preselected=preselected)

    if selected is None:
        selected = ()

    chosen = [stacklet_ids[i] for i in selected]

    # Auto-add dependencies
    STACKLET_DEPS = {"bots": ["messages", "ai"]}
    for sid in list(chosen):
        for dep in STACKLET_DEPS.get(sid, []):
            if dep not in chosen:
                chosen.append(dep)
                dim(f"Adding {STACKLETS[dep]['name']} (needed by {STACKLETS[sid]['name']})")

    if chosen:
        nl()
        for sid in chosen:
            done(STACKLETS[sid]["name"])

    # ── Summary ────────────────────────────────────────────────────────────

    timezone = detect_timezone()
    data_dir = "~/famstack-data"

    clear()
    banner("famstack", "Here's what we'll set up")

    # Services
    rule()
    if CORE:
        dim(f"  {CORE['name']}")
    for sid in chosen:
        s = STACKLETS[sid]
        out(f"  {BOLD}{s['name']}{RESET}  {DIM}{s['description']}{RESET}")
    if not chosen:
        dim("  No services selected")
    rule()
    nl()

    kv("Timezone", timezone)
    kv("Data", data_dir)
    nl()

    for u in users:
        if u["role"] == "admin":
            out(f"  {BOLD}{u['name']}{RESET}  {DIM}{u['email']}  (admin){RESET}")
        else:
            out(f"  {u['name']}  {DIM}{u['email']}{RESET}")
    nl()

    rule()
    nl()
    out("This saves to stack.toml and users.toml in your famstack directory.")
    out("After that, you'll choose which services to set up.")
    nl()

    if not confirm("Save configuration?"):
        dim("Cancelled — nothing was changed.")
        return None

    # ── Write config ──────────────────────────────────────────────────────

    clear()
    section("Writing Configuration", "Saving your choices")
    nl()

    language = "en"
    ai_config = {}

    config = {
        "timezone": timezone,
        "data_dir": data_dir,
        "language": language,
        **ai_config,
    }

    with Spinner("Writing stack.toml"):
        write_stack_toml(config)

    with Spinner("Writing users.toml"):
        write_users_toml(users)

    with Spinner("Writing secrets"):
        # Default passwords match the user ID (e.g. arthur/arthur).
        # Simple and frictionless for a local network setup. Users can
        # change their passwords in each service after first login.
        from .secrets import TomlSecretStore
        from .users import user_id, password_key
        secrets = TomlSecretStore(REPO_ROOT / ".stack" / "secrets.toml")
        for u in users:
            secrets.set("global", password_key(u), user_id(u))
        secrets.set("global", "ADMIN_PASSWORD", user_id(admin))

    with Spinner("Initializing infrastructure") as sp:
        ok, output = run_stack("init", "--json")
        if not ok:
            sp.fail()
            nl()
            warn("Infrastructure setup had issues:")
            for line in output.split("\n")[-3:]:
                dim(f"  {line}")
            nl()

    # ── Set up stacklets ─────────────────────────────────────────────────

    nl()
    rule()
    nl()
    bold("Configuration complete!")
    nl()
    out("Now let's set up your services. Each one takes a minute or two.")
    out("You can skip any and run 'stack up <name>' later.")

    install_order = ["ai", "messages", "docs", "photos", "chatai", "bots"]
    ordered = [sid for sid in install_order if sid in chosen]
    for sid in chosen:
        if sid not in ordered:
            ordered.append(sid)

    STACKLET_INTRO = {
        "ai": (
            "The AI engine behind famstack. Runs large language models,",
            "speech-to-text, and text-to-speech entirely on your Mac.",
            "Other services use this for voice transcription and smart features.",
        ),
        "messages": (
            "Private family WhatsApp-like chat powered by Matrix. With mobile app.",
            "Send messages, photos, and voice notes from any device.",
            "Other services send notifications here too. Its the backbone of famstack.",
        ),
        "docs": (
            "Drop a PDF, scan, or photo of a document and it gets digitized,",
            "categorized, and made searchable. Tax returns, school letters,",
            "receipts — everything in one place, findable in seconds.",
        ),
        "photos": (
            "Back up photos from every phone automatically and browse",
            "by person or place. Like Google Photos, but on your own hardware.",
        ),
        "chatai": (
            "A browser-based chat interface for your local AI. Ask questions,",
            "have conversations, or talk to it. Like ChatGPT, but private",
            "and running on your Mac.",
        ),
        "bots": (
            "Small AI-powered helpers that live in your family chat.",
            "Send a document and it gets filed. Send a voice message",
            "and it gets transcribed. More bots are coming.",
        ),
    }

    set_up = []
    skipped = []

    for sid in ordered:
        sname = STACKLETS[sid]["name"]
        desc = STACKLETS[sid]["description"]

        print_status(only={"core"} | set(chosen))
        section(sname, desc)

        intro = STACKLET_INTRO.get(sid)
        if intro:
            for line in intro:
                out(line)
            nl()

        if not confirm(f"Set up {sname}?"):
            skipped.append(sid)
            dim(f"Skipped — run 'stack up {sid}' when you're ready.")
            continue

        if sid == "ai":
            # ── Language ────────────────────────────────────────────
            section("AI Language", "Voice and speech recognition language")
            out("This controls the voice the AI uses to speak and the")
            out("default language for speech recognition.")
            dim("English works best for now. Other languages are experimental.")
            nl()
            idx = choose("What language should the AI speak?", ["English", "Deutsch (experimental)"])
            language = "de" if idx == 1 else "en"
            nl()
            done(f"{'Deutsch' if language == 'de' else 'English'}")
            config["language"] = language

            # ── LLM provider ───────────────────────────────────────
            section("LLM Provider", "Where does the language model run?")
            dim("Advanced: if you already run your own OpenAI-compatible LLM")
            dim("endpoint, you can use that instead of oMLX.")
            nl()

            if confirm("Bring your own LLM endpoint?", default=False):
                endpoint_ok = False
                while not endpoint_ok:
                    nl()
                    out("Enter the URL of your OpenAI-compatible endpoint.")
                    dim("Examples: https://api.openai.com/v1, http://192.168.1.50:11434/v1")
                    nl()
                    ep_url = ask("Endpoint URL")
                    if not ep_url:
                        break
                    ep_url = ep_url.strip().rstrip("/")
                    if not ep_url.startswith("http"):
                        ep_url = f"http://{ep_url}"
                    if not ep_url.endswith("/v1"):
                        ep_url = f"{ep_url}/v1"

                    ep_key = ask("API key (leave empty if none)")
                    ep_key = ep_key.strip() if ep_key else ""

                    sys.path.insert(0, str(REPO_ROOT / "stacklets" / "ai"))
                    from backend import _probe
                    probe = _probe(ep_url, ep_key)
                    if probe.reachable:
                        done(f"Connected to {ep_url}")
                        config["provider"] = "external"
                        config["openai_url"] = ep_url
                        config["openai_key"] = ep_key
                        if ep_key:
                            from .secrets import TomlSecretStore
                            secrets_store = TomlSecretStore(REPO_ROOT / ".stack" / "secrets.toml")
                            secrets_store.set("ai", "AI_API_KEY", ep_key)
                        endpoint_ok = True
                    else:
                        warn(f"Cannot reach {ep_url}")
                        out("Check the URL and make sure the server is running.")
                        nl()
                        if not confirm("Try again?"):
                            break

                if not endpoint_ok:
                    nl()
                    out("No worries — we'll set up oMLX for you instead.")
                    config["provider"] = "managed"
            else:
                config["provider"] = "managed"

            nl()
            write_stack_toml(config)

        nl()
        ok = run_stack_live("up", sid, confirmed=True)
        nl()

        if ok:
            set_up.append(sid)
            _announce(sname)
        else:
            warn(f"{sname} had issues — check 'stack logs {sid}' for details.")
            nl()

    # ── Done ──────────────────────────────────────────────────────────

    clear()
    banner("famstack")
    print_status(only={"core"} | set(chosen))

    if set_up:
        rule()
        nl()
        bold(f"{len(set_up)} service{'s' if len(set_up) != 1 else ''} running")
        nl()

    not_selected = [sid for sid in STACKLETS if sid not in chosen and sid not in skipped]
    all_skipped = skipped + not_selected
    if all_skipped:
        out("Set up more services any time:")
        for sid in all_skipped:
            out(f"  {TEAL}stack up {sid}{RESET}")
        nl()

    heading("Useful commands")
    out(f"  {TEAL}stack status{RESET}         See what's running")
    out(f"  {TEAL}stack down <name>{RESET}    Stop a service")
    out(f"  {TEAL}stack logs <name>{RESET}    View logs")
    out(f"  {TEAL}stack destroy <name>{RESET} Remove a service and its data")
    nl()

    return {"stacklets": set_up, "skipped": skipped, "users": users, "config": config}


# ── Main ──────────────────────────────────────────────────────────────────────

_saved_term = None

def _save_terminal():
    """Save terminal state before we do anything."""
    global _saved_term
    try:
        import termios
        _saved_term = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

def _restore_terminal():
    """Restore terminal to saved state."""
    if _saved_term:
        try:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _saved_term)
        except Exception:
            pass


if __name__ == "__main__":
    _save_terminal()
    try:
        wizard()
    except KeyboardInterrupt:
        _restore_terminal()
        print(f"\n\n  {DIM}Cancelled — nothing was changed.{RESET}\n")
        sys.exit(1)
    finally:
        _restore_terminal()
