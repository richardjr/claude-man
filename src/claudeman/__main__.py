"""Entry point.

``claudeman`` (and ``python -m claudeman``) with no subcommand launches the TUI; with a
subcommand it delegates to the ``claudemanctl`` CLI so both binaries share one package.
"""

from __future__ import annotations

import sys

# Every top-level claudemanctl group/verb, so `claudeman <group> …` reaches the CLI instead of
# launching the TUI. Kept as a literal (deriving it from cli.build_parser() would import the CLI +
# registry on every TUI launch); tests/test_main_dispatch.py pins parity with the real parser.
_CTL_GROUPS = {"profile", "project", "packs", "model", "sync", "config", "image", "doctor"}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and (args[0] in _CTL_GROUPS or args[0] in ("-h", "--help", "--version")):
        from .cli import main as ctl_main
        return ctl_main(args)
    # Launch the TUI (textual imported lazily so the CLI/tests don't need it).
    from .tui.app import run
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
