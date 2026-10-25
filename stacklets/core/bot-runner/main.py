"""Bot runner — discovers and runs bots from across all enabled stacklets.

Convention-based discovery:
  1. Scans /stacklets/*/bot/bot.toml (and /extensions/*, for stacklets
     that live outside the repo) for enabled stacklets
  2. Waits for Matrix to be available
  3. Creates Matrix accounts and rooms
  4. Waits until no enabled stacklet has a migration left to run
  5. Launches all bots concurrently in one async process

A stacklet ships a bot by adding a bot/ directory with bot.toml and
a Python file. The runner discovers it automatically on next restart.

Bot ID convention: always ends with -bot (e.g. archivist-bot).
Module convention: strip -bot suffix -> archivist.py -> ArchivistBot.
A declaration's `module` names another file, or another stacklet's
(`"<stacklet>/<stem>"`), whose code then answers as the declared account.
"""

import asyncio
import os
import re
import signal
import sys
from pathlib import Path

import tomllib

from loguru import logger

from stack.migrations import Migrations, until_migrated
from stack.stacklet_code import import_stacklets, load


# ── Logging ──────────────────────────────────────────────────────────────
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<7}</level> | {message}",
    level=os.environ.get("LOG_LEVEL", "INFO"),
)

# ── Config from environment ──────────────────────────────────────────────
MATRIX_HOMESERVER = os.environ.get("MATRIX_HOMESERVER", "http://stack-messages-synapse:8008")
MATRIX_SERVER_NAME = os.environ.get("MATRIX_SERVER_NAME", "")
DATA_DIR = Path("/data")
STACKLETS_DIR = Path("/stacklets")
# Stacklets that do not ship with the repo are mounted from the framework's
# extensions dir. Both trees have the same shape, so the runner searches
# them in the order the CLI discovers them: repo first, extensions second.
EXTENSIONS_DIR = Path("/extensions")
STACKLET_ROOTS = (STACKLETS_DIR, EXTENSIONS_DIR)
SETUP_STATE_DIR = Path("/setup-state")
# How often held bots look again at the migrations they wait for: the
# longest a message waits once the last migration has run.
MIGRATIONS_POLL_SECS = 5


