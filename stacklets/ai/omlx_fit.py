"""omlx_fit.py: fit an oMLX on this Mac to the memory profile.

A small Mac (`stack.profile`, under 24 GB) runs oMLX with three settings
that let the default chat model work beside Docker's VM, Whisper and
macOS:

    setting           oMLX's field(s)                          value
    memory ceiling    memory_guard_tier, ..._custom_ceiling_gb "custom", 9.0
    chunked prefill   chunked_prefill                          true
    idle unload       idle_timeout_seconds                     600

The rules are the same for every oMLX on this Mac, the one the stack
installs and the oMLX app alike:

- The ceiling only replaces oMLX's default memory guard ("balanced"). A
  tier the admin chose (safe, aggressive, custom) stays.
- Chunked prefill is always on.
- The idle timeout only fills an unset one (oMLX writes null, which keeps
  the model loaded). A timeout the admin set stays, and an older oMLX
  whose settings have no idle timeout gets none, as it would not read it.
- A Mac with a larger profile gets nothing: oMLX keeps its own settings.

Why, for each setting:

- The memory ceiling. oMLX refuses or aborts what does not fit under its
  memory guard's ceiling. Its default tier, "balanced", and "aggressive"
  both compute the ceiling from the memory free at that moment: on a
  16 GB Mac "balanced" gave 4.4 to 5.3 GB, under the 5.8 GB of the
  default model, so oMLX refused it, and "aggressive" 6.9 to 7.9 GB,
  which a contract-sized request (6,000 tokens in, 4,000 out) still went
  over. A fixed 9 GB, under Apple's 10.7 GB GPU limit, held: macOS swaps
  the rest instead.
- Chunked prefill. Reading a document's prompt in one piece took the
  process from 5.8 to 7.0 GB at 2,000 tokens. Read in chunks, the peak
  stays low.
- The idle unload. oMLX unloads the model after that long without a
  request, so its memory goes back to Docker and macOS in quiet periods.

Two routes reach the settings, one per kind of oMLX:

    the oMLX the stack installs        the oMLX app (or any oMLX the
    (Homebrew service)                 admin runs on this Mac)
            |                                    |
    fit_settings_file(path, profile)    fit_running_omlx(client, profile)
            |                                    |
    writes ~/.omlx/settings.json        GET  /admin/api/global-settings
    the hook restarts the service       POST /admin/api/global-settings
                                        oMLX applies and saves them itself

Both decide with `changes_for`, so the rules live in one place. The
second route never quits or restarts the app: oMLX 0.7.4 applies all
three settings at run time and saves them to its settings file. A
setting it reports as not applied at run time (not in its answer's
`runtime_applied`) is saved and takes effect at the app's next start;
the result says which.

    from stack.profile import PROFILES

    changes_for({"memory": {"memory_guard_tier": "balanced"},
                 "scheduler": {"chunked_prefill": False},
                 "idle_timeout": {"idle_timeout_seconds": None}}, PROFILES["small"])
    # {'memory_guard_tier': 'custom', 'memory_guard_custom_ceiling_gb': 9.0,
    #  'chunked_prefill': True, 'idle_timeout_seconds': 600}

    fitting = fit_running_omlx(client, PROFILES["small"])   # client logged in
    fitting.changed       # ['memory ceiling 9 GB', 'chunked prefill', 'idle unload after 10 min']
    fitting.at_next_start # []: all three applied at once

What it does not do: decide whether a server may be changed. The caller
asks that first (`model_server`): an oMLX on another machine belongs to
someone else and is never changed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from stack.profile import Profile

from .omlx import OMLXClient

__all__ = ["DEFAULT_MEMORY_GUARD", "Fitting", "changes_for", "fit_settings_file",
           "fit_running_omlx"]

# oMLX's own default tier, the only one the profile's ceiling replaces.
DEFAULT_MEMORY_GUARD = "balanced"


@dataclass(frozen=True)
class Fitting:
    """What fitting a running oMLX changed, by the setting's name.

    `changed` names each setting the stack changed, `at_next_start` the
    ones among them oMLX saved but applies only when it starts next.
    `refused` is oMLX's reason when it took none of them.

        Fitting(changed=["chunked prefill"]).lines()
        # ["Fitted the oMLX app to this Mac's memory: chunked prefill."]
    """

    changed: list[str] = field(default_factory=list)
    at_next_start: list[str] = field(default_factory=list)
    refused: str = ""

    def lines(self) -> list[str]:
        """What the run prints: nothing when nothing changed, else what
        changed and, in one more line, what waits for the app's next start.

            Fitting(["idle unload after 10 min"], ["idle unload after 10 min"]).lines()[1]
            # 'Idle unload after 10 min takes effect when the oMLX app starts next.'
        """
        if self.refused:
            return [f"The oMLX app kept its memory settings: {self.refused}"]
        if not self.changed:
            return []
        lines = [f"Fitted the oMLX app to this Mac's memory: {', '.join(self.changed)}."]
        if self.at_next_start:
            waiting = _and(self.at_next_start)
            verb = "takes" if len(self.at_next_start) == 1 else "take"
            lines.append(f"{waiting[0].upper()}{waiting[1:]} "
                         f"{verb} effect when the oMLX app starts next.")
        return lines


def changes_for(settings: dict, profile: Profile) -> dict:
    """The fields of oMLX's global settings to change so `settings` meet
    the profile, as oMLX's `POST /admin/api/global-settings` takes them;
    empty when nothing is to change.

    `settings` is in the shape oMLX writes to `~/.omlx/settings.json` and
    answers `GET /admin/api/global-settings` with: `memory`, `scheduler`
    and `idle_timeout` sections. Nothing else of it is read.

        changes_for({"memory": {"memory_guard_tier": "aggressive"},
                     "scheduler": {"chunked_prefill": True}}, PROFILES["small"])
        # {}: the admin's tier stays, prefill is chunked, no idle timeout known
        changes_for({}, PROFILES["large"])
        # {}
    """
    ceiling = profile.omlx_ceiling_gb
    if ceiling is None:
        return {}
    wanted: dict = {}
    if _read(settings, "memory_guard_tier", DEFAULT_MEMORY_GUARD) == DEFAULT_MEMORY_GUARD:
        wanted["memory_guard_tier"] = "custom"
        wanted["memory_guard_custom_ceiling_gb"] = ceiling
    wanted["chunked_prefill"] = True
    idle = settings.get("idle_timeout")
    if (isinstance(idle, dict) and profile.omlx_idle_timeout_s is not None
            and idle.get("idle_timeout_seconds") is None):
        wanted["idle_timeout_seconds"] = profile.omlx_idle_timeout_s
    return {name: value for name, value in wanted.items() if _read(settings, name) != value}


def fit_settings_file(path: Path, profile: Profile) -> bool:
    """Write the profile's changes into the oMLX settings file at `path`,
    for the oMLX the stack installs, which reads it at its start only.
    Returns whether the file changed; the caller restarts the service.
    A missing file is left missing: oMLX's install writes it.

        fit_settings_file(Path.home() / ".omlx" / "settings.json", PROFILES["small"])
        # True on the first start of a 16 GB Mac, False after
    """
    if not path.exists():
        return False
    settings = json.loads(path.read_text())
    changes = changes_for(settings, profile)
    if not changes:
        return False
    for name, value in changes.items():
        section, key = FIELDS[name]
        settings.setdefault(section, {})[key] = value
    path.write_text(json.dumps(settings, indent=2))
    return True


def fit_running_omlx(client: OMLXClient, profile: Profile) -> Fitting:
    """Send the profile's changes to a running oMLX through its admin API,
    with `client` logged in. oMLX applies them and saves them to its own
    settings file; the app is never quit or restarted. Sends nothing when
    nothing is to change, or when oMLX does not answer its settings.

        fit_running_omlx(client, PROFILES["small"]).changed
        # ['memory ceiling 9 GB', 'chunked prefill'] on an app with an idle timeout set
    """
    settings = client.global_settings()
    if settings is None:
        return Fitting()
    changes = changes_for(settings, profile)
    if not changes:
        return Fitting()
    answer = client.update_global_settings(**changes)
    if not answer.get("success"):
        return Fitting(refused=str(answer.get("detail") or "it did not answer"))
    live = set(answer.get("runtime_applied", []))
    named = [name for name in NAMED_FIELDS if name in changes]
    return Fitting(changed=[_label(name, profile) for name in named],
                   at_next_start=[_label(name, profile) for name in named if name not in live])


# ── oMLX's fields ────────────────────────────────────────────────────────

# Where each field of the admin API sits in oMLX's settings file and in
# its `GET /admin/api/global-settings` answer (oMLX 0.7.4, `admin/routes.py`).
FIELDS = {
    "memory_guard_tier": ("memory", "memory_guard_tier"),
    "memory_guard_custom_ceiling_gb": ("memory", "memory_guard_custom_ceiling_gb"),
    "chunked_prefill": ("scheduler", "chunked_prefill"),
    "idle_timeout_seconds": ("idle_timeout", "idle_timeout_seconds"),
}


def _read(settings: dict, name: str, default=None):
    """The value of the admin API's field `name` in nested `settings`."""
    section, key = FIELDS[name]
    return (settings.get(section) or {}).get(key, default)


# ── Naming what changed ──────────────────────────────────────────────────

# The fields oMLX lists in `runtime_applied` when it applied a setting at
# once, in the order the output names them. The memory guard's tier
# stands for its ceiling too.
NAMED_FIELDS = ("memory_guard_tier", "chunked_prefill", "idle_timeout_seconds")


def _label(name: str, profile: Profile) -> str:
    """The setting behind field `name`, as the run's output names it."""
    if name == "memory_guard_tier":
        return f"memory ceiling {profile.omlx_ceiling_gb:g} GB"
    if name == "chunked_prefill":
        return "chunked prefill"
    return f"idle unload after {(profile.omlx_idle_timeout_s or 0) // 60} min"


def _and(labels: list[str]) -> str:
    """`["a", "b", "c"]` as "a, b and c"."""
    return labels[0] if len(labels) == 1 else f"{', '.join(labels[:-1])} and {labels[-1]}"
