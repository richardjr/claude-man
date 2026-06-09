"""``claudemanctl`` — the scriptable CLI surface.

Importable without ``textual``. Phase-0/1 implements the read-only/safe verbs (profile
list, project status, shell/claude spawn, image build/smoke command rendering); the rest
print an honest "not yet implemented (phase N)" and exit non-zero.
"""

from __future__ import annotations

import argparse
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


def cmd_profile_usage(args) -> int:
    from . import usage

    by_profile = usage.usage_by_profile()
    if not by_profile:
        print("(no profiles defined)")
        return 0
    h = usage.human
    print(f"{'PROFILE':<12} {'ACCOUNT':<24} {'IN':>8} {'OUT':>8} {'CACHE':>9} {'TOTAL':>9} {'SESS':>5}")
    grand = usage.Usage()
    for name in sorted(by_profile):
        u = by_profile[name]
        grand.add(u)
        try:
            acct = profiles.load(name).account_email
        except FileNotFoundError:
            acct = ""
        cache = u.cache_creation + u.cache_read
        print(f"{name:<12} {acct:<24} {h(u.input):>8} {h(u.output):>8} {h(cache):>9} "
              f"{h(u.total):>9} {u.sessions:>5}")
    cache = grand.cache_creation + grand.cache_read
    print(f"{'TOTAL':<12} {'':<24} {h(grand.input):>8} {h(grand.output):>8} {h(cache):>9} "
          f"{h(grand.total):>9} {grand.sessions:>5}")
    print("\n(usage produced inside claude-man containers; cache = creation + read)")
    return 0


def cmd_profile_limits(args) -> int:
    from . import usage_api

    names = [args.name] if args.name else profiles.list_names()
    if not names:
        print("(no profiles defined)")
        return 0
    print(f"{'PROFILE':<12} {'5-HOUR':<16} {'WEEKLY':<16} RESETS (5h)")
    needs_renew = False
    for name in names:
        res = usage_api.fetch_for_profile(name)
        if res.util is not None:
            print(f"{name:<12} {usage_api.render_bar(res.util.five_hour.pct):<16} "
                  f"{usage_api.render_bar(res.util.seven_day.pct):<16} {res.util.five_hour.resets_at}")
        else:
            needs_renew = needs_renew or res.note in ("re-mint", "auth")
            print(f"{name:<12} {res.note}")
    if needs_renew:
        print("\n're-mint' = token lacks the user:profile scope · 'auth' = token expired/invalid. "
              "Either way: `claudemanctl profile renew <name>` re-mints it (with usage access).",
              file=sys.stderr)
    return 0


def cmd_profile_seed(args) -> int:
    from .profiles import seed

    try:
        prof = profiles.load(args.name)
    except FileNotFoundError:
        print(
            f"no profile {args.name!r}; create it with `claudemanctl profile add {args.name}`",
            file=sys.stderr,
        )
        return 1
    captured = seed.capture_profile_seed(prof)
    dest = config.profile_seed_dir(args.name)
    if not captured:
        print(f"nothing to capture from {prof.seed.source} "
              f"(include: {', '.join(prof.seed.include) or 'none'})")
        return 0
    print(f"captured into {dest}:")
    for item in captured:
        print(f"  {item}")
    print("new projects on this profile inherit these (settings.json hooks/statusLine stripped)")
    return 0


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
    # For a single project, also show its published ports (config — registry-only, recreate to apply).
    if args.slug and projects.exists(args.slug):
        ports = projects.load(args.slug).ports
        if ports:
            print("\npublished ports:")
            _print_ports(ports)
    return 0


