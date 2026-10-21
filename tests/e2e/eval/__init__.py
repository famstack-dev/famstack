"""Pipeline quality eval — `stacktests eval`.

Real Paperless, real AI stacklet, hand-curated documents under
`cases/`. Report-only: per-field scorecards print to stdout, the
pytest run always passes. Excluded from default pytest collection
via `pyproject.toml`'s `norecursedirs`; the entrypoint is
`tests/e2e/stacktests eval`. `correction_fold.py` is a script of its own
that needs only a model server (see tests/README.md).
"""
