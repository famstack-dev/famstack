"""Snapshots: a stacklet's state, cloned on the Mac before a change.

A snapshot is a copy of a stacklet's data folder and of the stack's
configuration, taken while the stacklet is stopped. On APFS each file is
a clone: the snapshot takes seconds and no space until a file changes.
It is the way back after a change that broke a stacklet.

    stack.snapshot.store    the `.snapshots/` folder: take a snapshot, list them, remove one
    stack.snapshot.removal  what `stack snapshot remove` deletes, and what it refuses
    stack.snapshot.volume   the volume: clone a file, free space, skip Time Machine
    stack.snapshot.release  the snapshot a restart takes before it applies a release
    stack.snapshot.pending  the record of a restart, kept until its snapshot is whole
    stack.snapshot.rollback          the plan of a rollback to a snapshot
    stack.snapshot.rollback_steps    its steps: stop, move, restore, tag, check out, start
    stack.snapshot.rollback_record   its record, which finishes a stopped one and holds its stacklets
    stack.snapshot.rollback_images   its images: the check before the stop, the tags
    stack.snapshot.cli      `stack snapshot create|list`
    stack.snapshot.remove_cli     `stack snapshot remove`
    stack.snapshot.rollback_cli   `stack rollback`
    stack.snapshot.restart_cli    the snapshot of `stack restart`, and the start it refuses
    stack.snapshot.answer   how these commands print their answer and ask

A snapshot is not a backup. It does not survive the loss of the Mac,
because it stays on the same disk as the data. The backup stacklet's
database dumps are a different thing.
"""
