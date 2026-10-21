"""The cross-project relay (docs/RELAY.md): a project's agent asks ANOTHER project's agent for help.

A project opts in as a target with a ``[project.helpdesk]`` (who may ask + the tasks it offers). A
requester's agent drops a request file into its own ``/workspace/.relay/out/`` with the host-written
helper; the HOST relay (inside the TUI, or ``claudemanctl relay run``) picks it up, identifies the
sender by whose bind it came from, checks it against the target's helpdesk, asks the operator when the
task (or an egress-lock crossing) requires it, runs ONE headless session in the target's container
(the 7-run seam, one-agent-per-container guarded) and writes the reply back into the requester's
``/workspace/.relay/in/``. No new mount, socket, capability or network path — the floor is untouched.

Modules: ``protocol`` (the wire files, pure), ``policy`` (who/what/approval/prompt, pure), ``fsio``
(the no-follow bind IO), ``store`` (host-only records + audit log), ``context`` (the CLAUDE.md block,
directory and helper), ``engine`` (the relay loop + operator verbs).
"""
