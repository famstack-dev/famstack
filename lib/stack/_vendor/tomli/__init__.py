# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2021 Taneli Hukkinen
# Licensed to PSF under a Contributor Agreement.
#
# Vendored from tomli 2.4.1 — https://github.com/hukkin/tomli
# tomli is the backport of tomllib (Python 3.11+ stdlib).
# Vendored here so famstack runs on macOS system Python (3.9).

__all__ = ("loads", "load", "TOMLDecodeError")
__version__ = "2.4.1"

from ._parser import TOMLDecodeError, load, loads
