"""Snapshots: a stacklet's state, cloned on the Mac before a change.

A snapshot is a copy of a stacklet's data folder and of the stack's
configuration, taken while the stacklet is stopped. On APFS each file is
a clone: the snapshot takes seconds and no space until a file changes.
It is the way back after a change that broke a stacklet.

    stack.snapshot.store    the `.snapshots/` folder: take a snapshot, list them, remove one
    stack.snapshot.release  the snapshot a restart takes before it applies a release
    stack.snapshot.pending  the record of a restart, kept until its snapshot is whole
    stack.snapshot.cli      `stack snapshot create <id>...` and `stack snapshot list`

A snapshot is not a backup. It does not survive the loss of the Mac,
because it stays on the same disk as the data. The backup stacklet's own
word "snapshot" (its database dumps) is a different thing.
"""
