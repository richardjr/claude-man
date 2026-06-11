"""SEC-6 — slugs/profile names are shape-validated at the argparse boundary (dependency-free).

A malformed slug from argv must be rejected by the parser itself, BEFORE any handler can use it
for path construction (``projects/<slug>.toml``) or a terminal spawn (the ``bash -lc`` keep-open
path). argparse surfaces a failed ``type=`` as a usage error -> ``SystemExit(2)``.
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import cli  # noqa: E402
from claudeman.registry.schema import ValidationError, validate_slug  # noqa: E402

BAD_SLUGS = (
    "../escape",            # path traversal toward a sibling config dir
    "a/b",                  # separator
    "demo; rm -rf /",       # shell metacharacters (the bash -lc surface)
    "$(id)",
    "demo slug",            # whitespace
    "Demo",                 # uppercase (slugs are lowercase by schema)
    "-leading-dash",
    "",
)

SLUG_VERBS = (
    ["project", "shell"],
    ["project", "claude"],
    ["project", "nvim"],
    ["project", "up"],
    ["project", "stop"],
    ["project", "create"],
    ["project", "delete"],
    ["project", "recreate"],
    ["project", "repo", "list"],
    ["project", "env", "list"],
    ["project", "ports", "list"],
    ["sync", "review"],
)

NAME_VERBS = (
    ["profile", "renew"],
    ["profile", "verify"],
    ["profile", "seed"],
)


def _parse(argv):
    """parse_args with argparse's usage noise suppressed."""
    with contextlib.redirect_stderr(io.StringIO()):
        return cli.build_parser().parse_args(argv)


class SlugBoundaryValidationTest(unittest.TestCase):
    def test_validate_slug_accepts_and_rejects(self) -> None:
        self.assertEqual(validate_slug("my-project-2"), "my-project-2")
        for bad in BAD_SLUGS:
            with self.assertRaises(ValidationError, msg=bad):
                validate_slug(bad)

    def test_slug_verbs_reject_malformed_slugs(self) -> None:
        for verb in SLUG_VERBS:
            for bad in BAD_SLUGS:
                with self.assertRaises(SystemExit, msg=f"{verb} {bad!r}") as ctx:
                    _parse([*verb, bad])
                self.assertEqual(ctx.exception.code, 2)

    def test_profile_name_verbs_reject_malformed_names(self) -> None:
        for verb in NAME_VERBS:
            for bad in BAD_SLUGS:
                with self.assertRaises(SystemExit, msg=f"{verb} {bad!r}") as ctx:
                    _parse([*verb, bad])
                self.assertEqual(ctx.exception.code, 2)

    def test_profile_option_rejected_on_create_and_recreate(self) -> None:
        for argv in (["project", "create", "demo", "--profile", "../evil"],
                     ["project", "recreate", "demo", "--profile", "../evil"]):
            with self.assertRaises(SystemExit) as ctx:
                _parse(argv)
            self.assertEqual(ctx.exception.code, 2)

    def test_valid_slugs_still_parse(self) -> None:
        args = _parse(["project", "shell", "my-project"])
        self.assertEqual(args.slug, "my-project")
        args = _parse(["project", "create", "demo2", "--profile", "work"])
        self.assertEqual((args.slug, args.profile), ("demo2", "work"))
        args = _parse(["project", "status"])  # optional slug stays optional
        self.assertIsNone(args.slug)


if __name__ == "__main__":
    unittest.main()