def _open_terminal(slug: str, program: str) -> int:
    """Ensure ``slug``'s container is running, then spawn a detached ``program`` terminal into it.

    A ``docker exec`` needs a RUNNING container, so a STOPPED/DEFINED project is started first
    (synchronously — the start may build the image) and the terminal opened only once it's up;
    previously this spawned a window whose ``docker exec`` failed against a dead container. An
    already-running container (including an orphan) is exec'd straight into; a non-running container
    with no registry entry can't be managed. Mirrors the TUI ``_open_terminal`` flow.
    """
    from . import lifecycle
    from .docker import runner

    if not runner.is_running(slug):
        if not projects.exists(slug):
            extra = (" (orphan container exists — reconcile it, or `docker start` it by hand)"
                     if runner.exists(slug)
                     else f"; create it with `claudemanctl project create {slug}`")
            print(f"no managed project {slug!r}{extra}", file=sys.stderr)
            return 1
        print(f"{slug} not running — starting it first …", file=sys.stderr)
        res = lifecycle.up(projects.load(slug))
        print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
        if not res.ok:
            return 1
    label = "claude" if program == "claude" else "shell"
    spawn = terminals.spawn_claude if program == "claude" else terminals.spawn_shell
    try:
        spawn(slug)
    except (RuntimeError, OSError) as exc:
        print(f"failed to open {label} for {slug!r}: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_project_shell(args) -> int:
    return _open_terminal(args.slug, "bash")


def cmd_project_claude(args) -> int:
    return _open_terminal(args.slug, "claude")


def cmd_project_create(args) -> int:
    from . import lifecycle

    res = lifecycle.create_project(
        args.slug, profile=args.profile, overlay=args.overlay, egress=args.egress
    )
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def _resolve_update(project, args) -> str:
    """Decide the ``rebuild_to`` version for a CLI ``project up``: honour ``--no-update`` /
    ``--update-yes``, silently build the tracked version for a fresh project, and prompt on a TTY when
    an existing image is behind. Fails open (returns ``""`` = no rebuild) on any check failure."""
    from . import lifecycle

    if getattr(args, "no_update", False):
        return ""
    chk = lifecycle.check_update(project)
    if chk.build_to:               # fresh project — build the tracked version, no prompt
        return chk.build_to
    if not chk.prompt:             # up to date / disabled / offline
        if chk.note:
            print(f"{project.slug}: {chk.note}", file=sys.stderr)
        return ""
    if getattr(args, "update_yes", False):
        return chk.target
    msg = f"newer claude {chk.target} available (image has {chk.current})"
    if not sys.stdin.isatty():
        print(f"{msg}; skipping rebuild (non-interactive — pass --update-yes to rebuild)",
              file=sys.stderr)
        return ""
    ans = input(f"{msg}. Rebuild the image now? [y/N] ").strip().lower()
    return chk.target if ans in ("y", "yes") else ""


def cmd_project_up(args) -> int:
    from . import lifecycle

    if not projects.exists(args.slug):
        print(
            f"no project {args.slug!r}; create it with "
            f"`claudemanctl project create {args.slug}`",
            file=sys.stderr,
        )
        return 1
    project = projects.load(args.slug)
    rebuild_to = _resolve_update(project, args)
    # Stream progress (image rebuild / build) to stdout, like `image build`.
    res = lifecycle.up(project, on_progress=print, rebuild_to=rebuild_to)
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def cmd_project_stop(args) -> int:
    from . import lifecycle

    res = lifecycle.stop(args.slug)
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def cmd_project_recreate(args) -> int:
    from . import lifecycle

    if not projects.exists(args.slug):
        print(f"no project {args.slug!r}", file=sys.stderr)
        return 1
    res = lifecycle.recreate(args.slug, profile_name=args.profile, force=args.force)
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def _uid_advisory() -> None:
    """One-line warning if the host uid != the container uid (in-container git writes would then
    trip "dubious ownership" host-side). Surface + instruct — claude-man never auto-writes
    safe.directory (that guard exists for exactly this surface)."""
    from .checkout import gitstate

    if not gitstate.host_uid_matches_container():
        import os

        print(
            f"warning: host uid {os.getuid()} != container uid {config.CONTAINER_UID}; "
            f"in-container git writes will trip 'dubious ownership' host-side. Remediation: run "
            f"claude-man as uid {config.CONTAINER_UID}, or scope `git config --global --add "
            f"safe.directory <workspace>/<dir>` per checkout (never the '*' wildcard).",
            file=sys.stderr,
        )


def _print_repo_states(states) -> None:
    from .checkout import gitstate

    if not states:
        print("(no repos configured)")
        return
    print(f"{'DIR':<24} {'BRANCH':<22} {'STATE':<18} {'↑/↓':<8} LAST COMMIT")
    for s in states:
        print(f"{s.dir:<24} {gitstate.branch_label(s):<22} {gitstate.state_label(s):<18} "
              f"{gitstate.ab_label(s):<8} {gitstate.commit_label(s)}")


def cmd_project_repos_list(args) -> int:
    from .checkout import gitstate

    if not projects.exists(args.slug):
        print(f"no project {args.slug!r}", file=sys.stderr)
        return 1
    _print_repo_states(gitstate.project_states(projects.load(args.slug)))
    return 0


def cmd_project_repo_add(args) -> int:
    from . import lifecycle

    _uid_advisory()
    res = lifecycle.add_repo(
        args.slug, args.url, branch=args.branch, dir=args.dir or "", clone=not args.no_clone
    )
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def cmd_project_repo_rm(args) -> int:
    from . import lifecycle

    res = lifecycle.remove_repo(args.slug, args.target, purge=args.purge)
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def cmd_project_sync_repos(args) -> int:
    from .checkout import gitstate, repos as repos_mod

    if not projects.exists(args.slug):
        print(f"no project {args.slug!r}", file=sys.stderr)
        return 1
    project = projects.load(args.slug)
    if not project.repos:
        print("(no repos configured)")
        return 0
    print(f"syncing {len(project.repos)} repo(s) for {args.slug} …", file=sys.stderr)
    # Clone any not-yet-present repo first (idempotent — clone_one skips an existing .git), so a repo
    # added while offline or whose add-time clone failed is picked up here, then fetch the rest.
    problems = [r for r in repos_mod.clone_all(project) if not r.ok]
    problems += [r for r in repos_mod.fetch_all(project) if not r.ok]
    for r in problems:
        print(f"  {r.dir}: {r.detail}", file=sys.stderr)
    _print_repo_states(gitstate.project_states(project))
    return 0


def cmd_project_pull(args) -> int:
    from . import lifecycle
    from .checkout import gitstate

    if not projects.exists(args.slug):
        print(f"no project {args.slug!r}", file=sys.stderr)
        return 1
    project = projects.load(args.slug)
    if not project.repos:
        print("(no repos configured)")
        return 0
    _uid_advisory()
    # Two steps (not ROADMAP phases): first fetch + show the ff-only plan (read-only), then apply to the
    # eligible repos under the per-slug lock. Mirrors the TUI's preview -> confirm -> apply, but
    # non-interactive (the plan is printed for the operator's record, then the eligible repos are pulled).
    print(f"fetching + planning ff-only pull for {args.slug} …", file=sys.stderr)
    plan = lifecycle.pull_plan(args.slug)
    for it in plan.items:
        print(f"  {'PULL' if it.eligible else 'skip'}  {it.dir}: {it.reason}", file=sys.stderr)
    for fe in plan.fetch_errors:
        print(f"  fetch: {fe}", file=sys.stderr)
    if not plan.eligible:
        print("nothing to fast-forward")
        return 0
    res = lifecycle.pull_apply(args.slug, plan.eligible)
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    _print_repo_states(gitstate.project_states(project))
    return 0 if res.ok else 1


def _print_mounts(mounts) -> None:
    if not mounts:
        print("(no env mounts configured)")
        return
    print(f"{'KIND':<6} {'SOURCE':<40} {'DEST':<26} MODE")
    for m in mounts:
        if m.error:
            print(f"{m.kind:<6} {(m.src or '-'):<40} {(m.dst or '-'):<26} ⚠ INVALID: {m.error}")
        elif m.kind == "ssh":
            print(f"{'ssh':<6} {'host agent + ~/.ssh config,known_hosts':<40} "
                  f"{config.CONTAINER_SSH_DIR:<26} agent-forward")
        elif m.kind == "env":
            print(f"{'env':<6} {'(value hidden — 0600 state tier)':<40} {m.name:<26} pass-through")
        else:
            print(f"{'file':<6} {m.src:<40} {m.dst:<26} {'ro' if m.ro else 'rw'}")


def cmd_project_env_list(args) -> int:
    if not projects.exists(args.slug):
        print(f"no project {args.slug!r}", file=sys.stderr)
        return 1
    _print_mounts(projects.load(args.slug).env_mount)
    return 0


def cmd_project_env_add(args) -> int:
    from . import lifecycle
    from .registry.schema import EnvMount, ValidationError

    if args.kind == "env":
        name = (args.src or "").strip()
        if not name:
            print("env var needs a name: project env add <slug> env <NAME>", file=sys.stderr)
            return 1
        if args.stdin:
            value = sys.stdin.read().strip()
        else:
            import getpass
            value = getpass.getpass(f"value for {name} (input hidden): ").strip()
        if not value:
            print("no value entered; nothing changed", file=sys.stderr)
            return 1
        res = lifecycle.add_env_var(args.slug, name, value)  # value -> 0600 state tier, never argv
        print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
        return 0 if res.ok else 1
    if args.kind == "ssh":
        mount = EnvMount(kind="ssh")
    else:
        if not args.src or not args.dst:
            print("file mount needs: project env add <slug> file <host-src> <container-dst>",
                  file=sys.stderr)
            return 1
        try:
            mount = EnvMount(kind="file", src=args.src, dst=args.dst, ro=not args.rw)
        except ValidationError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    res = lifecycle.add_mount(args.slug, mount)
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def cmd_project_env_rm(args) -> int:
    from . import lifecycle

    res = lifecycle.remove_mount(args.slug, args.target)
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def cmd_project_resync(args) -> int:
    from . import lifecycle

    res = lifecycle.resync(args.slug)
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def _parse_port_spec(spec: str) -> tuple[int, int]:
    """Parse ``<container>`` or ``<host>:<container>`` -> ``(host, container)``. host ``0`` -> defaults
    to == container in PortMapping. Raises ``ValueError`` on a non-numeric part (caller surfaces it)."""
    spec = spec.strip()
    if ":" in spec:
        host_s, _, container_s = spec.partition(":")
        return int(host_s), int(container_s)
    return 0, int(spec)


def _print_ports(ports) -> None:
    if not ports:
        print("(no published ports)")
        return
    print(f"{'BIND':<16} {'HOST':<7} {'CONTAINER':<10} {'PROTO':<5} REACHABLE")
    for p in ports:
        if p.error:
            print(f"{'?':<16} {'?':<7} {'?':<10} {'?':<5} ⚠ INVALID: {p.error}")
            continue
        reach = "host only (localhost)" if not p.exposed else f"LAN — anyone routing to {p.bind}"
        print(f"{p.bind:<16} {p.host_eff:<7} {p.container:<10} {p.proto:<5} {reach}")


def cmd_project_ports_list(args) -> int:
    if not projects.exists(args.slug):
        print(f"no project {args.slug!r}", file=sys.stderr)
        return 1
    _print_ports(projects.load(args.slug).ports)
    return 0


def cmd_project_ports_add(args) -> int:
    from . import lifecycle
    from .registry.schema import PortMapping, ValidationError

    try:
        host, container = _parse_port_spec(args.spec)
    except ValueError:
        print(f"bad port spec {args.spec!r}: use <container> or <host>:<container> "
              f"(e.g. 5173 or 8080:5173)", file=sys.stderr)
        return 1
    try:
        mapping = PortMapping(container=container, host=host, bind=args.bind, proto=args.proto)
    except ValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    res = lifecycle.add_port(args.slug, mapping)
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def cmd_project_ports_rm(args) -> int:
    from . import lifecycle

    res = lifecycle.remove_port(args.slug, args.target)
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def cmd_project_assets(args) -> int:
    from . import assets

    if not projects.exists(args.slug):
        print(f"no project {args.slug!r}", file=sys.stderr)
        return 1
    project = projects.load(args.slug)
    print(f"asset source: {config.project_assets_dir(args.slug)}")
    print(f"  workspace/ -> /workspace      (sync: {', '.join(project.sync.workspace) or 'none'})")
    print(f"  claude/    -> ~/.claude        (sync: {', '.join(project.sync.claude) or 'none'})")
    print(f"  backups:   {config.backups_dir(args.slug)}")
    if not project.sync.enabled:
        print("  (asset sync is DISABLED for this project — [project.sync] enabled=false)")
    if args.bootstrap:
        note = assets.bootstrap(project)
        print(note or "CLAUDE.md already present in the asset source or workspace bind")
    return 0


def cmd_project_sync(args) -> int:
    from . import lifecycle

    if not projects.exists(args.slug):
        print(f"no project {args.slug!r}", file=sys.stderr)
        return 1
    direction = "in" if args.in_ else "out"
    res = lifecycle.sync(args.slug, direction=direction)
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def cmd_project_delete(args) -> int:
    from . import lifecycle

    if not projects.exists(args.slug):
        print(f"no project {args.slug!r}", file=sys.stderr)
        return 1
    # Sync-check first: print each repo's risk so the operator can't lose work unknowingly. A risky
    # repo blocks the delete unless --force (the CLI analogue of the TUI's "delete anyway").
    plan = lifecycle.delete_plan(args.slug)
    for it in plan.items:
        print(f"  {'⚠' if it.risky else '✓'} {it.dir}: {it.reason}")
    if plan.risky and not args.force:
        print(f"{args.slug}: unsynced work above — commit/push first, or re-run with --force to "
              f"delete anyway (add --keep-workspace to preserve the /workspace checkout).",
              file=sys.stderr)
        return 1
    res = lifecycle.delete_project(args.slug, keep_workspace=args.keep_workspace)
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


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


# --------------------------------------------------------------------------
# config (global settings — ssh keys + general features)
# --------------------------------------------------------------------------
def cmd_config_show(args) -> int:
    from . import gh_token, gitconfig, ssh_agent
    from .registry import settings as settings_registry

    s = settings_registry.load()
    print(f"config: {config.settings_toml_path()}")
    name, email = gitconfig.resolve_identity()
    src = "config" if (s.git_user_name or s.git_user_email) else ("host" if (name or email) else "unset")
    print(f"git identity: {name or '(unset)'} <{email or '(unset)'}>  [{src}]")
    print(f"gh token: {'set' if gh_token.is_set() else '(none — set with `config gh-token`)'}")
    pin = f", pin {s.claude_version_pin}" if s.claude_version_pin else ""
    print(f"claude image: channel {s.claude_channel}{pin}, "
          f"on-start update check {'on' if s.image_update_check else 'off'}")
    print(f"ssh auto-load: {'on' if s.ssh_auto_load else 'off'}")
    if not s.ssh_keys:
        print("ssh keys: (none — add with `claudemanctl config ssh add <path>`)")
        return 0
    loaded = ssh_agent.loaded_fingerprints()  # read-only agent query (once for the whole list)
    print("ssh keys:")
    for k in s.ssh_keys:
        print(f"  {k}  [{ssh_agent.key_status(k, loaded)}]")
    return 0


def cmd_config_ssh_add(args) -> int:
    from . import lifecycle

    res = lifecycle.add_ssh_key(args.path)
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def cmd_config_ssh_rm(args) -> int:
    from . import lifecycle

    res = lifecycle.remove_ssh_key(args.path)
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def cmd_config_ssh_load(args) -> int:
    from . import lifecycle

    res = lifecycle.ensure_ssh_keys(force=True)
    print(res.detail, file=sys.stderr if not res.ok else sys.stdout)
    return 0 if res.ok else 1


def cmd_config_gh_token(args) -> int:
    from . import gh_token

    if args.clear:
        print("gh token cleared (recreate to apply)" if gh_token.clear() else "no gh token was set")
        return 0
    if args.stdin:
        token = sys.stdin.read().strip()
    else:
        import getpass
        token = getpass.getpass("GitHub token (input hidden): ").strip()
    if not token:
        print("no token entered; nothing changed", file=sys.stderr)
        return 1
    gh_token.save(token)  # 0600 in the state tier; never echoed, never in argv
    print("gh token saved (0600). Recreate a project to inject it as GH_TOKEN.")
    return 0


def cmd_config_git(args) -> int:
    from . import gitconfig
    from .registry import settings as settings_registry

    if args.clear:
        settings_registry.set_git_identity("", "")
        print("git identity cleared (containers will inherit the host git config). Recreate to apply.")
        return 0
    if args.name is not None or args.email is not None:
        s = settings_registry.load()
        updated = settings_registry.set_git_identity(
            args.name if args.name is not None else s.git_user_name,
            args.email if args.email is not None else s.git_user_email,
        )
        print(f"git identity set: {updated.git_user_name} <{updated.git_user_email}>. Recreate to apply.")
        return 0
    name, email = gitconfig.resolve_identity()
    print(f"resolved git identity: {name or '(unset)'} <{email or '(unset)'}>")
    print("set with `config git --name '…' --email '…'`, or `--clear` to inherit the host git config.")
    return 0


def cmd_config_image(args) -> int:
    from .registry import schema
    from .registry import settings as settings_registry

    no_pin = getattr(args, "no_pin", False)
    if args.channel is None and args.pin is None and not no_pin and args.check is None:
        s = settings_registry.load()
        print(f"on-start update check: {'on' if s.image_update_check else 'off'}")
        print(f"claude channel: {s.claude_channel}")
        print(f"claude version pin: {s.claude_version_pin or '(none — track the channel)'}")
        print("set with `config image [--channel latest|stable] [--pin X | --no-pin] [--check on|off]`")
        return 0
    update_check = None if args.check is None else (args.check == "on")
    pin = "" if no_pin else args.pin  # --no-pin clears; --pin X sets; neither -> leave unchanged
    try:
        s = settings_registry.set_image_settings(
            update_check=update_check, claude_channel=args.channel, claude_version_pin=pin,
        )
    except schema.ValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"image settings: update check {'on' if s.image_update_check else 'off'}, "
          f"channel {s.claude_channel}, pin {s.claude_version_pin or '(none)'}. "
          f"Start (or recreate) a project to apply.")
    return 0


