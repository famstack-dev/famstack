"""Pipeline quality eval — `stacktests eval`.

Real Paperless, real AI stacklet, hand-curated documents under
`cases/`. Report-only: per-field scorecards print to stdout, the
pytest run always passes. Excluded from default pytest collection
via `pyproject.toml`'s `norecursedirs`; the only entrypoint is
`tests/integration/stacktests eval`.
"""
