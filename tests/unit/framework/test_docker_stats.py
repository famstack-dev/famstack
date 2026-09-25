"""`stack host` reads memory from `docker stats`, which prints sizes in its
own units: binary (`MiB`, `GiB`) for memory, decimal (`kB`, `MB`) for I/O."""

from stack.docker import parse_size


def test_docker_stats_sizes_read_as_bytes():
    assert parse_size("512MiB") == 512 * 1024 ** 2
    assert parse_size("1.5GiB") == int(1.5 * 1024 ** 3)
    assert parse_size("7.8kB") == 7800
    assert parse_size("0B") == 0


def test_an_unreadable_size_counts_as_nothing():
    # A container that is starting reports `--` until its first sample.
    assert parse_size("--") == 0
    assert parse_size("") == 0
