# Security Policy

claude-man's whole purpose is running an agent inside a hardened sandbox, so security reports
are taken seriously and handled with priority.

## Reporting a vulnerability

Please **do not** open a public issue for anything that could be a vulnerability (sandbox
escape, credential leak, hardening-floor relaxation, denylist bypass, …).

Instead, report it privately via
[GitHub Security Advisories](https://github.com/richardjr/claude-man/security/advisories/new)
for this repository. You should normally get a first response within a week.

## What counts

The trust boundaries and threat model are documented in
[`docs/SECURITY.md`](docs/SECURITY.md), and the load-bearing invariants every change must
preserve are listed at the top of [`CLAUDE.md`](CLAUDE.md). In short, a report is in scope if
it demonstrates a way to:

- get Claude account credentials (`.credentials.json`, OAuth tokens, `ANTHROPIC_*` keys) into
  or out of a container, or bill a different account than the project's profile;
- weaken the hardened container floor (`--read-only`, `--cap-drop ALL`, `no-new-privileges`,
  non-root user, the fixed writable-mount set) through configuration the tool accepts;
- make sync-back / asset-sync read or write something the denylist says it never touches;
- escape the workspace / config containment guards (path traversal, symlink tricks, mount
  targeting);
- bypass the strict-egress firewall — reach a destination outside the allowlist from a
  `project lock`'d (strict-egress) container, or otherwise defeat the squid-sidecar /
  `--internal`-network boundary (the primary control against a compromised dependency
  exfiltrating the OAuth/`GH` token — see invariant 3 in `CLAUDE.md` and the Network
  containment section of `docs/SECURITY.md`).

Known, intentionally-documented limitations (e.g. roadmap phases that are still stubs) are
listed in [`ROADMAP.md`](ROADMAP.md) and [`docs/REVIEW.md`](docs/REVIEW.md) — checking there
first saves everyone time, but when in doubt, report it anyway.

## Supported versions

Pre-1.0: only the latest `main` is supported; fixes land there.
