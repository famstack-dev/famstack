"""profile.py: the memory profile, what a Mac of this size gets.

    from stack.profile import profile_of

    profile = profile_of(stack.config)      # [core] profile, else the memory
    profile.name                            # "small"
    profile.chat_model                      # "mlx-community/Qwen3.5-9B-MLX-4bit"
    profile.speech_at_install               # False
    profile.omlx_ceiling_gb                 # 9.0
    profile.omlx_idle_timeout_s             # 600

A Mac's memory decides what fits beside Docker's VM and macOS. On a 16 GB
Mac the whole stack barely fits: OrbStack takes about 3.4 GB, the
whisper-server keeps 1.6 GB loaded at all times, and oMLX takes 0.75 GB
plus the 5.8 GB chat model. A contract then took 257 s with 2.5 GB of swap.
Those decisions were made in two places with two thresholds, the installer's
model choice and the AI's start. They are made here, once, in three
profiles:

    memory          profile   chat model                 speech   oMLX ceiling   idle unload
    under 24 GB     small     Qwen3.5-9B 4-bit (5.8 GB)  off      9 GB           after 10 min
    24 to 31 GB     medium    Qwen3.5-9B 4-bit           on       oMLX's own     off
    32 GB and more  large     Qwen3.6-35B-A3B 4-bit      on       oMLX's own     off

Who reads it:

- The installer writes `stack.toml` with the profile's chat model, and with
  `[ai] voice = false` on a small Mac (`installer.write_stack_toml`).
- The AI's start gives every oMLX on this Mac, the one the stack installs
  and the oMLX app, the fixed memory ceiling, chunked prefill and the idle
  timeout (`stacklets/ai/omlx_fit.py`).
- `stack doctor` says what speech costs on a small Mac that has no stored
  choice, which is an instance from before the profile.
- The agent's first start on a small Mac asks first, because its long
  prompts push the Mac into swap (`stacklets/agent/hooks/on_configure.py`).

`[core] profile = "small"` in `stack.toml` overrides the memory, for a Mac
that should be treated as smaller or larger than it is.

What it does not do: it changes no stored choice. The install writes the
profile's choices once; after that, `stack.toml` is what the stack follows,
and a value the admin set wins. A Mac whose memory macOS does not report
gets the medium profile, which is the choice both callers made for an
unknown Mac before: the lighter model, speech on, oMLX's own settings.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

__all__ = ["Profile", "PROFILES", "CHAT_MODELS", "profile_of", "profile_for", "memory_gb"]


@dataclass(frozen=True)
class Profile:
    """What a Mac of one memory size gets.

    `min_memory_gb` is where the profile starts. `chat_model` is the model
    the installer writes as `[ai] default`. `speech_at_install` is whether
    a fresh install keeps speech on. `omlx_ceiling_gb` is the fixed memory
    ceiling the AI's start gives oMLX, with chunked prefill; None leaves
    oMLX's own settings. `omlx_idle_timeout_s` is how long oMLX keeps a
    model loaded without a request; None leaves it loaded.

        >>> PROFILES["small"].speech_at_install
        False
        >>> PROFILES["large"].chat_model
        'unsloth/Qwen3.6-35B-A3B-UD-MLX-4bit'
    """

    name: str
    min_memory_gb: int
    chat_model: str
    speech_at_install: bool
    omlx_ceiling_gb: float | None
    omlx_idle_timeout_s: int | None


# Qwen3.6 only shipped a 27B dense and a 35B-A3B MoE, no small variant, so
# the small and medium profiles stay on Qwen3.5-9B: 3.6's smallest 4-bit
# model has 16 GB of weights, too big for a 16 GB Mac.
LIGHT_MODEL = "mlx-community/Qwen3.5-9B-MLX-4bit"
LARGE_MODEL = "unsloth/Qwen3.6-35B-A3B-UD-MLX-4bit"

# The chat models, largest first, with the Macs they are for, as the
# installer lists them in `stack.toml` for an admin who wants to switch.
CHAT_MODELS = {
    LARGE_MODEL: "32 GB+ RAM",
    LIGHT_MODEL: "16 GB+ RAM, lightweight",
}

# oMLX refuses or aborts what does not fit under its memory guard's
# ceiling. Its default tier computes the ceiling from the memory free at
# that moment: on a 16 GB Mac 4.4 to 5.3 GB, under the 5.8 GB model. A
# fixed 9 GB, under Apple's 10.7 GB GPU limit on 16 GB, held a
# contract-sized request (6,000 tokens in, 4,000 out); macOS swaps the
# rest instead (PR #434).
SMALL_MAC_CEILING_GB = 9.0

# The chat model keeps 5.8 GB loaded, also at night when nobody asks. After
# ten minutes without a request oMLX unloads it, and that memory goes back
# to Docker and macOS. The next request waits while the model loads again.
SMALL_MAC_IDLE_TIMEOUT_S = 600

# Largest first: a Mac gets the first profile whose minimum it reaches.
PROFILES = {
    "large": Profile("large", 32, LARGE_MODEL,
                     speech_at_install=True, omlx_ceiling_gb=None, omlx_idle_timeout_s=None),
    "medium": Profile("medium", 24, LIGHT_MODEL,
                      speech_at_install=True, omlx_ceiling_gb=None, omlx_idle_timeout_s=None),
    "small": Profile("small", 0, LIGHT_MODEL,
                     speech_at_install=False, omlx_ceiling_gb=SMALL_MAC_CEILING_GB,
                     omlx_idle_timeout_s=SMALL_MAC_IDLE_TIMEOUT_S),
}

# A Mac whose memory macOS does not report.
UNKNOWN_MEMORY = "medium"


def profile_of(config: dict) -> Profile:
    """The profile of this instance: `[core] profile` when set, else the
    one for this Mac's memory.

    `config` is the parsed `stack.toml`, `{}` before the install has
    written one. Raises ValueError for a `[core] profile` that names no
    profile, because a typo there would otherwise quietly fall back to the
    memory.

        >>> profile_of({"core": {"profile": "small"}}).name
        'small'
    """
    chosen = config.get("core", {}).get("profile", "")
    if not chosen:
        return profile_for(memory_gb())
    if chosen not in PROFILES:
        raise ValueError(f"[core] profile = \"{chosen}\" in stack.toml is not a profile. "
                         f"Use one of: {', '.join(sorted(PROFILES))}.")
    return PROFILES[chosen]


def profile_for(memory: float | None) -> Profile:
    """The profile for a Mac with this much memory in GB; None is a Mac
    that did not say.

        >>> profile_for(16).name, profile_for(24).name, profile_for(36).name
        ('small', 'medium', 'large')
        >>> profile_for(None).name
        'medium'
    """
    if memory is None:
        return PROFILES[UNKNOWN_MEMORY]
    return next(p for p in PROFILES.values() if memory >= p.min_memory_gb)


def memory_gb() -> float | None:
    """This Mac's memory in GB, from `sysctl -n hw.memsize`, or None when
    macOS does not say.

    On a 16 GB Mac, `sysctl` answers 17179869184 and this returns 16.0.
    """
    try:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                             text=True, timeout=5).stdout
        return int(out.strip()) / 1024 ** 3
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