def cmd_image_build(args) -> int:
    from .docker import images

    overlay = args.overlay or "base"
    # An overlay is `FROM claude-man:base`, so on a clean machine `image build node` needs the base
    # layer first. Build it if missing (never rebuild an existing base) so the single command works.
    if overlay != "base" and not args.dry_run and not images.image_exists("base"):
        print(f"base image {config.image_tag('base')} missing — building it first")
        rc = images.build_one("base", claude_version=args.claude_version)
        if rc != 0:
            return rc
    return images.build_one(overlay, claude_version=args.claude_version, dry_run=args.dry_run)


def cmd_image_smoke(args) -> int:
    from .docker import smoke as smoke_mod

    result = smoke_mod.smoke(args.overlay)
    for line in result.lines:
        print(line)
    if result.ok:
        print(f"\nimage {config.image_tag(args.overlay)} PASSED the hardened-profile smoke")
        return 0
    print(f"\nimage {config.image_tag(args.overlay)} FAILED the smoke", file=sys.stderr)
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
    prof.add_parser("usage", help="token usage per profile across claude-man projects").set_defaults(
        func=cmd_profile_usage
    )
    pl = prof.add_parser("limits", help="per-account 5h/weekly subscription usage (/api/oauth/usage)")
    pl.add_argument("name", nargs="?", help="a single profile (default: all)")
    pl.set_defaults(func=cmd_profile_limits)

    # project
    proj = sub.add_parser("project", help="projects").add_subparsers(dest="cmd", required=True)
    pc = proj.add_parser("create", help="create a project + container")
    pc.add_argument("slug")
    pc.add_argument("--profile")
    pc.add_argument("--overlay", choices=config.OVERLAYS)
    pc.add_argument("--egress", choices=config.EGRESS_MODES)
    pc.set_defaults(func=cmd_project_create)
    pup = proj.add_parser("up", help="create-if-needed + start (checks for a newer claude first)")
    pup.add_argument("slug")
    pup.add_argument("--update-yes", action="store_true", dest="update_yes",
                     help="rebuild the image to the newer claude without prompting")
    pup.add_argument("--no-update", action="store_true", dest="no_update",
                     help="skip the on-start claude-version check entirely")
    pup.set_defaults(func=cmd_project_up)
    for name, func, helptext in [
        ("stop", cmd_project_stop, "stop the container"),
        ("sync-repos", cmd_project_sync_repos, "git fetch each repo"),
        ("pull", cmd_project_pull, "fast-forward each repo (ff-only; skips dirty/diverged)"),
        ("shell", cmd_project_shell, "open a shell in a new terminal"),
        ("claude", cmd_project_claude, "run claude in a new terminal"),
        ("lock", cmd_project_lock, "switch to strict egress"),
        ("unlock", cmd_project_unlock, "return to open egress"),
    ]:
        sp = proj.add_parser(name, help=helptext)
        sp.add_argument("slug")
        sp.set_defaults(func=func)
    pdel = proj.add_parser("delete", help="tear down (container + state + registry; idempotent)")
    pdel.add_argument("slug")
    pdel.add_argument("--keep-workspace", action="store_true", dest="keep_workspace",
                      help="preserve the /workspace checkout on disk (remove container + registry only)")
    pdel.add_argument("--force", action="store_true",
                      help="delete even when repos hold unsynced (uncommitted/unpushed) work")
    pdel.set_defaults(func=cmd_project_delete)
    prc = proj.add_parser("recreate", help="rebuild the container (optionally switch profile)")
    prc.add_argument("slug")
    prc.add_argument("--profile", help="switch the project to this profile (account)")
    prc.add_argument("--force", action="store_true",
                     help="override the account-mismatch guard and re-seed the identity")
    prc.set_defaults(func=cmd_project_recreate)
    pst = proj.add_parser("status", help="live status JOINed with the registry")
    pst.add_argument("slug", nargs="?")
    pst.set_defaults(func=cmd_project_status)

    # project repo (add / rm / list) — manage a project's checked-out repos
    repo = proj.add_parser("repo", help="manage a project's repos").add_subparsers(
        dest="subcmd", required=True
    )
    radd = repo.add_parser("add", help="register a repo + clone it live into /workspace")
    radd.add_argument("slug")
    radd.add_argument("url", help="git remote (git@github.com:org/repo.git or https://…)")
    radd.add_argument("--branch", default="main")
    radd.add_argument("--dir", help="workspace subdir (default: derived from the url)")
    radd.add_argument("--no-clone", action="store_true", dest="no_clone",
                      help="register only; don't clone now (a later `up`/`sync-repos` clones it)")
    radd.set_defaults(func=cmd_project_repo_add)
    rrm = repo.add_parser("rm", help="drop a repo from the registry (checkout left on disk)")
    rrm.add_argument("slug")
    rrm.add_argument("target", help="the repo's workspace dir or its url")
    rrm.add_argument("--purge", action="store_true",
                     help="also delete the on-disk checkout (containment-checked rm -rf)")
    rrm.set_defaults(func=cmd_project_repo_rm)
    rls = repo.add_parser("list", help="per-repo live git state (fetch-less)")
    rls.add_argument("slug")
    rls.set_defaults(func=cmd_project_repos_list)

    # project env (add / rm / list) — environment mounts (ssh + files) synced into the container
    env = proj.add_parser("env", help="environment mounts (ssh + files)").add_subparsers(
        dest="envcmd", required=True
    )
    eadd = env.add_parser("add", help="add an env mount (ssh / file / env var; recreate to apply)")
    eadd.add_argument("slug")
    eadd.add_argument("kind", choices=("ssh", "file", "env"))
    eadd.add_argument("src", nargs="?", help="(file) host path ~/$VARS expanded; (env) the var NAME")
    eadd.add_argument("dst", nargs="?", help="(file) absolute container path")
    eadd.add_argument("--rw", action="store_true", help="(file) writable; default read-only")
    eadd.add_argument("--stdin", action="store_true",
                      help="(env) read the value from stdin instead of a hidden prompt")
    eadd.set_defaults(func=cmd_project_env_add)
    erm = env.add_parser("rm", help="remove an env mount (by 'ssh', a file's container dst, or env NAME)")
    erm.add_argument("slug")
    erm.add_argument("target")
    erm.set_defaults(func=cmd_project_env_rm)
    els = env.add_parser("list", help="list a project's env mounts")
    els.add_argument("slug")
    els.set_defaults(func=cmd_project_env_list)

    # project ports (add / rm / list) — publish container service ports (ingress; recreate to apply)
    ports = proj.add_parser(
        "ports", help="publish container service ports for testing (-p; recreate to apply)"
    ).add_subparsers(dest="portscmd", required=True)
    padd = ports.add_parser("add", help="publish a port (<container> or <host>:<container>; recreate to apply)")
    padd.add_argument("slug")
    padd.add_argument("spec", help="<container> (host=container) or <host>:<container> — container must be ≥1024")
    padd.add_argument("--bind", default="127.0.0.1",
                      help="host bind IP (default 127.0.0.1 = host-only; 0.0.0.0 = expose on the LAN)")
    padd.add_argument("--proto", choices=("tcp", "udp"), default="tcp")
    padd.set_defaults(func=cmd_project_ports_add)
    prm = ports.add_parser("rm", help="unpublish a port (by host port, optionally <host>/<proto>)")
    prm.add_argument("slug")
    prm.add_argument("target")
    prm.set_defaults(func=cmd_project_ports_rm)
    pls = ports.add_parser("list", help="list a project's published ports")
    pls.add_argument("slug")
    pls.set_defaults(func=cmd_project_ports_list)
    prs = proj.add_parser("resync", help="re-validate env-mount sources + re-seed ssh (no recreate)")
    prs.add_argument("slug")
    prs.set_defaults(func=cmd_project_resync)
    pas = proj.add_parser("assets", help="show the asset source dir(s); --bootstrap a stub CLAUDE.md")
    pas.add_argument("slug")
    pas.add_argument("--bootstrap", action="store_true",
                     help="create a stub CLAUDE.md in the asset source if none exists")
    pas.set_defaults(func=cmd_project_assets)
    psy = proj.add_parser("sync", help="sync assets out (binds -> source); --in to sync in")
    psy.add_argument("slug")
    psy.add_argument("--in", dest="in_", action="store_true",
                     help="force sync-IN (asset source -> binds; source wins)")
    psy.set_defaults(func=cmd_project_sync)

    # sync
    sy = sub.add_parser("sync", help="config sync-back").add_subparsers(dest="cmd", required=True)
    syr = sy.add_parser("review", help="open the accept/reject gate")
    syr.add_argument("slug")
    syr.set_defaults(func=cmd_sync_review)
    syp = sy.add_parser("plan", help="dry-run the reconcile")
    syp.add_argument("slug")
    syp.set_defaults(func=cmd_sync_plan)

    # config (global settings — ssh keys auto-loaded into the agent + future general features)
    cfg = sub.add_parser("config", help="global settings (ssh keys, general features)").add_subparsers(
        dest="cmd", required=True
    )
    cfg.add_parser("show", help="show settings + ssh key load status").set_defaults(func=cmd_config_show)
    cssh = cfg.add_parser("ssh", help="ssh keys claude-man auto-loads into the agent").add_subparsers(
        dest="sshcmd", required=True
    )
    csa = cssh.add_parser("add", help="add a key path to config + load it into the agent now")
    csa.add_argument("path", help="host private-key path (e.g. ~/.ssh/id_ed25519)")
    csa.set_defaults(func=cmd_config_ssh_add)
    csr = cssh.add_parser("rm", help="stop auto-loading a key (config only; agent untouched)")
    csr.add_argument("path")
    csr.set_defaults(func=cmd_config_ssh_rm)
    cssh.add_parser("load", help="load all configured keys into the agent now").set_defaults(
        func=cmd_config_ssh_load
    )
    cg = cfg.add_parser("git", help="git author identity injected into containers (recreate to apply)")
    cg.add_argument("--name", help="set git user.name (overrides host inherit)")
    cg.add_argument("--email", help="set git user.email")
    cg.add_argument("--clear", action="store_true", help="clear the override (inherit the host git config)")
    cg.set_defaults(func=cmd_config_git)
    cgt = cfg.add_parser("gh-token",
                         help="set/clear the GitHub token injected as GH_TOKEN (recreate to apply)")
    cgt.add_argument("--clear", action="store_true", help="remove the configured gh token")
    cgt.add_argument("--stdin", action="store_true",
                     help="read the token from stdin instead of an interactive (hidden) prompt")
    cgt.set_defaults(func=cmd_config_gh_token)
    cim = cfg.add_parser("image",
                         help="claude version: channel/pin + the on-start update check")
    cim.add_argument("--channel", choices=config.CLAUDE_CHANNELS,
                     help="track this release channel (default: latest)")
    cim.add_argument("--pin", help="pin an exact claude version (overrides the channel)")
    cim.add_argument("--no-pin", action="store_true", dest="no_pin",
                     help="clear the version pin (track the channel again)")
    cim.add_argument("--check", choices=("on", "off"),
                     help="enable/disable the on-start 'newer claude available?' check")
    cim.set_defaults(func=cmd_config_image)

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
