"""``claudemanctl`` — the scriptable CLI surface.

Importable without ``textual``. Phase-0/1 implements the read-only/safe verbs (profile
list, project status, shell/claude spawn, image build/smoke command rendering); the rest
print an honest "not yet implemented (phase N)" and exit non-zero.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from . import __version__, config
from .docker import status
from .registry import profiles, projects
from .tui import terminals


def _todo(phase: int, what: str) -> int:
    print(f"not yet implemented — {what} (phase {phase}); see ROADMAP.md", file=sys.stderr)
    return 2


# --------------------------------------------------------------------------
# profile
# --------------------------------------------------------------------------
def _token_status(name: str) -> str:
    age = profiles.token_age_days(name)
    if age is None:
        return "no token"
    return f"token {int(age)}d{' EXPIRING' if age > 330 else ''}"


def cmd_profile_list(_args) -> int:
    rows = profiles.list_profiles()
    if not rows:
        print("(no profiles defined — run `claudemanctl profile add <name> --default`)")
        return 0
    for p in rows:
        flag = " [default]" if p.default else ""
        email = f"  <{p.account_email}>" if p.account_email else ""
        print(f"{p.name}{flag}{email}  [{_token_status(p.name)}]  {p.display_name}")
    return 0


def cmd_profile_add(args) -> int:
    from .profiles import setup_token

    try:
        prof = setup_token.mint(
            args.name, sso=args.sso, login=args.login, console=args.console,
            email=args.email, default=args.default, display_name=args.display_name or "",
        )
    except Exception as exc:  # noqa: BLE001 - surface any mint/login failure to the operator
        print(f"profile add failed: {exc}", file=sys.stderr)
        return 1
    suffix = " [default]" if prof.default else ""
    print(f"profile {prof.name!r} added ({prof.account_email or 'unknown account'}){suffix}")
    return 0


def cmd_profile_verify(args) -> int:
    from .profiles import setup_token

    try:
        info = setup_token.verify(args.name)
    except Exception as exc:  # noqa: BLE001
        print(f"verify failed: {exc}", file=sys.stderr)
        return 1
    try:
        recorded = profiles.load(args.name).account_email
    except FileNotFoundError:
        recorded = ""
    live_email = info.get("email", "")  # only present for interactive claude.ai logins, not OAuth tokens
    valid = bool(info.get("loggedIn"))
    print(f"profile {args.name!r}:")
    print(f"  token        : {'VALID' if valid else 'INVALID/expired'} "
          f"(auth method: {info.get('authMethod') or '?'})")
    print(f"  account      : {recorded or '(not recorded — re-run `profile add` to capture it)'}"
          + ("  [captured from your host login at mint time]" if recorded else ""))
    if live_email:
        print(f"  live account : {live_email}  sub={info.get('subscriptionType') or '?'}  "
              f"org={info.get('orgName') or '-'}")
        if recorded and recorded != live_email:
            print(f"  ⚠ live {live_email!r} != recorded {recorded!r} — re-mint for the intended "
                  f"account", file=sys.stderr)
    else:
        print("  note: OAuth tokens don't expose the account email via `auth status` — the live")
        print("        check only proves the token is valid; identity is the mint-time record above.")
    if args.raw:
        import json as _json
        print(_json.dumps(info, indent=2))
    return 0


def cmd_profile_renew(args) -> int:
    from .profiles import setup_token

    try:
        setup_token.renew(args.name)
    except FileNotFoundError:
        print(
            f"no profile {args.name!r}; create it with `claudemanctl profile add {args.name}`",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"profile renew failed: {exc}", file=sys.stderr)
        return 1
    print(f"profile {args.name!r} token renewed")
    return 0


def cmd_profile_seed(args) -> int:
    return _todo(2, f"rebuild config seed for profile {args.name!r}")


# --------------------------------------------------------------------------
# project
# --------------------------------------------------------------------------
def cmd_project_status(args) -> int:
    defined = [
        (p.slug, p.profile or "(default)", p.egress, len(p.repos))
        for p in projects.list_projects()
    ]
    rows = status.join(defined, status.query_containers())
    if args.slug:
        rows = [r for r in rows if r.slug == args.slug]
        if not rows:
            print(f"no project {args.slug!r}", file=sys.stderr)
            return 1
    print(f"{'SLUG':<20} {'STATE':<8} {'PROFILE':<12} {'EGRESS':<7} {'REPOS':<5} VERSION")
    for r in rows:
        print(f"{r.slug:<20} {r.kind:<8} {r.profile:<12} {r.egress:<7} {r.repos:<5} {r.version or '-'}")
    return 0


def cmd_project_shell(args) -> int:
    terminals.spawn_shell(args.slug)
    return 0


def cmd_project_claude(args) -> int:
    terminals.spawn_claude(args.slug)
    return 0


def cmd_project_create(args) -> int:
    from . import lifecycle

    res = lifecycle.create_project(
        args.slug, profile=args.profile, overlay=args.overlay, egress=args.egress
    )
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def cmd_project_up(args) -> int:
    from . import lifecycle

    if not projects.exists(args.slug):
        print(
            f"no project {args.slug!r}; create it with "
            f"`claudemanctl project create {args.slug}`",
            file=sys.stderr,
        )
        return 1
    res = lifecycle.up(projects.load(args.slug))
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def cmd_project_stop(args) -> int:
    from . import lifecycle

    res = lifecycle.stop(args.slug)
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def cmd_project_recreate(args) -> int:
    return _todo(3, f"recreate project {args.slug!r}")


def cmd_project_sync_repos(args) -> int:
    return _todo(3, f"sync repos for {args.slug!r}")


def cmd_project_delete(args) -> int:
    return _todo(3, f"delete project {args.slug!r}")


def cmd_project_lock(args) -> int:
    return _todo(4, f"lock egress for {args.slug!r}")


def cmd_project_unlock(args) -> int:
    return _todo(4, f"unlock egress for {args.slug!r}")


# --------------------------------------------------------------------------
# sync / image
# --------------------------------------------------------------------------
def cmd_sync_review(args) -> int:
    return _todo(5, f"sync-back review for {args.slug!r}")


def cmd_sync_plan(args) -> int:
    return _todo(5, f"sync-back dry-run for {args.slug!r}")


def cmd_image_build(args) -> int:
    overlay = args.overlay or "base"
    if overlay == "base":
        dockerfile = "images/base/Dockerfile"
        tag = f"{config.IMAGE_REPO}:base"
    else:
        dockerfile = f"images/overlays/{overlay}.Dockerfile"
        tag = f"{config.IMAGE_REPO}:{overlay}"
    argv = [
        "docker", "build", "-f", dockerfile,
        "--build-arg", f"CLAUDE_VERSION={args.claude_version}",
        "-t", tag, ".",
    ]
    print("+ " + " ".join(argv))
    if args.dry_run:
        return 0
    return subprocess.run(argv, check=False).returncode


def cmd_image_smoke(args) -> int:
    from .docker import smoke as smoke_mod

    result = smoke_mod.smoke(args.overlay)
    for line in result.lines:
        print(line)
    if result.ok:
        print(f"\nimage {config.IMAGE_REPO}:{args.overlay} PASSED the hardened-profile smoke")
        return 0
    print(f"\nimage {config.IMAGE_REPO}:{args.overlay} FAILED the smoke", file=sys.stderr)
    return 1


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="claudemanctl", description="Manage claude-man projects.")
    p.add_argument("--version", action="version", version=f"claude-man {__version__}")
    sub = p.add_subparsers(dest="group", required=True)

    # profile
    prof = sub.add_parser("profile", help="account profiles").add_subparsers(dest="cmd", required=True)
    pa = prof.add_parser("add", help="mint a profile token via `claude setup-token`")
    pa.add_argument("name")
    pa.add_argument("--default", action="store_true", help="make this the default profile")
    pa.add_argument("--email", help="account email (else read from `claude auth status`)")
    pa.add_argument("--display-name", dest="display_name", help="human-readable label")
    pa.add_argument("--sso", action="store_true", help="force SSO `claude auth login` before minting")
    pa.add_argument("--login", action="store_true", help="run `claude auth login` before minting")
    pa.add_argument("--console", action="store_true",
                    help="login via Anthropic Console (API billing) before minting")
    pa.set_defaults(func=cmd_profile_add)
    pr = prof.add_parser("renew", help="re-mint an expired token")
    pr.add_argument("name")
    pr.set_defaults(func=cmd_profile_renew)
    pv = prof.add_parser("verify", help="show which account a profile's token authenticates as")
    pv.add_argument("name")
    pv.add_argument("--raw", action="store_true", help="also print the raw auth status JSON")
    pv.set_defaults(func=cmd_profile_verify)
    ps = prof.add_parser("seed", help="rebuild the profile config seed")
    ps.add_argument("name")
    ps.set_defaults(func=cmd_profile_seed)
    prof.add_parser("list", help="list profiles").set_defaults(func=cmd_profile_list)

    # project
    proj = sub.add_parser("project", help="projects").add_subparsers(dest="cmd", required=True)
    pc = proj.add_parser("create", help="create a project + container")
    pc.add_argument("slug")
    pc.add_argument("--profile")
    pc.add_argument("--overlay", choices=config.OVERLAYS)
    pc.add_argument("--egress", choices=config.EGRESS_MODES)
    pc.set_defaults(func=cmd_project_create)
    for name, func, helptext in [
        ("up", cmd_project_up, "create-if-needed + start"),
        ("stop", cmd_project_stop, "stop the container"),
        ("recreate", cmd_project_recreate, "rebuild + recreate"),
        ("sync-repos", cmd_project_sync_repos, "git fetch each repo"),
        ("shell", cmd_project_shell, "open a shell in a new terminal"),
        ("claude", cmd_project_claude, "run claude in a new terminal"),
        ("delete", cmd_project_delete, "tear down (idempotent)"),
        ("lock", cmd_project_lock, "switch to strict egress"),
        ("unlock", cmd_project_unlock, "return to open egress"),
    ]:
        sp = proj.add_parser(name, help=helptext)
        sp.add_argument("slug")
        sp.set_defaults(func=func)
    pst = proj.add_parser("status", help="live status JOINed with the registry")
    pst.add_argument("slug", nargs="?")
    pst.set_defaults(func=cmd_project_status)

    # sync
    sy = sub.add_parser("sync", help="config sync-back").add_subparsers(dest="cmd", required=True)
    syr = sy.add_parser("review", help="open the accept/reject gate")
    syr.add_argument("slug")
    syr.set_defaults(func=cmd_sync_review)
    syp = sy.add_parser("plan", help="dry-run the reconcile")
    syp.add_argument("slug")
    syp.set_defaults(func=cmd_sync_plan)

    # image
    im = sub.add_parser("image", help="container images").add_subparsers(dest="cmd", required=True)
    ib = im.add_parser("build", help="build base/overlay image")
    ib.add_argument("overlay", nargs="?", choices=config.OVERLAYS)
    ib.add_argument("--claude-version", default=config.DEFAULT_CLAUDE_VERSION)
    ib.add_argument("--dry-run", action="store_true")
    ib.set_defaults(func=cmd_image_build)
    ism = im.add_parser("smoke", help="smoke-test a hardened image")
    ism.add_argument("overlay", choices=config.OVERLAYS)
    ism.set_defaults(func=cmd_image_smoke)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
