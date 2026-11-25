"""The rollback drill's inventory: what the run changed on the Mac it ran on.

The drill writes an inventory of the Mac's Docker, its run folder, the
LaunchAgents and the free space before its first command and again after
its cleanup. The report must name every difference, so the comparison is
tested here on two fabricated inventories.
"""

from __future__ import annotations

from tests.unit.driver import driver_code

inventory = driver_code.load("drill_inventory")


def an_inventory(**changes) -> dict:
    """An inventory of a Mac with one other container and one image, changed by `changes`."""
    base = {
        "containers": ["orb-other Exited (0) 2 days ago"],
        "images": ["postgres:16-alpine sha256:aa"],
        "networks": ["bridge", "host", "none"],
        "volumes": [],
        "build_cache": [],
        "run_folder": ["beta1-test"],
        "launch_agents": ["local.famstack.keepawake.plist"],
        "free_kb": 100_000_000,
    }
    return {**base, **changes}


def test_two_equal_inventories_differ_in_nothing():
    """Given the same Mac before and after. Then the comparison names no difference."""
    assert inventory.differences(an_inventory(), an_inventory(free_kb=100_000_100)) == []


def test_each_added_and_removed_entry_is_named():
    """Given a network left behind and an image removed. Then both are named, with their kind.

    A network or an image that the cleanup misses stays on the Mac
    without an error, so only this comparison names it."""
    after = an_inventory(networks=["bridge", "host", "none", "stack"], images=[])

    assert inventory.differences(an_inventory(), after) == [
        "images: removed postgres:16-alpine sha256:aa",
        "networks: added stack",
    ]


def test_a_large_change_of_the_free_space_is_named():
    """Given 3 GB less free space after the run. Then the comparison names it.

    A snapshot or a 2 GB test file that the cleanup missed shows only
    here, because it is inside a folder that the inventory lists by name."""
    after = an_inventory(free_kb=100_000_000 - 3_000_000)

    assert inventory.differences(an_inventory(), after) == ["free space: 2.9 GB less"]


def test_build_cache_the_drill_added_is_named():
    """Given two build cache entries after the drill that were not there before. Then the difference names them.

    The drill's builds leave their layers there, and no image list shows them."""
    assert inventory.differences(an_inventory(), an_inventory(build_cache=["x1", "x2"])) == [
        "build cache: 2 entries added"]
