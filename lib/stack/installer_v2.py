"""
famstack installer v2 — messages-first, minimal questions.

Asks for family name, admin first name, and optional family members.
Derives the Matrix server name from the family name. Boots messages
as the first and only stacklet. Everything else is post-install onboarding.
"""

import os
import sys
import subprocess
from pathlib import Path

from .prompt import (
    ORANGE, TEAL, GREEN, RED, DIM, BOLD, RESET,
    clear, nl, out, dim, bold, done, warn,
    heading, section, banner, rule, kv,
    Spinner, ask, confirm,
)


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
    lines.append(f"  Sorry for that. If it keeps happening, we'd love to help.")
    lines.append(f"  Please reach out so we can fix it faster:")
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


def validate_name(value):
    if not value or len(value) < 2:
        return "Name is required"
    return None


def sanitize_server_name(family_name):
    """Turn a family name into a valid Matrix server name."""
    clean = "".join(c for c in family_name.lower() if c.isalnum() or c in "-_.")
    return clean or "home"


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
    """Guess the household language from timezone. Defaults to English."""
    return _TZ_LANGUAGE.get(timezone, "en")


# (min_ram_gb, model_id, label)
MODEL_TIERS = [
    (48, "mlx-community/Qwen3.5-35B-A3B-8bit", "48 GB+ RAM — best quality"),
    (36, "mlx-community/Qwen3.5-35B-A3B-4bit", "36 GB+ RAM"),
    (0,  "mlx-community/Qwen3.5-9B-MLX-4bit",  "16 GB+ RAM — lightweight"),
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


def email_from_name(name):
    first = name.split()[0] if name else "user"
    clean = "".join(c for c in first.lower() if c.isalnum())
    return f"{clean}@home.local"


def _create_stack():
    """Create a Stack instance from the repo root."""
    from .cli import create_stack
    return create_stack(REPO_ROOT)


def _has_brew() -> bool:
    import shutil
    return shutil.which("brew") is not None


def _ensure_brew():
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
            out("OrbStack is installed. Starting it for the first time...")
            dim("This opens OrbStack so it can set up Docker integration.")
            nl()
            subprocess.run(["open", "-a", "OrbStack"], timeout=10)
            _wait_for_docker()
            return

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


def write_stack_toml(family_name, server_name, timezone, language="en"):
    """Write a minimal stack.toml — just enough for messages."""
    default_model = detect_default_model()
    model_block = _model_comments(default_model)
    content = f'''# stack.toml — generated by famstack installer
#
# Edit freely — this file is gitignored and won't conflict with updates.
# Run 'stack up <stacklet>' after changes to apply them.

[core]
stack_owner = "{family_name}"
domain = ""
data_dir = "~/famstack-data"
timezone = "{timezone}"
language = "{language}"

[updates]
schedule = "0 0 3 * * *"

[ai]
provider = ""
openai_url = ""
openai_key = ""
whisper_url = "http://localhost:42062/v1"
language = "en"
{model_block}

[messages]
# Permanent — appears in every Matrix user ID (@user:{server_name})
server_name = "{server_name}"
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


# ── Wizard ───────────────────────────────────────────────────────────────────

def wizard():
    # Already configured — don't re-run
    if (REPO_ROOT / "stack.toml").exists() and (REPO_ROOT / "users.toml").exists():
        from .installer import show_existing_config
        show_existing_config()
        return None

    clear()

    # ── Welcome ────────────────────────────────────────────────────────

    banner("famstack", "Your family's private server")
    out("Everything stays on your network. Nothing leaves this machine.")
    nl()
    out("famstack is built for Apple Silicon Macs on your local network.")
    out("Do not expose it directly to the public internet.")
    nl()

    _ensure_brew()
    _ensure_docker()
    nl()

    # ── Family name ────────────────────────────────────────────────────

    section("Family", "Let's set up your family server")

    out("Your family name is used to identify your server")
    out("and shows up in chat as @name:family.")
    nl()
    dim("This is purely local — nothing is shared or sent anywhere.")
    nl()

    family_name = ask("Family name", validate=validate_name)
    if not family_name:
        return None

    server_name = sanitize_server_name(family_name)

    # ── Admin ──────────────────────────────────────────────────────────

    nl()
    out("Now create your account — the admin who manages the server.")
    nl()

    admin_name = ask("Your first name", validate=validate_name)
    if not admin_name:
        return None

    from .users import user_id
    admin = {"name": admin_name, "email": email_from_name(admin_name), "role": "admin"}
    done(f"@{user_id(admin)}:{server_name}")

    # ── Family members ─────────────────────────────────────────────────

    nl()
    out("Add family members — they'll get their own chat accounts.")
    dim("You can always add more later.")
    nl()

    users = [admin]

    while True:
        member_name = ask("Name (leave empty to continue)", validate=None)
        if not member_name:
            break

        member = {"name": member_name, "email": email_from_name(member_name), "role": "member"}
        users.append(member)
        done(f"@{user_id(member)}:{server_name}")
        nl()

    # ── Confirm ────────────────────────────────────────────────────────

    nl()
    rule()
    nl()
    plural = family_name if family_name.lower().endswith("s") else family_name + "s"
    bold(f"The {ORANGE}{plural}{RESET}")
    nl()
    for u in users:
        uid = user_id(u)
        tag = f"  {DIM}(admin){RESET}" if u["role"] == "admin" else ""
        out(f"  {TEAL}@{uid}:{server_name}{RESET}{tag}")
        dim(f"    {u['email']}")
    nl()
    dim("You can edit users.toml any time to add or remove members.")
    nl()
    rule()
    nl()

    out(f"{ORANGE}{BOLD}How it works{RESET}")
    nl()
    out(f"  Every family member gets an account on each service you enable.")
    out(f"  Log in with your {TEAL}first name{RESET} or {TEAL}email{RESET}, depending on the service.")
    out(f"  Default password is your {TEAL}first name{RESET}. Change it after first login.")
    nl()
    dim(f"  famstack also creates a {TEAL}stackadmin{RESET}{DIM} service account to manage")
    dim(f"  things behind the scenes. You'll find its password in")
    dim(f"  {TEAL}.stack/secrets.toml{RESET}{DIM} if you ever need it.")
    nl()

    out(f"{ORANGE}{BOLD}First step{RESET}")
    nl()
    out(f"  We'll start with {TEAL}Messages{RESET}, your family's private chat.")
    out(f"  It doubles as the operation center: manage your server")
    out(f"  from any device, get notifications, and add more services.")
    out(f"  Once it's running, you can continue the setup from there.")
    nl()

    if not confirm("Ready?"):
        dim("Cancelled — nothing was changed.")
        return None

    # ── Write config ──────────────────────────────────────────────────

    clear()
    section("Setting up", "Writing config and starting messages")
    nl()

    timezone = detect_timezone()
    language = detect_language(timezone)

    with Spinner("Writing stack.toml"):
        write_stack_toml(family_name, server_name, timezone, language)

    with Spinner("Writing users.toml"):
        write_users_toml(users)

    with Spinner("Writing secrets"):
        from .secrets import TomlSecretStore
        from .users import user_id as uid, password_key
        import secrets as _secrets
        store = TomlSecretStore(REPO_ROOT / ".stack" / "secrets.toml")
        for u in users:
            store.set("global", password_key(u), uid(u))
        # Prefix ensures the password never starts with a dash, which would
        # break CLI tools that parse it as a flag (e.g. register_new_matrix_user)
        admin_password = "s" + _secrets.token_urlsafe(8)
        store.set("global", "ADMIN_PASSWORD", admin_password)

    with Spinner("Initializing infrastructure") as sp:
        from .docker import check_docker, init_runtime, ensure_network
        ok, err = check_docker()
        if not ok:
            sp.fail()
            fail("Docker is not running.", err or "")
        init_runtime()
        ok, err = ensure_network()
        if err:
            sp.fail()
            fail("Could not create Docker network.", err)

        stck = _create_stack()
        stck.data.mkdir(parents=True, exist_ok=True)
        (stck.root / ".stack").mkdir(exist_ok=True)

    # ── Start messages, then core ────────────────────────────────────
    # Messages (Matrix) must be up first — the bot runner in core
    # needs it to create accounts and log in.

    nl()
    from .cli import CLI
    stck = _create_stack()
    cli = CLI(stck)

    out(f"  Bringing up {TEAL}Messages{RESET}...\n")

    # Skip interactive on_configure prompts — we already wrote server_name
    os.environ["STACK_SETUP_CONFIRMED"] = "1"
    result = cli.up("messages")

    if not result.get("ok"):
        err_msg = result.get("error", "Unknown error")
        fail("Messages failed to start.",
             f"{err_msg}\n\nRun 'stack logs messages' to see what went wrong,\n"
             "then 'stack up messages' to retry.")

    out(f"  Bringing up {TEAL}Core{RESET}...\n")
    result = cli.up("core")
    if not result.get("ok"):
        err_msg = result.get("error", "Unknown error")
        fail("Core failed to start.", err_msg)

    nl()

    # ── Done ──────────────────────────────────────────────────────────

    clear()
    nl()
    out(f"{ORANGE}{BOLD}famstack{RESET}")
    plural = family_name if family_name.lower().endswith("s") else family_name + "s"
    out(f"{GREEN}The {plural} are online{RESET}")

    from .users import user_id as uid2
    admin_id = uid2(admin)

    # Resolve URLs from the Stack instance
    messages_url = stck._public_url("messages", 42030)
    synapse_url = stck._public_url("messages", 42031)

    # ── Step-by-step guide ────────────────────────────────────────────

    rule()
    nl()
    bold("1. Open your browser")
    nl()
    if messages_url:
        out(f"   {BOLD}{TEAL}{messages_url}{RESET}")
    nl()

    bold("2. Sign in")
    nl()
    out(f"   Welcome to Element. Press {BOLD}Sign in{RESET}.")
    out("   If you see 'does not support this browser', click Continue anyway.")
    nl()
    out(f"   Username  {BOLD}{TEAL}{admin_id}{RESET}")
    out(f"   Password  {BOLD}{TEAL}{admin_id}{RESET}")
    dim("   (your first name, lowercase — change it after login)")
    nl()

    bold("3. Explore your rooms")
    nl()
    out(f"   {TEAL}#famchat{RESET}    Your private family conversations")
    out(f"   {TEAL}#famstack{RESET}   Notifications about your server")
    nl()

    rule()

    heading("Add more to your stack")
    out(f"  {TEAL}stack up photos{RESET}     Private photo library")
    out(f"  {TEAL}stack up docs{RESET}       Document archive with OCR")
    out(f"  {TEAL}stack up ai{RESET}         Local AI engine")
    out(f"  {TEAL}stack up chatai{RESET}     ChatGPT-like interface")
    out(f"  {TEAL}stack up bots{RESET}       AI helpers in chat")
    nl()
    out(f"  {TEAL}stack status{RESET}        See what's running")
    nl()

    dim(f"  Service admin password is in .stack/secrets.toml")
    nl()

    return {"stacklets": ["messages"], "users": users}
