# uv only — never pip or poetry

- Use `uv` for everything: `uv sync` to install, `uv run <cmd>` to execute in the environment,
  `uv add`/`uv remove` for dependencies (subject to the ask-before-deps rule). Never call `pip
  install`, `poetry`, or `python -m venv` directly — they bypass the lockfile.
- `pyproject.toml` + `uv.lock` are the source of truth; don't edit the lockfile by hand or
  install ad-hoc packages into the environment "just to try something" — use `uvx <tool>` for
  one-off tools instead.
- Match the project's pinned Python version (`requires-python` / `.python-version`); don't
  "upgrade" it to make something work.
- The container rootfs is read-only: a system-wide or `pip install --user` install fails. The
  writable surface is `/workspace`, so keep the environment in the project's `.venv` there (uv's
  default) — `uv venv` / `uv sync` Just Work. (pip and uv caches are already redirected to
  `/workspace`, so installs don't fill the small in-memory cache.)