def _read_secrets():
    """Read secrets from the mounted .stack/secrets.toml."""
    secrets_path = SETUP_STATE_DIR / "secrets.toml"
    if not secrets_path.exists():
        return {}
    try:
        with open(secrets_path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        logger.warning("Failed to read secrets: {}", e)
        return {}


def _enabled_stacklets():
    """Find enabled stacklets by checking setup-done markers."""
    enabled = set()
    for marker in SETUP_STATE_DIR.glob("*.setup-done"):
        enabled.add(marker.stem)
    # Core is always enabled (always_on), may not have a marker
    enabled.add("core")
    return enabled


def _subst_env(value: str) -> str:
    """Expand ${VAR} references in a bot.toml value from the environment.

    Lets a declaration be config-driven instead of hardcoded — the agent's
    id/name/room come from core's rendered env (AGENT_BOT_ID, AGENT_NAME),
    which trace back to `stack.toml [agent] name`. Unknown vars are left as-is.
    """
    return re.sub(
        r"\$\{(\w+)\}",
        lambda m: os.environ.get(m.group(1), m.group(0)),
        value,
    )


def discover_bots():
    """Scan enabled stacklets for bot/bot.toml declarations.

    Returns a list of bot configs ready for instantiation.

    The stacklets in `STACKLET_ROOTS` are registered first, in the order
    they are searched: a bot is a module of its stacklet's package and may
    import another stacklet's by its package name (`stacklets.memory.lib`).
    """
    import_stacklets(STACKLET_ROOTS)
    enabled = _enabled_stacklets()
    logger.info("Enabled stacklets: {}", ", ".join(sorted(enabled)))

    secrets = _read_secrets()
    logger.debug("Loaded {} secrets", len(secrets))

    bots = []

    for stacklet_id in sorted(enabled):
        bot_dir = bot_dir_for(stacklet_id)
        for bot_toml in _bot_toml_files(bot_dir):
            logger.info("Found bot declaration: {}", bot_toml)

            try:
                with open(bot_toml, "rb") as f:
                    decl = tomllib.load(f)
            except Exception as e:
                logger.warning("Failed to parse {}: {}", bot_toml, e)
                continue

            decl = {k: _subst_env(v) if isinstance(v, str) else v
                    for k, v in decl.items()}

            bot_id = decl.get("id", "")
            if not bot_id:
                logger.warning("Bot in {} has no 'id' field, skipping", bot_toml)
                continue

            if not MATRIX_SERVER_NAME:
                logger.error("MATRIX_SERVER_NAME not set — run 'stack up messages' first")
                continue

            # Password resolution. A bot may declare a fixed `password_key`
            # (e.g. the agent's "AGENT_BOT_PASSWORD") so its credential is
            # decoupled from its handle; otherwise derive {BOT_ID}_PASSWORD.
            key_name = decl.get("password_key") or f"{bot_id.upper().replace('-', '_')}_PASSWORD"
            secret_key = f"{stacklet_id}__{key_name}"
            password = secrets.get(secret_key, "")

            if not password:
                logger.warning("Bot {} has no password (expected {} in secrets.toml), skipping", bot_id, secret_key)
                continue

            # Session dir: per-stacklet under data. Multiple bots in one
            # stacklet share it safely — MicroBot namespaces its files by
            # bot name (stacker-bot.session.json vs mail-bot.session.json).
            session_dir = DATA_DIR / stacklet_id / "bot"
            session_dir.mkdir(parents=True, exist_ok=True)
            logger.debug("Bot {} session dir: {}", bot_id, session_dir)

            module = bot_module(decl, stacklet_id)
            if module is None:
                module_dir, module_stem, class_name = bot_dir, None, None
                logger.info("Bot {}: account only, no module answers as it here", bot_id)
            else:
                code_stacklet, module_stem = module
                module_dir = bot_dir_for(code_stacklet)
                class_name = module_stem.capitalize() + "Bot"
                logger.info("Bot {} -> {}/{}.py -> class {}",
                            bot_id, module_dir, module_stem, class_name)

            bots.append({
                "bot_id": bot_id,
                "stacklet_id": stacklet_id,
                "bot_dir": str(module_dir),
                "module_stem": module_stem,
                "class_name": class_name,
                "homeserver": MATRIX_HOMESERVER,
                "user_id": f"@{bot_id}:{MATRIX_SERVER_NAME}",
                "password": password,
                "session_dir": str(session_dir),
                "display_name": decl.get("name", bot_id),
                "room": decl.get("room"),
                "room_topic": decl.get("room_topic"),
                "join_rooms": list(decl.get("join_rooms") or []),
                "settings": decl.get("settings", {}),
            })

    return bots


def bot_module(decl, stacklet_id):
    """(stacklet, stem) of the module that answers as a declared account,
    or None when the runner only provisions the account.

    Without `module`, the declaring stacklet's file named after the handle
    minus "-bot": archivist-bot runs archivist.py, class ArchivistBot.
    `module = "<stem>"` names another file of the declaring stacklet,
    `"<stacklet>/<stem>"` one of another stacklet's, shipped or extension.
    The account stays the declaring stacklet's either way: its password,
    its login state, its settings. An empty `module` declares an account
    something outside the runner answers as (the agent, while nanobot
    does).
    """
    if "module" not in decl:
        return stacklet_id, decl["id"].removesuffix("-bot")
    module = decl["module"].strip()
    if not module:
        return None
    code_stacklet, _, stem = module.rpartition("/")
    return code_stacklet or stacklet_id, stem


def bot_dir_for(stacklet_id, roots=None):
    """Where a stacklet's bot/ dir is mounted, in `roots` (`STACKLET_ROOTS`
    as it is when called).

    The repo tree wins, matching the CLI: an extension can add a stacklet,
    never replace one the release ships. Falls back to the repo path when
    neither exists, so a missing bot logs a path an operator recognises.
    """
    for root in STACKLET_ROOTS if roots is None else roots:
        candidate = root / stacklet_id / "bot"
        if candidate.is_dir():
            return candidate
    return STACKLETS_DIR / stacklet_id / "bot"


def _bot_toml_files(bot_dir):
    """Every bot declaration in a stacklet's bot/ dir.

    The primary `bot.toml` plus any `<name>.bot.toml` siblings, so one
    stacklet can ship several bots — core hosts both stacker-bot
    (`bot.toml`) and mail-bot (`mail.bot.toml`). Email is a core ingestion
    capability, not its own stacklet, so it rides here.
    """
    if not bot_dir.is_dir():
        return []
    files = []
    primary = bot_dir / "bot.toml"
    if primary.exists():
        files.append(primary)
    files.extend(sorted(bot_dir.glob("*.bot.toml")))
    return files


async def wait_for_matrix(timeout=None):
    """Wait for Matrix homeserver to be reachable."""
    import urllib.request
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    url = f"{MATRIX_HOMESERVER}/_matrix/client/versions"
    elapsed = 0

    while True:
        try:
            req = urllib.request.Request(url)
            urllib.request.urlopen(req, timeout=5, context=ctx)
            logger.info("Matrix is available at {}", MATRIX_HOMESERVER)
            return True
        except Exception:
            if elapsed == 0:
                logger.info("Waiting for Matrix at {}...", MATRIX_HOMESERVER)
            await asyncio.sleep(10)
            elapsed += 10
            if timeout and elapsed >= timeout:
                return False


def _needs_account_creation(configs):
    """Check which bots don't have a saved session (likely no account yet)."""
    needs = []
    for cfg in configs:
        session_file = Path(cfg["session_dir"]) / f"{cfg['bot_id']}.session.json"
        if not session_file.exists():
            needs.append(cfg)
    return needs


def _ensure_bot_accounts(configs):
    """Create Matrix accounts for new bots and ensure rooms + joins for all.

    Account creation is skipped for bots with saved sessions. Room setup
    and admin user joins always run so new stacklets get their rooms
    immediately.
    """
    admin_user = os.environ.get("MATRIX_ADMIN_USER", "")
    admin_password = os.environ.get("MATRIX_ADMIN_PASSWORD", "")

    if not admin_user or not admin_password:
        logger.warning("No admin credentials — can't create accounts or rooms")
        return

    from accounts import setup_bot_accounts, ensure_rooms

    # Bots with saved sessions already have accounts — only new bots need one
    needs = _needs_account_creation(configs)
    if needs:
        logger.info("Bots needing accounts: {}", ", ".join(c["bot_id"] for c in needs))
        bot_accounts = [{
            "id": c["bot_id"],
            "name": c["display_name"],
            "password": c["password"],
        } for c in needs]
        ok = setup_bot_accounts(
            bot_accounts, MATRIX_HOMESERVER, MATRIX_SERVER_NAME,
            admin_user, admin_password,
        )
        if not ok:
            logger.warning("Account creation failed — bots without sessions won't start")
    else:
        logger.info("All bots have sessions — skipping account creation")

    # Rooms and joins must run every time — a new stacklet may have been
    # enabled since last boot, and its admin users need to be joined
    admin_user_ids = [
        uid.strip() for uid in
        os.environ.get("STACK_ADMIN_USER_IDS", "").split(",")
        if uid.strip()
    ]
    all_bots = [{
        "id": c["bot_id"],
        "room": c.get("room"),
        "room_topic": c.get("room_topic"),
        "join_rooms": c.get("join_rooms") or [],
    } for c in configs]
    ensure_rooms(
        all_bots, MATRIX_HOMESERVER, MATRIX_SERVER_NAME,
        admin_user, admin_password, admin_user_ids,
    )


def migrations_of(stacklet_ids):
    """The migrations of these stacklets, where the CLI finds them: shipped
    in the first stacklet root that has the stacklet, recorded in its data
    dir. Read without the code host, from the files and the records."""
    found = []
    for stacklet_id in sorted(stacklet_ids):
        stacklet_dir = next((root / stacklet_id for root in STACKLET_ROOTS
                             if (root / stacklet_id).is_dir()), None)
        if stacklet_dir is not None:
            found.append(Migrations(stacklet_id, stacklet_dir, DATA_DIR / stacklet_id))
    return found


async def hold_until_migrated():
    """Return once no enabled stacklet has a migration left to run.

    `stack up` starts this container before it runs the migrations, so a
    bot launched at once would act with this release's code on data still
    in the shape the previous one left. Held, it misses nothing: what
    arrives meanwhile stays on the room timeline and the bot drains it
    from its cursor when it starts. Commands run in this container with
    `docker exec` are separate processes and are not held, so a migration
    that needs one still runs.
    """
    held = []

    def say(keys):
        held.append(keys)
        logger.info("Holding the bots until these migrations have run: {}", ", ".join(keys))

    await until_migrated(migrations_of(_enabled_stacklets()), say, MIGRATIONS_POLL_SECS)
    if held:
        logger.info("The migrations have run, launching the bots")


def bot_module_path(cfg):
    """The file a discovered bot's module is loaded from, in the bot dir of
    the stacklet that ships the code (`bot_module`)."""
    return Path(cfg["bot_dir"]) / f"{cfg['module_stem']}.py"


def load_bot_class(cfg):
    """The class of a discovered bot, loaded from its module file.

    The module is `stacklets.<id>.bot.<name>` (`stack.stacklet_code`), so
    it imports the rest of its stacklet relatively. Until every bot does,
    the bot's directory goes first on `sys.path`, so the module imports
    its neighbours by their bare names too. Returns None when the
    declaration has no module file; anything the module raises while it
    loads is raised here.
    """
    bot_dir = cfg["bot_dir"]
    if bot_dir not in sys.path:
        sys.path.insert(0, bot_dir)

    module_path = bot_module_path(cfg)
    if not module_path.exists():
        return None

    return getattr(load(Path(bot_dir).parent, module_path), cfg["class_name"])


async def main():
    """Discover bots, wait for Matrix, ensure accounts, run."""
    configs = discover_bots()
    if not configs:
        logger.info("No bots discovered — waiting for stacklets to be enabled")
        # Keep running so the container doesn't restart-loop
        await asyncio.Event().wait()

    logger.info("Discovered {} bot(s): {}", len(configs),
                ", ".join(c["bot_id"] for c in configs))

    # Wait for Matrix
    await wait_for_matrix()

    # Ensure bot accounts exist (best-effort)
    _ensure_bot_accounts(configs)

    await hold_until_migrated()

    # Import and instantiate bots
    tasks = []
    bot_instances = []

    for cfg in configs:
        if cfg["module_stem"] is None:
            continue
        try:
            bot_class = load_bot_class(cfg)
        except Exception as e:
            logger.error("Failed to load {}: {}", cfg["bot_id"], e)
            continue
        if bot_class is None:
            logger.error("Bot module not found: {}", bot_module_path(cfg))
            continue

        bot = bot_class(
            homeserver=cfg["homeserver"],
            user_id=cfg["user_id"],
            password=cfg["password"],
            session_dir=cfg["session_dir"],
            **cfg["settings"],
        )
        # bot.toml's display name is authoritative; the bot applies it to its
        # Matrix profile on launch so renames reach existing accounts too.
        bot.display_name = cfg["display_name"]
        bot_instances.append(bot)
        tasks.append(asyncio.create_task(bot.start()))
        logger.info("Launching {} as {}", bot.name, cfg["user_id"])

    if not tasks:
        logger.error("No bots launched — check logs above")
        await asyncio.Event().wait()

    # Signal handling for clean shutdown
    def shutdown(*_):
        logger.info("Shutting down...")
        for bot in bot_instances:
            asyncio.create_task(bot.stop())

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("Bot runner active ({} bot{})", len(tasks), "s" if len(tasks) != 1 else "")
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("All bots stopped")


if __name__ == "__main__":
    asyncio.run(main())
